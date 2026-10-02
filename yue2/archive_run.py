"""Verify a completed queue locally before removing its exact Drive run folder."""
import hashlib
import json
import subprocess
import time
from pathlib import Path, PurePosixPath
from track_catalog import sync_run_safely

ROOT = Path(__file__).resolve().parent
from run_config import DRIVE_BASE
REMOTE = DRIVE_BASE + 'industrial_360s_t4'
LOCAL = ROOT / 'outputs/industrial_360s_t4'
REQUESTS = ROOT / 'requests/batch.jsonl'


def configure(manifest):
    global REMOTE, LOCAL, REQUESTS
    from run_config import load_run, DRIVE_BASE
    context = load_run(manifest)
    LOCAL = Path(manifest).resolve().parent
    REQUESTS = LOCAL / 'requests.jsonl'
    REMOTE = context.get('drive_base', DRIVE_BASE).rstrip('/') + '/' + context['run_name']


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def command(*args):
    result = subprocess.run(['rclone', *args], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result.stdout


def inventory():
    try:
        return json.loads(command('lsjson', REMOTE, '--recursive', '--files-only', '--hash'))
    except RuntimeError as exc:
        if 'directory not found' in str(exc).lower():
            return []
        raise


def expected():
    return [json.loads(line) for line in REQUESTS.read_text().splitlines() if line.strip()]


def verify_audio(requests=None):
    import soundfile as sf
    receipts = []
    for request in (expected() if requests is None else requests):
        folder = LOCAL / request['id']
        receipt = json.loads((folder / 'result.json').read_text())
        audio = folder / 'audio.flac'
        if receipt.get('status') != 'complete' or receipt.get('id') != request['id']:
            raise ValueError(f'Incomplete result: {request["id"]}')
        if digest(audio) != receipt.get('audio_sha256'):
            raise ValueError(f'Audio checksum mismatch: {audio}')
        info = sf.info(str(audio))
        if info.samplerate != 48000 or info.channels != 2:
            raise ValueError(f'Unexpected audio format: {audio}')
        if abs(info.duration - receipt['audio_seconds']) > 0.01:
            raise ValueError(f'Duration differs from receipt: {audio}')
        bounds = request['duration_validation']
        if not bounds['min_seconds'] <= info.duration <= bounds['max_seconds']:
            raise ValueError(f'Track duration outside configured bounds: {audio}')
        receipts.append({'id': request['id'], 'audio_seconds': info.duration,
                         'audio_sha256': receipt['audio_sha256']})
    return receipts


def download_completed():
    """Collect completed tracks after an allocation, keeping resume data on Drive."""
    files = inventory()
    paths = {entry['Path'] for entry in files}
    ready = [row for row in expected() if
             f'{row["id"]}/result.json' in paths and f'{row["id"]}/audio.flac' in paths]
    receipts = []
    for request in ready:
        name = request['id']
        # IDs were validated when the run was created; enforce that here too.
        if not name or PurePosixPath(name).name != name or name in {'.','..'}:
            raise ValueError('Unsafe song ID')
        folder = LOCAL / name
        try:
            receipt = verify_audio([request])[0]
            remote_receipts = [entry for entry in files if entry['Path'] == f'{name}/result.json']
            verify_inventory(remote_receipts)
        except (OSError, ValueError, KeyError):
            print(f'Downloading completed track: {name}', flush=True)
            folder.mkdir(parents=True, exist_ok=True)
            command('copy', REMOTE + '/' + name, str(folder), '--checksum',
                    '--transfers', '2', '--checkers', '2')
            verify_inventory([entry for entry in files if entry['Path'].startswith(name + '/')])
            receipt = verify_audio([request])[0]
        receipt['postprocessing'] = postprocess_tracks([request])[0]
        receipts.append(receipt)
    LOCAL.mkdir(parents=True, exist_ok=True)
    marker = LOCAL / 'downloaded_tracks.json'
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'updated_at': time.time(), 'tracks': receipts,
                                    'expected': len(expected()), 'drive_retained_for_resume': True}, indent=2))
    temporary.replace(marker)
    print(f'Local completed tracks: {len(receipts)}/{len(expected())}', flush=True)
    sync_run_safely(LOCAL)
    return receipts


def postprocess_tracks(requests=None):
    """Run only on verified local originals. Failures remain retryable next harvest."""
    from processTracks import process_track
    from visualizer_queue import enqueue_track
    results = []
    for request in (expected() if requests is None else requests):
        name = request['id']
        if not name or PurePosixPath(name).name != name or name in {'.', '..'}:
            raise ValueError('Unsafe song ID')
        try:
            report = process_track(LOCAL / name / 'audio.flac')
            result = {'id': name, 'status': report['processing_status'],
                      'output': report['output'], 'output_seconds': report['output_seconds']}
            try:
                result['visualizer'] = enqueue_track(report['output'], prompt=request)
            except Exception as exc:
                print(f'Visualizer enqueue failed (will retry): {name}: {exc}', flush=True)
                result['visualizer'] = {'status': 'failed', 'error': str(exc)}
            results.append(result)
        except Exception as exc:
            print(f'Track postprocessing failed (will retry): {name}: {exc}', flush=True)
            results.append({'id': name, 'status': 'failed', 'error': str(exc)})
    return results


def verify_inventory(files):
    cache = {}
    for entry in files:
        path = PurePosixPath(entry['Path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe remote artifact path')
        local = LOCAL / str(path)
        if local.is_symlink() or not local.is_file() or local.stat().st_size != entry['Size']:
            raise ValueError(f'Missing or wrong-sized archive file: {local}')
        md5 = entry.get('Hashes', {}).get('md5')
        if not md5:
            raise ValueError(f'Missing remote integrity hash: {path}')
        if str(path) not in cache:
            cache[str(path)] = digest(local, 'md5')
        # Includes every duplicate Drive object. Conflicting duplicates abort deletion.
        if cache[str(path)] != md5:
            raise ValueError(f'Archive checksum mismatch (or conflicting duplicate): {path}')


def local_complete():
    marker = LOCAL / 'local_completion.json'
    if not marker.exists():
        return False
    data = json.loads(marker.read_text())
    if data['requests_sha256'] != digest(REQUESTS):
        raise ValueError('Queue changed; choose a new archive/run name')
    verify_audio()
    return data.get('drive_deleted', False)


def archive_if_complete(before_delete=None):
    """Return False for an incomplete queue; never delete an unverified file."""
    if local_complete():
        postprocess_tracks()
        sync_run_safely(LOCAL)
        return True
    files = inventory()
    destination = LOCAL / 'local_completion.json'
    if destination.exists():
        # Resume after interruption during deletion or before the final marker.
        marker = json.loads(destination.read_text())
        verify_audio()
        verify_inventory(marker['remote_inventory'])
        known = {r['ID']: r for r in marker['remote_inventory']}
        for row in files:
            if row['ID'] not in known or row['Hashes'] != known[row['ID']]['Hashes']:
                raise RuntimeError('Unexpected Drive object during cleanup recovery')
        verify_inventory(files)
        postprocess_tracks()
        if before_delete:
            before_delete()
        if files:
            command('purge', REMOTE, '--drive-use-trash=false')
        if inventory():
            raise RuntimeError('Drive cleanup incomplete')
        marker.update(drive_deleted=True, archived_at=time.time())
        temp = destination.with_suffix('.tmp')
        temp.write_text(json.dumps(marker, indent=2)); temp.replace(destination)
        sync_run_safely(LOCAL)
        return True
    paths = {entry['Path'] for entry in files}
    if not all(f'{r["id"]}/result.json' in paths and f'{r["id"]}/audio.flac' in paths for r in expected()):
        return False
    LOCAL.mkdir(parents=True, exist_ok=True)
    print('Downloading completed YuE2 queue from Drive', flush=True)
    command('copy', REMOTE, str(LOCAL), '--checksum', '--transfers', '2', '--checkers', '2')
    receipts = verify_audio()
    verify_inventory(files)
    postprocess_tracks()
    if before_delete:
        before_delete()  # Stop only this queue's Colab worker before removing backups.
    current = inventory()
    if current != files:
        # Ordering may vary, so compare object IDs and their actual content hashes.
        def version(rows):
            return sorted((r['ID'], r['Path'], r['Size'], r['Hashes'].get('md5')) for r in rows)
        if version(current) != version(files):
            raise RuntimeError('Drive changed during archive; retry download before deleting')
    verify_inventory(current)
    marker = {'status': 'complete', 'requests_sha256': digest(REQUESTS), 'tracks': receipts,
              'remote': REMOTE, 'remote_inventory': current, 'verified_at': time.time(),
              'drive_deleted': False}
    temp = destination.with_suffix('.tmp')
    temp.write_text(json.dumps(marker, indent=2)); temp.replace(destination)
    # Exact run directory only. All its remote objects now have verified local copies.
    command('purge', REMOTE, '--drive-use-trash=false')
    if inventory():
        raise RuntimeError('Drive cleanup incomplete')
    marker['drive_deleted'] = True
    marker['archived_at'] = time.time()
    temp.write_text(json.dumps(marker, indent=2)); temp.replace(destination)
    print(f'Archived {len(receipts)} tracks locally; verified Drive copies removed', flush=True)
    sync_run_safely(LOCAL)
    return True


if __name__ == '__main__':
    raise SystemExit(0 if archive_if_complete() else 2)
