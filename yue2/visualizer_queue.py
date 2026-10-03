#!/usr/bin/env python3
"""Persistent karaoke render queue; downloads enqueue and a separate service renders."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from track_registry import DEFAULT_DATABASE, atomic_json, key_for, locked_database

ROOT = Path(__file__).resolve().parent
VIS_ROOT = Path(os.environ.get('YUE2_VIS_ROOT', str(ROOT.parent / 'visualizer'))).expanduser().resolve()
QUEUE_ROOT = ROOT / 'queue/visualizer'
MAX_ATTEMPTS = 3


class KaraokeReviewNeeded(ValueError):
    pass


@contextmanager
def state_lock(folder):
    with (Path(folder) / '.state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish_state(job, state):
    with state_lock(job['job_path']):
        atomic_json(Path(job['job_path']) / 'state.json', state)
        update_registry(job, state)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def renderer_revision(root):
    files = [root / 'vis2GPUV7.py', *sorted((root / 'vis').rglob('*.py'))]
    return hashlib.sha256(''.join(str(p.relative_to(root)) + digest(p) for p in files).encode()).hexdigest()


def update_registry(job, state, enqueue=False):
    workflow = {**state, 'job_id': job['id'], 'job_path': job['job_path'],
                'audio': job['audio'], 'audio_sha256': job['audio_sha256'],
                'output_dir': job['output_dir'], 'log_path': str(Path(job['job_path']) / 'render.log'),
                'updated_at': datetime.now(timezone.utc).isoformat()}
    with locked_database(job['database']) as data:
        for path in (Path(job['audio']), Path(job['original'])):
            key = key_for(data, path)
            current = data.get(key)
            if not current or current.get('audio_workflow', {}).get('output_sha256') != job['audio_sha256']:
                continue
            if not enqueue and current.get('visualizer_workflow', {}).get('job_id') != job['id']:
                continue  # A newer audio/config job owns this track now.
            current['visualizer_workflow'] = workflow
            atomic_json(path.with_name(path.stem + '_metadata.json'), current)
    from track_catalog import sync_run_safely
    sync_run_safely(Path(job['original']).parent.parent)


def state_is_current(job, state):
    if state.get('status') != 'complete':
        return False
    videos = state.get('videos', {})
    if set(videos) != {'widescreen', 'portrait'}:
        return False
    for video in videos.values():
        path = Path(video['path'])
        if not path.is_file() or (path.stat().st_size, path.stat().st_mtime_ns) != (video['size'], video['mtime_ns']):
            return False
    words = Path(state.get('words_file', ''))
    return words.is_file() and digest(words) == state.get('words_sha256')


def enqueue_track(audio, prompt=None, database=DEFAULT_DATABASE, queue_root=QUEUE_ROOT, vis_root=VIS_ROOT, words_file=None):
    audio = Path(audio).resolve()
    queue_root, vis_root = Path(queue_root).resolve(), Path(vis_root).resolve()
    metadata = json.loads(audio.with_name(audio.stem + '_metadata.json').read_text())
    workflow = metadata.get('audio_workflow', {})
    if workflow.get('role') != 'master' or workflow.get('status') != 'complete' or workflow.get('output_path') != str(audio):
        raise ValueError(f'Expected a verified truncated master: {audio}')
    audio_hash = digest(audio)
    if audio_hash != workflow['output_sha256']:
        raise ValueError(f'Master audio has changed: {audio}; run processTracks.py first')
    if prompt is None:
        from processTracks import prompt_for
        prompt = prompt_for(Path(workflow['original_path']))
    if not isinstance(prompt, dict) or not isinstance(prompt.get('lyrics'), str) or not prompt['lyrics'].strip():
        raise ValueError(f'No generation lyrics for: {audio}')
    if prompt.get('id') != audio.parent.name:
        raise ValueError('Generation prompt ID does not match the track directory')
    config = json.loads((vis_root / 'config.json').read_text())
    if not isinstance(config, dict):
        raise ValueError('Expected a visualizer config object')
    identity = {'audio': str(audio), 'audio_sha256': audio_hash, 'prompt': prompt,
                'config': config, 'renderer_revision': renderer_revision(vis_root),
                'database': str(Path(database).resolve())}
    if words_file:
        words_file = Path(words_file).resolve()
        if json.loads(words_file.read_text()).get('audio_sha256') != audio_hash:
            raise ValueError('Prepared captions do not belong to this exact audio')
        identity['prepared_words_sha256'] = digest(words_file)
    job_id = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    folder = queue_root / 'jobs' / job_id
    queue_root.mkdir(parents=True, exist_ok=True)
    with (queue_root / '.enqueue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        folder.mkdir(parents=True, exist_ok=True)
        with state_lock(folder):
            job_path = folder / 'job.json'
            state_path = folder / 'state.json'
            if job_path.exists():
                job = json.loads(job_path.read_text())
                state = json.loads(state_path.read_text())
                if state.get('status') == 'complete' and not state_is_current(job, state):
                    state = {'status': 'queued', 'attempts': 0, 'reason': 'Missing or modified render artifacts'}
                    atomic_json(state_path, state)
            else:
                output = vis_root / 'output/yue2' / audio.parent.parent.name / audio.parent.name / job_id[:12]
                job = {**identity, 'id': job_id, 'original': workflow['original_path'],
                       'job_path': str(folder), 'vis_root': str(vis_root), 'output_dir': str(output),
                       'created_at': time.time()}
                # Publish job.json last: the worker never sees an incomplete snapshot.
                atomic_json(folder / 'prompt.json', prompt)
                atomic_json(folder / 'config.json', config)
                if words_file:
                    raw = words_file.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != identity['prepared_words_sha256']:
                        raise ValueError('Prepared captions changed while enqueueing')
                    (folder/'words.json').write_bytes(raw)
                state = {'status': 'queued', 'attempts': 0}
                atomic_json(state_path, state)
                atomic_json(job_path, job)
            update_registry(job, state, enqueue=True)
    return {'job_id': job_id, **state, 'job_path': str(folder)}


def next_job(queue_root):
    candidates = []
    for path in (Path(queue_root) / 'jobs').glob('*/job.json'):
        job = json.loads(path.read_text())
        state = json.loads(path.with_name('state.json').read_text())
        if state.get('status') in {'queued', 'retrying', 'running', 'waiting_for_compute'} and state.get('next_attempt_at', 0) <= time.time():
            candidates.append((job['created_at'], job['id'], job, state))
    return min(candidates, key=lambda row: row[:2])[2:] if candidates else (None, None)


def run_job(job, state, compute_fd=None):
    folder = Path(job['job_path'])
    attempt = state.get('attempts', 0) + 1
    current = {**state, 'status': 'running', 'attempts': attempt, 'started_at': time.time()}
    current.pop('next_attempt_at', None)
    publish_state(job, current)
    proc = None
    try:
        if attempt > MAX_ATTEMPTS:
            raise RuntimeError('Retry limit reached after worker termination; inspect render.log and retry explicitly')
        if digest(job['audio']) != job['audio_sha256']:
            current.update(status='superseded', error='Master changed before rendering')
            return current
        command = [os.environ.get('YUE2_VIS_PYTHON', sys.executable), str(Path(job['vis_root']) / 'vis2GPUV8.py'), job['audio'],
                   '--lyrics', str(folder / 'prompt.json'), '--config', str(folder / 'config.json'),
                   '--output-dir', job['output_dir'], '--karaoke-root', str(Path(job['vis_root']) / 'output/karaoke'),
                   '--threads', '4']
        if job.get('prepared_words_sha256'):
            prepared = folder/'words.json'
            if digest(prepared) != job['prepared_words_sha256']:
                raise ValueError('Prepared caption snapshot changed; enqueue a new job')
            command += ['--words', str(prepared)]
        (Path(job['output_dir'])/'karaoke_review.json').unlink(missing_ok=True)
        print(f"Rendering {Path(job['audio']).stem}; attempt {attempt}; log: {folder / 'render.log'}", flush=True)
        environment = {**os.environ, 'PYTHONUNBUFFERED': '1', 'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4',
                       'OPENBLAS_NUM_THREADS': '4'}
        if compute_fd is not None:
            environment['YUE2_COMPUTE_FD'] = str(compute_fd)
        with (folder / 'render.log').open('a') as log:
            proc = subprocess.Popen(command, cwd=job['vis_root'], env=environment,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                    pass_fds=(compute_fd,) if compute_fd is not None else ())
            code = proc.wait()
        if code:
            review_path = Path(job['output_dir'])/'karaoke_review.json'
            if review_path.is_file():
                review = json.loads(review_path.read_text())
                if review.get('status') == 'needs_review' and review.get('audio_sha256') == job['audio_sha256']:
                    current['karaoke_quality'] = review
                    raise KaraokeReviewNeeded('; '.join(review.get('reasons', ['Unreliable karaoke captions'])))
            raise RuntimeError(f'Visualizer exited {code}; see {folder / "render.log"}')
        receipt = json.loads((Path(job['output_dir']) / 'render_receipt.json').read_text())
        if receipt.get('status') != 'complete' or receipt['provenance']['audio_sha256'] != job['audio_sha256']:
            raise ValueError('Visualizer did not publish a complete matching receipt')
        if digest(job['audio']) != job['audio_sha256']:
            current.update(status='superseded', error='Master changed during rendering')
            return current
        current.update(status='complete', videos=receipt['videos'], words_file=receipt['words_file'],
                       word_count=receipt['word_count'], review_count=receipt['review_count'],
                       karaoke_quality=receipt.get('karaoke_quality',{}),
                       issues=receipt['issues'], words_sha256=receipt['provenance']['words_sha256'], finished_at=time.time())
        current.pop('error', None)
        if not state_is_current(job, current):
            raise ValueError('Missing render artifacts')
        print(f"Karaoke videos completed: {Path(job['audio']).stem}", flush=True)
    except (KeyboardInterrupt, SystemExit):
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        current.update(status='queued', attempts=attempt - 1, reason='Worker interrupted; resume next startup')
        raise
    except KaraokeReviewNeeded as exc:
        current.update(status='needs_review', error=str(exc))
        current.pop('next_attempt_at', None)
        print(f"Karaoke needs review: {Path(job['audio']).stem}: {exc}", flush=True)
    except Exception as exc:
        current.update(status='failed' if attempt >= MAX_ATTEMPTS else 'retrying', error=str(exc))
        if attempt < MAX_ATTEMPTS:
            current['next_attempt_at'] = time.time() + min(600, 60 * 2 ** (attempt - 1))
        print(f"Visualizer {current['status']}: {Path(job['audio']).stem}: {exc}", flush=True)
    finally:
        publish_state(job, current)
    return current


def worker(queue_root=QUEUE_ROOT, once=False):
    queue_root = Path(queue_root)
    queue_root.mkdir(parents=True, exist_ok=True)
    with (queue_root / '.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def interrupted(signum, frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, interrupted)
        while True:
            job, state = next_job(queue_root)
            if job:
                from local_compute import compute_lease
                state = {**state, 'status': 'waiting_for_compute'}
                publish_state(job, state)
                with compute_lease('visualizer', Path(job['audio']).stem) as fd:
                    result = run_job(job, state, compute_fd=fd)
                if once:
                    return 0 if result['status'] == 'complete' else 1
            elif once:
                return 0
            else:
                time.sleep(10)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', type=Path, default=QUEUE_ROOT)
    parser.add_argument('--enqueue', type=Path, help='Recursively enqueue processed masters under a directory, or a single master')
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    parser.add_argument('--words',type=Path,help='Prepared caption JSON for a single --enqueue audio file')
    parser.add_argument('--status', action='store_true', help='Show jobs and exit')
    parser.add_argument('--retry-failed', action='store_true', help='Requeue failed jobs and exit')
    parser.add_argument('--once', action='store_true', help='Process one ready job and exit')
    args = parser.parse_args(argv)
    if args.words and (not args.enqueue or not args.enqueue.is_file()):
        parser.error('--words requires a single --enqueue audio file')
    if args.enqueue:
        if not args.enqueue.exists():
            parser.error('Enqueue path does not exist')
        files = [args.enqueue] if args.enqueue.is_file() else [p.with_name(p.parent.name + '.flac') for p in args.enqueue.rglob('audio.flac')]
        errors = 0
        for path in sorted(files):
            try:
                result = enqueue_track(path, database=args.database, queue_root=args.queue, words_file=args.words)
                print(f'{path.parent.name}: {result["status"]} ({result["job_id"][:12]})')
            except Exception as exc:
                print(f'{path}: FAILED: {exc}')
                errors += 1
        return 1 if errors else 0
    if args.status or args.retry_failed:
        counts = {}
        for path in sorted((args.queue / 'jobs').glob('*/job.json')):
            job = json.loads(path.read_text())
            state = json.loads(path.with_name('state.json').read_text())
            if args.retry_failed and state['status'] == 'failed':
                state = {'status': 'queued', 'attempts': 0}
                publish_state(job, state)
            counts[state['status']] = counts.get(state['status'], 0) + 1
            print(f'{state["status"]:10} {Path(job["audio"]).stem}  {path.parent}')
        print(json.dumps(counts, sort_keys=True))
        return 0
    return worker(args.queue, args.once)


if __name__ == '__main__':
    raise SystemExit(main())
