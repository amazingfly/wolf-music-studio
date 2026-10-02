"""Auditable campaign metrics, including reconstruction of legacy draft outcomes."""
from collections import Counter
from pathlib import Path
import re
from checkSongPrompts import strict_json
from track_registry import atomic_json


def outcomes(campaign, state=None):
    campaign = Path(campaign)
    state = state or strict_json((campaign/'manifest.json').read_text())
    by_key = {}
    for path in (campaign/'attempt_results').glob('*/*.json'):
        value = strict_json(path.read_text())
        by_key[(value['id'], value['attempt'])] = value
    # Legacy campaigns predate the ledger. Recover the exact existing review outcomes,
    # not a new comparison against the now larger corpus.
    for area in ('invalid','tooSimilar'):
        for path in (campaign/area).glob('*.review.json'):
            match = re.fullmatch(r'(.+)_attempt(\d+)\.review\.json', path.name)
            if not match: continue
            key = (match.group(1), int(match.group(2)))
            if key in by_key: continue
            review = strict_json(path.read_text())
            by_key[key] = {'id':key[0], 'attempt':key[1], 'result':area,
                'raw_valid':area!='invalid', 'validation_passed':area!='invalid',
                'similarity_passed':False, 'packaging_fixes':[],
                'repair_attempted':False, 'repair_accepted':False,
                'error':review.get('error'), 'reconstructed_legacy':True}
    imported = 0
    for slot in state['slots']:
        if slot['status'] != 'accepted': continue
        review = strict_json((campaign/'reviews'/(slot['id']+'.json')).read_text())
        generation = Path(review['generation_directory'])
        if generation.name == 'pilot_import':
            imported += 1; continue
        match = re.fullmatch(r'attempt(\d+)',generation.name)
        if not match: continue
        key = (slot['id'],int(match.group(1)))
        if key not in by_key:
            by_key[key] = {'id':key[0], 'attempt':key[1], 'result':'accepted',
                'raw_valid':not review.get('packaging_fixes'), 'validation_passed':True,
                'similarity_passed':not review.get('lyric_repair_directory'),
                'packaging_fixes':review.get('packaging_fixes',[]),
                'repair_attempted':bool(review.get('lyric_repair_directory')),
                'repair_accepted':bool(review.get('lyric_repair_directory')),
                'reconstructed_legacy':True}
    return list(by_key.values()), imported


def metrics(campaign, state=None):
    campaign = Path(campaign)
    state = state or strict_json((campaign/'manifest.json').read_text())
    rows, imported = outcomes(campaign,state)
    total = len(rows)
    slots = Counter(s['status'] for s in state['slots'])
    accepted = sum(r['result']=='accepted' for r in rows)
    raw_success = sum(r['raw_valid'] and r['similarity_passed'] for r in rows)
    packaged_success = sum(r['validation_passed'] and r['similarity_passed'] for r in rows)
    raw_failures = total - raw_success
    first_drafts = [r for r in rows if r['attempt']==1]
    results = Counter(r['result'] for r in rows)
    finished_requests = []
    for base in ('attempts','repairs'):
        for path in (campaign/base).glob('*/*/generation.json'):
            finished_requests.append((base, strict_json(path.read_text())))
    full_requests = [r for base,r in finished_requests if base=='attempts']
    repair_requests = [r for base,r in finished_requests if base=='repairs']
    active_directories = {Path(s['generation_directory']).resolve() for s in state['slots']
                          if s['status']=='generating' and s.get('generation_directory')}
    # generation_directory is committed on acceptance; while generating use the scheduled path.
    active_directories.update((campaign/'attempts'/s['id']/f'attempt{s["attempts"]:02d}').resolve()
                              for s in state['slots'] if s['status']=='generating')
    unfinished = [p.parent for p in (campaign/'attempts').glob('*/*/request.json')
                  if not (p.parent/'generation.json').exists()]
    def rate(n): return round(100*n/total,2) if total else None
    return {'campaign':state['campaign'], 'writer_version':state.get('writer_version',1),
        'status':state['status'], 'scheduled_tracks':len(state['slots']), 'tracks':dict(slots),
        'reviewed_full_drafts':total, 'imported_pilots_excluded_from_draft_rates':imported,
        'raw_first_pass_accepted':raw_success, 'raw_first_pass_failed':raw_failures,
        'raw_first_pass_success_percent':rate(raw_success),
        'after_packaging_accepted':packaged_success, 'after_packaging_success_percent':rate(packaged_success),
        'terminal_marker_fixes':sum(bool(r['packaging_fixes']) for r in rows),
        'raw_validation_failures':sum(not r['raw_valid'] for r in rows),
        'unresolved_validation_failures':sum(not r['validation_passed'] for r in rows),
        'original_similarity_rejections':sum(r['validation_passed'] and not r['similarity_passed'] for r in rows),
        'line_repairs_attempted':sum(r['repair_attempted'] for r in rows),
        'line_repairs_accepted':sum(r['repair_accepted'] for r in rows),
        'eventual_draft_accepted':accepted, 'eventual_draft_failed':total-accepted,
        'eventual_draft_success_percent':rate(accepted), 'final_results':dict(results),
        'accepted_on_first_full_draft':sum(s['status']=='accepted' and s['attempts']==1 for s in state['slots']),
        'accepted_after_full_rewrite':sum(s['status']=='accepted' and s['attempts']>1 for s in state['slots']),
        'initial_full_drafts_reviewed':len(first_drafts),
        'initial_full_drafts_raw_accepted':sum(r['raw_valid'] and r['similarity_passed'] for r in first_drafts),
        'initial_full_drafts_eventually_accepted':sum(r['result']=='accepted' for r in first_drafts),
        'validation_failure_reasons':dict(Counter(r['error'] for r in rows if r.get('error'))),
        'completed_full_requests':len(full_requests), 'completed_repair_requests':len(repair_requests),
        'in_flight_full_requests':sum(p.resolve() in active_directories for p in unfinished),
        'interrupted_unreviewed_full_requests':sum(p.resolve() not in active_directories for p in unfinished),
        'full_generation_seconds':round(sum(r.get('seconds',0) for r in full_requests),1),
        'repair_generation_seconds':round(sum(r.get('seconds',0) for r in repair_requests),1),
        'submitted_songs':sum(len(s['ids']) for s in state['submissions'] if s.get('published')),
        'published_configs':sum(bool(s.get('published')) for s in state['submissions']),
        'sampling':state['sampling'], 'assets_sha256':state['assets_sha256'],
        'implementation_sha256':state.get('implementation_sha256',{}),
        'runtime_policy':state.get('runtime_policy',{}),
        'rate_denominator':'Every reviewed full draft, including full rewrites; repairs counted separately. In-flight and interrupted unreviewed requests excluded.',
        'comparison_limit':'Production-version comparison, not a controlled listening test. V2 sees a larger evolving historical corpus. Musical quality is scored by listening, separately.'}


def write_metrics(campaign,state=None):
    value=metrics(campaign,state)
    atomic_json(Path(campaign)/'metrics.json',value)
    return value
