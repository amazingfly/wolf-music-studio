import json
import re
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import run_config
import archive_run


@pytest.fixture
def config(tmp_path,monkeypatch):
    monkeypatch.setattr(run_config,'ROOT',tmp_path)
    path=tmp_path/'industrial.json'
    path.write_text(json.dumps({'songs':[{'id':'one','style':'industrial, female vocals','lyrics':'[Verse]\nTest lyrics'}]}))
    return path


def test_json_name_timestamp_and_shared_archive_paths(config,monkeypatch):
    manifest=run_config.create_run(config)
    context=run_config.load_run(manifest)
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}_industrial',manifest.parent.name)
    for key in ('REMOTE','LOCAL','REQUESTS'):
        monkeypatch.setattr(archive_run,key,getattr(archive_run,key))
    archive_run.configure(manifest)
    assert archive_run.LOCAL==manifest.parent
    assert archive_run.REMOTE==run_config.DRIVE_BASE+manifest.parent.name
    assert archive_run.expected()[0]['seed']==85300
    assert archive_run.expected()[0]['target_seconds']==360
    assert run_config.load_run(manifest)==context
    second=run_config.create_run(config)
    assert second.parent!=manifest.parent


def test_original_edits_do_not_change_saved_run(config):
    manifest=run_config.create_run(config)
    original=run_config.load_run(manifest)
    config.write_text('{"songs":[]}')
    assert run_config.load_run(manifest)==original
    (manifest.parent/'requests.jsonl').write_text('{}\n')
    with pytest.raises(ValueError,match='changed'): run_config.load_run(manifest)


@pytest.mark.parametrize('payload',[
    [], {'songs':[]}, {'id':'../escape','style':'x','lyrics':'x'},
    {'style':'x','lyrics':'x','target_seconds':90},
    {'style':'x','lyrics':'x','seed':True},
    {'style':'x','lyrics':'x','ignored_setting':1},
    [{'id':'same','style':'x','lyrics':'x'},{'id':'same','style':'y','lyrics':'y'}],
])
def test_bad_configs_do_not_create_run(config,payload):
    config.write_text(json.dumps(payload))
    with pytest.raises(ValueError): run_config.create_run(config)
    assert not (config.parent/'outputs').exists()


def test_single_song_and_array_and_legacy_jsonl(config):
    song={'id':'single','style':'hard techno','lyrics':'Test','seed':7}
    for payload in (song,[song],{'songs':[song]},{'generations':[song]}):
        config.write_text(json.dumps(payload))
        assert run_config.songs(config)[0]['seed']==7
    legacy=config.with_suffix('.jsonl'); legacy.write_text(json.dumps(song)+'\n')
    assert run_config.songs(legacy)[0]['id']=='single'


def test_colab_package_uses_selected_frozen_config(config,monkeypatch,tmp_path):
    import tarfile
    import colab_supervisor
    manifest=run_config.create_run(config)
    monkeypatch.setattr(colab_supervisor,'MANIFEST',manifest)
    monkeypatch.setattr(archive_run,'REQUESTS',manifest.parent/'requests.jsonl')
    package=tmp_path/'kit.tar.gz'
    colab_supervisor.pack_run(package)
    with tarfile.open(package) as bundle:
        assert bundle.extractfile('requests/batch.jsonl').read()==archive_run.REQUESTS.read_bytes()
        context=json.load(bundle.extractfile('run_context.json'))
        assert context['run_name']==manifest.parent.name
        assert 'rclone_snapshot.py' in bundle.getnames()
        assert not any('__pycache__' in name for name in bundle.getnames())
