import importlib.util
import hashlib
from pathlib import Path

spec=importlib.util.spec_from_file_location('partial_archive',Path(__file__).parents[1]/'archive_run.py')
archive=importlib.util.module_from_spec(spec); spec.loader.exec_module(archive)


def test_download_completed_only_and_skip_verified_copies(tmp_path,monkeypatch):
    monkeypatch.setattr(archive,'LOCAL',tmp_path)
    processed=[]
    def postprocess(rows):
        processed.extend(row['id'] for row in rows)
        return [{'id': row['id'], 'status': 'processed'} for row in rows]
    monkeypatch.setattr(archive,'postprocess_tracks',postprocess)
    monkeypatch.setattr(archive,'expected',lambda:[{'id':'done'},{'id':'unfinished'}])
    contents={'audio.flac':b'audio','result.json':b'{}'}
    records=[{'Path':'done/'+name,'Size':len(data),'Hashes':{'md5':hashlib.md5(data).hexdigest()}}
             for name,data in contents.items()]
    monkeypatch.setattr(archive,'inventory',lambda:records)
    def verify(rows=None):
        assert rows==[{'id':'done'}]
        if (tmp_path/'done/audio.flac').read_bytes()!=b'audio': raise ValueError('bad checksum')
        return [{'id':'done','audio_sha256':'verified'}]
    monkeypatch.setattr(archive,'verify_audio',verify)
    calls=[]
    def command(*args):
        calls.append(args)
        assert args[0]=='copy'
        for name,data in contents.items(): (tmp_path/'done'/name).write_bytes(data)
    monkeypatch.setattr(archive,'command',command)
    assert len(archive.download_completed())==1
    assert len(calls)==1
    assert not (tmp_path/'unfinished').exists()
    assert len(archive.download_completed())==1
    assert len(calls)==1
    (tmp_path/'done/audio.flac').write_bytes(b'corrupt')
    assert len(archive.download_completed())==1
    assert len(calls)==2
    assert processed==['done','done','done']
