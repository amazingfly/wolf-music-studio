import importlib.util
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('snapshot_upload', Path(__file__).parents[1] / 'rclone_snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_atomic_checkpoint_replacement_during_copy(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    target = tmp_path / 'snapshot'; target.mkdir()
    checkpoint = source / 'semantic-progress.npy'
    checkpoint.write_bytes(b'complete-old-version')
    (source / 'partial.tmp').write_bytes(b'incomplete')
    copy = module.shutil.copyfileobj

    def replace_while_copying(reader, writer, length):
        staged = source / 'next.tmp'
        staged.write_bytes(b'complete-new-version-with-different-length')
        staged.replace(checkpoint)
        return copy(reader, writer, length)

    with patch.object(module.shutil, 'copyfileobj', side_effect=replace_while_copying):
        assert module.snapshot(source, target) == 1
    assert (target / checkpoint.name).read_bytes() == b'complete-old-version'
    assert checkpoint.read_bytes() == b'complete-new-version-with-different-length'
    assert not (target / 'partial.tmp').exists()
    checkpoint.write_bytes(b'another-update')
    assert (target / checkpoint.name).read_bytes() == b'complete-old-version'


def test_incremental_snapshot_skips_uploaded_audio_but_copies_new_progress(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    first=tmp_path/'first'; first.mkdir()
    second=tmp_path/'second'; second.mkdir()
    (source/'audio.flac').write_bytes(b'finished-song')
    (source/'semantic-progress.npy').write_bytes(b'progress')
    assert module.snapshot(source,first)==2
    previous={p.name:[p.stat().st_size,p.stat().st_mtime_ns] for p in first.iterdir()}
    (source/'next.tmp').write_bytes(b'new-longer-progress')
    (source/'next.tmp').replace(source/'semantic-progress.npy')
    assert module.snapshot(source,second,previous)==1
    assert not (second/'audio.flac').exists()
    assert (second/'semantic-progress.npy').read_bytes()==b'new-longer-progress'
