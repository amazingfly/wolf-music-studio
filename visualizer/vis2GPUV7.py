#!/usr/bin/env python3
"""Automation entry point: FLAC input, exact lyrics, karaoke, verified atomic MP4s.

V6 and the existing visual effects/recognition modules remain unchanged.
"""
import argparse
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
VERSION = 1


def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def probe_video(path, duration, width, height, fps):
    import subprocess
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], capture_output=True, text=True,
                            timeout=60, check=True)
    data = json.loads(result.stdout)
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
    if (video['codec_name'], video['width'], video['height'], audio['codec_name']) != ('h264', width, height, 'aac'):
        raise ValueError(f'Unexpected video/audio format: {path}')
    numerator, denominator = map(int, video['avg_frame_rate'].split('/'))
    if abs(numerator / denominator - fps) > .01:
        raise ValueError(f'Unexpected frame rate: {path}')
    tolerance = max(.3, 2 / fps)
    for stream in (video, audio):
        if abs(float(stream['duration']) - duration) > tolerance:
            raise ValueError(f'Incomplete or wrong-duration {stream["codec_type"]}: {path}')
    return {'duration': float(data['format']['duration']), 'width': width, 'height': height,
            'fps': fps, 'video_codec': video['codec_name'], 'audio_codec': audio['codec_name']}


def render_track(audio, output_dir, config_path=ROOT / 'config.json', lyrics_file=None,
                 words_file=None, vocals=None, karaoke_root=None, threads=4,
                 formats=('widescreen', 'portrait'), max_duration=None):
    import soundfile as sf
    from vis.config import load_config
    from vis.karaoke import prepare, validate_timeline
    from vis.renderer import render_visualizer_pipeline

    audio = Path(audio).resolve()
    output_dir = Path(output_dir).resolve()
    config_path = Path(config_path).resolve()
    if not audio.is_file() or audio.suffix.lower() not in {'.flac', '.ogg', '.wav', '.mp3'}:
        raise ValueError(f'Expected an existing FLAC, OGG, WAV or MP3: {audio}')
    if not config_path.is_file() or not isinstance(json.loads(config_path.read_text()), dict):
        raise ValueError(f'Expected a JSON config object: {config_path}')
    if not formats or any(f not in {'widescreen', 'portrait'} for f in formats):
        raise ValueError('Select widescreen and/or portrait')
    if threads < 1 or max_duration is not None and max_duration <= 0:
        raise ValueError('Threads and preview duration must be positive')
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / '.render.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = fingerprint(audio)
        duration = sf.info(audio).duration
        config = load_config(str(config_path))
        if words_file:
            words_file = Path(words_file).resolve()
        else:
            words_file = prepare(audio, output_root=karaoke_root or output_dir / 'karaoke',
                                 lyrics_file=lyrics_file, vocals=vocals, threads=threads)
        timeline = validate_timeline(json.loads(Path(words_file).read_text()))
        if timeline.get('audio_sha256') != before or abs(timeline['duration'] - duration) > .05:
            raise ValueError('Caption timeline does not belong to this exact audio/duration')
        fps = config['video']['fps']
        if not isinstance(fps, int) or fps <= 0:
            raise ValueError('Video FPS must be a positive integer')
        expected_duration = min(duration, max_duration) if max_duration else duration
        provenance = {'version': VERSION, 'audio': str(audio), 'audio_sha256': before,
                      'config_sha256': fingerprint(config_path), 'words_sha256': fingerprint(words_file),
                      'preview_seconds': max_duration,
                      'renderer_sha256': hashlib.sha256(''.join(fingerprint(p) for p in
                          [Path(__file__), *sorted((ROOT / 'vis').rglob('*.py'))]).encode()).hexdigest()}
        receipt_path = output_dir / 'render_receipt.json'
        previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        reusable = previous.get('provenance') == provenance
        receipt = {'status': 'rendering', 'provenance': provenance, 'words_file': str(words_file),
                   'word_count': len(timeline['words']), 'review_count': timeline.get('review_count', 0),
                   'issues': timeline.get('issues', []),
                   'videos': dict(previous.get('videos', {})) if reusable else {}}
        write_json(receipt_path, receipt)
        for orientation in formats:
            width, height = (1280, 720) if orientation == 'widescreen' else (720, 1280)
            final = output_dir / orientation / (audio.stem + '.mp4')
            final.parent.mkdir(parents=True, exist_ok=True)
            cached = receipt['videos'].get(orientation, {})
            if final.exists() and cached.get('sha256') == fingerprint(final):
                probe_video(final, expected_duration, width, height, fps)
                continue
            temporary = final.with_name('.' + final.stem + '.inprogress.mp4')
            song_config = deepcopy(config)
            song_config['karaoke'] = {'words_file': str(words_file)}
            try:
                render_visualizer_pipeline(str(audio), str(temporary), width=width, height=height,
                                           fps=fps, config=song_config, max_duration=max_duration)
                video = probe_video(temporary, expected_duration, width, height, fps)
                if fingerprint(audio) != before:
                    raise ValueError('Source audio changed while rendering; video not published')
                temporary.replace(final)
                stat = final.stat()
                receipt['videos'][orientation] = {**video, 'path': str(final),
                    'sha256': fingerprint(final), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
                write_json(receipt_path, receipt)
            finally:
                temporary.unlink(missing_ok=True)
        if fingerprint(audio) != before:
            raise ValueError('Source audio changed while rendering')
        receipt['status'] = 'complete'
        write_json(receipt_path, receipt)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('-c', '--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('-o', '--output-dir', type=Path, required=True)
    parser.add_argument('--lyrics', type=Path, help='Exact generation prompt JSON or lyric text')
    parser.add_argument('--words', type=Path, help='Existing timeline with this audio checksum')
    parser.add_argument('--vocals', type=Path)
    parser.add_argument('--karaoke-root', type=Path)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--no-cellphone', action='store_true')
    parser.add_argument('--no-widescreen', action='store_true')
    parser.add_argument('--max-duration', type=float, help='Preview only; caption recovery still uses the full audio')
    args = parser.parse_args(argv)
    formats = tuple(f for f, enabled in [('widescreen', not args.no_widescreen),
                                         ('portrait', not args.no_cellphone)] if enabled)
    render_track(args.audio, args.output_dir, args.config, args.lyrics, args.words,
                 args.vocals, args.karaoke_root, args.threads, formats, args.max_duration)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
