#!/usr/bin/env python3
"""Compare songwriter versions and save listening ratings separately from YuE2 configs."""
import argparse
from collections import defaultdict
from datetime import datetime
import html
import json
from pathlib import Path
import fcntl
from checkSongPrompts import strict_json
from run_config import ROOT, component
from songwriter_metrics import metrics, write_metrics
from track_registry import atomic_json

DEFAULT_OLD=ROOT/'songwriter/runs/gemmaWolf_20261002'
DEFAULT_NEW=ROOT/'songwriter/runs/gemmaWolf_v2_20261002'
SCORES=('overall','pace','vocals','story')


def rate(campaign, track, values, notes=None):
    campaign=Path(campaign).expanduser().resolve()
    component(track)
    song=campaign/'accepted'/(track+'.json')
    if not song.exists(): raise ValueError('Rate an accepted track from this campaign')
    for name, value in values.items():
        if name not in SCORES or type(value) not in (int,float) or not 0<=value<=10:
            raise ValueError('Listening scores must be between 0 and 10')
    if notes is not None and not isinstance(notes,str): raise ValueError('Notes must be text')
    directory=campaign/'listening_ratings';directory.mkdir(exist_ok=True)
    with (directory/'ratings.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=directory/(track+'.json')
        previous=strict_json(path.read_text()) if path.exists() else {'id':track}
        previous.update(values)
        if notes is not None: previous['notes']=notes
        previous['updated_at']=datetime.now().astimezone().isoformat()
        atomic_json(path,previous)
    return previous


def import_ratings(path, campaigns):
    value=strict_json(Path(path).read_text())
    if (not isinstance(value,dict) or set(value)!={'format','ratings'} or
        value['format']!='yue2_listening_ratings_v1' or not isinstance(value['ratings'],list)):
        raise ValueError('Expected an exported songwriter listening-ratings file')
    allowed={Path(p).resolve() for p in campaigns};seen=set();entries=[]
    for item in value['ratings']:
        if (not isinstance(item,dict) or not {'campaign','id'}<=set(item) or
            set(item)-{'campaign','id','notes',*SCORES}):
            raise ValueError('Invalid listening rating entry')
        campaign=Path(item['campaign']).resolve();track=component(item['id'])
        if campaign not in allowed or (campaign,track) in seen:
            raise ValueError('Unknown campaign or duplicate track in ratings import')
        if not (campaign/'accepted'/(track+'.json')).is_file():raise ValueError('Unknown accepted track')
        scores={s:item[s] for s in SCORES if s in item}
        if any(type(v) not in (int,float) or not 0<=v<=10 for v in scores.values()):
            raise ValueError('Listening scores must be between 0 and 10')
        if 'notes' in item and not isinstance(item['notes'],str):raise ValueError('Notes must be text')
        seen.add((campaign,track));entries.append((campaign,track,scores,item.get('notes')))
    for c,t,s,n in entries:rate(c,t,s,n)
    return len(entries)


def media_index(root):
    """Match exact output directory IDs; prefer the canonical trimmed FLAC."""
    result=defaultdict(list)
    for requests in (Path(root).expanduser().resolve()/'outputs').glob('*/requests.jsonl'):
        for line in requests.read_text().splitlines():
            if not line.strip():continue
            row=strict_json(line);track=row['id'];component(track)
            directory=requests.parent/track
            if not directory.exists():continue
            canonical=directory/(track+'.flac')
            original=directory/'audio.flac'
            audio=canonical if canonical.exists() else original if original.exists() else None
            result[track].append({'directory':str(directory),'audio':str(audio) if audio else None,
                'trimmed':canonical.exists(), 'videos':[str(p) for p in directory.rglob('*.mp4')]})
    return result


def track_rows(campaign,media):
    campaign=Path(campaign).expanduser().resolve()
    manifest=campaign/'baseline_manifest.json'
    if not manifest.exists(): manifest=campaign/'manifest.json'
    state=strict_json(manifest.read_text())
    values=[]
    for slot in state['slots']:
        value={k:slot[k] for k in ['id','idea_id','variation','audio_seed','template','status','attempts']}
        accepted=campaign/'accepted'/(slot['id']+'.json')
        if accepted.exists() and slot['status']=='accepted':
            row=strict_json(accepted.read_text());value.update(title=row['title'],prompt_path=str(accepted),lyrics=row['lyrics'])
        rating=campaign/'listening_ratings'/(slot['id']+'.json')
        value['rating']=strict_json(rating.read_text()) if rating.exists() else {}
        value['media']=media.get(slot['id'],[])
        values.append(value)
    return values


def comparison(old,new,root=ROOT):
    media=media_index(root)
    reports=[]
    for campaign in ((old,new) if old is not None else (new,)):
        campaign=Path(campaign).expanduser().resolve()
        baseline=campaign/'baseline_metrics.json'
        report=strict_json(baseline.read_text()) if baseline.exists() else write_metrics(campaign)
        tracks=track_rows(campaign,media)
        audio_ratings=[t['rating'] for t in tracks if t['media'] and any(m['audio'] for m in t['media']) and t['rating']]
        report={**report,'listening':{'finished_audio_tracks':sum(any(m['audio'] for m in t['media']) for t in tracks),
            'rated_audio_tracks':len(audio_ratings),
            'average_scores':{s:round(sum(r[s] for r in audio_ratings if s in r)/sum(s in r for r in audio_ratings),2)
                              if any(s in r for r in audio_ratings) else None for s in SCORES}},
                'directory':str(campaign),'tracks_detail':tracks}
        reports.append(report)
    if len(reports)==2:
        lookup={(t['idea_id'],t['variation']):t for t in reports[1]['tracks_detail']}
        pairs=[{'idea_id':t['idea_id'],'variation':t['variation']+1,'old':t,'new':lookup.get((t['idea_id'],t['variation']))}
               for t in reports[0]['tracks_detail']]
    else:
        pairs=[{'idea_id':t['idea_id'],'variation':t['variation']+1,'old':None,'new':t}
               for t in reports[0]['tracks_detail']]
    return {'created_at':datetime.now().astimezone().isoformat(),'campaigns':reports,'pairs':pairs,
            'notes':'Rates retain original failures even when repaired. Matching concept/variant pairs use the same reference and audio seed; situations differ. Judge the finished music separately.'}


def render(report,destination):
    destination=Path(destination)
    rows=[]
    for pair in report['pairs']:
        cells=[]
        for version in ('old','new'):
            t=pair[version]
            if not t: cells.append('<td>Unscheduled</td>');continue
            esc=lambda v:html.escape(str(v),quote=True)
            content=f"<b>{esc(t.get('title',t['id']))}</b><p>{esc(t['status'])}; seed {t['audio_seed']}; drafts {t['attempts']}</p>"
            for media in t['media']:
                if media['audio']:
                    content+=f'<audio controls preload="none" src="{esc(Path(media["audio"]).as_uri())}"></audio>'
                for video in media['videos']:
                    content+=f'<p><a href="{esc(Path(video).as_uri())}">Visualizer MP4</a></p>'
            if t.get('prompt_path'):
                content+=f'<p><a href="{esc(Path(t["prompt_path"]).as_uri())}">Prompt JSON</a></p>'
                content+=f'<details><summary>Lyrics</summary><pre>{esc(t["lyrics"])}</pre></details>'
                campaign=report['campaigns'][0 if version=='old' else -1]['directory']
                command=f'python compareSongwriters.py --rate-campaign {campaign} --rate {t["id"]} --overall 8 --pace 8 --vocals 8 --story 8'
                content+=f'<details><summary>Rate this track (0–10)</summary><p>Edit scores and run:</p><pre>{esc(command)}</pre></details>'
                content+=f'<fieldset class="rating" data-campaign="{esc(campaign)}" data-id="{esc(t["id"])}"><legend>Listening score (0–10)</legend>'
                for score in SCORES:
                    content+=f'<label>{score} <input type="number" min="0" max="10" step="0.5" data-score="{score}" value="{esc(t["rating"].get(score,""))}"></label> '
                content+=f'<label>Notes <textarea data-score="notes">{esc(t["rating"].get("notes",""))}</textarea></label></fieldset>'
            content+=f'<p>Ratings: {esc(json.dumps(t["rating"])) if t["rating"] else "Unrated"}</p>'
            cells.append('<td>'+content+'</td>')
        names=' '.join(t.get('title',t['id']) for t in (pair['old'],pair['new']) if t)
        generated=any(t and t.get('prompt_path') for t in (pair['old'],pair['new']))
        rows.append(f'<tr class="track" data-name="{html.escape(names.casefold(),quote=True)}" data-generated="{int(generated)}"><th>{html.escape(pair["idea_id"])}<br>Variant {pair["variation"]}</th>'+''.join(cells)+'</tr>')
    compact=[{k:v for k,v in c.items() if k!='tracks_detail'} for c in report['campaigns']]
    summary='<table><tr><th>Version</th><th>Drafts</th><th>Raw pass / fail</th><th>Raw success</th><th>Ending fixes</th><th>Repairs pass / tried</th><th>Final pass / fail</th><th>Final success</th><th>Audio / rated</th><th>Listening averages</th></tr>'
    for c in compact:
        raw=f'{c["raw_first_pass_success_percent"]}%' if c['reviewed_full_drafts'] else 'Awaiting draft'
        final=f'{c["eventual_draft_success_percent"]}%' if c['reviewed_full_drafts'] else 'Awaiting draft'
        scores=', '.join(f'{k}: {v}' for k,v in c['listening']['average_scores'].items() if v is not None) or 'Unrated'
        summary+=f'<tr><th>{html.escape(c["campaign"])}</th><td>{c["reviewed_full_drafts"]}</td><td>{c["raw_first_pass_accepted"]} / {c["raw_first_pass_failed"]}</td><td>{raw}</td><td>{c["terminal_marker_fixes"]}</td><td>{c["line_repairs_accepted"]} / {c["line_repairs_attempted"]}</td><td>{c["eventual_draft_accepted"]} / {c["eventual_draft_failed"]}</td><td>{final}</td><td>{c["listening"]["finished_audio_tracks"]} / {c["listening"]["rated_audio_tracks"]}</td><td>{scores}</td></tr>'
    summary+='</table>'
    rating_help='<p>Listening scores below are saved in this browser. <button id="export">Export ratings JSON</button> Then import that file to save scores into the campaign records and refresh averages:</p><pre>python compareSongwriters.py --import-ratings ~/Downloads/songwriter_listening_ratings.json</pre>'
    script='''<script>
function filter(){let q=document.getElementById("search").value.toLowerCase();let all=document.getElementById("unfinished").checked;document.querySelectorAll("tr.track").forEach(r=>r.hidden=(!all&&r.dataset.generated!=="1")||!r.dataset.name.includes(q))}
document.getElementById("search").addEventListener("input",filter);document.getElementById("unfinished").addEventListener("change",filter);filter();
document.querySelectorAll("fieldset.rating").forEach(f=>{const key="yue2-rating:"+f.dataset.campaign+":"+f.dataset.id;let stored={};try{stored=JSON.parse(localStorage.getItem(key)||"{}")}catch(e){};f.querySelectorAll("[data-score]").forEach(i=>{if(Object.hasOwn(stored,i.dataset.score))i.value=stored[i.dataset.score];i.addEventListener("input",()=>{let v={};f.querySelectorAll("[data-score]").forEach(j=>v[j.dataset.score]=j.value);try{localStorage.setItem(key,JSON.stringify(v))}catch(e){}})})});
document.getElementById("export").addEventListener("click",()=>{const ratings=[];let invalid=false;document.querySelectorAll("fieldset.rating").forEach(f=>{let v={campaign:f.dataset.campaign,id:f.dataset.id};f.querySelectorAll("[data-score]").forEach(i=>{if(i.dataset.score==="notes"){if(i.value)v.notes=i.value}else if(i.value!==""){if(!i.checkValidity())invalid=true;v[i.dataset.score]=Number(i.value)}});if(Object.keys(v).length>2)ratings.push(v)});if(invalid){alert("Scores must be between 0 and 10.");return}const u=URL.createObjectURL(new Blob([JSON.stringify({format:"yue2_listening_ratings_v1",ratings},null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=u;a.download="songwriter_listening_ratings.json";a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)});
</script>'''
    page='<!doctype html><meta charset="utf-8"><title>Songwriter comparison</title><style>body,input,button,textarea,fieldset,table,th,td,details,summary,pre,a{background:#fff;color:#000}body{font:16px sans-serif;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #000;padding:12px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}audio{width:100%}input,textarea,button,fieldset{border:1px solid #000;padding:8px}input::placeholder{color:#000;opacity:1}input[type=number]{width:60px}textarea{display:block;width:90%}</style><h1>Songwriter version comparison</h1><p>'+html.escape(report['notes'])+'</p>'+summary+'<details><summary>Full metrics and provenance</summary><pre>'+html.escape(json.dumps(compact,indent=2))+'</pre></details>'+rating_help+'<p><input id="search" placeholder="Search song names" aria-label="Search song names"> <label><input id="unfinished" type="checkbox">Include pairs without an accepted prompt</label></p><table><tr><th>Concept</th><th>Baseline campaign</th><th>New campaign</th></tr>'+''.join(rows)+'</table>'+script
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(page)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old',type=Path,help='Baseline campaign; inferred from the new campaign when omitted')
    parser.add_argument('--new',type=Path,help='New campaign; defaults to the most recently created campaign')
    parser.add_argument('--output',type=Path,default=ROOT/'songwriter/comparison')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--rate-campaign',type=Path)
    parser.add_argument('--rate')
    parser.add_argument('--import-ratings',type=Path,help='Import listening scores exported by the HTML viewer')
    for s in SCORES:parser.add_argument('--'+s,type=float)
    parser.add_argument('--notes')
    args=parser.parse_args(argv)
    available=sorted((ROOT/'songwriter/runs').glob('*/manifest.json'),key=lambda p:strict_json(p.read_text()).get('created_at',''))
    if args.new is None:
        if not available:
            print('No songwriter campaigns yet; start ./studio songwriter first.')
            return 0
        args.new=available[-1].parent
    args.new=args.new.expanduser().resolve()
    if args.old is None:
        baseline=strict_json((args.new/'manifest.json').read_text()).get('baseline_campaign')
        if baseline and (Path(baseline)/'manifest.json').exists():args.old=Path(baseline)
        else:
            previous=[p.parent for p in available if p.parent.resolve()!=args.new]
            args.old=previous[-1] if previous else None
    if args.import_ratings:
        try: print(f'Imported {import_ratings(args.import_ratings,[p for p in [args.old,args.new] if p is not None])} listening ratings')
        except (ValueError,OSError,TypeError,KeyError) as exc:parser.error(str(exc))
    if args.rate:
        values={s:getattr(args,s) for s in SCORES if getattr(args,s) is not None}
        if not values and args.notes is None:parser.error('Supply a listening score or notes')
        try:rate(args.rate_campaign or args.new,args.rate,values,args.notes)
        except ValueError as exc:parser.error(str(exc))
    report=comparison(args.old,args.new,args.root)
    atomic_json(args.output/'comparison.json',report)
    render(report,args.output/'comparison.html')
    for c in report['campaigns']:
        print(f"{c['campaign']}: {c['reviewed_full_drafts']} reviewed drafts; raw success {c['raw_first_pass_success_percent']}%; final success {c['eventual_draft_success_percent']}%; {c['tracks']}")
    print(args.output/'comparison.html')
    return 0

if __name__=='__main__':raise SystemExit(main())
