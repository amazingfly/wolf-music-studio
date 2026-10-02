import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import checkSongPrompts as checks
import gemma_songwriter as writer
from track_registry import atomic_json


def alphabet(n):
    result = ''
    while True:
        result = chr(97+n%26) + result
        n //= 26
        if not n:
            return result


def row(name='test', prefix='cedar'):
    words = [prefix+alphabet(i) for i in range(300)]
    headings = ['Intro', 'Verse 1', 'Pre-Chorus', 'Chorus', 'Bridge - Symphonic Metalcore Escalation',
                'Final Chorus', 'Outro']
    lines = [f'[{section}]\n'+ ' '.join(words[i*40:(i+1)*40]) for i, section in enumerate(headings)]
    lines[4] += '\n(clear female phrase climbing into a soaring metal belt, ending in a short gritty yell)'
    return {'id':name, 'title':name, 'lyrics':'\n\n'.join(lines)+'\n[End]',
            'style':'electronicore, 168 bpm, heavy double-kick drums, supersaw synth, fast female vocals',
            'seed':85300,'cot':'full','target_seconds':360,
            'duration_validation':{'min_seconds':250,'max_seconds':380}}


def test_strict_json_and_actual_pipeline_schema():
    good = row()
    assert checks.parse_song(json.dumps(good)) == good
    assert checks.parse_song('```json\n'+json.dumps(good)+'\n```') == good
    for bad in ['{"id":"one","id":"two"}', '{"seed":NaN}', json.dumps({**good,'video_path':'bad'}),
                json.dumps({**good,'seed':True}), json.dumps({**good,'target_seconds':180})]:
        with pytest.raises(ValueError):
            checks.parse_song(bad)
    with pytest.raises(ValueError, match='End'):
        checks.quality_checks({**good,'lyrics':good['lyrics'].replace('[End]','')})
    with pytest.raises(ValueError, match='belt'):
        checks.quality_checks({**good,'lyrics':good['lyrics'].replace('climbing into a soaring metal belt','beautiful singing')})
    checks.quality_checks(good)


def test_cues_are_not_lyrics_and_seed_copies_deduplicate():
    old = row('old','cedar'); new = row('new','maple')
    chorus_cue = '\n(soaring melodic female vocal, light male backing, driving synth-metal beat)\n'
    old['lyrics'] += chorus_cue; new['lyrics'] += chorus_cue
    corpus = checks.SongCorpus([(old,'old.json'),({**old,'seed':999},'seed.json')])
    assert len(corpus.documents) == 1
    assert corpus.compare(new)['status'] == 'accepted'
    assert corpus.compare({**old,'id':'copy'})['status'] == 'tooSimilar'
    assert 'SAVE THE PUP' in checks.sung_text('[Intro]\n(male sub-bass chant)\n(Male: SAVE THE PUP)')
    assert 'sub-bass' not in checks.sung_text(old['lyrics'])


def test_shared_long_phrases_and_complete_lines_are_quarantined():
    old = row('old','cedar'); new = row('new','maple')
    line = 'amber teeth carry little sister across cold river'
    old['lyrics'] += '\n'+line
    new['lyrics'] += '\n'+line
    report = checks.SongCorpus([(old,'old')]).compare(new)
    assert report['status'] == 'tooSimilar'
    assert report['closest'][0]['longest_shared_words'] >= 8
    assert report['closest'][0]['shared_lines'] == [line]
    new = row('new','maple'); new['lyrics'] += '\ni feel the rain'
    old['lyrics'] += '\ni feel the snow'
    assert checks.SongCorpus([(old,'old')]).compare(new)['status'] == 'accepted'


def test_collage_is_checked_against_whole_library():
    old = [row(f'old{i}', f'forest{alphabet(i)}') for i in range(4)]
    corpus = checks.SongCorpus([(r, str(i)) for i,r in enumerate(old)])
    # Independent three-word borrowed fragments scattered among new words.
    snippets = []
    for i, previous in enumerate(old):
        words = checks.features(previous)['tokens']
        for j in range(0,120,3):
            snippets += words[j:j+3] + ['unborrowed'+alphabet(i*100+j)]
    new = row('collage','fresh'); new['lyrics'] = '\n'.join(' '.join(snippets[i:i+16]) for i in range(0,len(snippets),16))
    report = corpus.compare(new)
    assert report['status'] == 'tooSimilar' and report['collage_reuse']


def assets(tmp_path, count=2):
    source = tmp_path/'assets'
    source.mkdir()
    for name,value in [('ideas.json',[{'id':f'idea{i}','concept':'a distinct wolf story'} for i in range(count)]),
                       ('examples.json',{'songs':[row()]}),('reference_sources.json',{})]:
        atomic_json(source/name,value)
    (source/'system_prompt.txt').write_text('Generate new songs.')
    return source


def test_campaign_round_robin_seeds_and_frozen_assets(tmp_path):
    source = assets(tmp_path)
    campaign = tmp_path/'campaign'
    state = writer.prepare(campaign,source,per_idea=10)
    assert len(state['slots']) == 20
    assert [s['idea_id'] for s in state['slots'][:4]] == ['idea0','idea1','idea0','idea1']
    assert len({s['audio_seed'] for s in state['slots'] if s['idea_id']=='idea0'}) == 10
    assert writer.generation_seed(5,'idea0',0,1) != writer.generation_seed(5,'idea0',0,2)
    (source/'system_prompt.txt').write_text('updated source')
    assert writer.prepare(campaign,source)['assets_sha256'] == state['assets_sha256']
    (campaign/'assets/system_prompt.txt').write_text('changed frozen version')
    with pytest.raises(ValueError,match='asset changed'):
        writer.prepare(campaign,source)


def test_submissions_are_atomic_and_restart_does_not_duplicate_after_watcher_move(tmp_path):
    source = assets(tmp_path,count=1)
    campaign = tmp_path/'campaign'; root=tmp_path/'workspace'
    state = writer.prepare(campaign,source,per_idea=12,batch_size=4)
    for index,slot in enumerate(state['slots']):
        song = row(slot['id'], 'fresh'+alphabet(index)); song['seed']=slot['audio_seed']
        writer.accept(campaign,state,slot,song,{'status':'accepted'},campaign/'attempts')
    writer.flush_queue(campaign,state,root,final=True)
    pending = root/'queue/pending'
    files=sorted(pending.glob('*.json'))
    assert [len(checks.songs(p)) for p in files] == [1,4,4,3]
    assert len({r['id'] for p in files for r in checks.songs(p)}) == 12
    running=root/'queue/running/a/input'; running.mkdir(parents=True)
    files[0].rename(running/files[0].name)
    state['submissions'][0]['published']=False  # Crash before the publication commit.
    writer.flush_queue(campaign,state,root,final=True)
    assert not files[0].exists() and state['submissions'][0]['published']
    assert len(list(pending.glob('*.json'))) == 3
    assert len(checks.songs(campaign/'accepted_config.json')) == 12


def test_fake_campaign_rejects_invalid_and_similar_then_queues_distinct_song(tmp_path):
    source = assets(tmp_path,count=1)
    root=tmp_path/'workspace'; campaign=root/'songwriter/runs/test'
    state=writer.prepare(campaign,source,per_idea=2,batch_size=2)
    existing=row('historic','copied'); atomic_json(root/'outputs/batch/source_config.json',existing)
    calls=[]
    class Session:
        url='http://fake'; active=False; entered=0; exited=0
        def __init__(self,**kw): pass
        def __enter__(self): Session.active=True; Session.entered+=1; return self
        def __exit__(self,*_): Session.active=False; Session.exited+=1
    def client(_url,_idea,track_id,_seed,audio_seed,directory,**kw):
        assert Session.active
        directory.mkdir(parents=True)
        calls.append(track_id)
        if len(calls)==1: return '{bad JSON'
        result=row(track_id,'copied' if len(calls)==2 else 'new'+alphabet(len(calls)))
        result['seed']=audio_seed
        return json.dumps(result)
    assert writer.run_campaign(campaign,state,root,Session,client)==0
    assert not Session.active
    assert Session.entered==1 and Session.exited==1  # Both songs and retries share one load.
    assert len(calls)==4
    assert Session.entered==1  # Resuming completed campaign never loads a model.
    assert all(s['status']=='accepted' for s in state['slots'])
    assert len(list((campaign/'tooSimilar').glob('*.json')))==2
    assert len(list((campaign/'invalid').glob('*.json')))==1
    assert len(list((root/'queue/pending').glob('*.json')))==2
    assert writer.run_campaign(campaign,state,root,Session,client)==0
    assert len(calls)==4


def test_restart_recovers_completed_response_without_running_model_again(tmp_path):
    source=assets(tmp_path,count=1)
    root=tmp_path/'workspace';campaign=root/'songwriter/runs/resumed'
    state=writer.prepare(campaign,source,per_idea=1,max_attempts=1)
    slot=state['slots'][0];slot.update(status='generating',attempts=1)
    directory=campaign/'attempts'/slot['id']/'attempt01'
    song=row(slot['id']);song['seed']=slot['audio_seed']
    atomic_json(directory/'generation.json',{'finish_reason':'stop'})
    (directory/'response.txt').write_text(json.dumps(song))
    writer.save(campaign,state)
    def forbidden(**_): pytest.fail('must recover finished draft instead of starting model')
    writer.run_campaign(campaign,state,root,session_factory=forbidden)
    assert slot['status']=='accepted'
    assert len(list((root/'queue/pending').glob('*.json')))==1
