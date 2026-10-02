"""Local lyric recovery and acoustic word alignment; no cloud/CUDA requirement.

The generation prompt is evidence, not a transcript. Short reference corrections
must beat the recognizer's text acoustically; long missing passages stay missing.
All decisions and raw recognizer output are retained for review.
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_ROOT = Path(os.environ.get('YUE2_ROOT', str(ROOT.parent / 'yue2'))).expanduser().resolve()
VERSION = 5


def apostrophes(text):
    return text.translate(str.maketrans({c: "'" for c in '’‘′＇`'}))


def words(text):
    return re.findall(r"[\w]+(?:'[\w]+)*", apostrophes(text), re.UNICODE)


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', apostrophes(text))
                   if c.isascii() and (c.isalpha() or c == "'")).lower()


def clean_lyrics(text):
    """Remove stage directions, preserving labelled sung replies and ad-libs."""
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\((?:male|female|both|all|choir)\s*:\s*([^)]*)\)', r'\1', text, flags=re.I)
    # Standalone parenthetical lines describe delivery; inline parentheses can be sung.
    text = re.sub(r'^\s*\([^\n]*\)\s*$', '', text, flags=re.M)
    text = text.replace('(', '').replace(')', '')
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def records(value):
    if isinstance(value, dict):
        if isinstance(value.get('lyrics'), str):
            yield value
        for child in value.values():
            yield from records(child)
    elif isinstance(value, list):
        for child in value:
            yield from records(child)


def find_prompt(audio, root=DEFAULT_PROMPT_ROOT, explicit=None):
    key = re.sub(r'[^a-z0-9]', '', Path(audio).stem.lower())
    if explicit and Path(explicit).suffix.lower() != '.json':
        return {'source': str(Path(explicit).resolve()), 'lyrics': Path(explicit).read_text()}
    matches = []
    paths = [Path(explicit)] if explicit else sorted(Path(root).rglob('*.json'))
    for path in paths:
        if any(p in {'.git', 'node_modules', '.venv'} for p in path.parts):
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError, UnicodeError):
            continue
        for record in records(data):
            # source_id is deliberately excluded: rewrites can reference an older song.
            ids = [record.get(k, '') for k in ('id', 'track_identifier', 'title', 'file_name')]
            exact = any(re.sub(r'[^a-z0-9]', '', Path(str(v)).stem.lower()) == key for v in ids)
            if exact or (explicit and len(list(records(data))) == 1):
                matches.append({'source': str(path.resolve()), **record})
    if not matches:
        raise ValueError(f'No exact lyric prompt for {Path(audio).stem}; supply --lyrics FILE')
    unique = {clean_lyrics(m['lyrics']) for m in matches}
    if len(unique) > 1:
        raise ValueError('Conflicting exact prompts; select --lyrics FILE: ' + ', '.join(m['source'] for m in matches))
    return sorted(matches, key=lambda m: ('/queue/done/' not in m['source'], m['source']))[0]


def fingerprint(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def run(command, log):
    print('[karaoke] ' + ' '.join(map(str, command[:5])), flush=True)
    with open(log, 'w') as f:
        result = subprocess.run(list(map(str, command)), stdout=f, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}); see {log}\n' + Path(log).read_text()[-2500:])


def prepare_vocals(audio, work, provided=None, threads=4):
    source = Path(provided) if provided else work / 'stems' / 'htdemucs' / Path(audio).stem / 'vocals.wav'
    if not source.exists():
        if provided:
            raise FileNotFoundError(source)
        env = os.environ.copy()
        env.update(OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads))
        log = work / 'separation.log'
        print('[karaoke] Separating vocals with HTDemucs on CPU (cached).', flush=True)
        with log.open('w') as f:
            subprocess.run([sys.executable, '-m', 'demucs', '--two-stems', 'vocals', '-n', 'htdemucs',
                            '-d', 'cpu', '--shifts', '1', '--overlap', '0.25', '-o', str(work / 'stems'), str(audio)],
                           env=env, stdout=f, stderr=subprocess.STDOUT, check=True)
    target = work / 'vocals-16k.wav'
    if not target.exists():
        run(['ffmpeg', '-v', 'error', '-y', '-i', source, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', target], work / 'resample.log')
    return target


def transcribe(vocals, work, cli, model, lyrics, threads=4, gpu=True, name='recognition'):
    target = work / (name + '.json')
    if target.exists():
        return json.loads(target.read_text())
    # Vocabulary hints rather than a long forced transcript. Whisper only accepts
    # a short initial context; passing a whole song silently truncates it.
    vocabulary = list(dict.fromkeys(w.lower() for w in words(lyrics) if len(w) >= 6))[:65]
    prompt = 'Song lyrics. ' + ', '.join(vocabulary) + '.'
    command = [cli, '-m', model, '-f', vocals, '-l', 'en', '-t', str(threads),
               '-ojf', '-of', str(work / name), '-bs', '5', '-bo', '5']
    if lyrics:
        command.extend(['--prompt', prompt, '--carry-initial-prompt'])
    if not gpu:
        command.append('-ng')
    run(command, work / (name + '.log'))
    return json.loads(target.read_text())


def reference_candidates(segments, lyrics):
    """Monotonic global matching disambiguates repeated choruses.

    Only short edits with matching words on BOTH sides become candidates. Entire
    missing verses cannot be invented by mapping the prompt evenly onto a song.
    """
    reference = words(lyrics)
    heard = []
    owners = []
    for i, seg in enumerate(segments):
        for word in words(seg['text']):
            heard.append(word)
            owners.append(i)
    candidate = [[w for w in words(s['text'])] for s in segments]
    edits = []
    matcher = SequenceMatcher(None, list(map(normalized, heard)), list(map(normalized, reference)), autojunk=False)
    ops = matcher.get_opcodes()
    replacements = {}
    for n, (tag, a, b, c, d) in enumerate(ops):
        if tag == 'equal':
            continue
        bounded = n > 0 and n + 1 < len(ops) and ops[n-1][0] == ops[n+1][0] == 'equal'
        if not bounded or max(b-a, d-c) > 8 or not d-c or a == 0 or b >= len(heard):
            edits.append({'kind': 'unresolved_reference_difference', 'heard': ' '.join(heard[a:b]),
                          'intended': ' '.join(reference[c:d])})
            continue
        owner = owners[a] if a < b else owners[a-1]
        if owners[a-1] != owner or owners[b] != owner:
            edits.append({'kind': 'reference_difference_at_phrase_boundary',
                          'heard': ' '.join(heard[a:b]), 'intended': ' '.join(reference[c:d])})
            continue
        replacements[a] = (b, reference[c:d], owner)
    candidate = [[] for _ in segments]
    i = 0
    while i < len(heard):
        if i in replacements:
            end, replacement, owner = replacements[i]
            candidate[owner].extend(replacement)
            if end > i:
                i = end
                continue
        candidate[owners[i]].append(heard[i])
        i += 1
    return candidate, edits


class AcousticAligner:
    """CTC alignment on isolated vocals; returns measured boundaries, never evenly spaced words."""
    def __init__(self, threads=4, bundle='WAV2VEC2_ASR_BASE_960H'):
        import torch
        import torchaudio
        self.torch = torch
        self.ta = torchaudio
        torch.set_num_threads(threads)
        self.bundle = getattr(torchaudio.pipelines, bundle)
        self.model = self.bundle.get_model().eval()
        self.labels = {c: i for i, c in enumerate(self.bundle.get_labels())}

    def emission(self, samples):
        with self.torch.inference_mode():
            tensor = self.torch.from_numpy(samples).float().unsqueeze(0)
            return self.model(tensor)[0].log_softmax(-1)

    def align(self, emission, text, start, duration, frame_bounds=None):
        torch = self.torch
        tokens, owners, valid = [], [], []
        for word in text:
            spelling = normalized(word).upper()
            spelling = ''.join(c for c in spelling if c in self.labels)
            if not spelling:
                continue
            if tokens:
                tokens.append(self.labels['|'])
                owners.append(-1)
            idx = len(valid)
            valid.append(word)
            tokens.extend(self.labels[c] for c in spelling)
            owners.extend([idx] * len(spelling))
        if not tokens:
            return [], float('-inf')
        target = torch.tensor([tokens], dtype=torch.int32)
        try:
            path, scores = self.ta.functional.forced_align(emission, target, blank=0)
        except RuntimeError:
            return [], float('-inf')
        spans = self.ta.functional.merge_tokens(path[0], scores[0].exp())
        if len(spans) != len(tokens):
            raise RuntimeError('CTC token/span mismatch')
        ratio = duration / emission.shape[1]
        output = []
        for i, word in enumerate(valid):
            pieces = [span for span, owner in zip(spans, owners) if owner == i]
            score = sum(float(p.score) for p in pieces) / len(pieces)
            begin = start + pieces[0].start * ratio
            end = start + pieces[-1].end * ratio
            if frame_bounds is not None:
                begin = float(frame_bounds[0][pieces[0].start])
                end = float(frame_bounds[1][pieces[-1].end - 1])
            output.append({'word': word, 'start': round(begin, 3),
                           'end': round(end, 3),
                           'confidence': round(score, 4)})
        # Frame-normalized likelihood makes alternate lengths comparable.
        return output, float(scores.mean())


def group_segments(segments, max_duration=24):
    """Coarse ASR boundaries are not word boundaries: align across them."""
    grouped = []
    for segment in segments:
        if (grouped and segment['end'] - grouped[-1]['start'] <= max_duration
                and segment['start'] - grouped[-1]['end'] < 2):
            grouped[-1]['text'] += ' ' + segment['text']
            grouped[-1]['end'] = segment['end']
        else:
            grouped.append(dict(segment))
    return grouped


def align_song(vocals, recognition, lyrics, work, threads=4, bundle='WAV2VEC2_ASR_LARGE_960H', mix_recognition=None):
    import numpy as np
    import soundfile as sf
    samples, rate = sf.read(vocals, dtype='float32')
    segments = []
    for item in recognition['transcription']:
        text = re.sub(r'\[[^\]]*\]|\([^)]*\)', '', item['text']).strip()
        if not words(text):
            continue
        segments.append({'text': text, 'start': max(0, item['offsets']['from']/1000),
                         'end': min(len(samples)/rate, item['offsets']['to']/1000)})
    segments = group_segments(segments)
    candidates, issues = reference_candidates(segments, lyrics)
    mix_candidates = None
    if mix_recognition:
        mix_text = ' '.join(s['text'] for s in mix_recognition['transcription'])
        mix_candidates, _ = reference_candidates(segments, mix_text)
    aligner = AcousticAligner(threads, bundle)
    decisions, selected_words, owners, sources_used = [], [], [], []
    emissions, frame_starts, frame_ends = [], [], []
    for i, (segment, candidate) in enumerate(zip(segments, candidates)):
        print(f'[karaoke] Acoustic alignment {i+1}/{len(segments)}: {segment["text"][:65]}', flush=True)
        start = max(0, segment['start'] - 0.8)
        end = min(len(samples)/rate, segment['end'] + 0.8)
        # Split oversized segments at recognition boundaries, not arbitrarily in the transcript.
        if end - start > 40:
            raise ValueError('Recognition segment exceeds 40 seconds; rerun recognition with shorter segments')
        chunk = samples[int(start*rate):int(end*rate)]
        if len(chunk) < 400:
            continue
        emission_path = work / f'emission-{i:03d}.npy'
        if emission_path.exists():
            emission = aligner.torch.from_numpy(np.load(emission_path))
        else:
            emission = aligner.emission(np.ascontiguousarray(chunk))
            np.save(emission_path, emission.numpy())
        original = words(segment['text'])
        aligned, score = aligner.align(emission, original, start, len(chunk)/rate)
        chosen = 'recognition'
        attempts = []
        selected_text = original
        sources = [('reference_corrected', candidate)]
        if mix_candidates:
            sources.insert(0, ('mix_recognition', mix_candidates[i]))
        for source, proposal in sources:
            # Test edits separately so one incorrect intended word does not veto
            # otherwise well-supported corrections elsewhere in the passage.
            ops = SequenceMatcher(None, list(map(normalized, selected_text)),
                                  list(map(normalized, proposal)), autojunk=False).get_opcodes()
            for tag, a, b, c, d in reversed(ops):
                if tag == 'equal':
                    continue
                trial = selected_text[:a] + proposal[c:d] + selected_text[b:]
                alternative, alt_score = aligner.align(emission, trial, start, len(chunk)/rate)
                accepted = bool(alternative and alt_score > score + 0.001)
                attempts.append({'source': source, 'heard': ' '.join(selected_text[a:b]),
                                 'proposed': ' '.join(proposal[c:d]), 'accepted': accepted,
                                 'score_delta': alt_score-score if np.isfinite(alt_score) and np.isfinite(score) else None})
                if accepted:
                    aligned, score, chosen, selected_text = alternative, alt_score, source, trial
        selected_words.extend(selected_text)
        owners.extend([i] * len(selected_text))
        sources_used.extend([chosen] * len(selected_text))
        # Assemble a single acoustic timeline, trimming duplicate overlap frames.
        # Final forced alignment crosses ASR chunk boundaries, so no real words
        # are discarded just because a recognizer placed a boundary too early.
        step = (len(chunk)/rate) / emission.shape[1]
        starts = start + np.arange(emission.shape[1]) * step
        ends = starts + step
        left = (segments[i-1]['end'] + segment['start'])/2 if i else 0
        right = (segment['end'] + segments[i+1]['start'])/2 if i+1 < len(segments) else len(samples)/rate
        mask = ((starts+ends)/2 >= left) & ((starts+ends)/2 < right)
        emissions.append(emission[:, mask, :])
        frame_starts.extend(np.maximum(starts[mask], left))
        frame_ends.extend(np.minimum(ends[mask], right))
        decisions.append({**segment, 'selected': chosen, 'candidate': ' '.join(candidate),
                          'acoustic_score': score if np.isfinite(score) else None,
                          'corrections': attempts})
    if not emissions:
        raise ValueError('No vocal transcript to align')
    print('[karaoke] Aligning final words across the complete acoustic timeline.', flush=True)
    output, _ = aligner.align(aligner.torch.cat(emissions, dim=1), selected_words, 0,
                              len(samples)/rate, (frame_starts, frame_ends))
    if len(output) != len(selected_words):
        raise ValueError('Final alignment could not represent every word; inspect recognition and unsupported characters')
    for word, owner, source in zip(output, owners, sources_used):
        word.update(segment=owner, source=source, review=word['confidence'] < 0.35)
    return output, decisions, issues


def validate_timeline(data):
    previous = 0.0
    duration = float(data['duration'])
    for word in data['words']:
        a, b = float(word['start']), float(word['end'])
        if not (0 <= a < b <= duration + 0.001) or a < previous - 0.001:
            raise ValueError(f'Invalid/nonmonotonic word timing: {word}')
        if not isinstance(word['word'], str) or not word['word'].strip():
            raise ValueError('Empty caption word')
        previous = b
    if not data['words']:
        raise ValueError('No words recovered; inspect recognition.log and the vocal stem')
    return data


def prepare(audio, output_root='output/karaoke', prompt_root=DEFAULT_PROMPT_ROOT, lyrics_file=None,
            vocals=None, cli=None, model=None, threads=4, gpu=True, bundle='WAV2VEC2_ASR_LARGE_960H'):
    audio = Path(audio).resolve()
    prompt = find_prompt(audio, prompt_root, lyrics_file)
    lyrics = clean_lyrics(prompt['lyrics'])
    cli = Path(cli or ROOT / 'tools/whisper.cpp/build/bin/whisper-cli').resolve()
    model = Path(model or ROOT / 'models/ggml-large-v3-q5_0.bin').resolve()
    if not cli.is_file() or not model.is_file():
        raise FileNotFoundError('whisper.cpp executable/model missing. Run scripts/setup_karaoke.sh (see README).')
    provenance = {'version': VERSION, 'audio_sha256': fingerprint(audio), 'lyrics': lyrics,
                  'whisper_model_sha256': fingerprint(model), 'whisper_binary_sha256': fingerprint(cli),
                  'aligner': bundle, 'gpu': gpu, 'vocals_sha256': fingerprint(vocals) if vocals else None}
    key = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:16]
    work = Path(output_root).resolve() / f'{audio.stem}-{key}'
    work.mkdir(parents=True, exist_ok=True)
    final = work / 'words.json'
    if final.exists():
        validate_timeline(json.loads(final.read_text()))
        print(f'[karaoke] Cached alignment: {final}', flush=True)
        return final
    write_json(work / 'prompt.json', prompt)
    (work / 'lyrics.txt').write_text(lyrics + '\n')
    write_json(work / 'provenance.json', provenance)
    if not vocals:
        stem_work = Path(output_root).resolve() / ('stems-' + provenance['audio_sha256'][:16])
        stem_work.mkdir(parents=True, exist_ok=True)
        vocals = prepare_vocals(audio, stem_work, threads=threads)
    stem = prepare_vocals(audio, work, vocals, threads)
    recognition = transcribe(stem, work, cli, model, lyrics, threads, gpu)
    mix = work / 'mix-16k.wav'
    if not mix.exists():
        run(['ffmpeg', '-v', 'error', '-y', '-i', audio, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', mix], work / 'mix-resample.log')
    mix_recognition = transcribe(mix, work, cli, model, '', threads, gpu, name='recognition-mix')
    output, decisions, issues = align_song(stem, recognition, lyrics, work, threads, bundle, mix_recognition)
    import soundfile as sf
    data = {'version': VERSION, 'audio': str(audio), 'audio_sha256': provenance['audio_sha256'],
            'prompt_source': prompt['source'], 'duration': sf.info(stem).duration,
            'words': output, 'review_count': sum(w['review'] for w in output),
            'alignment_model': bundle, 'decisions': decisions, 'issues': issues}
    validate_timeline(data)
    write_json(final, data)
    print(f'[karaoke] Saved {len(output)} words; {data["review_count"]} need review: {final}', flush=True)
    return final


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('audio')
    p.add_argument('--output-root', default='output/karaoke')
    p.add_argument('--prompt-root', default=str(DEFAULT_PROMPT_ROOT))
    p.add_argument('--lyrics', dest='lyrics_file')
    p.add_argument('--vocals', help='Use an already separated vocal stem')
    p.add_argument('--whisper-cli', dest='cli')
    p.add_argument('--whisper-model', dest='model')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--cpu', action='store_true')
    p.add_argument('--alignment-model', dest='bundle', default='WAV2VEC2_ASR_LARGE_960H')
    args = vars(p.parse_args())
    args['gpu'] = not args.pop('cpu')
    prepare(**args)


if __name__ == '__main__':
    main()
