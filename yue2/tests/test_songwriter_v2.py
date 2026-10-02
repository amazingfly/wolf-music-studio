import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from test_songwriter import row, assets
import gemma_songwriter as writer
from checkSongPrompts import SongCorpus, strict_json
from songwriter_policy import creative_song, repair_plan, apply_patch_response
from songwriter_metrics import metrics
from compareSongwriters import rate, comparison, media_index
from track_registry import atomic_json


def creative(value):return json.dumps({k:value[k] for k in ('title','style','lyrics')})


def v2_assets(tmp_path):
    source=assets(tmp_path,count=1)
    atomic_json(source/'ideas.json',[{'id':'idea0','concept':'wolf girl jewelry and dangerous bodies',
                                     'situations':['a stolen ring on a bridge','an anklet on a roof']}])
    return source


def test_packaging_supplies_metadata_and_only_fixes_end_marker():
    song=row('invented');slot={'id':'correct_id','audio_seed':129}
    value,changes=creative_song(creative(song),slot)
    assert value['id']=='correct_id' and value['seed']==129 and value['lyrics']==song['lyrics']
    assert not changes
    original=song['lyrics'].replace('\n[End]','')
    fixed,changes=creative_song(creative({**song,'lyrics':original}),slot)
    assert fixed['lyrics']==original+'\n[End]' and changes==['appended_terminal_End']
    for text in [creative(song)[:-3],json.dumps({**json.loads(creative(song)),'seed':999}),
                 creative({**song,'lyrics':original.replace('[Outro]','[Bridge]')}),
                 creative({**song,'lyrics':original[:original.index('[Outro]')]+ '[Outro]\n('}),
                 creative({**song,'lyrics':original.replace('climbing into a soaring metal belt','pretty singing')})]:
        with pytest.raises(ValueError):creative_song(text,slot)


def borrowed(chorus=False):
    old=row('old','cedar'); new=row('new','maple')
    marker='[Chorus]' if chorus else '[Verse 1]'
    phrase='amber teeth carry little sister across cold river'
    old['lyrics']=old['lyrics'].replace(marker,marker+'\n'+phrase)
    new['lyrics']=new['lyrics'].replace(marker,marker+'\n'+phrase)
    corpus=SongCorpus([(old,'old')]);review=corpus.compare(new)
    return new,corpus,review


def test_targeted_repair_preserves_every_other_line_and_choruses_cannot_be_repaired():
    new,corpus,review=borrowed()
    assert review['status']=='tooSimilar'
    plan=repair_plan(new,review,corpus);assert plan and len(plan['lines'])==1
    number=plan['lines'][0]['line']
    patch={'replacements':[{'line':number,'text':'Her wet muzzle cradles what the current almost took'}]}
    fixed=apply_patch_response(new,plan,json.dumps(patch))
    assert corpus.compare(fixed)['status']=='accepted'
    for i,(a,b) in enumerate(zip(new['lyrics'].splitlines(keepends=True),fixed['lyrics'].splitlines(keepends=True)),1):
        if i!=number:assert a==b
    for bad in [{'replacements':[{'line':number+1,'text':'bad'}]},
                {'replacements':[{'line':number,'text':'[Chorus] changed'}]},
                {'replacements':[{'line':number,'text':'bad\nnew line'}]},
                {'replacements':[]}]:
        with pytest.raises(ValueError):apply_patch_response(new,plan,json.dumps(bad))
    new,corpus,review=borrowed(chorus=True)
    assert repair_plan(new,review,corpus) is None


def test_v2_resident_campaign_records_raw_failures_and_local_repair_separately(tmp_path):
    source=v2_assets(tmp_path); root=tmp_path/'workspace';campaign=root/'songwriter/runs/v2'
    state=writer.prepare(campaign,source,per_idea=2,writer_version=2)
    historic=row('old','cedar');phrase='amber teeth carry little sister across cold river'
    historic['lyrics']=historic['lyrics'].replace('[Verse 1]','[Verse 1]\n'+phrase)
    atomic_json(root/'outputs/old/source_config.json',historic)
    calls=[]
    class Session:
        url='fake';entered=0;exited=0
        def __init__(self,**kwargs):pass
        def __enter__(self):Session.entered+=1;return self
        def __exit__(self,*args):Session.exited+=1
    def client(url,idea,track,seed,audio_seed,directory,**kw):
        calls.append(kw)
        if kw.get('patch'):
            plan=kw['patch']['plan'];return json.dumps({'replacements':[{'line':plan['lines'][0]['line'],
                          'text':'Her wet muzzle cradles what the current almost took'}]})
        r=row(track,'maple' if len(calls)==1 else 'birch')
        if len(calls)==1:r['lyrics']=r['lyrics'].replace('[Verse 1]','[Verse 1]\n'+phrase)
        else:r['lyrics']=r['lyrics'].replace('\n[End]','')
        return creative(r)
    writer.run_campaign(campaign,state,root,Session,client)
    m=metrics(campaign)
    assert Session.entered==Session.exited==1 and len(calls)==3
    assert m['reviewed_full_drafts']==2 and m['raw_first_pass_accepted']==0
    assert m['raw_first_pass_failed']==2 and m['original_similarity_rejections']==1
    assert m['line_repairs_attempted']==m['line_repairs_accepted']==1
    assert m['terminal_marker_fixes']==1 and m['after_packaging_accepted']==1
    assert m['eventual_draft_accepted']==2 and m['eventual_draft_success_percent']==100
    assert len(list((campaign/'tooSimilar').glob('*.review.json')))==1
    assert 'chorus' not in str(writer.prior_hooks(campaign,state,'idea0'))
    assert calls[0]['writer_version']==2
    writer.run_campaign(campaign,state,root,Session,client)
    assert len(calls)==3 and metrics(campaign)['reviewed_full_drafts']==2


def test_failed_repair_is_not_hidden_and_falls_back_to_full_rewrite(tmp_path):
    source=v2_assets(tmp_path);root=tmp_path/'workspace'; campaign=root/'songwriter/runs/v2'
    state=writer.prepare(campaign,source,per_idea=1,writer_version=2)
    old=row('old','cedar'); phrase='amber teeth carry little sister across cold river'
    old['lyrics']=old['lyrics'].replace('[Verse 1]','[Verse 1]\n'+phrase)
    atomic_json(root/'outputs/old/source_config.json',old)
    from contextlib import nullcontext
    class Model:url='fake'
    calls=[]
    def client(*args,**kw):
        calls.append(kw)
        if kw.get('patch'):return '{bad patch'
        value=row('new','maple' if len(calls)==1 else 'birch')
        if len(calls)==1:value['lyrics']=value['lyrics'].replace('[Verse 1]','[Verse 1]\n'+phrase)
        return creative(value)
    writer.run_campaign(campaign,state,root,lambda **kw:nullcontext(Model()),client)
    m=metrics(campaign)
    assert len(calls)==3 and m['reviewed_full_drafts']==2
    assert m['raw_first_pass_failed']==1 and m['line_repairs_attempted']==1 and m['line_repairs_accepted']==0
    assert m['eventual_draft_failed']==1 and m['eventual_draft_accepted']==1


def test_comparison_ratings_preserve_configs_and_pair_tracks(tmp_path):
    source=v2_assets(tmp_path); root=tmp_path/'workspace'; old=root/'songwriter/runs/old';new=root/'songwriter/runs/new'
    for c,version in [(old,1),(new,2)]:
        state=writer.prepare(c,source,per_idea=1,writer_version=version)
        slot=state['slots'][0];song=row(slot['id']);song['seed']=slot['audio_seed']
        writer.accept(c,state,slot,song,{'status':'accepted'},c/'pilot_import')
        if c==new:
            track=slot['id']; prompt=c/'accepted'/(track+'.json');before=prompt.read_bytes()
            batch=root/'outputs/batch';atomic_json(batch/'requests.jsonl',song) # One line JSONL below.
            (batch/'requests.jsonl').write_text(json.dumps(song)+'\n')
            d=batch/track;d.mkdir();(d/'audio.flac').touch();(d/(track+'.flac')).touch()
            rate(c,track,{'overall':8,'pace':9},'Love the drums')
            assert prompt.read_bytes()==before
            with pytest.raises(ValueError):rate(c,track,{'overall':11})
    report=comparison(old,new,root)
    assert report['pairs'][0]['old']['audio_seed']==report['pairs'][0]['new']['audio_seed']
    assert report['campaigns'][0]['listening']['average_scores']['overall'] is None
    assert report['campaigns'][1]['listening']['average_scores']['overall']==8
    assert media_index(root)[track][0]['trimmed']


def test_pause_after_current_draft_flushes_but_never_loads_next_song(tmp_path):
    source=v2_assets(tmp_path); root=tmp_path/'workspace';c=root/'songwriter/runs/v2'
    state=writer.prepare(c,source,per_idea=2,writer_version=2)
    from contextlib import nullcontext
    class Model:url='fake'
    def client(*args,**kw):
        (c/'pause_after_draft').touch();return creative(row('new'))
    writer.run_campaign(c,state,root,lambda **kw:nullcontext(Model()),client)
    assert state['status']=='paused_at_draft_boundary'
    assert state['slots'][0]['status']=='accepted' and state['slots'][1]['attempts']==0


def test_browser_rating_import_validates_whole_file_before_writing(tmp_path):
    from compareSongwriters import import_ratings,render
    source=v2_assets(tmp_path);c=tmp_path/'campaign';s=writer.prepare(c,source,per_idea=1,writer_version=2)
    slot=s['slots'][0];song=row(slot['id']);song['seed']=slot['audio_seed']
    writer.accept(c,s,slot,song,{'status':'accepted'},c/'pilot_import')
    path=tmp_path/'ratings.json'; entry={'campaign':str(c),'id':slot['id'],'overall':8,'notes':'Tender ending'}
    atomic_json(path,{'format':'yue2_listening_ratings_v1','ratings':[entry,{**entry,'id':'unknown','overall':20}]})
    with pytest.raises(ValueError):import_ratings(path,[c])
    assert not (c/'listening_ratings').exists()
    atomic_json(path,{'format':'yue2_listening_ratings_v1','ratings':[entry]})
    assert import_ratings(path,[c])==1
    assert strict_json((c/'listening_ratings'/(slot['id']+'.json')).read_text())['overall']==8
    render(comparison(c,c,tmp_path/'workspace'),tmp_path/'viewer.html')
    page=(tmp_path/'viewer.html').read_text()
    assert 'Export ratings JSON' in page and 'fieldset class="rating"' in page and '#fff' in page


def test_client_uses_free_creative_strings_and_restricts_only_repair_addresses(tmp_path,monkeypatch):
    import io
    import songwriter_client as client
    source=v2_assets(tmp_path);payloads=[]
    def open_request(request,timeout):
        payload=strict_json(request.data.decode());payloads.append(payload)
        schema=payload['response_format']['json_schema']['schema']
        assert payload['cache_prompt'] and payload['id_slot']==0
        if 'replacements' in schema['properties']:
            value={'replacements':[{'line':3,'text':'The wet bridge bows beneath her borrowed burden'}]}
        else:value=json.loads(creative(row()))
        events=[{'choices':[{'delta':{'content':json.dumps(value)},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}],'timings':{'cache_n':4000}}]
        return io.BytesIO((''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode())
    monkeypatch.setattr(client.urllib.request,'urlopen',open_request)
    idea=strict_json((source/'ideas.json').read_text())[0]
    client.request_song('http://fake',idea,'id',123,85300,tmp_path/'full',assets=source,writer_version=2)
    client.request_song('http://fake',idea,'id',124,85300,tmp_path/'repair',assets=source,writer_version=2,
        patch={'song':json.loads(creative(row())),'plan':{'lines':[{'line':3,'text':'copied old words'}]}})
    schema=payloads[0]['response_format']['json_schema']['schema']
    assert all(v=={'type':'string'} for v in schema['properties'].values())
    replacements=payloads[1]['response_format']['json_schema']['schema']['properties']['replacements']
    assert replacements['minItems']==replacements['maxItems']==1
    assert replacements['items']['properties']['line']['enum']==[3]
    assert payloads[1]['max_tokens']==512
    assert payloads[0]['messages'][:-1]==payloads[1]['messages'][:-1]
    assert strict_json((tmp_path/'repair/generation.json').read_text())['request_kind']=='line_repair'
