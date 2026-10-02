"""Local, rebuildable SQLite catalog of YuE2 prompts, audio variants and TrackTags."""
from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / 'outputs' / 'track_catalog.sqlite3'
AUDIO_SUFFIXES = {'.flac', '.ogg', '.wav', '.mp3', '.m4a'}


def read_json(path, default, warnings):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(default, dict) and not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value
    except (OSError, ValueError) as exc:
        warnings.append(f'{path}: {exc}')
        return default


def hashtags(metadata):
    """Clipboard form: one space between hashtags, with no trailing newline."""
    result = []
    for tag in metadata.get('hashtags', []):
        clean = '#' + re.sub(r'\s+', '', str(tag).lstrip('#'))
        if clean != '#' and clean.casefold() not in {item.casefold() for item in result}:
            result.append(clean)
    return ' '.join(result)


def prompt_text(track):
    if not track.get('prompt'):
        raise ValueError('No source prompt found for this track')
    return json.dumps(track['prompt'], ensure_ascii=False, indent=2)


@contextmanager
def connect(db=DEFAULT_DB):
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db, timeout=20)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE IF NOT EXISTS tracks (location TEXT PRIMARY KEY, batch TEXT NOT NULL, payload TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE)')
        # These are user-authored data, deliberately independent of rebuildable tracks.
        connection.execute('CREATE TABLE IF NOT EXISTS favorites (category_id INTEGER NOT NULL, location TEXT NOT NULL, prompt TEXT NOT NULL, PRIMARY KEY (category_id, location))')
        with connection:
            yield connection
    finally:
        connection.close()


def load_tracks(db=DEFAULT_DB):
    with connect(db) as connection:
        memberships = {}
        for location, name in connection.execute('SELECT f.location, c.name FROM favorites f JOIN categories c ON c.id=f.category_id ORDER BY c.name_key'):
            memberships.setdefault(location, []).append(name)
        tracks = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM tracks ORDER BY batch DESC, location')]
        for track in tracks:
            track['categories'] = memberships.get(track['location'], [])
        return tracks


def create_category(name, db=DEFAULT_DB):
    name = name.strip()
    if not name or len(name) > 100 or not all(c.isprintable() for c in name):
        raise ValueError('Category names must contain 1–100 printable characters')
    with connect(db) as connection:
        try:
            cursor = connection.execute('INSERT INTO categories (name, name_key) VALUES (?, ?)', (name, name.casefold()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('A category with that name already exists') from exc
        return cursor.lastrowid


def list_categories(db=DEFAULT_DB):
    with connect(db) as connection:
        return [{'id': row[0], 'name': row[1], 'count': row[2]} for row in connection.execute(
            'SELECT c.id, c.name, COUNT(f.location) FROM categories c LEFT JOIN favorites f ON f.category_id=c.id GROUP BY c.id ORDER BY c.name_key')]


def toggle_favorite(category_id, track, db=DEFAULT_DB):
    with connect(db) as connection:
        if not connection.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
            raise ValueError('Category no longer exists')
        location = track['location']
        if connection.execute('SELECT 1 FROM favorites WHERE category_id=? AND location=?', (category_id, location)).fetchone():
            connection.execute('DELETE FROM favorites WHERE category_id=? AND location=?', (category_id, location))
            return False
        if not track.get('prompt'):
            raise ValueError('This track has no source prompt to save as an example')
        connection.execute('INSERT INTO favorites VALUES (?, ?, ?)',
                           (category_id, location, json.dumps(track['prompt'], ensure_ascii=False)))
        return True


def export_category(category_id, destination, db=DEFAULT_DB):
    """Export saved examples as validated pipeline JSON, without catalog metadata."""
    from run_config import songs
    with connect(db) as connection:
        if not connection.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
            raise ValueError('Category no longer exists')
        saved = [json.loads(row[0]) for row in connection.execute(
            'SELECT prompt FROM favorites WHERE category_id=? ORDER BY location', (category_id,))]
    if not saved:
        raise ValueError('This category has no tracks to export')
    fields = {'id', 'title', 'style', 'lyrics', 'cot', 'seed', 'target_seconds', 'duration_validation'}
    rows = []
    reserved = {row.get('id') for row in saved}
    used = set()
    for index, prompt in enumerate(saved, 1):
        row = {key: value for key, value in prompt.items() if key in fields}
        if 'style' not in row and 'prompt' in prompt:
            row['style'] = prompt['prompt']
        base = row.get('id', f'example_{index:02d}')
        name = base
        number = 2
        if name in used or ('id' not in row and name in reserved):
            name = f'{base}_example_{number}'
            while name in reserved or name in used:
                number += 1
                name = f'{base}_example_{number}'
        row['id'] = name
        used.add(name)
        rows.append(row)
    destination = Path(destination).expanduser().resolve()
    if destination.suffix.lower() != '.json':
        raise ValueError('Export filename must end in .json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'songs': rows}, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        songs(temporary)
        os.link(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return destination, len(rows)


def initialized(db=DEFAULT_DB):
    with connect(db) as connection:
        return connection.execute("SELECT value FROM settings WHERE key='full_scan'").fetchone() is not None


def tag_records(outputs, warnings):
    records = read_json(outputs / 'master_database.json', {}, warnings)
    if not isinstance(records, dict):
        warnings.append('TrackTags master database must be an object')
        return {}
    by_path = {}
    for record in records.values():
        if isinstance(record, dict) and record.get('file_path'):
            by_path[str(Path(record['file_path']).expanduser().resolve())] = record
    return by_path


def requests_for(run, warnings):
    rows = []
    path = run / 'requests.jsonl'
    if path.exists():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    warnings.append(f'{path}:{number}: {exc}')
    else:
        data = read_json(run / 'source_config.json', [], warnings)
        if isinstance(data, dict):
            data = data.get('songs', data.get('generations', [data]))
        if isinstance(data, list):
            rows = data
        # The original run predates frozen per-run configs.
        if not rows and run.name == 'industrial_360s_t4':
            for folder in sorted(run.iterdir()):
                if folder.is_dir():
                    value = read_json(run.parent.parent / 'requests' / f'{folder.name}.json', {}, warnings)
                    if value:
                        rows.append(value)
    return {row['id']: row for row in rows if isinstance(row, dict) and
            isinstance(row.get('id'), str) and Path(row['id']).name == row['id'] and row['id'] not in {'.', '..'}}


def audio_file(path, tags, warnings):
    path = path.resolve()
    metadata = tags.get(str(path), {})
    local = read_json(path.with_name(path.stem + '_metadata.json'), {}, warnings)
    if isinstance(local, dict) and local:
        # Sidecars with stale paths must not attach another file's analysis.
        if not local.get('file_path') or Path(local['file_path']).resolve() == path:
            metadata = {**metadata, **local}
    workflow = metadata.get('audio_workflow', {})
    preferred = (workflow.get('role') == 'master' and workflow.get('status') == 'complete'
                 and workflow.get('output_path') == str(path))
    kind = 'trimmed' if path.stem.casefold().startswith(('trim_', 'testtrim_')) else ('original' if path.name.casefold() == 'audio.flac' else 'named')
    if preferred:
        kind = 'trimmed'
    return {'path': str(path), 'name': path.name, 'kind': kind, 'exists': path.is_file(),
            'preferred': preferred, 'metadata': metadata, 'modified_at': path.stat().st_mtime if path.is_file() else None}


def file_order(file):
    return (not file.get('preferred', False), Path(file['path']).suffix.lower() != '.flac',
            {'trimmed': 0, 'named': 1, 'original': 2}[file['kind']], file['name'])


def build_track(folder, prompt, run, files, warnings):
    receipt = read_json(folder / 'result.json', {}, warnings)
    context = read_json(run / 'run.json', {}, warnings)
    finished = receipt.get('finished_at')
    date = datetime.fromtimestamp(finished).astimezone().isoformat() if isinstance(finished, (int, float)) else context.get('created_at', '')
    if not date:
        match = re.match(r'\d{4}-\d{2}-\d{2}', run.name)
        date = match[0] if match else ''
    if not date and files:
        modified = next((f['modified_at'] for f in files if f['modified_at']), None)
        date = datetime.fromtimestamp(modified).astimezone().isoformat() if modified else ''
    files.sort(key=file_order)
    present = [f for f in files if f['exists']]
    master = next((f for f in present if f.get('preferred')), None)
    flacs = [f for f in present if Path(f['path']).suffix.lower() == '.flac']
    return {'location': str(folder.resolve()), 'id': prompt.get('id', folder.name),
            'title': prompt.get('title', prompt.get('id', folder.name)), 'batch': run.name,
            'date': date, 'prompt': prompt, 'files': files, 'receipt': receipt,
            'duration': master['metadata']['audio_workflow']['output_seconds'] if master else receipt.get('audio_seconds'),
            'original_duration': receipt.get('audio_seconds'),
            'visualizer_audio': master['path'] if master else None,
            'processing_status': master['metadata']['audio_workflow']['status'] if master else next(
                (f['metadata']['audio_workflow'].get('status') for f in files if f['metadata'].get('audio_workflow')), 'pending'),
            'status': 'available' if present else ('missing' if receipt or files else 'pending'),
            'trimmed': any(f['kind'] == 'trimmed' for f in present),
            'only_audio_flac': len(flacs) == 1 and flacs[0]['kind'] == 'original',
            'named_flac': any(f['kind'] != 'original' for f in flacs)}


def update_files(track, files):
    """Recalculate availability after attaching an export outside the run folder."""
    track['files'] = sorted(files, key=file_order)
    present = [f for f in files if f['exists']]
    master = next((f for f in track['files'] if f['exists'] and f.get('preferred')), None)
    if master:
        track['duration'] = master['metadata']['audio_workflow']['output_seconds']
        track['visualizer_audio'] = master['path']
        track['processing_status'] = 'complete'
    local_flacs = [f for f in present if Path(f['path']).parent == Path(track['location']) and Path(f['path']).suffix.lower() == '.flac']
    track['trimmed'] = any(f['kind'] == 'trimmed' for f in present)
    track['only_audio_flac'] = len(local_flacs) == 1 and local_flacs[0]['kind'] == 'original'
    track['named_flac'] = any(f['kind'] != 'original' for f in local_flacs)
    track['status'] = 'available' if present else ('missing' if files or track['receipt'] else 'pending')


def scan_run(run, tags, warnings):
    prompts = requests_for(run, warnings)
    folders = {p for p in run.iterdir() if p.is_dir() and ((p / 'result.json').exists() or (p / 'request.json').exists() or any(p.glob('*.flac')))}
    folders.update(run / name for name in prompts)
    tracks = []
    for folder in sorted(folders):
        prompt = read_json(folder / 'request.json', prompts.get(folder.name, {}), warnings)
        if not isinstance(prompt, dict):
            warnings.append(f'Invalid prompt in {folder}')
            prompt = prompts.get(folder.name, {})
        paths = {p.resolve() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES} if folder.exists() else set()
        paths.update(Path(p) for p in tags if Path(p).parent == folder.resolve())
        tracks.append(build_track(folder, prompt, run, [audio_file(p, tags, warnings) for p in paths], warnings))
    return tracks


def sync_catalog(outputs=None, db=None, run=None):
    outputs = Path(outputs or ROOT / 'outputs').resolve()
    db = Path(db or outputs / 'track_catalog.sqlite3')
    warnings = []
    tags = tag_records(outputs, warnings)
    runs = [Path(run).resolve()] if run else [p for p in outputs.iterdir() if p.is_dir() and
            ((p / 'requests.jsonl').exists() or (p / 'source_config.json').exists() or
             any(p.glob('*/result.json')) or any(p.glob('*/request.json')))]
    tracks = []
    for directory in sorted(runs):
        tracks.extend(scan_run(directory, tags, warnings))
    if run is None:
        known = {f['path'] for track in tracks for f in track['files']}
        extra = {p.resolve() for p in outputs.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES}
        extra.update(Path(p) for p in tags)
        by_id = {}
        for track in tracks:
            by_id.setdefault(track['id'].casefold(), []).append(track)
        # Link uniquely named exports, never guess across repeated song IDs.
        for path in sorted(extra):
            if str(path) in known:
                continue
            file = audio_file(path, tags, warnings)
            identifier = re.sub(r'^(?:trim_|testtrim_)', '', path.stem, flags=re.IGNORECASE).casefold()
            candidates = by_id.get(identifier, [])
            if len(candidates) == 1:
                track = candidates[0]
                update_files(track, [*track['files'], file])
                continue
            track = build_track(path.parent, {}, path.parent, [file], warnings)
            track.update(location=str(path), id=path.stem, title=file['metadata'].get('track_identifier', path.stem), status='available' if file['exists'] else 'missing')
            tracks.append(track)
    elif db.exists():
        # Incremental completion/tagging updates retain previously linked exports.
        previous = {row['location']: row for row in load_tracks(db)}
        for track in tracks:
            paths = {f['path'] for f in track['files']}
            linked = [audio_file(Path(f['path']), tags, warnings)
                      for f in previous.get(track['location'], {}).get('files', [])
                      if f['path'] not in paths and Path(f['path']).parent != Path(track['location'])]
            update_files(track, [*track['files'], *linked])
    with connect(db) as connection:
        if run is None:
            connection.execute('DELETE FROM tracks')
        for track in tracks:
            connection.execute('INSERT OR REPLACE INTO tracks VALUES (?, ?, ?)',
                               (track['location'], track['batch'], json.dumps(track, ensure_ascii=False)))
        connection.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                           ('last_scan' if run else 'full_scan', str(time.time())))
    return len(tracks), warnings


def sync_run_safely(run):
    """Catalog errors must not undo a successful generation or archive."""
    try:
        _, warnings = sync_catalog(outputs=Path(run).parent, run=run)
        for warning in warnings:
            print(f'Catalog warning: {warning}', flush=True)
    except Exception as exc:
        print(f'Catalog update deferred (run trackLibrary.py --refresh): {exc}', flush=True)


def search(tracks, name='', filters=None):
    filters = filters or {}
    words = name.casefold().split()
    result = []
    for track in tracks:
        if filters.get('category') and filters['category'].casefold() not in {name.casefold() for name in track.get('categories', [])}:
            continue
        title = (track['title'] + ' ' + track['id']).casefold().replace('_', ' ')
        if not all(word.replace('_', ' ') in title for word in words):
            continue
        if filters.get('batch', '').casefold() not in track['batch'].casefold():
            continue
        day = track['date'][:10]
        if filters.get('from') and (not day or day < filters['from']):
            continue
        if filters.get('to') and (not day or day > filters['to']):
            continue
        tags = {tag.casefold().lstrip('#') for f in track['files'] for tag in hashtags(f['metadata']).split()}
        required = filters.get('tags', '').replace(',', ' ').casefold().split()
        if not all(tag.lstrip('#') in tags for tag in required):
            continue
        if filters.get('trimmed', '') in {'yes', 'no'} and track['trimmed'] != (filters['trimmed'] == 'yes'):
            continue
        variant = filters.get('files', '')
        if variant == 'original-only' and not track['only_audio_flac']:
            continue
        if variant == 'named' and not track['named_flac']:
            continue
        if filters.get('status') and track['status'] != filters['status']:
            continue
        text = json.dumps([track['prompt'], [f['metadata'] for f in track['files']]], ensure_ascii=False).casefold()
        if not all(word in text for word in filters.get('text', '').casefold().split()):
            continue
        if filters.get('untagged') == 'yes' and tags:
            continue
        result.append(track)
    return result
