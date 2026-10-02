"""Locked, atomic updates shared by the downloader and TrackTags."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager

DEFAULT_DATABASE = Path(__file__).resolve().parent / 'outputs/master_database.json'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked_database(path=DEFAULT_DATABASE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError(f'Expected a JSON object: {path}; refusing to overwrite it')
        yield data
        atomic_json(path, data)


def key_for(data, path):
    path = Path(path).resolve()
    for key, record in data.items():
        if isinstance(record, dict) and record.get('file_path') and Path(record['file_path']).resolve() == path:
            return key
    title = path.parent.name if path.name == 'audio.flac' else path.stem
    key = f'{title}_{path.name}'
    return str(path) if key in data else key


def record_for(data, path):
    return data.get(key_for(data, path), {})


def merge_record(path, record, database=DEFAULT_DATABASE):
    """Read current state under lock, preserve workflow fields, then publish both JSONs."""
    path = Path(path).resolve()
    with locked_database(database) as data:
        key = key_for(data, path)
        current = data.get(key, {})
        workflow = current.get('audio_workflow')
        if workflow and record.get('analysis_audio_sha256') and record['analysis_audio_sha256'] != workflow.get('output_sha256'):
            raise ValueError(f'Audio changed during tagging: {path}; rerun TrackTags')
        merged = {**current, **record, 'file_path': str(path)}
        if workflow:
            merged['audio_workflow'] = workflow
        data[key] = merged
        atomic_json(path.with_name(path.stem + '_metadata.json'), merged)
    return key, merged
