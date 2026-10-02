#!/usr/bin/env python3
"""Conservative, auditable trimming of generated music. Original FLACs stay intact.

Dependencies: numpy, scipy, soundfile; onnxruntime for AI-confirmed late joins.
No speech detector or GPU required. Audio analysis and classification stay local.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import html
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from scipy.ndimage import uniform_filter1d
import soundfile as sf

VERSION = '1.2'

@dataclass(frozen=True)
class Settings:
    hop_seconds: float = .1
    window_seconds: float = .2
    relative_db: float = 32
    absolute_floor_dbfs: float = -78
    minimum_event_seconds: float = .8
    join_seconds: float = 1.5
    detached_gap_seconds: float = 2
    minimum_main_seconds: float = 20
    max_detached_fraction: float = .35
    tail_padding_seconds: float = .3
    maximum_fade_seconds: float = 12
    late_audio: str = 'join'
    minimum_late_music_seconds: float = 5
    join_pause_seconds: float = .5
    join_fade_seconds: float = .005
    trim_leading: bool = False
    head_padding_seconds: float = .2


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def runs(mask):
    """Half-open spans of true values."""
    a=np.flatnonzero(np.diff(np.r_[False,mask,False].astype(np.int8)))
    return [(int(a[i]),int(a[i+1])) for i in range(0,len(a),2)]


def merge(spans, gap):
    out=[]
    for start,end in spans:
        if out and start-out[-1][1]<=gap:out[-1]=(out[-1][0],end)
        else:out.append((start,end))
    return out


def features(path, cfg):
    """Stream stereo-safe level and spectral features at native sample rate."""
    info=sf.info(path)
    if info.frames==0:raise ValueError('Empty input')
    if info.format!='FLAC':raise ValueError('Expected a FLAC input')
    hop=max(1,round(info.samplerate*cfg.hop_seconds))
    window=max(hop,round(info.samplerate*cfg.window_seconds))
    win=np.hanning(window)
    freq=np.fft.rfftfreq(window,1/info.samplerate)
    valid=(freq>=150)&(freq<=min(12000,info.samplerate*.45))
    low=(freq>=20)&(freq<150)
    edges=np.geomspace(40,min(16000,info.samplerate*.47),33)
    bands=[(freq>=a)&(freq<b) for a,b in zip(edges[:-1],edges[1:])]
    level=[];raw=[];flat=[];shapes=[];low_ratio=[]
    # Peak/energy across channels rather than averaging channels: opposite-phase
    # stereo must never cancel into a false silence decision.
    for block in sf.blocks(path,blocksize=window,overlap=window-hop,
                           dtype='float32',always_2d=True):
        if not np.isfinite(block).all():raise ValueError('Non-finite audio samples')
        data=block.astype(np.float64)
        raw.append(20*np.log10(max(float(np.sqrt(np.mean(data*data,axis=0)).max()),1e-12)))
        data-=data.mean(axis=0,keepdims=True)
        level.append(20*np.log10(max(float(np.sqrt(np.mean(data*data,axis=0)).max()),1e-12)))
        if len(data)<window:data=np.pad(data,((0,window-len(data)),(0,0)))
        power=np.square(np.abs(np.fft.rfft(data*win[:,None],axis=0))).sum(axis=1)
        p=power[valid]+1e-30
        flat.append(float(np.exp(np.mean(np.log(p)))/np.mean(p)) if len(p) else 1.)
        bp=np.array([power[b].sum() for b in bands]);shapes.append(bp/max(float(bp.sum()),1e-30))
        low_ratio.append(float(power[low].sum()/max(float(power.sum()),1e-30)))
    shapes=np.array(shapes)
    flux=np.r_[0,np.abs(np.diff(shapes,axis=0)).sum(axis=1)]
    return {'duration':info.duration,'samplerate':info.samplerate,'channels':info.channels,
            'subtype':info.subtype,'frames':info.frames,'hop':hop/info.samplerate,
            'window':window/info.samplerate,'level':np.array(level),'raw_level':np.array(raw),
            'flatness':np.array(flat),'flux':flux,'low_ratio':np.array(low_ratio)}


def decide(f,cfg):
    """Find persistent song activity, group gaps, follow attached fade tails.

    This is a signal heuristic, not an AI judgement of musical meaning. Ambiguous
    long sections are retained and marked for review rather than silently lost.
    """
    y=f['level'];dt=f['hop'];duration=f['duration'];n=len(y)
    reference=float(np.percentile(y,85))
    low=float(np.percentile(y,15))
    # If the recording never gets quiet, its lower musical level is not a noise
    # floor. Do not raise the gate until it erases the quieter parts of a song.
    noise_floor=low if low<reference-25 else -90.
    noise_floor=float(np.clip(noise_floor,-110,-35))
    threshold=max(cfg.absolute_floor_dbfs,reference-cfg.relative_db,
                  min(noise_floor+10,reference-24))
    w=max(1,round(2/dt))
    local=uniform_filter1d(y,size=w,mode='nearest')
    variation=np.sqrt(np.maximum(0,uniform_filter1d(y*y,size=w,mode='nearest')-local*local))
    stable=variation<1.6
    hiss=(f['flatness']>.28)&stable
    rumble=(f['low_ratio']>.92)&stable
    # Loud, sustained harsh metal must not be mistaken for hiss. Noise rejection
    # only operates well below the main song level.
    noise_like=(hiss|rumble)&(y<reference-20)
    active=(y>threshold)&~noise_like
    # Fill tiny within-note holes, then demand persistence: one click is not a song.
    micro=merge(runs(active),round(.25/dt))
    events=[(a,b) for a,b in micro if (b-a)*dt>=cfg.minimum_event_seconds
            and float(np.mean(active[a:b]))>=.55]
    joined=merge(events,round(cfg.join_seconds/dt))
    groups=merge(joined,round(cfg.detached_gap_seconds/dt))
    flags=[]
    details={'reference_dbfs':reference,'noise_floor_dbfs':noise_floor,
             'activity_threshold_dbfs':threshold}
    if not groups:
        flags.append('No sustained activity confidently detected; retained entire file.')
        return dict(start_seconds=0.,end_seconds=duration,decision='retain_ambiguous',
                    flags=flags,excluded_activity=[],activity_groups=[],**details)
    counts=[int(np.count_nonzero(active[a:b])) for a,b in groups]
    main_index=int(np.argmax(counts));main=groups[main_index]
    selected_end=groups[-1][1];excluded=[]
    later=groups[main_index+1:]
    detached_seconds=sum(np.count_nonzero(active[a:b])*dt for a,b in later)
    main_seconds=counts[main_index]*dt
    if main_index:
        flags.append('Activity exists before the largest group; leading audio is retained by default.')
    if later:
        safe_late=(main_seconds>=cfg.minimum_main_seconds and
                   detached_seconds<=main_seconds*cfg.max_detached_fraction and
                   all((b-a)*dt<=60 for a,b in later))
        if cfg.late_audio=='truncate' or (cfg.late_audio=='drop' and safe_late):
            selected_end=main[1]
            excluded=[{'start_seconds':max(0,a*dt),'end_seconds':min(duration,b*dt+f['window']),
                       'reason':'Detached activity after long gap; main-song policy.'} for a,b in later]
            flags.append('Detached later audio excluded; compare original in report.')
        elif cfg.late_audio=='drop':
            flags.append('Later section too substantial to discard automatically; retained for review.')
        else:flags.append('Detached later audio retained by --late-audio keep.')
    # Follow a connected decay below the structural gate, without jumping across
    # silence to another click/phrase. Quiet steady noise cannot extend the tail.
    end_index=selected_end
    limit=min(n,end_index+round(cfg.maximum_fade_seconds/dt))
    quiet_gate=max(-90,reference-55,noise_floor+6)
    missing=0;last=end_index
    release_frames=max(1,round(.6/dt))
    for j in range(end_index,limit):
        prev=y[max(0,j-round(1/dt)):j+1]
        decay=len(prev)>1 and prev[0]-float(np.median(prev[-max(1,round(.3/dt)):]))>1.5
        tonal=f['flatness'][j]<.20 and f['low_ratio'][j]<.95
        supported=(y[j]>quiet_gate and (tonal or decay or y[j]>threshold))
        # Plateau hiss/rumble must not count as a decay tail.
        if noise_like[j] and not decay:supported=False
        if supported:
            missing=0;last=j+1
        else:
            missing+=1
            if missing>=release_frames:break
    end=min(duration,last*dt+f['window']+cfg.tail_padding_seconds)
    if cfg.late_audio=='truncate' and later:
        # A strict truncation is one contiguous source interval. Neither decay
        # tracking nor the safety fallback may reconnect a later passage.
        end=min(end,later[0][0]*dt)
    # A declared tail cut should not accidentally land inside a rejected short
    # burst. Refuse a boundary if it is still loud; keep the original and report.
    boundary=int(min(n-1,end/dt))
    if end<duration-.1 and y[boundary]>reference-22 and cfg.late_audio!='truncate':
        flags.append('Proposed boundary remains loud; retained entire file for review.')
        end=duration
    start=max(0,groups[0][0]*dt-cfg.head_padding_seconds) if cfg.trim_leading else 0.
    if end>=duration-.1:
        end=duration
        if np.mean(active[max(0,n-round(2/dt)):])>.5:
            flags.append('Sustained activity reaches source end; no confident tail cut.')
    if duration-end<.5:end=duration
    return dict(start_seconds=start,end_seconds=end,decision='trim' if start>0 or end<duration else 'retain',
                flags=flags,excluded_activity=excluded,
                activity_groups=[{'start_seconds':a*dt,'end_seconds':min(duration,b*dt+f['window']),
                                  'active_seconds':c*dt} for (a,b),c in zip(groups,counts)],**details)


def atomic_json(path,value):
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                     prefix='.'+path.name,suffix='.tmp',delete=False) as h:
        tmp=Path(h.name)
        json.dump(value,h,indent=2,ensure_ascii=False,allow_nan=False);h.write('\n');h.flush();os.fsync(h.fileno())
    try:tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)


def join_decision(source, f, cfg, detector):
    """Keep a verified >5 s continuation; shorten only the intervening gap.

    Music scores are evidence, not ground truth. Substantial ambiguous audio is
    retained with its gap for review; known noise and short detached scraps are
    excluded. Internal gaps in the main body are never removed automatically.
    """
    choice=decide(f,replace(cfg,late_audio='drop'))
    groups=choice['activity_groups']
    choice['classification']=[]
    if not groups:
        choice['keep_intervals']=[[choice['start_seconds'],choice['end_seconds']]]
        return choice
    main_index=int(np.argmax([g['active_seconds'] for g in groups]))
    later=groups[main_index+1:]
    if not later:
        choice['keep_intervals']=[[choice['start_seconds'],choice['end_seconds']]]
        return choice
    if groups[main_index]['active_seconds']<cfg.minimum_main_seconds:
        choice['flags'].append('Main section too short to edit internal gaps; retained for review.')
        choice['keep_intervals']=[[0.,f['duration']]]
        return choice
    if detector is None:raise ValueError('A music detector is required for --late-audio join')
    def attached_end(group, ceiling):
        # Follow the same release envelope locally; do not let an earlier main
        # group determine a later passage's decay endpoint.
        dt=f['hop'];y=f['level'];reference=choice['reference_dbfs']
        index=max(0,round((group['end_seconds']-f['window'])/dt))
        limit=min(len(y),round(ceiling/dt),index+round(cfg.maximum_fade_seconds/dt))
        quiet_gate=max(-90,reference-55,choice['noise_floor_dbfs']+6)
        last=index;missing=0
        for j in range(index,limit):
            prev=y[max(0,j-round(1/dt)):j+1]
            decay=len(prev)>1 and prev[0]-float(np.median(prev[-max(1,round(.3/dt)):]))>1.5
            tonal=f['flatness'][j]<.20 and f['low_ratio'][j]<.95
            if y[j]>quiet_gate and (tonal or decay or y[j]>choice['activity_threshold_dbfs']):
                last=j+1;missing=0
            else:
                missing+=1
                if missing>=max(1,round(.6/dt)):break
        return min(ceiling,max(group['end_seconds'],last*dt+f['window'])+cfg.tail_padding_seconds)
    first_end=attached_end(groups[main_index],later[0]['start_seconds'])
    intervals=[[choice['start_seconds'],first_end]]
    choice['flags']=[x for x in choice['flags'] if 'Detached later audio excluded' not in x]
    choice['excluded_activity']=[]
    for i,g in enumerate(later):
        start=max(intervals[-1][1],g['start_seconds']-cfg.head_padding_seconds)
        ceiling=later[i+1]['start_seconds'] if i+1<len(later) else f['duration']
        end=attached_end(g,ceiling)
        result=detector.classify(source,start,end)
        supported=result['confirmed_music_seconds']
        verified=(supported>cfg.minimum_late_music_seconds and result['music_coverage']>=.45)
        result['action']='join' if verified else 'exclude'
        if verified:
            if start-intervals[-1][1]<=cfg.join_pause_seconds:
                # Nothing useful to shorten: keep the tiny gap sample-for-sample.
                intervals[-1][1]=end
            else:intervals.append([start,end])
            choice['flags'].append(f'Kept continuation at {start:.2f}s: {supported:.2f}s classified as music.')
        else:
            noise_labels={'Silence','Noise','Static','White noise','Hum','Buzz'}
            top=result['top_classes'][0]
            confidently_noise=(top['label'] in noise_labels and top['score']>=.65)
            if end-start>cfg.minimum_late_music_seconds+1 and not confidently_noise and supported<=cfg.minimum_late_music_seconds:
                # Uncertain long passages must not be silently discarded.
                intervals[-1][1]=end
                result['action']='retain_ambiguous_with_gap'
                choice['flags'].append(f'Uncertain later audio at {start:.2f}s retained with gap for review.')
            else:
                choice['excluded_activity'].append({'start_seconds':start,'end_seconds':end,
                    'reason':'Not more than five seconds of confidently classified music.'})
                choice['flags'].append(f'Excluded later fragment at {start:.2f}s: {supported:.2f}s classified as music.')
        choice['classification'].append(result)
    choice.update(keep_intervals=intervals,end_seconds=intervals[-1][1],
                  decision='trim_and_join' if len(intervals)>1 else 'trim')
    return choice


def write_audio(source,target,first,last,overwrite=False,intervals=None,pause_seconds=.5,fade_seconds=.005,source_sha256=None):
    """Integer-PCM crop/joins, atomic publication and full decoded readback.

    Straight crops preserve every retained PCM sample exactly. Joins insert
    silence, with 5 ms ramps at the two splice edges to prevent a click.
    """
    if source.resolve()==target.resolve():raise ValueError('Output cannot replace source')
    if target.exists() and not overwrite:raise FileExistsError(f'{target}: use --overwrite explicitly')
    info=sf.info(source)
    if info.subtype not in {'PCM_16','PCM_24','PCM_S8'}:raise ValueError(f'Unsupported FLAC subtype {info.subtype}')
    intervals=intervals or [(first,last)]
    for i,(a,b) in enumerate(intervals):
        if not 0<=a<b<=info.frames:raise ValueError('Invalid sample interval')
        if i and a<intervals[i-1][1]:raise ValueError('Overlapping source intervals')
    gap_frames=round(pause_seconds*info.samplerate)
    total=sum(b-a for a,b in intervals)+gap_frames*(len(intervals)-1)
    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.'+target.name,suffix='.tmp',delete=False) as h:tmp=Path(h.name)
    expected=hashlib.sha256()
    try:
        with sf.SoundFile(source) as src,sf.SoundFile(tmp,'w',samplerate=info.samplerate,
             channels=info.channels,format='FLAC',subtype=info.subtype) as dst:
            def write(b):
                # libsndfile ignores low bits in PCM_24/16. Canonicalise those
                # bits before hashing so fade readback verification is exact.
                shift={'PCM_24':8,'PCM_16':16,'PCM_S8':24}[info.subtype]
                b=((b.astype(np.int64)>>shift)<<shift).astype(np.int32)
                expected.update(b.tobytes());dst.write(b)
            for segment,(a,z) in enumerate(intervals):
                src.seek(a);left=z-a;offset=0
                fade=min(round(fade_seconds*info.samplerate),(z-a)//2)
                while left:
                    b=src.read(min(left,65536),dtype='int32',always_2d=True)
                    if not len(b):raise ValueError('Unexpected source EOF')
                    if fade and len(intervals)>1:
                        pos=np.arange(offset,offset+len(b))
                        gain=np.ones(len(b))
                        if segment:gain*=np.minimum(1,pos/max(1,fade-1))
                        if segment<len(intervals)-1:gain*=np.minimum(1,(z-a-1-pos)/max(1,fade-1))
                        b=np.rint(b.astype(np.float64)*gain[:,None]).astype(np.int32)
                    write(b);left-=len(b);offset+=len(b)
                if segment<len(intervals)-1:
                    for start in range(0,gap_frames,65536):write(np.zeros((min(65536,gap_frames-start),info.channels),dtype=np.int32))
        actual=hashlib.sha256()
        with sf.SoundFile(tmp) as checked:
            if (checked.frames,checked.samplerate,checked.channels,checked.subtype)!=(total,info.samplerate,info.channels,info.subtype):
                raise ValueError('Output format/frame-count verification failed')
            for b in checked.blocks(blocksize=65536,dtype='int32',always_2d=True):actual.update(b.tobytes())
        if actual.digest()!=expected.digest():raise ValueError('Output PCM differs from source interval')
        if source_sha256 is not None and sha256(source)!=source_sha256:
            raise ValueError('Source changed while trimming; output not published')
        if overwrite:tmp.replace(target)
        else:os.link(tmp,target);tmp.unlink()
        return actual.hexdigest()
    finally:tmp.unlink(missing_ok=True)


def trim_file(source,cfg,overwrite=False,dry_run=False,manual_end=None,detector=None,analysis=None,
              output_path=None,report_path=None):
    source=Path(source).resolve()
    prefix='testTrim_truncated_' if cfg.late_audio=='truncate' else 'testTrim_'
    target=Path(output_path).resolve() if output_path is not None else source.with_name(prefix+source.parent.name+'.flac')
    if target == source:raise ValueError('Output must not overwrite the original audio')
    if target.exists() and not overwrite and not dry_run:raise FileExistsError(f'{target}: use --overwrite explicitly')
    before,f=analysis if analysis is not None else (sha256(source),features(source,cfg))
    choice=join_decision(source,f,cfg,detector) if cfg.late_audio=='join' and manual_end is None else decide(f,cfg)
    if manual_end is not None:
        if not 0<manual_end<=f['duration']:raise ValueError('--end must fall inside the file')
        choice.update(end_seconds=manual_end,decision='manual');choice['flags'].append('Manual end override.')
    spans=choice.get('keep_intervals',[[choice['start_seconds'],choice['end_seconds']]])
    intervals=[(math.floor(a*f['samplerate']),min(f['frames'],math.ceil(b*f['samplerate']))) for a,b in spans]
    first,last=intervals[0][0],intervals[-1][1]
    output_frames=sum(b-a for a,b in intervals)+round(cfg.join_pause_seconds*f['samplerate'])*(len(intervals)-1)
    if sha256(source)!=before:raise ValueError('Source changed during analysis')
    report={'version':VERSION,'variant':cfg.late_audio,'source':str(source),'output':str(target),'source_sha256':before,
            'settings':asdict(cfg),'input_seconds':f['duration'],'output_seconds':output_frames/f['samplerate'],
            'removed_seconds':f['duration']-output_frames/f['samplerate'],
            'samplerate':f['samplerate'],'channels':f['channels'],'subtype':f['subtype'],
            'first_frame':first,'last_frame_exclusive':last,'keep_frame_intervals':intervals,
            'joins':len(intervals)-1,'join_pause_seconds':cfg.join_pause_seconds,
            'join_edge_fade_seconds':cfg.join_fade_seconds if len(intervals)>1 else 0.,**choice}
    if detector:report['classifier']=detector.identity
    # 0.5-second summaries keep the HTML/report compact while retaining decisions.
    report['timeline']=[{'time':i*f['hop'],'dbfs':float(f['level'][i]),'flatness':float(f['flatness'][i])}
                        for i in range(0,len(f['level']),max(1,round(.5/f['hop'])))]
    if not dry_run:
        report['pcm_sha256']=write_audio(source,target,first,last,overwrite,intervals,
                                        cfg.join_pause_seconds,cfg.join_fade_seconds,before)
        if sha256(source)!=before:raise ValueError('Source changed while trimming')
        report['output_sha256']=sha256(target)
        atomic_json(Path(report_path) if report_path is not None else target.with_suffix('.json'),report)
    print(f'{source.parent.name} [{cfg.late_audio}]: {f["duration"]:.2f}s -> {report["output_seconds"]:.2f}s; removed {report["removed_seconds"]:.2f}s'+
          (' [REVIEW]' if choice['flags'] else ''),flush=True)
    return report


def write_html(reports,path):
    grouped={}
    for r in reports:grouped.setdefault(r['source'],[]).append(r)
    cards=[]
    labels={'join':'Joined — confirmed later music retained',
            'truncate':'Truncated — main song only',
            'drop':'Trimmed — detached fragments dropped',
            'keep':'Trimmed — later audio and original gaps retained'}
    for source,variants in grouped.items():
        variants.sort(key=lambda r: {'join':0,'truncate':1}.get(r.get('variant'),2))
        first=variants[0];duration=first['input_seconds']
        rel=lambda p:html.escape(os.path.relpath(p,path.parent),quote=True)
        pts=first['timeline']
        xy=' '.join(f'{p["time"]/duration*1000:.2f},{120-np.clip((p["dbfs"]+100)/100,0,1)*110:.2f}' for p in pts)
        rows=[f'''<div class="player"><label>Original · {duration:.2f}s</label>
<audio controls preload="none" data-spans="[[0,{duration}]]" data-pause="0" src="{rel(source)}"></audio>
<button onclick="skip(this,-10)">−10s</button><button onclick="skip(this,10)">+10s</button></div>''']
        boundaries=[]
        for r in variants:
            variant=r.get('variant',r['settings']['late_audio'])
            label=labels.get(variant,variant)
            spans=[(a/r['samplerate'],b/r['samplerate']) for a,b in r['keep_frame_intervals']]
            boundaries.append(spans[0][1])
            removed=[];previous=0.
            for a,b in spans:
                if a>previous:removed.append((previous,a))
                previous=b
            if previous<duration:removed.append((previous,duration))
            shading=''.join(f'<rect x="{a/duration*1000}" y="0" width="{(b-a)/duration*1000}" height="130" fill="#ffdada"/>' for a,b in removed)
            flags=' '.join(r['flags']) or 'No ambiguity flag.'
            attrs=html.escape(json.dumps(spans),quote=True)
            detail=html.escape(json.dumps({k:v for k,v in r.items() if k!='timeline'},indent=2))
            rows.append(f'''<section><div class="player"><label>{html.escape(label)} · {r['output_seconds']:.2f}s · removed {r['removed_seconds']:.2f}s</label>
<audio controls preload="none" data-spans="{attrs}" data-pause="{r['join_pause_seconds']}" src="{rel(r['output'])}"></audio>
<button onclick="skip(this,-10)">−10s</button><button onclick="skip(this,10)">+10s</button></div>
<svg viewBox="0 0 1000 130" role="img" aria-label="{html.escape(label,quote=True)}: audio activity and discarded regions">{shading}
<polyline points="{xy}" fill="none" stroke="black" stroke-width="1"/>
<line x1="{r['end_seconds']/duration*1000}" x2="{r['end_seconds']/duration*1000}" y1="0" y2="130" stroke="red" stroke-width="2"/></svg>
<p>{html.escape(flags)}</p><details><summary>{html.escape(label)}: decision details</summary><pre>{detail}</pre></details></section>''')
        cut=min(boundaries)
        cards.append(f'''<article><h2>{html.escape(Path(source).parent.name)}</h2>
<button onclick="align(this,{max(0,cut-8)})">Compare eight seconds before main ending</button>
<button onclick="align(this,{cut})">Compare at main ending ({cut:.2f}s)</button>
<button onclick="align(this,this.closest('article').querySelector('audio').currentTime)">Align all versions to original position</button>
{''.join(rows)}</article>''')
    page='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>YuE2 trim comparison</title>
<style>body{background:white;color:black;font:16px sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}article{border-top:2px solid black;padding:16px 0}section{margin-top:16px}svg{width:100%;height:130px}audio{vertical-align:middle;width:min(750px,85%);margin:6px 0}label{display:block;font-weight:bold}button{background:white;color:black;border:1px solid black;padding:8px;margin:6px 4px 6px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>Original / joined / truncated</h1><p>Joined versions retain later music confirmed by the classifier, with 0.5-second gaps by default. Truncated versions stop after the main song, retaining its attached fade and padding. Shaded regions show discarded source audio. Each player has independent controls; only one plays at a time.</p>
'''+''.join(cards)+'''<script>
function pauseAll(){document.querySelectorAll('audio').forEach(a=>a.pause())}
function seekTo(a,t){if(a.readyState)a.currentTime=Math.max(0,Math.min(t,a.duration||t));else{a.addEventListener('loadedmetadata',()=>a.currentTime=Math.max(0,Math.min(t,a.duration)),{once:true});a.load()}}
function mapped(a,sourceTime){let offset=0;const spans=JSON.parse(a.dataset.spans);const pause=Number(a.dataset.pause);for(let i=0;i<spans.length;i++){const [s,e]=spans[i];if(sourceTime<s)return offset;if(sourceTime<=e)return offset+sourceTime-s;offset+=e-s;if(i<spans.length-1)offset+=pause;}return offset}
function align(button,sourceTime){pauseAll();button.closest('article').querySelectorAll('audio').forEach(a=>seekTo(a,mapped(a,sourceTime)))}
function skip(button,delta){const a=button.closest('.player').querySelector('audio');seekTo(a,Math.max(0,a.currentTime+delta))}
document.addEventListener('play',e=>{if(e.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
</script>'''
    path.write_text(page,encoding='utf-8')



def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('input',type=Path,help='audio.flac or a run directory; directories search audio.flac recursively')
    ap.add_argument('--late-audio',choices=['both','join','truncate','drop','keep'],default='both',help='Write joined and truncated versions, or select one policy')
    ap.add_argument('--gap',type=float,default=2,help='Seconds separating late activity into detached sections')
    ap.add_argument('--join-pause',type=float,default=.5,help='Silence inserted between retained passages')
    ap.add_argument('--model-dir',type=Path,help='Pinned YAMNet ONNX files')
    ap.add_argument('--no-model-download',action='store_true',help='Require model files already cached locally')
    ap.add_argument('--trim-leading',action='store_true',help='Also trim leading inactivity')
    ap.add_argument('--end',type=float,help='Manual end in seconds, for a single file only')
    ap.add_argument('--dry-run',action='store_true',help='Analyse only; write no files')
    ap.add_argument('--overwrite',action='store_true',help='Replace existing testTrim outputs')
    a=ap.parse_args(argv)
    if not math.isfinite(a.gap) or a.gap<2:ap.error('--gap must be finite and at least two seconds')
    if a.end is not None and not math.isfinite(a.end):ap.error('--end must be finite')
    if not math.isfinite(a.join_pause) or not 0<=a.join_pause<=2:ap.error('--join-pause must be between zero and two seconds')
    files=sorted(a.input.rglob('audio.flac')) if a.input.is_dir() else [a.input]
    if not files or any(not p.is_file() for p in files):ap.error('No input audio.flac files found')
    if not a.dry_run and not a.overwrite:
        policies=['join','truncate'] if a.late_audio=='both' else [a.late_audio]
        existing=[p.with_name(('testTrim_truncated_' if policy=='truncate' else 'testTrim_')+p.parent.name+'.flac')
                  for p in files for policy in policies]
        existing=[p for p in existing if p.exists()]
        if existing:ap.error(f'Output exists: {existing[0]}; use --overwrite to replace testTrim outputs')
    if a.end is not None and len(files)!=1:ap.error('--end requires a single file')
    cfg=Settings(detached_gap_seconds=a.gap,late_audio=a.late_audio,trim_leading=a.trim_leading,join_pause_seconds=a.join_pause)
    detector=None
    if a.late_audio in {'join','both'} and a.end is None:
        from trim_music import MusicDetector
        detector=MusicDetector(a.model_dir,not a.no_model_download)
    reports=[];failures=[]
    policies=['join','truncate'] if a.late_audio=='both' else [a.late_audio]
    for p in files:
        try:analysis=(sha256(p),features(p,cfg))
        except Exception as exc:
            failures.append({'source':str(p),'error':str(exc)});print(f'ERROR {p}: {exc}',flush=True)
            continue
        for policy in policies:
            try:reports.append(trim_file(p,replace(cfg,late_audio=policy),a.overwrite,a.dry_run,a.end,
                                         detector if policy=='join' else None,analysis))
            except Exception as exc:
                failures.append({'source':str(p),'variant':policy,'error':str(exc)})
                print(f'ERROR {p} [{policy}]: {exc}',flush=True)
    if not a.dry_run:
        parent=a.input.resolve() if a.input.is_dir() else a.input.resolve().parent
        atomic_json(parent/'testTrim_report.json',{'version':VERSION,'files':reports,'failures':failures})
        if reports:write_html(reports,parent/'testTrim_review.html')
    return 1 if failures else 0

if __name__=='__main__':raise SystemExit(main())
