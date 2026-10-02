#!/usr/bin/env python3
"""Combine validated song downloads and atomically submit a directory-queue config."""
import argparse
from datetime import datetime
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from run_config import ROOT, component, songs


def duration(value):
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([smhdw])', value)
    if not match:
        raise argparse.ArgumentTypeError('Use a positive duration such as 30m, 5h, or 2d')
    seconds = float(match[1]) * dict(s=1, m=60, h=3600, d=86400, w=604800)[match[2]]
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('Duration must be positive and finite')
    return seconds


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use an ISO date/time; times without an offset are local') from exc


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f'Invalid JSON number: {value}')


def select_files(directory, pattern=None, after=None, before=None):
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f'Input directory does not exist: {directory}')
    matches = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != '.json':
            continue
        if pattern:
            wildcard = any(char in pattern for char in '*?[')
            if not (fnmatch.fnmatchcase(path.name, pattern) if wildcard else pattern in path.name):
                continue
        modified = path.stat().st_mtime
        if after is not None and modified < after:
            continue
        if before is not None and modified > before:
            continue
        matches.append((modified, path.name, path))
    return [entry[2] for entry in sorted(matches)]


def combine(files):
    combined = []
    # Validate snapshots, so the parser and provenance refer to the same bytes.
    with tempfile.TemporaryDirectory(prefix='yue2-config-') as directory:
        snapshot = Path(directory) / 'input.json'
        for source in files:
            try:
                raw = source.read_bytes()
                data = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
                snapshot.write_bytes(raw)
                songs(snapshot)  # Validate each original input using the pipeline schema.
                if isinstance(data, dict):
                    data = data.get('songs', data.get('generations', [data]))
                for value in data:
                    row = dict(value)
                    row.setdefault('id', f'song_{len(combined) + 1:02d}')
                    row.setdefault('seed', 85300 + len(combined))
                    row.setdefault('source_concept', source.name)
                    row.setdefault('source_sha256', hashlib.sha256(raw).hexdigest())
                    combined.append(row)
            except (ValueError, TypeError, OSError) as exc:
                raise ValueError(f'{source}: {exc}') from exc
        snapshot.write_text(json.dumps({'songs': combined}, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return songs(snapshot)  # Also reject ID collisions across files and fill defaults.


def publish(rows, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.tmp',
                                         dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'songs': rows}, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        songs(temporary)  # Validate the exact final file before exposing it to the watcher.
        os.link(temporary, destination)  # Atomic publication, refusing existing destinations.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-name', '--name', required=True, help='Output basename (optional .json suffix)')
    parser.add_argument('-iString', '--input-string', help='Case-sensitive filename substring or shell glob')
    parser.add_argument('-time', '--time', type=duration, help='Modified within this duration: 30m, 5h, 2d')
    parser.add_argument('--since', type=timestamp, help='Inclusive modification-time start (ISO date/time)')
    parser.add_argument('--until', type=timestamp, help='Inclusive modification-time end (ISO date/time)')
    parser.add_argument('--input-dir', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--dry-run', action='store_true', help='Select and validate without enqueueing')
    args = parser.parse_args(argv)
    if not any((args.input_string, args.time, args.since is not None, args.until is not None)):
        parser.error('Provide -iString, -time, --since, or --until')
    if args.time is not None and (args.since is not None or args.until is not None):
        parser.error('Use either -time or --since/--until')
    try:
        name = component(args.name.removesuffix('.json'))
        now = time.time()
        after = now - args.time if args.time is not None else args.since
        before = now if args.time is not None else args.until
        if after is not None and before is not None and after > before:
            raise ValueError('--since must be at or before --until')
        files = select_files(args.input_dir, args.input_string, after, before)
        if not files:
            raise ValueError('No JSON files matched; nothing queued')
        rows = combine(files)
        destination = ROOT / 'queue' / 'pending' / f'{name}.json'
        for path in files:
            print(f'Input: {path}')
        print(f'Validated {len(rows)} songs from {len(files)} files')
        if args.dry_run:
            print(f'Dry run; would queue: {destination}')
        else:
            publish(rows, destination)
            print(f'Queued: {destination}')
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
