import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import wave

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import track_catalog as catalog
import trackLibrary as ui
from track_player import Player


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def generated(outputs, batch, name='song', title='Night Wolf'):
    run = outputs / batch
    folder = run / name
    folder.mkdir(parents=True)
    prompt = {'id': name, 'title': title, 'style': 'hard metal', 'lyrics': '[Verse]\nWolf rises', 'seed': 42}
    (run / 'requests.jsonl').write_text(json.dumps(prompt) + '\n')
    (folder / 'audio.flac').touch()
    save(folder / 'result.json', {'status': 'complete', 'audio_seconds': 360, 'finished_at': 1790199082})
    return run, folder, prompt


def test_import_identity_metadata_variants_and_repeat_scan(tmp_path):
    outputs = tmp_path / 'outputs'
    run, folder, prompt = generated(outputs, '2026-09-23_batch_one')
    generated(outputs, '2026-09-24_batch_two')
    (folder / 'TRIM_song.flac').touch()
    meta = {'file_path': str(folder / 'TRIM_song.flac'), 'hashtags': ['#Metal', '#Hardcore'], 'audio_metrics': {'bpm': 170, 'key': 'A minor'}}
    save(outputs / 'master_database.json', {'one': meta})
    db = outputs / 'catalog.sqlite3'
    count, warnings = catalog.sync_catalog(outputs, db)
    assert (count, warnings) == (2, [])
    assert catalog.initialized(db)
    tracks = catalog.load_tracks(db)
    assert len({t['location'] for t in tracks}) == 2
    trimmed = next(t for t in tracks if t['trimmed'])
    assert json.loads(catalog.prompt_text(trimmed)) == prompt
    assert trimmed['files'][0]['name'] == 'TRIM_song.flac'
    assert catalog.hashtags(trimmed['files'][0]['metadata']) == '#Metal #Hardcore'
    assert trimmed['named_flac'] and not trimmed['only_audio_flac']
    assert len(catalog.search(tracks, 'night wolf', {'tags': '#metal hardcore', 'trimmed': 'yes'})) == 1
    assert len(catalog.search(tracks, 'song', {'files': 'original-only'})) == 1
    assert len(catalog.search(tracks, filters={'batch': 'two', 'untagged': 'yes'})) == 1
    assert len(catalog.search(tracks, filters={'text': '170 minor'})) == 1
    assert not catalog.search(tracks, filters={'from': '2099-01-01'})
    assert not catalog.search(tracks, filters={'to': '2000-01-01'})
    assert catalog.sync_catalog(outputs, db)[0] == 2
    (folder / 'TRIM_song.flac').unlink()
    catalog.sync_catalog(outputs, db)
    row = next(t for t in catalog.load_tracks(db) if t['location'] == str(folder))
    assert not row['trimmed'] and row['only_audio_flac']
    assert any(not f['exists'] and f['metadata'] for f in row['files'])


def test_unique_exports_link_and_ambiguous_exports_stay_separate(tmp_path):
    outputs = tmp_path / 'outputs'
    run, folder, _ = generated(outputs, 'batch_one', 'unique')
    generated(outputs, 'batch_two', 'duplicate')
    generated(outputs, 'batch_three', 'duplicate')
    exports = outputs / 'ogg'
    exports.mkdir()
    (exports / 'TRIM_unique.ogg').touch()
    (exports / 'duplicate.ogg').touch()
    db = outputs / 'catalog.sqlite3'
    assert catalog.sync_catalog(outputs, db)[0] == 4
    tracks = catalog.load_tracks(db)
    assert sum(len(t['files']) for t in tracks) == 5
    linked = next(t for t in tracks if t['id'] == 'unique')
    assert len(linked['files']) == 2 and linked['trimmed'] and linked['only_audio_flac']
    ambiguous = next(t for t in tracks if t['location'] == str(exports / 'duplicate.ogg'))
    assert not ambiguous['prompt']
    catalog.sync_catalog(outputs, db, run=run)
    assert len(next(t for t in catalog.load_tracks(db) if t['id'] == 'unique')['files']) == 2


def test_request_snapshot_recovers_without_batch_config(tmp_path):
    run, folder, prompt = generated(tmp_path, 'batch')
    (run / 'requests.jsonl').unlink()
    save(folder / 'request.json', prompt)
    (tmp_path / 'master_database.json').write_text('{broken')
    count, warnings = catalog.sync_catalog(tmp_path)
    assert count == 1 and warnings
    assert catalog.load_tracks(tmp_path / 'track_catalog.sqlite3')[0]['prompt'] == prompt


def test_hashtags_and_missing_prompt():
    assert catalog.hashtags({'hashtags': ['#Metal', 'hard core', '#metal', '##rock']}) == '#Metal #hardcore #rock'
    assert catalog.hashtags({'hashtags':['#'+str(i) for i in range(10)]},limit=5) == '#0 #1 #2 #3 #4'
    with pytest.raises(ValueError, match='No source prompt'):
        catalog.prompt_text({'prompt': {}})


def test_filters_validate():
    with pytest.raises(ValueError):
        ui.validate_filters({'from': 'bad'})
    with pytest.raises(ValueError):
        ui.validate_filters({'from': '2026-10-01', 'to': '2026-09-01'})
    with pytest.raises(ValueError):
        ui.validate_filters({'trimmed': 'maybe'})
    assert ui.validate_filters({'trimmed': 'yes', 'batch': ''}) == {'trimmed': 'yes'}


class FakePlayer:
    state = {'idle': True, 'position': 0, 'duration': 0, 'path': '', 'paused': False, 'error': ''}
    def __init__(self):
        self.calls = []
    def play(self, path): self.calls.append(('play', path))
    def seek(self, seconds): self.calls.append(('seek', seconds))
    def pause(self): self.calls.append(('pause',))
    def stop(self): self.calls.append(('stop',))
    def close(self): pass


def wait_until(condition, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for condition')


def test_browser_search_playback_and_clipboard_independent(tmp_path, monkeypatch):
    run, folder, prompt = generated(tmp_path, 'batch')
    save(folder / 'audio_metadata.json', {'hashtags': ['#Metal', '#Rock']})
    catalog.sync_catalog(tmp_path)
    player = FakePlayer()
    browser = ui.Browser(tmp_path / 'track_catalog.sqlite3', tmp_path, player=player)
    copied = []
    monkeypatch.setattr(ui, 'copy_clipboard', lambda text: copied.append(text) or 'Copied')
    browser.handle('\n')
    browser.handle(ui.curses.KEY_RIGHT)
    browser.handle('\x10')
    assert [call[0] for call in player.calls] == ['play', 'seek', 'pause']
    browser.handle(ui.curses.KEY_F3)
    wait_until(lambda: len(copied) == 1)
    assert json.loads(copied[0]) == prompt
    browser.handle(ui.curses.KEY_F4)
    wait_until(lambda: len(copied) == 2)
    assert copied[1] == '#Metal #Rock'
    for letter in 'nothing': browser.handle(letter)
    assert not browser.rows and len(player.calls) == 3
    browser.handle('\x1b')
    assert len(browser.rows) == 1
    assert browser.handle('\x11') == 'quit'


@pytest.mark.skipif(not shutil.which('mpv'), reason='mpv unavailable')
def test_real_mpv_background_pause_seek_switch_and_shutdown(tmp_path):
    path = tmp_path / 'test.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000 * 30)
    player = Player(extra_args=('--ao=null',))
    try:
        before = time.monotonic()
        player.play(path)
        assert time.monotonic() - before < .1
        wait_until(lambda: player.state['duration'] >= 29 and not player.state['idle'])
        player.pause()
        wait_until(lambda: player.state['paused'])
        player.seek(10)
        wait_until(lambda: player.state['position'] >= 9)
        player.seek(-5)
        wait_until(lambda: 4 <= player.state['position'] < 8)
        player.play(path)
        wait_until(lambda: not player.state['paused'] and player.state['position'] < 2)
        player.stop()
        wait_until(lambda: player.state['idle'])
        assert not player.state['error']
    finally:
        player.close()
    assert not player.thread.is_alive()


def test_tracktags_updates_catalog_when_reusing_finished_record(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('tracktags_test', Path(__file__).parents[1] / 'outputs/trackTags.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run, folder, prompt = generated(tmp_path, 'batch')
    metadata = {'file_path': str(folder / 'audio.flac'), 'hashtags': ['#Metal'],
                'analysis_version': module.ANALYSIS_VERSION, 'hashtags_top5': '#Metal',
                'top_genres': [{'genre': 'Metal', 'confidence': .9}], 'description': 'Metal song',
                'description_keywords': ['Metal'], 'audio_metrics': {'bpm': 170, 'key': 'A minor', 'danceability': .8}}
    save(tmp_path / 'master_database.json', {'song': metadata})
    monkeypatch.setattr(module, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(module, 'MASTER_DB_FILE', str(tmp_path / 'master_database.json'))
    monkeypatch.setattr(module, 'download_models_if_missing', lambda: pytest.fail('Completed metadata needs no model download'))
    monkeypatch.setattr(module, 'analyze_track', lambda *args: pytest.fail('Must not reanalyze'))
    assert module.main([str(folder / 'audio.flac')]) == 0
    rows = catalog.load_tracks(tmp_path / 'track_catalog.sqlite3')
    assert rows[0]['prompt'] == prompt
    assert rows[0]['files'][0]['metadata']['hashtags'] == ['#Metal']


def test_clipboard_desktop_receives_exact_text(monkeypatch):
    import track_player
    monkeypatch.setenv('DISPLAY', ':test')
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setattr(track_player.shutil, 'which', lambda name: '/usr/bin/xclip' if name == 'xclip' else None)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs['input']))
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(track_player.subprocess, 'run', run)
    assert track_player.copy_clipboard('#metal #rock') == 'Copied to clipboard'
    assert calls == [(['xclip', '-selection', 'clipboard'], b'#metal #rock')]


@pytest.mark.parametrize('restored', [False, True])
def test_tracktags_new_or_restored_analysis_updates_catalog(tmp_path, monkeypatch, restored):
    spec = importlib.util.spec_from_file_location('tracktags_test_write', Path(__file__).parents[1] / 'outputs/trackTags.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run, folder, prompt = generated(tmp_path, 'batch')
    analysis = {'hashtags': ['#Hardcore'], 'top_genres': [{'genre': 'Hardcore', 'confidence': .9}],
                'analysis_version': module.ANALYSIS_VERSION, 'hashtags_top5': '#Hardcore',
                'description': 'Hardcore song', 'description_keywords': ['Hardcore'],
                'audio_metrics': {'bpm': 170, 'key': 'A minor', 'danceability': .8}}
    if restored:
        save(folder / 'audio_metadata.json', dict(analysis, file_path=str(folder / 'audio.flac')))
    save(tmp_path / 'genres.json', {'classes': ['Hardcore']})
    monkeypatch.setattr(module, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(module, 'MASTER_DB_FILE', str(tmp_path / 'master_database.json'))
    monkeypatch.setattr(module, 'GENRE_JSON', str(tmp_path / 'genres.json'))
    monkeypatch.setattr(module, 'download_models_if_missing', lambda: None)
    monkeypatch.setattr(module, 'analyze_track', lambda *args: analysis)
    assert module.main([str(folder / 'audio.flac')]) == 0
    row = catalog.load_tracks(tmp_path / 'track_catalog.sqlite3')[0]
    assert row['prompt'] == prompt
    assert row['files'][0]['metadata']['hashtags'] == ['#Hardcore']


def test_index_only_first_import(tmp_path):
    generated(tmp_path, 'batch')
    assert ui.main(['--outputs', str(tmp_path), '--index-only']) == 0
    assert catalog.initialized(tmp_path / 'track_catalog.sqlite3')
    assert len(catalog.load_tracks(tmp_path / 'track_catalog.sqlite3')) == 1


def test_favorites_survive_refresh_and_export_only_configs(tmp_path):
    run, folder, prompt = generated(tmp_path, 'batch')
    prompt.update(source_concept='/private/download.json', source_sha256='abc')
    (run / 'requests.jsonl').write_text(json.dumps(prompt) + '\n')
    db = tmp_path / 'track_catalog.sqlite3'
    catalog.sync_catalog(tmp_path)
    category = catalog.create_category('Shining Metal', db)
    second = catalog.create_category('Best vocals', db)
    track = catalog.load_tracks(db)[0]
    assert catalog.toggle_favorite(category, track, db)
    assert catalog.toggle_favorite(second, track, db)
    catalog.sync_catalog(tmp_path)
    tracks = catalog.load_tracks(db)
    assert tracks[0]['categories'] == ['Best vocals', 'Shining Metal']
    assert len(catalog.search(tracks, filters={'category': 'shining metal'})) == 1
    assert not catalog.search(tracks, filters={'category': 'metal'})
    destination, count = catalog.export_category(category, tmp_path / 'export.json', db)
    assert count == 1
    exported = json.loads(destination.read_text())
    expected = {k: v for k, v in prompt.items() if k not in {'source_concept', 'source_sha256'}}
    assert exported == {'songs': [expected]}
    from run_config import songs
    assert songs(destination)[0]['style'] == prompt['style']
    with pytest.raises(FileExistsError):
        catalog.export_category(category, destination, db)
    assert json.loads(destination.read_text()) == exported
    assert not list(tmp_path.glob('*.tmp'))
    assert not catalog.toggle_favorite(category, tracks[0], db)
    assert catalog.load_tracks(db)[0]['categories'] == ['Best vocals']
    with pytest.raises(ValueError, match='no tracks'):
        catalog.export_category(category, tmp_path / 'empty.json', db)
    assert not (tmp_path / 'empty.json').exists()


def test_favorite_snapshot_survives_removed_source(tmp_path):
    run, folder, prompt = generated(tmp_path, 'batch')
    db = tmp_path / 'track_catalog.sqlite3'
    catalog.sync_catalog(tmp_path)
    category = catalog.create_category('Examples', db)
    catalog.toggle_favorite(category, catalog.load_tracks(db)[0], db)
    shutil.rmtree(run)
    catalog.sync_catalog(tmp_path)
    assert catalog.load_tracks(db) == []
    assert catalog.list_categories(db)[0]['count'] == 1
    path, _ = catalog.export_category(category, tmp_path / 'examples.json', db)
    assert json.loads(path.read_text())['songs'] == [prompt]


def test_export_duplicate_ids_are_unique_without_changing_content(tmp_path):
    db = tmp_path / 'library.sqlite3'
    category = catalog.create_category('Metal', db)
    for index, name in enumerate(['song', 'song', 'song_example_2']):
        track = {'location': str(tmp_path / str(index)), 'prompt': {'id': name, 'style': f'metal {index}', 'lyrics': 'Sing'}}
        catalog.toggle_favorite(category, track, db)
    path, count = catalog.export_category(category, tmp_path / 'examples.json', db)
    from run_config import songs
    rows = songs(path)
    assert count == 3
    assert [r['id'] for r in rows] == ['song', 'song_example_3', 'song_example_2']
    assert [r['style'] for r in rows] == ['metal 0', 'metal 1', 'metal 2']


def test_categories_validation_and_bad_export(tmp_path):
    db = tmp_path / 'library.sqlite3'
    for name in ['', '  ', 'bad\nname', 'x' * 101]:
        with pytest.raises(ValueError): catalog.create_category(name, db)
    category = catalog.create_category(' Metal ', db)
    with pytest.raises(ValueError, match='already exists'):
        catalog.create_category('metal', db)
    with pytest.raises(ValueError, match='no source prompt'):
        catalog.toggle_favorite(category, {'location': 'none', 'prompt': {}}, db)
    track = {'location': 'bad', 'prompt': {'id': 'bad', 'lyrics': 'No style'}}
    catalog.toggle_favorite(category, track, db)
    with pytest.raises(ValueError, match='missing style'):
        catalog.export_category(category, tmp_path / 'bad.json', db)
    assert not (tmp_path / 'bad.json').exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_category_screen_create_add_export_browse(tmp_path):
    generated(tmp_path, 'batch')
    db = tmp_path / 'track_catalog.sqlite3'
    catalog.sync_catalog(tmp_path)
    browser = ui.Browser(db, tmp_path, player=FakePlayer())
    assert browser.handle('\x07') == 'categories'
    destination = tmp_path / 'favorites.json'
    class Screen:
        keys = iter(['n', *'Best metal', '\n', ' ', 'e', '\x15', *str(destination), '\n', '\n'])
        def get_wch(self): return next(self.keys)
        def erase(self): pass
        def refresh(self): pass
        def getmaxyx(self): return (24, 100)
        def addnstr(self, *args): pass
    ui.category_screen(Screen(), browser)
    assert browser.filters == {'category': 'Best metal'}
    assert browser.track['categories'] == ['Best metal']
    assert len(json.loads(destination.read_text())['songs']) == 1
    assert not browser.player.calls
