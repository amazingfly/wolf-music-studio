#!/usr/bin/env python3
"""Resumable Gemma songwriting, independent review, and atomic YuE2 queue submission."""
import argparse
from contextlib import ExitStack, nullcontext
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time

from checkSongPrompts import SongCorpus, parse_song, quality_checks, strict_json
from gemma_runtime import GemmaSession
from makeConfig import publish
from run_config import ROOT, component, songs
from songwriter_client import ASSETS, SAMPLING, request_song
from track_registry import atomic_json
from songwriter_policy import creative_song, repair_plan, apply_patch_response

AUDIO_SEEDS = [85300, 85301, 85303, 160601, 160602, 20261002, 330017, 551879, 763243, 990331]
REFERENCES = ['bone_and_iron_v3', 'frizzed_and_fractured_v2', 'pelt_and_polish_v3',
              'bone_and_iron_v3', 'frizzed_and_fractured_v2', 'bone_and_iron_v3',
              'frizzed_and_fractured_v2', 'pelt_and_polish_v3', 'bone_and_iron_v3', 'frizzed_and_fractured_v2']


def generation_seed(base, idea, variation, attempt):
    raw = f'{base}:{idea}:{variation}:{attempt}'.encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], 'big') % (2**32 - 1)


def schedule(ideas, per_idea, stamp):
    # Round-robin concepts: the initial listening batch explores the whole list.
    return [{'id': f'gemmawolf_{component(idea["id"])}_v{v+1:02d}_{stamp}',
             'idea_id': idea['id'], 'variation': v,
             'audio_seed': AUDIO_SEEDS[(v+i) % len(AUDIO_SEEDS)],
             'template': REFERENCES[(v+i) % len(REFERENCES)],
             'status': 'pending', 'attempts': 0}
            for v in range(per_idea) for i, idea in enumerate(ideas)]


def prepare(campaign, assets=ASSETS, per_idea=10, batch_size=10, base_seed=20261002, max_attempts=4,
            writer_version=1, baseline=None):
    campaign = Path(campaign).resolve()
    campaign.mkdir(parents=True, exist_ok=True)
    manifest = campaign / 'manifest.json'
    if manifest.exists():
        value = strict_json(manifest.read_text())
        for filename, digest in value['assets_sha256'].items():
            if hashlib.sha256((campaign/'assets'/filename).read_bytes()).hexdigest() != digest:
                raise ValueError(f'Campaign asset changed: {filename}; start a new campaign for new prompts')
        return value
    frozen = campaign / 'assets'
    frozen.mkdir(exist_ok=True)
    hashes = {}
    for filename in ['system_prompt.txt', 'ideas.json', 'examples.json', 'reference_sources.json']:
        raw = (Path(assets)/filename).read_bytes()
        (frozen/filename).write_bytes(raw)
        hashes[filename] = hashlib.sha256(raw).hexdigest()
    ideas = strict_json((frozen/'ideas.json').read_text())
    if not isinstance(ideas, list) or not ideas or len({i['id'] for i in ideas}) != len(ideas):
        raise ValueError('Concepts must be a nonempty list with unique IDs')
    for idea in ideas:
        component(idea['id'])
        if not isinstance(idea.get('concept'), str) or not idea['concept'].strip():
            raise ValueError('Each idea needs a concept')
        if writer_version == 2 and (not isinstance(idea.get('situations'), list) or
            len(idea['situations']) < per_idea or any(not isinstance(s, str) or not s.strip() for s in idea['situations'])):
            raise ValueError('V2 needs a distinct situation for each scheduled variation')
    value = {'campaign': component(campaign.name), 'created_at': datetime.now().astimezone().isoformat(),
             'assets_sha256': hashes, 'writer_version': writer_version,
             'baseline_campaign': str(Path(baseline).resolve()) if baseline else None,
             'implementation_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                 for name in ['gemma_songwriter.py','songwriter_client.py','songwriter_policy.py','checkSongPrompts.py','gemma_prompt_cache.py']},
             'per_idea': per_idea, 'batch_size': batch_size,
             'base_seed': base_seed, 'max_attempts': max_attempts, 'sampling': SAMPLING,
             'slots': schedule(ideas, per_idea, component(campaign.name)), 'submissions': [], 'status': 'ready'}
    atomic_json(manifest, value)
    return value


def save(campaign, state):
    state['updated_at'] = datetime.now().astimezone().isoformat()
    atomic_json(Path(campaign)/'manifest.json', state)
    from songwriter_metrics import write_metrics
    write_metrics(campaign, state)


def accepted_rows(campaign, state):
    rows = []
    for slot in state['slots']:
        if slot['status'] == 'accepted':
            row = parse_song((Path(campaign)/'accepted'/(slot['id']+'.json')).read_text())
            if row['id'] != slot['id'] or row['seed'] != slot['audio_seed']:
                raise ValueError('Accepted prompt identity changed')
            rows.append(row)
    return rows


def write_aggregate(campaign, state):
    rows = accepted_rows(campaign, state)
    if rows:
        destination = Path(campaign)/'accepted_config.json'
        # Validate the aggregate before replacing the review copy. Pending uses publish().
        temporary = destination.with_suffix('.tmp')
        atomic_json(temporary, {'songs': rows})
        songs(temporary)
        temporary.replace(destination)
    return rows


def submission_exists(root, name, expected):
    expected_ids = [row['id'] for row in expected]
    for area in ['pending', 'running', 'done', 'failed', 'cancelled', 'completed']:
        for path in (Path(root)/'queue'/area).rglob(name):
            strict_json(path.read_text())
            if songs(path) != expected:
                raise ValueError(f'Queued submission changed: {path}; refusing duplicate submission')
            return str(path)
    # Also recover after a user archives the queue's job folders but retains run provenance.
    for manifest in (Path(root)/'outputs').glob('*/run.json'):
        info = strict_json(manifest.read_text())
        if Path(info.get('source_config', '')).name == name:
            source = manifest.parent/'source_config.json'
            if [r['id'] for r in songs(source)] == expected_ids:
                if songs(source) != expected:
                    raise ValueError(f'Archived submission changed: {source}')
                return str(source)
    return None


def flush_queue(campaign, state, root=ROOT, final=False):
    """Durable intention before publish; recover a watcher rename without duplicating music."""
    campaign, root = Path(campaign), Path(root)
    rows = write_aggregate(campaign, state)
    by_id = {row['id']: row for row in rows}
    for entry in state['submissions']:
        if entry.get('published'):
            continue
        expected = [by_id[track_id] for track_id in entry['ids']]
        existing = submission_exists(root, entry['filename'], expected)
        destination = root/'queue/pending'/entry['filename']
        if not existing:
            publish(expected, destination)
            existing = str(destination)
        entry.update(published=True, queued_path=existing, published_at=time.time())
        save(campaign, state)
        print(f'QUEUED {len(expected)} songs: {entry["filename"]}', flush=True)
    submitted = {track_id for entry in state['submissions'] for track_id in entry['ids']}
    available = [row for row in rows if row['id'] not in submitted]
    while available:
        # Start the first verified pilot immediately; following configs contain ten songs.
        count = 1 if not state['submissions'] else state['batch_size']
        if len(available) < count and not final:
            break
        selected, available = available[:count], available[count:]
        filename = f'{state["campaign"]}_batch{len(state["submissions"])+1:03d}.json'
        archived = campaign/'submissions'/filename
        if archived.exists():
            if songs(archived) != selected:
                raise ValueError(f'Uncommitted submission differs: {archived}')
        else:
            publish(selected, archived)
        entry = {'filename': filename, 'ids': [r['id'] for r in selected], 'published': False}
        state['submissions'].append(entry)
        save(campaign, state)
        existing = submission_exists(root, filename, selected)
        destination = root/'queue/pending'/filename
        if not existing:
            publish(selected, destination)
            existing = str(destination)
        entry.update(published=True, queued_path=existing, published_at=time.time())
        save(campaign, state)
        print(f'QUEUED {len(selected)} songs: {filename}', flush=True)
    return rows


def prior_hooks(campaign, state, idea_id):
    values = []
    if state.get('writer_version', 1) == 2:
        idea = next(i for i in strict_json((Path(campaign)/'assets/ideas.json').read_text()) if i['id'] == idea_id)
        # Human-authored semantic briefs; never feed earlier accepted chorus words back.
        return [{'variation': s['variation']+1, 'story_choice': idea['situations'][s['variation']]}
                for s in state['slots'] if s['status'] == 'accepted' and s['idea_id'] == idea_id]
    for slot in state['slots']:
        if slot['status'] != 'accepted' or slot['idea_id'] != idea_id:
            continue
        row = strict_json((Path(campaign)/'accepted'/(slot['id']+'.json')).read_text())
        hook = re.search(r'\[Chorus\](.*?)(?=\n\[|\Z)', row['lyrics'], re.S)
        values.append({'title': row['title'], 'chorus': hook.group(1).strip() if hook else ''})
    return values


def assess(text, slot, corpus, writer_version=1):
    changes = []
    if writer_version == 2:
        row, changes = creative_song(text, slot)
    else:
        row = parse_song(text)
    if row['id'] != slot['id'] or row['seed'] != slot['audio_seed']:
        raise ValueError('Use the requested ID and audio seed exactly')
    quality_checks(row)
    review = corpus.compare(row)
    if writer_version == 2:
        review['packaging_fixes'] = changes
    return row, review


def accept(campaign, state, slot, row, review, provenance):
    atomic_json(Path(campaign)/'accepted'/(slot['id']+'.json'), row)
    atomic_json(Path(campaign)/'reviews'/(slot['id']+'.json'), {**review, 'generation_directory': str(provenance)})
    slot.update(status='accepted', accepted_at=time.time(), generation_directory=str(provenance))
    save(campaign, state)
    print(f'ACCEPTED: {row["title"]} ({row["id"]})', flush=True)


def import_pilot(campaign, state, path, root=ROOT):
    slot = state['slots'][0]
    if slot['status'] == 'accepted':
        return
    original = parse_song(Path(path).read_text())
    quality_checks(original)
    # Assign campaign identity only. The authored musical fields/lyrics remain byte-for-byte strings.
    row = {**original, 'id': slot['id'], 'seed': slot['audio_seed']}
    review = SongCorpus.from_workspace(root).compare(row)
    if review['status'] != 'accepted':
        raise ValueError('Pilot is too similar to existing lyrics; inspect comparison before launching')
    directory = Path(campaign)/'pilot_import'
    shutil.copytree(Path(path).parent, directory, dirs_exist_ok=True)
    atomic_json(directory/'identity_assignment.json', {'original_id': original['id'], 'campaign_id': row['id'],
        'original_audio_seed': original['seed'], 'campaign_audio_seed': row['seed'], 'lyrics_changed': False})
    accept(campaign, state, slot, row, review, directory)


def run_campaign(campaign, state, root=ROOT, session_factory=GemmaSession, client=request_song):
    """One lazy model session and hardware turn for the entire campaign."""
    state['runtime_policy'] = {'model_lifecycle':'resident_campaign','prompt_cache':'resident_shared_prefix',
                               'context':int(os.environ.get('YUE2_GEMMA_CONTEXT',12288)),
                               'gpu_layers':int(os.environ.get('YUE2_GEMMA_GPU_LAYERS',16)),'ctk':'q8_0','ctv':'q8_0'}
    save(campaign,state)
    with ExitStack() as lifetime:
        model = None
        def borrow(**_request_detail):
            nonlocal model
            if model is None:
                model = lifetime.enter_context(session_factory(detail=f'songwriting campaign: {Path(campaign).name}',
                    cache_directory=Path(campaign)/'kv_cache'))
            return nullcontext(model)
        return _run_campaign(campaign, state, root, borrow, client)


def _run_campaign(campaign, state, root=ROOT, session_factory=GemmaSession, client=request_song):
    campaign = Path(campaign)
    ideas = {i['id']: i for i in strict_json((campaign/'assets/ideas.json').read_text())}
    # Recover a power loss between writing an accepted prompt and committing its slot.
    for slot in state['slots']:
        path = campaign/'accepted'/(slot['id']+'.json')
        review_path = campaign/'reviews'/(slot['id']+'.json')
        if slot['status'] != 'accepted' and path.exists() and review_path.exists():
            row = parse_song(path.read_text()); quality_checks(row)
            review = strict_json(review_path.read_text())
            if row['id'] != slot['id'] or row['seed'] != slot['audio_seed'] or review['status'] != 'accepted':
                raise ValueError('Incomplete acceptance has inconsistent identity/review')
            slot.update(status='accepted', generation_directory=review['generation_directory'])
            save(campaign, state)
    flush_queue(campaign, state, root)
    state['status'] = 'running'; save(campaign, state)
    for slot in state['slots']:
        if (campaign/'pause_after_draft').exists():
            flush_queue(campaign, state, root, final=True)
            state['status'] = 'paused_at_draft_boundary'; save(campaign, state)
            return 0
        if slot['status'] in {'accepted', 'exhausted'}:
            continue
        feedback = slot.get('feedback', '')
        previous = campaign/'attempts'/slot['id']/f'attempt{slot["attempts"]:02d}'
        recovered = None
        if slot['status'] == 'generating' and (previous/'generation.json').exists():
            if strict_json((previous/'generation.json').read_text()).get('finish_reason') == 'stop':
                recovered = (previous, (previous/'response.txt').read_text())
        while recovered is not None or slot['attempts'] < state['max_attempts']:
            if recovered is not None:
                directory, text = recovered; recovered = None
                attempt = slot['attempts']
                print(f'RECOVERED completed draft: {slot["id"]}, attempt {attempt}', flush=True)
            else:
                slot['attempts'] += 1
                attempt = slot['attempts']
                seed = generation_seed(state['base_seed'], slot['idea_id'], slot['variation'], attempt)
                directory = campaign/'attempts'/slot['id']/f'attempt{attempt:02d}'
                slot.update(status='generating', generation_seed=seed)
                save(campaign, state)
                print(f'GENERATING {slot["id"]}, attempt {attempt}, Gemma seed {seed}, Yue2 seed {slot["audio_seed"]}', flush=True)
                # Borrow the resident campaign model, including retries and reviews.
                with session_factory(detail=f'songwriting: {slot["id"]}') as model:
                    try:
                        cache_kwargs = {'prompt_cache':getattr(model,'prompt_cache',None)} if client is request_song else {}
                        if state.get('writer_version', 1) == 2:
                            cache_kwargs['writer_version'] = 2
                        text = client(model.url, ideas[slot['idea_id']], slot['id'], seed, slot['audio_seed'], directory,
                            variation=slot['variation'], avoid=prior_hooks(campaign, state, slot['idea_id']),
                            assets=campaign/'assets', feedback=feedback, template=slot['template'], **cache_kwargs)
                    except ValueError as exc:
                        text = None; feedback = str(exc)
            # Refresh so new Colab/library prompts are included as well as campaign acceptances.
            corpus = SongCorpus.from_workspace(root)
            for prior in accepted_rows(campaign, state):
                corpus.add(prior, campaign/'accepted'/(prior['id']+'.json'))
            ledger = {'writer_version': state.get('writer_version', 1), 'id': slot['id'],
                      'idea_id': slot['idea_id'], 'variation': slot['variation'], 'attempt': attempt,
                      'generation_directory': str(directory), 'corpus_unique_lyrics': len(corpus.documents),
                      'packaging_fixes': [], 'repair_attempted': False, 'repair_accepted': False,
                      'raw_valid': False, 'validation_passed': False, 'similarity_passed': False,
                      'result': 'invalid'}
            try:
                if text is None:
                    raise ValueError(feedback)
                row, review = assess(text, slot, corpus, state.get('writer_version', 1))
                ledger.update(validation_passed=True, packaging_fixes=review.get('packaging_fixes', []),
                              raw_valid=not review.get('packaging_fixes'),
                              similarity_passed=review['status']=='accepted',
                              result='accepted' if review['status']=='accepted' else 'tooSimilar')
            except ValueError as exc:
                feedback = str(exc)
                ledger['error'] = feedback
                atomic_json(campaign/'invalid'/f'{slot["id"]}_attempt{attempt:02d}.review.json',
                            {'error': feedback, 'generation_directory': str(directory)})
                print(f'INVALID {slot["id"]}: {feedback}', flush=True)
            else:
                if review['status'] == 'accepted':
                    accept(campaign, state, slot, row, review, directory)
                    atomic_json(campaign/'attempt_results'/slot['id']/f'attempt{attempt:02d}.json', ledger)
                    flush_queue(campaign, state, root)
                    break
                name = f'{slot["id"]}_attempt{attempt:02d}'
                atomic_json(campaign/'tooSimilar'/(name+'.json'), row)
                atomic_json(campaign/'tooSimilar'/(name+'.review.json'), review)
                plan = repair_plan(row, review, corpus) if state.get('writer_version', 1) == 2 else None
                if plan:
                    ledger['repair_attempted'] = True
                    repair_directory = campaign/'repairs'/slot['id']/f'attempt{attempt:02d}'
                    atomic_json(repair_directory/'plan.json', plan)
                    try:
                        existing_generation = repair_directory/'generation.json'
                        if existing_generation.exists() and strict_json(existing_generation.read_text()).get('finish_reason') == 'stop':
                            patch_text = (repair_directory/'response.txt').read_text()
                        else:
                            with session_factory(detail=f'line repair: {slot["id"]}') as model:
                                kwargs = {'prompt_cache': getattr(model, 'prompt_cache', None)} if client is request_song else {}
                                patch_text = client(model.url, ideas[slot['idea_id']], slot['id'],
                                    generation_seed(state['base_seed'], slot['idea_id'], slot['variation'], attempt+1000),
                                    slot['audio_seed'], repair_directory, variation=slot['variation'],
                                    assets=campaign/'assets', template=slot['template'], writer_version=2,
                                    patch={'song': {k:row[k] for k in ['title','style','lyrics']}, 'plan':plan}, **kwargs)
                        repaired = apply_patch_response(row, plan, patch_text)
                        repair_corpus = SongCorpus.from_workspace(root)
                        for prior in accepted_rows(campaign, state):
                            repair_corpus.add(prior, campaign/'accepted'/(prior['id']+'.json'))
                        repaired_review = repair_corpus.compare(repaired)
                        atomic_json(repair_directory/'patched_song.json', repaired)
                        atomic_json(repair_directory/'review.json', repaired_review)
                        if repaired_review['status'] == 'accepted':
                            repaired_review.update(packaging_fixes=review.get('packaging_fixes', []),
                                lyric_repair_directory=str(repair_directory), original_similarity_review=str(campaign/'tooSimilar'/(name+'.review.json')))
                            ledger.update(repair_accepted=True, result='accepted')
                            accept(campaign, state, slot, repaired, repaired_review, directory)
                            atomic_json(campaign/'attempt_results'/slot['id']/f'attempt{attempt:02d}.json', ledger)
                            flush_queue(campaign, state, root)
                            break
                    except ValueError as exc:
                        ledger['repair_error'] = str(exc)
                        atomic_json(repair_directory/'failure.json', {'error':str(exc)})
                matches = [m for m in review['closest'] if m['reasons']]
                feedback = 'Write a genuinely new song, not a paraphrase. Too much prior lyric reuse: ' + json.dumps(matches[:2])
                if not matches:
                    feedback += ' Excessive three-word phrasing reused across multiple earlier songs.'
                feedback = feedback[:5000]
                if state.get('writer_version', 1) == 2:
                    feedback = 'The previous draft reused an existing lyric line or phrase. Invent a new plot treatment and new sung wording, especially the hook. Keep the reference instrumentation and section cues. Do not paraphrase your previous response.'
                print(f'QUARANTINED {slot["id"]}: {campaign/"tooSimilar"/(name+".json")}', flush=True)
            atomic_json(campaign/'attempt_results'/slot['id']/f'attempt{attempt:02d}.json', ledger)
            slot.update(status='pending', feedback=feedback)
            save(campaign, state)
            if (campaign/'pause_after_draft').exists():
                flush_queue(campaign, state, root, final=True)
                state['status'] = 'paused_at_draft_boundary'; save(campaign, state)
                return 0
        else:
            slot['status'] = 'exhausted'; save(campaign, state)
            print(f'EXHAUSTED {slot["id"]}; drafts retained for review', flush=True)
    flush_queue(campaign, state, root, final=True)
    counts = {status: sum(s['status'] == status for s in state['slots']) for status in ['accepted','exhausted']}
    state['status'] = 'complete' if not counts['exhausted'] else 'finished_with_rejections'
    save(campaign, state)
    print(f'Campaign finished: {counts}; aggregate: {campaign/"accepted_config.json"}', flush=True)
    return 0


class Tee:
    def __init__(self, original, file):
        self.original, self.file = original, file
    def write(self, text):
        self.original.write(text); self.file.write(text); self.file.flush()
        return len(text)
    def flush(self):
        self.original.flush(); self.file.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path)
    parser.add_argument('--assets', type=Path, help='Defaults to songwriter/v2 for new V2 campaigns')
    parser.add_argument('--per-idea', type=int, default=10)
    parser.add_argument('--batch-size', type=int, default=10)
    parser.add_argument('--base-seed', type=int, default=20261002)
    parser.add_argument('--max-attempts', type=int, default=4)
    parser.add_argument('--writer-version', type=int, choices=[1,2], default=2,
                        help='New campaign policy; existing campaigns retain their frozen version')
    parser.add_argument('--baseline', type=Path, help='Original campaign to compare with this new campaign')
    parser.add_argument('--import-pilot', type=Path)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--retry-exhausted', action='store_true')
    args = parser.parse_args(argv)
    if min(args.per_idea, args.batch_size, args.max_attempts) < 1:
        parser.error('Counts must be positive')
    prefix='gemmaWolf_v2_' if args.writer_version==2 else 'gemmaWolf_'
    campaign = (args.campaign or ASSETS/'runs'/datetime.now().strftime(prefix+'%Y%m%d_%H%M%S')).expanduser().resolve()
    assets = args.assets or (ASSETS/'v2' if args.writer_version==2 else ASSETS)
    if args.status:
        state = strict_json((campaign/'manifest.json').read_text())
        from songwriter_metrics import write_metrics
        print(json.dumps(write_metrics(campaign, state), indent=2))
        return 0
    campaign.mkdir(parents=True, exist_ok=True)
    with (campaign/'writer.lock').open('a') as lock, (campaign/'pipeline.log').open('a') as logfile:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_out, original_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tee(sys.stdout, logfile), Tee(sys.stderr, logfile)
        def interrupted(signum, _frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, interrupted)
        try:
            state = prepare(campaign, assets, args.per_idea, args.batch_size, args.base_seed, args.max_attempts,
                            args.writer_version, args.baseline)
            if args.retry_exhausted:
                state['max_attempts'] += args.max_attempts
                for slot in state['slots']:
                    if slot['status'] == 'exhausted':
                        slot.update(status='pending')
                save(campaign, state)
            if args.import_pilot:
                import_pilot(campaign, state, args.import_pilot)
            return run_campaign(campaign, state)
        finally:
            sys.stdout.flush(); sys.stderr.flush()
            sys.stdout, sys.stderr = original_out, original_err

if __name__ == '__main__':
    raise SystemExit(main())
