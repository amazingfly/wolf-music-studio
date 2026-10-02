import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import queue_supervisor as queue
import run_config


def setup_queue(tmp_path):
    for name in ('pending', 'running', 'completed', 'failed'):
        (tmp_path/name).mkdir()
    return tmp_path


def add_config(root, name='songs.json'):
    path=root/'pending'/name
    path.write_text(json.dumps({'style':'industrial', 'lyrics':'[Verse]\nA story'}))
    os.utime(path, (1, 1))
    return path


def test_claim_restart_and_reserved_filename(tmp_path, monkeypatch):
    root=setup_queue(tmp_path)
    monkeypatch.setattr(run_config, 'ROOT', root)
    source=add_config(root, 'job.json')
    job=queue.next_job(root)
    assert not source.exists()
    info=queue.prepare_job(job)
    assert Path(info['manifest']).parent.name.endswith('_job')
    assert queue.next_job(root)==job
    assert queue.prepare_job(job)==info
    assert len(list((root/'outputs').iterdir()))==1


def test_fresh_copy_waits(tmp_path):
    root=setup_queue(tmp_path)
    path=add_config(root)
    os.utime(path, None)
    assert queue.next_job(root) is None


class StopTest(BaseException):
    pass


def test_two_configs_reuse_session_then_release(tmp_path, monkeypatch):
    root=setup_queue(tmp_path)
    monkeypatch.setattr(run_config, 'ROOT', root)
    add_config(root, 'first.json')
    add_config(root, 'second.json')
    monkeypatch.setattr(sys, 'argv', ['queue', '--directory', str(root)])
    events=[]
    monkeypatch.setattr(queue, 'run_job', lambda manifest,session,gpu: events.append(('run', session)) or 0)
    monkeypatch.setattr(queue, 'release', lambda session: events.append(('stop', session)))
    monkeypatch.setattr(queue.time, 'sleep', lambda seconds: (_ for _ in ()).throw(StopTest()))
    with pytest.raises(StopTest):
        queue.main()
    assert events==[('run','yue2-directory-queue')]*2+[('stop','yue2-directory-queue')]
    assert len(list((root/'done').iterdir()))==2
    assert json.loads((root/'state.json').read_text())['gpu_released']


def test_invalid_config_does_not_allocate(tmp_path, monkeypatch):
    root=setup_queue(tmp_path)
    path=add_config(root)
    path.write_text('{}')
    os.utime(path, (1,1))
    monkeypatch.setattr(sys, 'argv', ['queue', '--directory', str(root)])
    monkeypatch.setattr(queue, 'run_job', lambda *args: pytest.fail('Invalid config allocated GPU'))
    monkeypatch.setattr(queue, 'release', lambda session: None)
    monkeypatch.setattr(queue.time, 'sleep', lambda seconds: (_ for _ in ()).throw(StopTest()))
    with pytest.raises(StopTest):
        queue.main()
    assert len(list((root/'failed').iterdir()))==1


def test_release_failure_is_not_ignored(monkeypatch):
    monkeypatch.setattr(queue.subprocess, 'run', lambda *a,**k: SimpleNamespace(returncode=1,stdout='',stderr='network error'))
    with pytest.raises(RuntimeError):
        queue.release('test')


def test_interruption_retries_same_manifest_before_next_config(tmp_path, monkeypatch):
    root=setup_queue(tmp_path)
    monkeypatch.setattr(run_config,'ROOT',root)
    add_config(root,'first.json')
    add_config(root,'second.json')
    monkeypatch.setattr(sys,'argv',['queue','--directory',str(root)])
    calls=[]
    def run(manifest,*args):
        calls.append(manifest)
        return 247 if len(calls)==1 else 0
    monkeypatch.setattr(queue,'run_job',run)
    monkeypatch.setattr(queue,'release',lambda session: None)
    def sleep(seconds):
        if seconds==10: raise StopTest()
    monkeypatch.setattr(queue.time,'sleep',sleep)
    with pytest.raises(StopTest): queue.main()
    assert len(calls)==3
    assert calls[0]==calls[1]
    assert calls[2]!=calls[1]
    assert len(list((root/'done').iterdir()))==2
    assert not list((root/'failed').iterdir())


def test_recovered_jobs_use_original_order(tmp_path):
    root=setup_queue(tmp_path)
    for name,started in [('aaa',20),('zzz',10)]:
        job=root/'running'/name
        job.mkdir()
        (job/'job.json').write_text(json.dumps({'started_at':started}))
    assert queue.next_job(root).name=='zzz'
