import importlib.util
import json
from pathlib import Path
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parents[1]))
import autoTrim
import processTracks as processing
from track_registry import atomic_json, merge_record
import track_catalog as catalog


def make_source(tmp_path, name='wolf_song'):
    run = tmp_path / 'outputs/batch'
    folder = run / name
    folder.mkdir(parents=True)
    rate = 8000
    rng = np.random.default_rng(5)
    # A main song, a long silence, then a substantial disconnected continuation.
    audio = np.zeros((rate * 43, 2))
    audio[:rate * 25] = rng.normal(0, .1, (rate * 25, 2))
    audio[rate * 33:rate * 41] = rng.normal(0, .1, (rate * 8, 2))
    source = folder / 'audio.flac'
    sf.write(source, audio, rate, subtype='PCM_24')
    prompt = {'id': name, 'title': 'Wolf Song', 'style': 'metal', 'lyrics': '[verse]\nWolf', 'seed': 1}
    (run / 'requests.jsonl').write_text(json.dumps(prompt) + '\n')
    atomic_json(folder / 'result.json', {'status': 'complete', 'audio_seconds': 43,
                                       'audio_sha256': autoTrim.sha256(source)})
    return source, tmp_path / 'outputs/master_database.json', prompt


def test_strict_master_exact_pcm_registry_idempotency_and_catalog(tmp_path, monkeypatch):
    source, db, prompt = make_source(tmp_path)
    before = autoTrim.sha256(source)
    merge_record(source, {'hashtags': ['#metal'], 'audio_metrics': {'bpm': 180}}, db)
    report = processing.process_track(source, db)
    target = source.with_name(source.parent.name + '.flac')
    assert report['output'] == str(target)
    assert report['joins'] == 0 and 24 < report['output_seconds'] < 27
    assert before == autoTrim.sha256(source)
    with sf.SoundFile(source) as original, sf.SoundFile(target) as edited:
        assert edited.subtype == original.subtype
        assert np.array_equal(edited.read(dtype='int32'), original.read(edited.frames, dtype='int32'))
    records = json.loads(db.read_text())
    master = next(r for r in records.values() if r['file_path'] == str(target))
    assert master['prompt'] == prompt and master['hashtags'] == ['#metal']
    assert 'audio_metrics' not in master  # Metrics of untrimmed original are not measurements of the crop.
    assert master['audio_workflow']['role'] == 'master'
    modified = target.stat().st_mtime_ns
    monkeypatch.setattr(autoTrim, 'features', lambda *_: pytest.fail('unchanged source must skip analysis'))
    assert processing.process_track(source, db)['processing_status'] == 'unchanged'
    assert target.stat().st_mtime_ns == modified
    catalog_db = tmp_path / 'outputs/catalog.sqlite3'
    catalog.sync_catalog(tmp_path / 'outputs', catalog_db)
    track = catalog.load_tracks(catalog_db)[0]
    assert track['files'][0]['path'] == str(target) and track['files'][0]['kind'] == 'trimmed'
    assert track['trimmed'] and track['named_flac'] and not track['only_audio_flac']
    assert track['duration'] == report['output_seconds'] and track['visualizer_audio'] == str(target)


def test_collision_and_modified_master_preserved_until_explicit_overwrite(tmp_path):
    source, db, _ = make_source(tmp_path)
    target = source.with_name(source.parent.name + '.flac')
    target.write_bytes(b'precious export')
    with pytest.raises(FileExistsError):
        processing.process_track(source, db)
    assert target.read_bytes() == b'precious export'
    assert next(iter(json.loads(db.read_text()).values()))['audio_workflow']['status'] == 'failed'
    processing.process_track(source, db, overwrite=True)
    with target.open('ab') as stream:
        stream.write(b'modification')
    damaged = target.read_bytes()
    with pytest.raises(FileExistsError):
        processing.process_track(source, db)
    assert target.read_bytes() == damaged


def test_source_receipt_integrity_and_invalid_database_preserved(tmp_path):
    source, db, _ = make_source(tmp_path)
    db.write_text('[]')
    with pytest.raises(RuntimeError, match='Expected a JSON object'):
        processing.process_track(source, db)
    assert db.read_text() == '[]'
    db.write_text('{}')
    atomic_json(source.with_name('result.json'), {'status': 'complete', 'audio_sha256': 'wrong'})
    with pytest.raises(ValueError, match='differs from generation receipt'):
        processing.process_track(source, db)


def test_changed_settings_rebuild_owned_master_and_invalidate_measured_metrics(tmp_path, monkeypatch):
    from dataclasses import replace
    source, db, _ = make_source(tmp_path)
    first = processing.process_track(source, db)
    target = Path(first['output'])
    merge_record(target, {'analysis_status': 'complete', 'analysis_audio_sha256': first['output_sha256'],
                          'audio_metrics': {'bpm': 180}, 'hashtags': ['#oldanalysis'],
                          'visualizer_workflow': {'status': 'complete', 'audio_sha256': first['output_sha256']}}, db)
    monkeypatch.setattr(processing, 'SETTINGS', replace(processing.SETTINGS, tail_padding_seconds=.8))
    second = processing.process_track(source, db)
    assert second['processing_status'] == 'processed'
    assert second['output_seconds'] > first['output_seconds']
    master = next(r for r in json.loads(db.read_text()).values() if r['file_path'] == str(target))
    assert 'audio_metrics' not in master and 'analysis_audio_sha256' not in master
    assert master['hashtags'] == ['#oldanalysis'] and master['analysis_status'] == 'inherited'
    assert master['inherited_from_previous_revision']
    assert master['analysis_source_sha256'] == first['output_sha256']
    assert master['visualizer_workflow']['status'] == 'stale'


def test_recursive_cli_only_processes_originals_and_reports_errors(tmp_path, monkeypatch):
    source, db, _ = make_source(tmp_path)
    tagged = []
    monkeypatch.setattr(processing, 'tag_processed_track', lambda target, database:
                        tagged.append(target) or {'hashtags_top5': '#Metal'})
    other = source.parent / 'subdir/audio.flac'
    other.parent.mkdir()
    other.write_bytes(b'bad audio')
    (source.parent / 'testTrim_wolf.flac').write_bytes(b'test')
    (source.parent / 'named.flac').write_bytes(b'named')
    assert processing.main([str(tmp_path / 'outputs'), '--database', str(db)]) == 1
    records = json.loads(db.read_text())
    assert len(records) == 3  # original, canonical master, failed nested original
    assert (source.parent / 'wolf_song.flac').exists()
    assert tagged == [str(source.parent / 'wolf_song.flac')]


def registry_worker(args):
    path, database = args
    merge_record(path, {'file_name': Path(path).name}, database)


def test_parallel_database_writers_preserve_every_record(tmp_path):
    db = tmp_path / 'master.json'
    jobs = [(str(tmp_path / f'{i}.flac'), str(db)) for i in range(12)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(registry_worker, jobs))
    assert len(json.loads(db.read_text())) == 12


def test_tracktags_analyzes_workflow_master_and_keeps_metadata(tmp_path, monkeypatch):
    source, db, _ = make_source(tmp_path)
    report = processing.process_track(source, db)
    target = Path(report['output'])
    spec = importlib.util.spec_from_file_location('tags_processing_test', Path(__file__).parents[1] / 'outputs/trackTags.py')
    tags = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tags)
    classes = tmp_path / 'classes.json'
    atomic_json(classes, {'classes': ['Metal']})
    monkeypatch.setattr(tags, 'MASTER_DB_FILE', str(db))
    monkeypatch.setattr(tags, 'GENRE_JSON', str(classes))
    monkeypatch.setattr(tags, 'download_models_if_missing', lambda: None)
    monkeypatch.setattr(tags, 'update_catalog', lambda _: None)
    calls = []
    def analyze(path, classes):
        calls.append(path)
        # Simulate a second writer adding data while inference runs.
        merge_record(target, {'visualizer_note': 'preserved'}, db)
        return {'top_genres': [{'genre': 'Metal', 'confidence': .9}], 'hashtags': ['#Metal'],
                'description': 'Fast', 'description_keywords': ['fast'],
                'audio_metrics': {'bpm': 180, 'key': 'A minor', 'danceability': 1.1}}
    monkeypatch.setattr(tags, 'analyze_track', analyze)
    assert tags.main([str(target)]) == 0
    assert tags.main([str(target)]) == 0
    assert calls == [str(target)]
    record = next(r for r in json.loads(db.read_text()).values() if r['file_path'] == str(target))
    assert record['audio_workflow']['method'] == 'truncate'
    assert record['visualizer_note'] == 'preserved' and record['prompt']['seed'] == 1
    assert record['analysis_audio_sha256'] == report['output_sha256']
    assert record['analysis_version'] == tags.ANALYSIS_VERSION
    assert record['hashtags_top5'] == '#Metal'


def test_archive_tags_trimmed_master_before_visualizer_and_retries_failure(tmp_path, monkeypatch):
    import archive_run as archive
    import visualizer_queue
    monkeypatch.setattr(archive, 'LOCAL', tmp_path)
    events = []
    master = tmp_path/'song/song.flac'
    def trim(source):
        events.append(('trim', source))
        return {'processing_status': 'unchanged', 'output': str(master), 'output_seconds': 10}
    def tag(target):
        events.append(('tag', target))
        if sum(e[0] == 'tag' for e in events) == 1:
            raise RuntimeError('temporary tagging failure')
        return {'status': 'complete', 'hashtags_top5': '#Metal'}
    def visualize(target, prompt):
        events.append(('visualize', target))
        return {'status': 'queued'}
    monkeypatch.setattr(processing, 'process_track', trim)
    monkeypatch.setattr(processing, 'tag_processed_track', tag)
    monkeypatch.setattr(visualizer_queue, 'enqueue_track', visualize)
    first = archive.postprocess_tracks([{'id':'song'}])[0]
    second = archive.postprocess_tracks([{'id':'song'}])[0]
    assert first['tagging']['status'] == 'failed' and first['visualizer']['status'] == 'queued'
    assert second['tagging']['status'] == 'complete'
    assert [e[0] for e in events] == ['trim','tag','visualize']*2
    assert all(path == str(master) for action,path in events if action != 'trim')


def test_top_ten_ranking_and_legacy_analysis_upgrade(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('tags_upgrade_test', Path(__file__).parents[1]/'outputs/trackTags.py')
    tags = importlib.util.module_from_spec(spec); spec.loader.exec_module(tags)
    classes = ['Rock---Style'+str(i) for i in range(12)]
    genres, hashtags = tags.ranked_genres(np.arange(12)/12, classes)
    assert len(genres) == len(hashtags) == 10
    assert hashtags == ['#Style'+str(i) for i in range(11,1,-1)]
    audio = tmp_path/'legacy.flac'; audio.write_bytes(b'audio')
    db = tmp_path/'custom_database.json'
    old = {'top_genres': genres[:5], 'hashtags':hashtags[:5], 'description':'Legacy',
           'description_keywords':['metal'], 'audio_metrics':{'bpm':180,'key':'A minor','danceability':1}}
    merge_record(audio, old, db)
    assert not tags.is_finished(old) and tags.is_finished(old, current_version=False)
    classes_path = tmp_path/'classes.json'; atomic_json(classes_path, {'classes':classes})
    monkeypatch.setattr(tags, 'GENRE_JSON', str(classes_path))
    monkeypatch.setattr(tags, 'download_models_if_missing', lambda: None)
    monkeypatch.setattr(tags, 'update_catalog', lambda _: None)
    calls = []
    def analyze(*args):
        calls.append(args[0])
        return {**old, 'top_genres':genres, 'hashtags':hashtags}
    monkeypatch.setattr(tags, 'analyze_track', analyze)
    assert tags.main([str(audio),'--database',str(db)]) == 0
    assert tags.main([str(audio),'--database',str(db)]) == 0
    assert calls == [str(audio)]
    result = next(iter(json.loads(db.read_text()).values()))
    assert result['hashtags_top5'] == ' '.join(hashtags[:5]) and len(result['top_genres']) == 10


def test_tagging_helper_retries_and_skips_verified_complete_master(tmp_path, monkeypatch):
    source, db, _ = make_source(tmp_path)
    report = processing.process_track(source, db); target = Path(report['output'])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise RuntimeError('missing model')
        merge_record(target, {'analysis_version':2,'analysis_status':'complete',
            'analysis_audio_sha256':report['output_sha256'], 'top_genres':[{'genre':'Metal','confidence':.9}],
            'hashtags':['#Metal'], 'hashtags_top5':'#Metal','description':'Metal',
            'description_keywords':['metal'],'audio_metrics':{'bpm':180,'key':'A minor','danceability':1}}, db)
        assert kwargs['env']['CUDA_VISIBLE_DEVICES'] == '-1'
    monkeypatch.setattr(processing.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='missing model'):
        processing.tag_processed_track(target, db)
    failed = next(r for r in json.loads(db.read_text()).values() if r['file_path'] == str(target))
    assert failed['tagging_workflow']['status'] == 'failed'
    assert processing.tag_processed_track(target,db)['cached'] is False
    assert processing.tag_processed_track(target,db)['cached'] is True
    assert len(calls) == 2
    assert '--database' in calls[0] and str(target) in calls[0]


def test_archive_postprocessing_failures_are_retryable(tmp_path, monkeypatch):
    import archive_run as archive
    monkeypatch.setattr(archive, 'LOCAL', tmp_path)
    def fail(*_, **__):
        raise ValueError('retry me')
    monkeypatch.setattr(processing, 'process_track', fail)
    result = archive.postprocess_tracks([{'id': 'song'}])
    assert result == [{'id': 'song', 'status': 'failed', 'error': 'retry me'}]
    with pytest.raises(ValueError, match='Unsafe song ID'):
        archive.postprocess_tracks([{'id': '../escape'}])
