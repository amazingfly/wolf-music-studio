"""Resume partial Drive restores without a GPU or song model."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[1]/'src'))
import run_batch
import yue2


class FakePipeline:
    weights = {'test':'weights'}
    def __init__(self): self.decoded = 0
    def _request(self, **kwargs): return kwargs
    def effective_config(self, req): return {'test':'configuration'}
    def plan(self, **kwargs): pytest.fail('Saved plan must be reused')
    def generate_semantic(self, *args, **kwargs): pytest.fail('Saved semantics must be reused')
    def synthesize(self, *args): pytest.fail('Saved latents must be reused')
    def decode(self, latents):
        self.decoded += 1
        assert np.all(latents == .25)
        return np.tile((.1*np.sin(np.arange(48000)/10))[:,None], (1,2))
    def close(self): pass


def request():
    return {'id':'wolf','style':'metal','lyrics':'Wolf','cot':False,'seed':17,
            'duration_validation':{'min_seconds':.5,'max_seconds':2}}


def test_missing_completed_audio_redecodes_existing_latents_and_preserves_receipt(tmp_path, monkeypatch):
    pipe = FakePipeline(); req = request()
    previous = {'status':'complete','id':'wolf','audio_sha256':'old-audio'}
    run_batch.atomic(tmp_path/'result.json',previous)
    plan = tmp_path/'plan'; plan.mkdir(); (plan/'plan_manifest.json').write_text('{}')
    np.save(tmp_path/'semantic.npy',np.array([1,2,3],dtype=np.int32))
    np.save(tmp_path/'latent.npy',np.array([.25],dtype=np.float32))
    before = (tmp_path/'latent.npy').stat().st_mtime_ns
    monkeypatch.setitem(yue2.__dict__,'SymbolicPlan',SimpleNamespace(load=lambda _:SimpleNamespace(truncated=False)))
    monkeypatch.setitem(yue2.__dict__,'SemanticResult',lambda plan,tokens,*args:SimpleNamespace(tokens=tokens,truncated=False))
    monkeypatch.setattr(run_batch,'memory',lambda *_:None)
    result = run_batch.run_one(pipe,req,tmp_path)
    assert result['status'] == 'complete' and pipe.decoded == 1
    assert (tmp_path/'latent.npy').stat().st_mtime_ns == before
    saved = json.loads((tmp_path/'result.json').read_text())
    assert saved['audio_sha256'] == run_batch.sha256_file(tmp_path/'audio.flac')
    assert json.loads((tmp_path/'result.recovery.json').read_text())['previous_result'] == previous
    assert run_batch.run_one(pipe,req,tmp_path)['status'] == 'already_complete'
    assert pipe.decoded == 1


def test_existing_corrupt_completed_audio_is_preserved(tmp_path):
    audio = tmp_path/'audio.flac'; audio.write_bytes(b'damaged audio')
    receipt = {'status':'complete','audio_sha256':'different'}
    run_batch.atomic(tmp_path/'result.json',receipt)
    with pytest.raises(ValueError,match='checksum mismatch'):
        run_batch.run_one(FakePipeline(),request(),tmp_path)
    assert audio.read_bytes() == b'damaged audio'
    assert json.loads((tmp_path/'result.json').read_text()) == receipt
