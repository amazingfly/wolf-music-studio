"""V2 packaging and conservative, line-only repairs. Musical checks stay unchanged."""
from difflib import SequenceMatcher
import hashlib
import json
import re

from checkSongPrompts import features, quality_checks, strict_json, sung_text, tokens, validate_song

CREATIVE_SCHEMA = {'type': 'object', 'properties': {k: {'type': 'string'} for k in
                   ('title', 'style', 'lyrics')}, 'required': ['title', 'style', 'lyrics'],
                   'additionalProperties': False}
PATCH_SCHEMA = {'type': 'object', 'properties': {'replacements': {'type': 'array',
    'minItems': 1, 'maxItems': 2, 'items': {'type': 'object', 'properties': {
    'line': {'type': 'integer'}, 'text': {'type': 'string'}},
    'required': ['line', 'text'], 'additionalProperties': False}}},
    'required': ['replacements'], 'additionalProperties': False}


def creative_song(text, slot):
    value = strict_json(text)
    if not isinstance(value, dict) or set(value) != {'title', 'style', 'lyrics'}:
        raise ValueError('V2 expects exactly title, style, lyrics; the harness supplies metadata')
    if any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise ValueError('Creative fields must be nonempty strings')
    row = validate_song({**value, 'id': slot['id'], 'seed': slot['audio_seed'], 'cot': 'full',
        'target_seconds': 360, 'duration_validation': {'min_seconds': 250, 'max_seconds': 380}})
    changes = []
    try:
        quality_checks(row)
    except ValueError as exc:
        # Repair ONLY a terminal marker on a closed JSON response with an existing outro.
        # The client must already have rejected finish_reason=length or missing completion.
        lyrics = row['lyrics']
        headings = list(re.finditer(r'^\[([^\]]+)\]', lyrics, re.M))
        tail = lyrics[headings[-1].end():] if headings else ''
        if (str(exc) != 'End the resolved [Outro] with [End]' or not headings or
            not headings[-1].group(1).casefold().startswith('outro') or
            re.search(r'\[end\]', lyrics, re.I) or len(tokens(sung_text(tail))) < 3 or
            lyrics.count('(') != lyrics.count(')') or lyrics.count('[') != lyrics.count(']')):
            raise
        row['lyrics'] = lyrics.rstrip() + '\n[End]'
        quality_checks(row)
        changes.append('appended_terminal_End')
    return row, changes


def line_map(lyrics):
    """Same sung-token stream as the comparator, plus original 1-based line numbers."""
    words, numbers, sections = [], [], {}
    section = ''
    for number, line in enumerate(lyrics.splitlines(), 1):
        match = re.match(r'^\[([^\]]+)\]', line)
        if match:
            section = match.group(1).casefold()
        sections[number] = section
        current = tokens(sung_text(line))
        words.extend(current); numbers.extend([number] * len(current))
    return words, numbers, sections


def repair_plan(row, report, corpus):
    """Reject chorus copying, collages and broad overlap. At most two short verse lines."""
    if report['status'] != 'tooSimilar' or report.get('collage_reuse'):
        return None
    matches = [m for m in report['closest'] if m['reasons']]
    allowed = {'shared contiguous lyric phrase of 8+ words',
               'copied complete meaningful lyric line of 6+ words'}
    if not matches or any(set(m['reasons']) - allowed or m['longest_shared_words'] > 12 or
                          m['weighted_trigram_coverage'] >= .08 for m in matches):
        return None
    words, numbers, sections = line_map(row['lyrics'])
    if words != features(row)['tokens']:
        return None  # Exotic multiline cues: don't risk selecting the wrong sung lines.
    selected = set()
    for old in corpus.documents:
        for block in SequenceMatcher(None, words, old['tokens'], autojunk=False).get_matching_blocks():
            if block.size >= 8:
                selected.update(numbers[block.a:block.a+block.size])
        for number, line in enumerate(row['lyrics'].splitlines(), 1):
            if ' '.join(tokens(sung_text(line))) in {v for m in matches for v in m['shared_lines']}:
                selected.add(number)
    if not 1 <= len(selected) <= 2:
        return None
    lines = row['lyrics'].splitlines()
    for number in selected:
        if ('chorus' in sections[number] or any(s in lines[number-1] for s in ('[', ']', '(', ')')) or
            not tokens(lines[number-1])):
            return None
    changed_words = sum(len(tokens(lines[n-1])) for n in selected)
    if changed_words > 30 or changed_words / max(1, len(words)) > .08:
        return None
    return {'lines': [{'line': n, 'text': lines[n-1], 'section': sections[n],
                      'before': lines[n-2] if n > 1 else '',
                      'after': lines[n] if n < len(lines) else ''} for n in sorted(selected)],
            'song_title': row['title'], 'sung_words_replaced': changed_words}


def apply_patch_response(row, plan, text):
    patch = strict_json(text)
    if not isinstance(patch, dict) or set(patch) != {'replacements'} or not isinstance(patch['replacements'], list):
        raise ValueError('Repair must contain only a replacements array')
    expected = {line['line'] for line in plan['lines']}
    received = set()
    replacements = {}
    for entry in patch['replacements']:
        if (not isinstance(entry, dict) or set(entry) != {'line', 'text'} or
            type(entry['line']) is not int or entry['line'] not in expected or entry['line'] in received or
            not isinstance(entry['text'], str) or not entry['text'].strip() or
            any(c in entry['text'] for c in '\n\r[]()') or len(tokens(entry['text'])) > 24):
            raise ValueError('Repair attempted to alter unselected lines or insert cues/invalid text')
        received.add(entry['line']); replacements[entry['line']] = entry['text']
    if received != expected:
        raise ValueError('Repair must replace every selected line exactly once')
    # keepends preserves every byte outside the selected sung lines, including the final newline.
    lines = row['lyrics'].splitlines(keepends=True)
    for n, text in replacements.items():
        original = lines[n-1]
        ending = '\r\n' if original.endswith('\r\n') else '\n' if original.endswith('\n') else ''
        lines[n-1] = text + ending
    result = {**row, 'lyrics': ''.join(lines)}
    validate_song(result); quality_checks(result)
    return result
