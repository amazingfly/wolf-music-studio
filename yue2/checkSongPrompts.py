#!/usr/bin/env python3
"""Validate YuE2 configs and quarantine substantial lyric borrowing for review."""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

from makeConfig import unique_keys, reject_constant, publish
from run_config import ROOT, songs
from track_registry import atomic_json

STOP = set('a an the i me my mine you your yours he she her hers his it its we our ours they them their '
           'is are was were am be been being do does did have has had got can could will would shall should '
           'and or but if as at by in into on onto to of for from with without not no so that this these those '
           'then than there here just now all some any every one up down out off back again oh yeah'.split())


def sung_text(lyrics):
    text = lyrics.replace('’', "'").replace('′', "'").replace('‘', "'")
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\((?:male|female|both|all|choir)\s*:\s*([^)]*)\)', r' \1 ', text, flags=re.I)
    text = re.sub(r'^\s*\([^\n]*\)\s*$', '', text, flags=re.M)
    # Remaining inline performance annotations are omitted; non-direction adlibs stay.
    text = re.sub(r'\((?:female|male|drums|guitar|synth|instrumental)[^)]*\)', '', text, flags=re.I)
    return text.replace('(', '').replace(')', '')


def tokens(text):
    text = text.casefold().replace('’', "'")
    contractions = {"i'm": 'i am', "don't": 'do not', "can't": 'can not', "won't": 'will not',
                    "it's": 'it is', "there's": 'there is', "i'll": 'i will', "i've": 'i have'}
    for a, b in contractions.items():
        text = text.replace(a, b)
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text)


def features(row):
    text = sung_text(row['lyrics'])
    wordlist = tokens(text)
    grams = {n: {tuple(wordlist[i:i+n]) for i in range(len(wordlist)-n+1)} for n in (3,4,5)}
    lines = {tuple(tokens(line)) for line in text.splitlines() if len(tokens(line)) >= 6}
    return {'tokens': wordlist, 'grams': grams, 'lines': lines,
            'content': {word for word in wordlist if word not in STOP},
            'fingerprint': hashlib.sha256(' '.join(wordlist).encode()).hexdigest()}


def walk_songs(data):
    if isinstance(data, dict):
        if isinstance(data.get('lyrics'), str) and isinstance(data.get('style'), str):
            yield data
        for value in data.values():
            yield from walk_songs(value)
    elif isinstance(data, list):
        for value in data:
            yield from walk_songs(value)


def strict_json(text):
    return json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)


def parse_song(text):
    text = text.strip()
    if text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, re.S)
        if not match:
            raise ValueError('Malformed fenced JSON response')
        text = match.group(1)
    value = strict_json(text)
    if isinstance(value, dict) and 'songs' in value:
        if not isinstance(value['songs'], list) or len(value['songs']) != 1 or set(value) != {'songs'}:
            raise ValueError('Expected exactly one song')
        value = value['songs'][0]
    if not isinstance(value, dict):
        raise ValueError('Expected a single song JSON object')
    return validate_song(value)


def validate_song(value):
    with tempfile.TemporaryDirectory(prefix='yue2-song-validation-') as directory:
        path = Path(directory) / 'song.json'
        atomic_json(path, value)
        rows = songs(path)
    if len(rows) != 1:
        raise ValueError('Expected a single song')
    return rows[0]


def quality_checks(row):
    """Bounded mechanical checks; this cannot score the eventual music or emotion."""
    words = tokens(sung_text(row['lyrics']))
    if not 250 <= len(words) <= 650:
        raise ValueError(f'Expected 250–650 sung words; got {len(words)}')
    headings = re.findall(r'^\[([^\]]+)\]', row['lyrics'], re.M)
    if not any(h.casefold() == 'intro' for h in headings):
        raise ValueError('Missing [Intro]')
    for section in ['verse 1', 'chorus', 'final chorus', 'outro']:
        if not any(h.casefold().startswith(section) for h in headings):
            raise ValueError(f'Missing [{section}]')
    if not any('breakdown' in h.casefold() or 'bridge' in h.casefold() for h in headings):
        raise ValueError('Missing bridge/breakdown contrast')
    if not re.search(r'\[end\]\s*$', row['lyrics'], re.I):
        raise ValueError('End the resolved [Outro] with [End]')
    cues = ' '.join(re.findall(r'\([^)]*\)', row['lyrics'])).casefold()
    if not ('clear' in cues and 'belt' in cues and ('climb' in cues or 'ris' in cues)):
        raise ValueError('Include an explicit clear female phrase rising/climbing into a metal belt cue')
    style = row['style'].casefold()
    for pattern, label in [(r'female', 'female lead'), (r'(?:double[- ]kick|double[- ]bass)', 'rapid kick drums'),
                           (r'synth|supersaw', 'synth'), (r'\b(?:15[5-9]|16[0-8])\s*bpm\b', '155–168 BPM')]:
        if not re.search(pattern, style):
            raise ValueError(f'Missing {label} in style')
    if len(tokens(style)) > 120:
        raise ValueError('Style is an adjective wall; keep it compact like the references')


class SongCorpus:
    def __init__(self, rows=()):
        self.documents = []
        self.seen = set()
        self.df = Counter()
        self.index = defaultdict(set)
        for row, source in rows:
            self.add(row, source)

    def add(self, row, source):
        item = features(row)
        if item['fingerprint'] in self.seen:
            return
        self.seen.add(item['fingerprint'])
        index = len(self.documents)
        self.documents.append({**item, 'id': row.get('id', row.get('title', 'unknown')), 'source': str(source)})
        for gram in item['grams'][3]:
            self.df[gram] += 1
            self.index[gram].add(index)

    @classmethod
    def from_workspace(cls, root=ROOT, exclude=()):
        root = Path(root)
        paths = set()
        for base in [root/'requests', root/'queue/pending', root/'queue/running', root/'queue/done',
                     root/'queue/cancelled', root/'queue/completed', root/'queue/failed']:
            if base.exists():
                paths.update(base.rglob('*.json')); paths.update(base.rglob('*.jsonl'))
        paths.update((root/'examples').rglob('*.json'))
        if (root/'songwriter/examples.json').exists(): paths.add(root/'songwriter/examples.json')
        paths.update((root/'outputs').glob('*/requests.jsonl'))
        paths.update((root/'outputs').glob('*/source_config.json'))
        if (root/'outputs/master_database.json').exists():
            paths.add(root/'outputs/master_database.json')
        paths.update((root/'songwriter/runs').glob('*/accepted/*.json'))
        excluded = {Path(p).resolve() for p in exclude}
        rows = []
        for path in sorted(paths):
            if path.resolve() in excluded:
                continue
            try:
                values = [strict_json(l) for l in path.read_text().splitlines() if l.strip()] if path.suffix == '.jsonl' else [strict_json(path.read_text())]
                for value in values:
                    rows.extend((row, path) for row in walk_songs(value))
            except (OSError, ValueError) as exc:
                # An unreadable historical corpus must never silently weaken checks.
                raise ValueError(f'Cannot read historical songs: {path}: {exc}') from exc
        return cls(rows)

    def compare(self, row):
        item = features(row)
        usable = {g for g in item['grams'][3] if sum(w not in STOP for w in g) >= 2}
        weight = lambda g: 1 + math.log((len(self.documents)+1)/(self.df[g]+1))
        total = sum(weight(g) for g in usable) or 1
        candidates = Counter(i for g in usable for i in self.index.get(g, ()))
        if item['fingerprint'] in self.seen:
            candidates.update(i for i, d in enumerate(self.documents) if d['fingerprint'] == item['fingerprint'])
        union = {g for g in usable if self.index.get(g)}
        global_coverage = sum(weight(g) for g in union) / total
        covered_positions = {position for i in range(len(item['tokens'])-2)
                             if tuple(item['tokens'][i:i+3]) in union for position in range(i,i+3)}
        matched_word_coverage = len(covered_positions) / max(1,len(item['tokens']))
        matches = []
        for index, hits in candidates.items():
            old = self.documents[index]
            shared = {n: item['grams'][n] & old['grams'][n] for n in (3,4,5)}
            informative = shared[3] & usable
            coverage = sum(weight(g) for g in informative) / total
            four_coverage = len(shared[4]) / max(1, len(item['grams'][4]))
            five_coverage = len(shared[5]) / max(1, len(item['grams'][5]))
            longest = max(SequenceMatcher(None, item['tokens'], old['tokens'], autojunk=False).get_matching_blocks(), key=lambda b: b.size)
            common_lines = {l for l in item['lines'] & old['lines'] if sum(w not in STOP for w in l) >= 3}
            reasons = []
            if item['fingerprint'] == old['fingerprint']:
                reasons.append('identical sung lyrics')
            if longest.size >= 8:
                reasons.append('shared contiguous lyric phrase of 8+ words')
            if common_lines:
                reasons.append('copied complete meaningful lyric line of 6+ words')
            if len(informative) >= 12 and coverage >= .20:
                reasons.append('substantial weighted three-word phrase reuse')
            if len(shared[4]) >= 8 and four_coverage >= .14:
                reasons.append('substantial four-word phrase reuse')
            if len(shared[5]) >= 4 and five_coverage >= .08:
                reasons.append('substantial five-word phrase reuse')
            content_jaccard = len(item['content'] & old['content']) / max(1, len(item['content'] | old['content']))
            if content_jaccard >= .78 and coverage >= .08:
                reasons.append('near duplicate vocabulary with matching phrasing')
            matches.append({'id': old['id'], 'source': old['source'], 'reasons': reasons,
                'weighted_trigram_coverage': round(coverage, 4), 'fourgram_coverage': round(four_coverage,4),
                'fivegram_coverage': round(five_coverage,4), 'longest_shared_words': longest.size,
                'longest_phrase': ' '.join(item['tokens'][longest.a:longest.a+longest.size]),
                'shared_lines': [' '.join(l) for l in sorted(common_lines)],
                'shared_phrases': [' '.join(g) for g in sorted(informative, key=lambda g: -weight(g))[:20]]})
        collage = len(union) >= 20 and (global_coverage >= .30 or matched_word_coverage >= .45)
        similar = collage or any(m['reasons'] for m in matches)
        return {'status': 'tooSimilar' if similar else 'accepted', 'corpus_unique_lyrics': len(self.documents),
                'global_weighted_trigram_coverage': round(global_coverage,4), 'collage_reuse': collage,
                'global_matched_word_coverage': round(matched_word_coverage,4),
                'closest': sorted(matches, key=lambda m: (bool(m['reasons']), m['weighted_trigram_coverage']), reverse=True)[:5]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='A JSON file, or directory of JSON song configs')
    parser.add_argument('--root', type=Path, default=ROOT, help='YuE2 workspace containing historical prompts')
    parser.add_argument('--output', type=Path, required=True, help='Review directory: accepted/, tooSimilar/, invalid/')
    parser.add_argument('--queue-name', help='Optionally publish accepted songs as one validated pending config')
    args = parser.parse_args(argv)
    paths = [args.input] if args.input.is_file() else sorted(p for p in args.input.glob('*.json') if not p.name.endswith('.review.json'))
    if not paths:
        parser.error('No JSON input files found')
    corpus = SongCorpus.from_workspace(args.root, exclude=paths)
    accepted = []; counts = Counter()
    for path in paths:
        try:
            raw = path.read_text()
            strict_json(raw)
            with tempfile.TemporaryDirectory(prefix='yue2-review-') as directory:
                snapshot = Path(directory)/'input.json'
                snapshot.write_text(raw)
                rows = songs(snapshot)
            for row in rows:
                result = corpus.compare(row)
                status = result['status']; counts[status] += 1
                atomic_json(args.output/status/(row['id']+'.json'), row)
                atomic_json(args.output/status/(row['id']+'.review.json'), result)
                if status == 'accepted':
                    accepted.append(row); corpus.add(row, path)
                print(f'{row["id"]}: {status}')
        except (ValueError, OSError) as exc:
            counts['invalid'] += 1
            atomic_json(args.output/'invalid'/(path.stem+'.review.json'), {'source':str(path),'error':str(exc)})
            if path.exists():
                target = args.output/'invalid'/(path.stem+'.json.txt')
                target.write_bytes(path.read_bytes())
    if accepted:
        publish(accepted, args.output/'accepted_config.json')
        if args.queue_name:
            from run_config import component
            publish(accepted, args.root/'queue/pending'/(component(args.queue_name)+'.json'))
    print(dict(counts))
    return 1 if counts['invalid'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
