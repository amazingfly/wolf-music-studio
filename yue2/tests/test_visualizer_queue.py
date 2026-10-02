import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import archive_run
import processTracks
import visualizer_queue as queue
from track_registry import atomic_json, merge_record


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'update_registry', lambda *_args, **_kw: None)
    audio = tmp_path / 'outputs/batch/wolf/wolf.flac'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'verified master')
    atomic_json(audio.with_name('wolf_metadata.json'), {'audio_workflow': {
        'role': 'master', 'status': 'complete', 'output_path': str(audio),
        'output_sha256': queue.digest(audio), 'original_path': str(audio.with_name('audio.flac'))}})
    vis = tmp_path / 'vis'
    vis.mkdir()
    (vis / 'vis2GPUV7.py').write_text('# version 7')
    atomic_json(vis / 'config.json', {'video': {'fps': 30}})
    return audio, vis, tmp_path / 'queue', tmp_path / 'master.json'


def test_enqueue_snapshot_idempotency_and_changed_input_new_job(tmp_path, monkeypatch):
    audio, vis, spool, db = fixture(tmp_path, monkeypatch)
    prompt = {'id': 'wolf', 'lyrics': '[verse]\nOriginal words', 'seed': 16}
    result = queue.enqueue_track(audio, prompt, db, spool, vis)
    folder = Path(result['job_path'])
    assert json.loads((folder / 'prompt.json').read_text()) == prompt
    assert json.loads((folder / 'config.json').read_text())['video']['fps'] == 30
    assert queue.enqueue_track(audio, prompt, db, spool, vis)['job_id'] == result['job_id']
    assert len(list((spool / 'jobs').iterdir())) == 1
    prompt['lyrics'] = 'Different words'
    other = queue.enqueue_track(audio, prompt, db, spool, vis)
    assert other['job_id'] != result['job_id']
    assert queue.next_job(spool)[0]['id'] == result['job_id']
    audio.write_bytes(b'modified file')
    with pytest.raises(ValueError, match='Master audio has changed'):
        queue.enqueue_track(audio, prompt, db, spool, vis)


def test_failure_retry_limit_and_restart_resume(tmp_path, monkeypatch):
    audio, vis, spool, db = fixture(tmp_path, monkeypatch)
    queue.enqueue_track(audio, {'id': 'wolf', 'lyrics': 'Sing'}, db, spool, vis)
    job, state = queue.next_job(spool)
    class FailedProcess:
        def __init__(self, *_args, **_kwargs):
            pass
        def wait(self):
            return 1
    monkeypatch.setattr(queue.subprocess, 'Popen', FailedProcess)
    first = queue.run_job(job, state)
    assert first['status'] == 'retrying' and first['attempts'] == 1
    assert queue.next_job(spool) == (None, None)
    final = queue.run_job(job, {'status': 'running', 'attempts': 2})
    assert final['status'] == 'failed' and final['attempts'] == 3
    assert queue.next_job(spool) == (None, None)
    assert queue.main(['--queue', str(spool), '--retry-failed']) == 0
    assert queue.next_job(spool)[1]['attempts'] == 0


def test_complete_receipt_published_and_missing_video_requeued(tmp_path, monkeypatch):
    audio, vis, spool, db = fixture(tmp_path, monkeypatch)
    prompt = {'id': 'wolf', 'lyrics': 'Sing'}
    queue.enqueue_track(audio, prompt, db, spool, vis)
    job, state = queue.next_job(spool)
    output = Path(job['output_dir'])
    output.mkdir(parents=True)
    words = output / 'words.json'
    atomic_json(words, {'words': []})
    videos = {}
    for name in ['widescreen', 'portrait']:
        path = output / (name + '.mp4')
        path.write_bytes(b'video')
        stat = path.stat()
        videos[name] = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    atomic_json(output / 'render_receipt.json', {
        'status': 'complete', 'provenance': {'audio_sha256': queue.digest(audio), 'words_sha256': queue.digest(words)},
        'videos': videos, 'words_file': str(words), 'word_count': 2, 'review_count': 1, 'issues': []})
    class CompleteProcess:
        def __init__(self, command, **kwargs):
            assert command[1] == str(vis / 'vis2GPUV8.py')
            assert command[command.index('--lyrics') + 1] == str(Path(job['job_path']) / 'prompt.json')
            assert kwargs['start_new_session']
        def wait(self):
            return 0
    monkeypatch.setattr(queue.subprocess, 'Popen', CompleteProcess)
    result = queue.run_job(job, state)
    assert result['status'] == 'complete' and result['review_count'] == 1
    assert queue.enqueue_track(audio, prompt, db, spool, vis)['status'] == 'complete'
    Path(videos['portrait']['path']).unlink()
    assert queue.enqueue_track(audio, prompt, db, spool, vis)['status'] == 'queued'


def test_changed_source_supersedes_job_without_launch(tmp_path, monkeypatch):
    audio, vis, spool, db = fixture(tmp_path, monkeypatch)
    queue.enqueue_track(audio, {'id': 'wolf', 'lyrics': 'Sing'}, db, spool, vis)
    job, state = queue.next_job(spool)
    audio.write_bytes(b'new source')
    monkeypatch.setattr(queue.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('stale job must not launch'))
    assert queue.run_job(job, state)['status'] == 'superseded'


def test_registry_updates_preserve_tags_and_reject_stale_worker(tmp_path, monkeypatch):
    original = tmp_path / 'batch/wolf/audio.flac'
    original.parent.mkdir(parents=True)
    audio = original.with_name('wolf.flac')
    db = tmp_path / 'master.json'
    for path in (audio, original):
        merge_record(path, {'audio_workflow': {'output_sha256': 'abc'}, 'hashtags': ['#metal'], 'prompt': {'lyrics': 'Keep'}}, db)
    job = {'id': 'new', 'audio': str(audio), 'original': str(original), 'audio_sha256': 'abc',
           'database': str(db), 'job_path': str(tmp_path), 'output_dir': str(tmp_path / 'video')}
    queue.update_registry(job, {'status': 'queued'}, enqueue=True)
    queue.update_registry({**job, 'id': 'old'}, {'status': 'complete'})
    for record in json.loads(db.read_text()).values():
        assert record['visualizer_workflow']['job_id'] == 'new'
        assert record['visualizer_workflow']['status'] == 'queued'
        assert record['hashtags'] == ['#metal'] and record['prompt']['lyrics'] == 'Keep'


def test_download_hook_enqueues_exact_request_and_keeps_trim_success_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_run, 'LOCAL', tmp_path)
    monkeypatch.setattr(processTracks, 'process_track', lambda _: {
        'processing_status': 'unchanged', 'output': 'wolf.flac', 'output_seconds': 250})
    request = {'id': 'wolf', 'lyrics': 'Exact words', 'seed': 16}
    calls = []
    monkeypatch.setattr(queue, 'enqueue_track', lambda path, prompt: calls.append((path, prompt)) or {'status': 'queued'})
    result = archive_run.postprocess_tracks([request])[0]
    assert calls == [('wolf.flac', request)] and result['visualizer']['status'] == 'queued'
    def fail(*_a, **_k):
        raise ValueError('queue unavailable')
    monkeypatch.setattr(queue, 'enqueue_track', fail)
    result = archive_run.postprocess_tracks([request])[0]
    assert result['status'] == 'unchanged' and result['visualizer']['status'] == 'failed'
