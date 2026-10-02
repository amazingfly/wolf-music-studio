import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('studio_under_test',ROOT/'studio.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)


def isolated(monkeypatch,tmp_path):
    monkeypatch.setattr(studio,'ROOT',tmp_path)
    monkeypatch.setattr(studio,'CONFIG',tmp_path/'local/settings.json')
    return studio.settings()


def test_init_preserves_existing_settings_and_does_not_create_jobs(monkeypatch,tmp_path):
    config=isolated(monkeypatch,tmp_path);studio.initialize()
    config['campaign']='my_second_version';studio.CONFIG.write_text(json.dumps(config));studio.initialize()
    assert studio.settings()['campaign']=='my_second_version'
    assert (tmp_path/'yue2/queue/pending').is_dir()
    assert not list((tmp_path/'yue2/queue/pending').iterdir())


def test_environment_resolves_spaces_and_separates_interpreters(monkeypatch,tmp_path):
    config=isolated(monkeypatch,tmp_path)
    config.update(visualizer_root='visualizer with spaces',gemma_root='gemma models',drive_base='customDrive:music/')
    env=studio.environment(config)
    assert env['YUE2_VIS_ROOT']==str(tmp_path/'visualizer with spaces')
    assert env['YUE2_GEMMA_ROOT']==str(tmp_path/'gemma models')
    assert env['YUE2_VIS_PYTHON']!=env['YUE2_COLAB_PYTHON']
    assert env['YUE2_DRIVE_BASE']=='customDrive:music/'
    assert env['YUE2_GEMMA_CONTEXT']=='12288'


def test_service_unit_quotes_checkout_and_never_refers_to_original_machine(monkeypatch,tmp_path):
    config=isolated(monkeypatch,tmp_path/'studio with spaces')
    text=studio.unit_text('visualizer',config)
    assert f'WorkingDirectory={tmp_path}/studio with spaces' in text
    assert 'studio.py" "_worker" "visualizer"' in text
    assert '/home/derek' not in text and '/mnt/storage/projects' not in text
    with pytest.raises(ValueError):studio.unit_quote('bad\npath')
    assert '%%' in studio.unit_quote('with%specifier')


def test_venv_interpreter_symlink_is_not_resolved_to_base_python(monkeypatch,tmp_path):
    isolated(monkeypatch,tmp_path)
    interpreter=tmp_path/'.venv/bin/python';interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(Path(sys.executable).resolve())
    assert studio.path('.venv/bin/python')==interpreter
    # Base Python is a forbidden installation target, even if addressed through a symlink.
    base=tmp_path/'base-python';base.symlink_to(Path(sys._base_executable).resolve())
    with pytest.raises(ValueError,match='isolated virtualenv'):
        studio.pip_install(base,[])


def test_settings_reject_unknown_keys_and_unsafe_campaign_names(monkeypatch,tmp_path):
    config=isolated(monkeypatch,tmp_path);studio.CONFIG.parent.mkdir()
    studio.CONFIG.write_text(json.dumps({'unknown':True}))
    with pytest.raises(ValueError,match='Unknown'):studio.settings()
    studio.CONFIG.write_text(json.dumps({'campaign':'../outside'}))
    with pytest.raises(ValueError,match='Campaign'):studio.settings()
    studio.CONFIG.write_text(json.dumps({'gemma_context':'4000'}))
    with pytest.raises(ValueError,match='context'):studio.settings()


def test_controller_install_and_cli_dispatch_use_isolated_python(monkeypatch,tmp_path):
    config=isolated(monkeypatch,tmp_path);calls=[]
    def execute(arguments,**kwargs):calls.append(arguments)
    monkeypatch.setattr(studio,'execute',execute)
    class Args:profile='controller';dev=True;tags=False;engines=False;models=False
    # Simulate an existing environment; the mocked pip installer records the actual target.
    (tmp_path/'.venv').mkdir();(tmp_path/'.venv/pyvenv.cfg').write_text('home=/usr/bin')
    monkeypatch.setattr(studio,'pip_install',lambda python,args:calls.append([python,*args]))
    studio.setup(Args())
    assert calls[0][0]==tmp_path/'.venv/bin/python'
    assert calls[0][-1]==tmp_path/'requirements-dev.txt'


def test_visualizer_dev_profile_installs_its_own_test_runner(monkeypatch,tmp_path):
    isolated(monkeypatch,tmp_path)
    calls = []
    monkeypatch.setattr(studio, 'execute', lambda *args, **kwargs: None)
    monkeypatch.setattr(studio, 'pip_install', lambda python, args: calls.append((python, args)))
    class Args:profile='visualizer';dev=True;tags=False;engines=False;models=False
    studio.setup(Args())
    assert calls[-1] == (tmp_path/'visualizer/.venv/bin/python', ['pytest==9.0.3'])


def test_remote_run_kit_keeps_selected_drive_path(tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT/'yue2'))
    import run_config
    monkeypatch.setattr(run_config,'ROOT',tmp_path)
    monkeypatch.setattr(run_config,'DRIVE_BASE','anotherRemote,root_folder_id=example:music/')
    config=tmp_path/'song.json';config.write_text(json.dumps({'id':'wolf','style':'metal','lyrics':'Original words'}))
    manifest=run_config.create_run(config)
    assert json.loads(manifest.read_text())['drive_base']==run_config.DRIVE_BASE


def test_publication_scanner_blocks_artifacts_and_tokens_without_echoing_secret(tmp_path):
    spec=importlib.util.spec_from_file_location('publish',ROOT/'scripts/check_publish.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'credential.txt';path.write_text('gh'+'p_'+'a'*30)
    assert 'GitHub token' in module.inspect(path,Path('credential.txt'))
    path.write_text('no credential')
    assert module.inspect(path,Path('yue2/outputs/state.json'))
    assert not module.inspect(path,Path('yue2/outputs/trackTags.py'))
