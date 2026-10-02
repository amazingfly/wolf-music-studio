import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import makeConfig as config


def write(path, payload, modified=10000):
    path.write_text(json.dumps(payload))
    os.utime(path, (modified, modified))
    return path


def test_selection_intersects_filename_and_time(tmp_path):
    song = {'style': 'metal', 'lyrics': 'Sing'}
    first = write(tmp_path / 'gemini-a.json', song, 8000)
    second = write(tmp_path / 'gemini-b.json', song, 10000)
    write(tmp_path / 'gemini-old.json', song, 7000)
    write(tmp_path / 'gemini-future.json', song, 11000)
    write(tmp_path / 'other.json', song, 9000)
    write(tmp_path / 'gemini.txt', song, 9000)
    assert config.select_files(tmp_path, 'gemini-', 8000, 10000) == [first, second]
    assert config.select_files(tmp_path, 'gemini-?.json', 8000, 10000) == [first, second]
    assert len(config.select_files(tmp_path, after=8000, before=10000)) == 3


def test_merge_supported_shapes_and_defaults(tmp_path):
    song = {'prompt': 'metal', 'lyrics': '[Verse]\nSing'}
    payloads = [song, [song], {'songs': [song]}, {'generations': [dict(song, id='explicit', seed=42)]}]
    files = [write(tmp_path / f'{i}.json', payload) for i, payload in enumerate(payloads)]
    rows = config.combine(files)
    assert [row['id'] for row in rows] == ['song_01', 'song_02', 'song_03', 'explicit']
    assert [row['seed'] for row in rows] == [85300, 85301, 85302, 42]
    assert all(row['style'] == 'metal' and row['target_seconds'] == 360 for row in rows)
    assert all(len(row['source_sha256']) == 64 for row in rows)
    destination = tmp_path / 'pending' / 'batch.json'
    config.publish(rows, destination)
    assert config.songs(destination) == rows
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        config.publish(rows, destination)
    assert destination.read_bytes() == original
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize('raw', ['{', '{"style":"x","style":"y","lyrics":"z"}',
                                  '{"style":"x","lyrics":"y","seed":NaN}',
                                  '{"style":"x"}', '{"songs":[]}'])
def test_invalid_input_never_published(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    write(tmp_path / 'gemini-good.json', {'style': 'x', 'lyrics': 'y'})
    (tmp_path / 'gemini-bad.json').write_text(raw)
    with pytest.raises(SystemExit) as exc:
        config.main(['-name', 'batch', '-iString', 'gemini-', '--input-dir', str(tmp_path)])
    assert exc.value.code == 1
    assert not (tmp_path / 'queue').exists()


def test_cross_file_duplicate_ids_rejected(tmp_path):
    files = [write(tmp_path / f'{i}.json', {'id': 'same', 'style': 'x', 'lyrics': 'y'}) for i in range(2)]
    with pytest.raises(ValueError, match='unique'):
        config.combine(files)


def test_cli_relative_time_dry_run_and_no_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(config.time, 'time', lambda: 20000)
    write(tmp_path / 'gemini-new.json', {'style': 'x', 'lyrics': 'y'})
    write(tmp_path / 'gemini-old.json', {'style': 'x', 'lyrics': 'old'}, 1000)
    args = ['-name', 'powerwolf.json', '-iString', 'gemini-', '-time', '5h', '--input-dir', str(tmp_path)]
    config.main(args + ['--dry-run'])
    assert not (tmp_path / 'queue').exists()
    config.main(args)
    assert len(config.songs(tmp_path / 'queue/pending/powerwolf.json')) == 1
    with pytest.raises(SystemExit):
        config.main(['-name', 'empty', '-iString', 'absent', '--input-dir', str(tmp_path)])
    assert not (tmp_path / 'queue/pending/empty.json').exists()


@pytest.mark.parametrize('args', [
    ['-name', '../escape', '-time', '5h'], ['-name', 'x'],
    ['-name', 'x', '-time', '0h'], ['-name', 'x', '-time', 'bad'],
    ['-name', 'x', '-time', '5h', '--since', '2026-09-24'],
    ['-name', 'x', '--since', '2026-09-25', '--until', '2026-09-24'],
])
def test_invalid_arguments(args):
    with pytest.raises(SystemExit) as exc:
        config.main(args)
    assert exc.value.code != 0


def test_final_validation_prevents_publication(tmp_path):
    destination = tmp_path / 'pending' / 'bad.json'
    with pytest.raises(ValueError):
        config.publish([{'lyrics': 'missing style'}], destination)
    assert not destination.exists()
    assert not list(destination.parent.iterdir())
