import importlib.util
import hashlib
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('archive_run_test', Path(__file__).parents[1] / 'archive_run.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


@pytest.fixture
def completed(tmp_path, monkeypatch):
    local=tmp_path/'outputs'; (local/'song').mkdir(parents=True)
    requests=tmp_path/'requests.jsonl'; requests.write_text('{"id":"song"}\n')
    monkeypatch.setattr(archive,'LOCAL',local)
    monkeypatch.setattr(archive,'REQUESTS',requests)
    monkeypatch.setattr(archive,'postprocess_tracks',lambda requests=None: [])
    monkeypatch.setattr(archive,'verify_audio',lambda: [{'id':'song'}])
    records=[]
    for name,content in [('audio.flac',b'verified-audio'),('result.json',b'{}')]:
        (local/'song'/name).write_bytes(content)
        records.append({'ID':name,'Path':f'song/{name}','Size':len(content),
                        'Hashes':{'md5':hashlib.md5(content).hexdigest()}})
    return local,records


def test_bad_download_never_deletes(completed,monkeypatch):
    local,records=completed
    (local/'song/audio.flac').write_bytes(b'corrupt-content')
    monkeypatch.setattr(archive,'inventory',lambda:records)
    calls=[]; monkeypatch.setattr(archive,'command',lambda *args:calls.append(args))
    with pytest.raises(ValueError): archive.archive_if_complete()
    assert all(args[0]!='purge' for args in calls)


def test_verified_archive_is_idempotent(completed,monkeypatch):
    local,records=completed
    remote=list(records); calls=[]
    monkeypatch.setattr(archive,'inventory',lambda:list(remote))
    def command(*args):
        calls.append(args)
        if args[0]=='purge': remote.clear()
    monkeypatch.setattr(archive,'command',command)
    assert archive.archive_if_complete()
    assert [c[0] for c in calls]==['copy','purge']
    assert archive.archive_if_complete()
    assert [c[0] for c in calls]==['copy','purge']
    assert json.loads((local/'local_completion.json').read_text())['drive_deleted']


def test_interrupted_deletion_finishes_without_redownload(completed,monkeypatch):
    local,records=completed
    (local/'local_completion.json').write_text(json.dumps({
        'requests_sha256':archive.digest(archive.REQUESTS),'remote_inventory':records,'drive_deleted':False}))
    monkeypatch.setattr(archive,'inventory',lambda:[])
    calls=[]; monkeypatch.setattr(archive,'command',lambda *args:calls.append(args))
    assert archive.archive_if_complete()
    assert calls==[]


def test_conflicting_duplicate_is_not_deleted(completed):
    _,records=completed
    duplicate=dict(records[0],ID='duplicate',Hashes={'md5':'invalid'})
    with pytest.raises(ValueError): archive.verify_inventory([*records,duplicate])
