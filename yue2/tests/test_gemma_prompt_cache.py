import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
import gemma_prompt_cache as cache


def payload(concept='jewelry'):
    return {'messages':[{'role':'system','content':'immutable system'},
        {'role':'user','content':'example one'},{'role':'assistant','content':'reference JSON'},
        {'role':'user','content':cache.BRIEF_HEADER+json.dumps({'concept':concept})}],
        'stream':True,'max_tokens':3500,'seed':17,'cache_prompt':True,'id_slot':0,
        'chat_template_kwargs':{'enable_thinking':False}}


def mock_server(monkeypatch):
    calls=[]
    def post(_url,route,value,timeout=1800):
        calls.append((route,value))
        assert route=='/v1/chat/completions'  # Never discard SWA checkpoints via slot restore.
        assert value['max_tokens']==1 and not value['stream']
        assert value['cache_prompt'] and value['id_slot']==0
        assert value['messages'][-1]['content']==cache.BRIEF_HEADER+'{"concept":"prefix cache warmup only"}'
        return {'timings':{'prompt_n':4755}}
    monkeypatch.setattr(cache,'post',post)
    return calls


def test_prefix_warmed_once_and_kept_live_across_concepts(tmp_path,monkeypatch):
    calls=mock_server(monkeypatch)
    instance=cache.PromptCache('fake',tmp_path,{'model':'same','context':12288})
    first=instance.prepare(payload('jewelry'))
    second=instance.prepare(payload('zombie fur'))
    assert first==second and len(calls)==1
    info=json.loads(next(tmp_path.glob('prefix_*.json')).read_text())
    assert info['warmup_timings']['prompt_n']==4755
    changed=payload();changed['messages'][0]['content']='different system'
    assert instance.prepare(changed)['key']!=first['key']
    assert len(calls)==2


def test_new_server_warms_once_instead_of_restoring_incomplete_swa_state(tmp_path,monkeypatch):
    calls=mock_server(monkeypatch)
    first=cache.PromptCache('fake',tmp_path,{'model':'same'}).prepare(payload())
    instance=cache.PromptCache('fake',tmp_path,{'model':'same'})
    assert instance.prepare(payload())==first and len(calls)==2
    instance.prepare(payload('other'))
    assert len(calls)==2
    changed=cache.PromptCache('fake',tmp_path,{'model':'changed'}).prepare(payload())
    assert changed['key']!=first['key']
    with pytest.raises(RuntimeError,match='brief header'):
        instance.prepare({'messages':[{'role':'user','content':'unsupported'}]})
