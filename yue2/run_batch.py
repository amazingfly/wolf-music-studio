#!/usr/bin/env python3
"""Durable sequential YuE2 runner for a single Colab allocation."""
from __future__ import annotations
import argparse, json, os, shutil, time, gc
from pathlib import Path
import numpy as np
from yue2.storage import identity, sha256_file

def array_atomic(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('wb') as f:
        np.save(f, value, allow_pickle=False)
    temp.replace(path)

def memory(out, stage):
    import torch
    torch.cuda.synchronize()
    report = {'stage': stage, 'gpu': torch.cuda.get_device_name(),
              'allocated_gib': torch.cuda.memory_allocated()/2**30,
              'peak_allocated_gib': torch.cuda.max_memory_allocated()/2**30,
              'reserved_gib': torch.cuda.memory_reserved()/2**30, 'time': time.time()}
    atomic(out/'memory.json', report)
    print(json.dumps(report), flush=True)
    torch.cuda.reset_peak_memory_stats()

def atomic(path: Path, value: object):
    tmp=path.with_name('.'+path.name+'.tmp'); tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n'); tmp.replace(path)

def run_one(pipe, req, out):
    out.mkdir(parents=True, exist_ok=True)
    request_identity = identity({'request': req, 'weights': pipe.weights,
                                 'config': pipe.effective_config(pipe._request(**{k:req[k] for k in ('id','style','lyrics','cot','seed')}))})
    receipt = out/'request_identity.json'
    if receipt.exists() and json.loads(receipt.read_text())['identity'] != request_identity:
        raise ValueError('Saved run configuration differs; use a new output directory')
    atomic(receipt, {'identity': request_identity})
    if (out/'result.json').is_file():
        saved=json.loads((out/'result.json').read_text())
        if saved.get('status') != 'complete':
            raise ValueError('Saved completion receipt is not complete')
        if (out/'audio.flac').is_file():
            if saved.get('audio_sha256') != sha256_file(out/'audio.flac'):
                raise ValueError('Completed audio checksum mismatch')
            atomic(out/'request.json', req)
            return {'id':req['id'],'status':'already_complete'}
        # An interrupted Drive upload can restore the tiny receipt before its
        # audio. Preserve that receipt, then resume from saved plan/tokens/latents.
        atomic(out/'result.recovery.json', {'reason':'Completed receipt restored without audio.flac',
                                         'previous_result':saved,'recovered_at':time.time()})
        (out/'result.json').unlink()
        print(f'Recovering {req["id"]}: missing audio.flac; resuming saved stages',flush=True)
    from yue2 import SymbolicPlan, SemanticResult
    memory(out, 'start')
    plan_dir=out/'plan'
    if (plan_dir/'plan_manifest.json').is_file(): plan=SymbolicPlan.load(plan_dir)
    else:
        plan=pipe.plan(**{k:req[k] for k in ('id','style','lyrics','cot','seed')}); plan.save(plan_dir)
    semantic_path=out/'semantic.npy'
    progress_path=out/'semantic-progress.json'
    latest=[]
    def checkpoint(phase, token):
        if phase!='semantic': return
        latest.append(int(token))
        if len(latest)%250==0:
            array_atomic(out/'semantic-progress.npy', np.asarray(latest,dtype=np.int32))
            atomic(progress_path, {'status':'in_progress','phase':'semantic','tokens':len(latest),'checkpoint_interval':250,'updated_at':time.time(),'note':'Replay-safe checkpoint; YuE2 official API has no native KV-cache restore.'})
            print(f'Semantic progress: {len(latest)}/9000 tokens ({len(latest)/25:.1f}s of music)',flush=True)
    if semantic_path.is_file():
        tokens=np.load(semantic_path,allow_pickle=False).astype(np.int32).tolist()
        semantic=SemanticResult(plan,tokens,{},False)
    else:
        semantic=pipe.generate_semantic(plan,on_token=checkpoint)
        array_atomic(semantic_path,np.asarray(semantic.tokens,dtype=np.int32))
        atomic(progress_path, {'status':'semantic_complete','tokens':len(semantic.tokens),'truncated':semantic.truncated})
    latent_path=out/'latent.npy'
    memory(out, 'semantic_complete')
    if latent_path.is_file(): latents=np.load(latent_path,allow_pickle=False)
    else:
        latents=pipe.synthesize(semantic)
        if not np.isfinite(latents).all(): raise FloatingPointError('Non-finite acoustic latents')
        array_atomic(latent_path,latents.astype(np.float32))
    if not np.isfinite(latents).all(): raise FloatingPointError('Invalid saved acoustic latents')
    memory(out, 'synthesis_complete')
    audio_path=out/'audio.flac'
    if not audio_path.is_file():
        import soundfile as sf
        audio=pipe.decode(latents)
        if not np.isfinite(audio).all() or float(np.max(np.abs(audio))) < 1e-6:
            raise FloatingPointError('Non-finite or silent audio')
        sf.write(str(audio_path)+'.tmp',audio,48000,subtype='PCM_24',format='FLAC')
        Path(str(audio_path)+'.tmp').replace(audio_path)
    seconds=0.0
    import soundfile as sf
    with sf.SoundFile(audio_path) as f: seconds=len(f)/f.samplerate
    limits=req.get('duration_validation',{'min_seconds':250,'max_seconds':380})
    if not limits['min_seconds'] <= seconds <= limits['max_seconds']:
        raise ValueError(f'Unexpected duration {seconds:.3f}s for 360s trial')
    memory(out, 'decode_complete')
    # Downloaded with the audio; the local archive hook imports this exact prompt
    # into the track catalog (the GPU worker does not share the local SQLite DB).
    atomic(out/'request.json', req)
    atomic(out/'result.json',{'status':'complete','id':req['id'],'audio_seconds':seconds,'semantic_tokens':len(semantic.tokens),'truncated':{'abc':plan.truncated,'semantic':semantic.truncated},'target_seconds':req.get('target_seconds',360),'finished_at':time.time(),'audio_sha256':sha256_file(audio_path),'identity':request_identity,'precision_trial':'float16'})
    pipe.close(); gc.collect()
    return {'id':req['id'],'status':'complete','audio_seconds':seconds}

def main():
    from yue2 import YuE2Pipeline
    ap=argparse.ArgumentParser(); ap.add_argument('--requests',default='/content/yue2/requests/batch.jsonl'); ap.add_argument('--output',default='/content/yue2-outputs'); ap.add_argument('--model',default='m-a-p/YuE2-3B'); ap.add_argument('--vae',default='m-a-p/YuE2-Vae'); ap.add_argument('--cache-dir',default='/content/hf-cache'); args=ap.parse_args()
    rows=[json.loads(x) for x in Path(args.requests).read_text().splitlines() if x.strip()]
    selected=os.environ.get('YUE2_SONG_ID')
    if selected:
        rows=[row for row in rows if row['id']==selected]
        if len(rows)!=1: raise ValueError('Selected song ID is missing or duplicated')
    cfg={'semantic':{'min_tokens':9000,'max_tokens':9000},'abc':{'max_tokens':4096}}
    from yue2.protocol import GenerationConfig
    with YuE2Pipeline.from_pretrained(args.model,vae=args.vae,
            revision='29b3558dd46954a0cd9021dc76d5c91864a0f1c7',
            vae_revision='9a94e1d0ea9f8087e98f77fa88df4a4068104d2a',
            cache_dir=args.cache_dir,device='cuda',memory_budget_gib=16,
            model_dtype='float16',backend='torch-eager',offload_ar=True,
            release_before_decode=True,query_chunk_size=256,vae_core_frames=128,
            generation_config=GenerationConfig.from_dict(cfg),progress=False) as pipe:
        for n,row in enumerate(rows,1):
            print(f'YUЕ2 SONG {n}/{len(rows)} {row["id"]}',flush=True)
            try: print(json.dumps(run_one(pipe,row,Path(args.output)/row['id'])),flush=True)
            except Exception as exc:
                atomic(Path(args.output)/row['id']/'failure.json',{'status':'failed','type':type(exc).__name__,'reason':str(exc)}); print(f'FAILED {row["id"]}: {exc}',flush=True)
                raise
if __name__=='__main__': main()
