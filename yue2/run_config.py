"""Validated song inputs and immutable identities shared by all run stages."""
import hashlib
import os
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRIVE_BASE = os.environ.get('YUE2_DRIVE_BASE', 'gDrive:yue2/').rstrip('/') + '/'


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError(f'Invalid run/song name: {value!r}')
    return value


def songs(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    data = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix == '.jsonl' else json.loads(text)
    if isinstance(data, dict):
        data = data.get('songs', data.get('generations', [data]))
    if not isinstance(data, list) or not data:
        raise ValueError('Config must be a song object, a nonempty array, or {"songs": [...]}')
    rows = []
    for index, value in enumerate(data):
        if not isinstance(value, dict):
            raise ValueError('Every song must be a JSON object')
        row = dict(value)
        row.setdefault('id', f'song_{index+1:02d}')
        component(row['id'])
        if 'style' not in row and 'prompt' in row:
            row['style'] = row.pop('prompt')
        for key in ('style', 'lyrics'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f'{row["id"]}: missing {key}')
        row.setdefault('title', row['id'])
        row.setdefault('cot', 'full')
        row.setdefault('seed', 85300 + index)
        row.setdefault('target_seconds', 360)
        row.setdefault('duration_validation', {'min_seconds':250, 'max_seconds':380})
        allowed = {'id','title','style','lyrics','cot','seed','target_seconds',
                   'duration_validation','source_concept','source_sha256'}
        if set(row) - allowed:
            raise ValueError(f'{row["id"]}: unsupported fields {sorted(set(row)-allowed)}')
        if row['cot'] not in {'full','melody','off'}:
            raise ValueError('cot must be full, melody or off')
        if type(row['seed']) is not int or not 0 <= row['seed'] < 2**63:
            raise ValueError('seed must be a nonnegative integer below 2**63')
        if row['target_seconds'] != 360:
            raise ValueError('This runner currently generates 360-second tracks')
        bounds = row['duration_validation']
        if (not isinstance(bounds, dict) or set(bounds) != {'min_seconds','max_seconds'} or
            any(type(v) not in (int,float) for v in bounds.values()) or
            not 0 < bounds['min_seconds'] <= 360 <= bounds['max_seconds'] < float('inf')):
            raise ValueError('Invalid duration_validation bounds')
        rows.append(row)
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Song IDs must be unique')
    return rows


def create_run(config):
    source = Path(config).expanduser().resolve()
    rows = songs(source)  # Validate before creating anything or requesting a GPU.
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', source.stem).strip('._-') or 'songs'
    name = component(datetime.now().astimezone().strftime('%Y-%m-%d_%H-%M-%S_%f') + '_' + stem)
    directory = ROOT / 'outputs' / name
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'source_config.json').write_bytes(source.read_bytes())
    requests = directory / 'requests.jsonl'
    requests.write_text(''.join(json.dumps(row,ensure_ascii=False) + '\n' for row in rows),encoding='utf-8')
    context = {'run_name': name, 'drive_base': DRIVE_BASE, 'source_config': str(source),
               'requests_sha256': hashlib.sha256(requests.read_bytes()).hexdigest(),
               'session': 'yue2-' + hashlib.sha256(name.encode()).hexdigest()[:16],
               'created_at': datetime.now().astimezone().isoformat()}
    manifest = directory / 'run.json'
    manifest.write_text(json.dumps(context,indent=2)+'\n')
    return manifest


def load_run(manifest):
    manifest = Path(manifest).expanduser().resolve()
    context = json.loads(manifest.read_text())
    component(context['run_name'])
    component(context['session'])
    if manifest.parent.name != context['run_name']:
        raise ValueError('Run manifest and folder name disagree')
    requests = manifest.parent / 'requests.jsonl'
    if hashlib.sha256(requests.read_bytes()).hexdigest() != context['requests_sha256']:
        raise ValueError('Saved song config changed; start a new run instead')
    songs(requests)
    return context
