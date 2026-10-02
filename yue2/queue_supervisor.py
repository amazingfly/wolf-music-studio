#!/usr/bin/env python3
"""Persistent directory queue; reuse Colab between configs and release it when idle."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from run_config import ROOT, create_run, load_run, seed_sweep_count


def write(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)


def next_job(queue):
    def started(job):
        try: return json.loads((job/'job.json').read_text()).get('started_at',0)
        except (OSError,ValueError): return 0
    existing=sorted((queue/'running').iterdir(),key=started)
    if existing:
        return existing[0]
    pending=sorted((queue/'pending').glob('*.json'),key=lambda p:(p.stat().st_mtime_ns,p.name))
    for source in pending:
        if time.time()-source.stat().st_mtime < 5:
            continue  # Avoid picking up an in-progress copy; .tmp + rename is best.
        job=queue/'running'/uuid.uuid4().hex
        job.mkdir()
        (job/'input').mkdir()
        source.rename(job/'input'/source.name)
        return job
    return None


def prepare_job(job, seed_sweep=1):
    record=job/'job.json'
    if record.exists():
        value=json.loads(record.read_text())
        load_run(value['manifest'])
        return value
    configs=list((job/'input').glob('*.json'))
    if len(configs)!=1:
        raise ValueError('Queue job must contain exactly one config')
    manifest=create_run(configs[0], seed_sweep=seed_sweep)
    value={'manifest':str(manifest),'config':configs[0].name,'started_at':time.time()}
    write(record,value)
    return value


def release(session):
    result=subprocess.run(['colab','stop','-s',session],text=True,capture_output=True,timeout=90)
    if result.returncode and 'not found' not in (result.stdout+result.stderr).lower():
        raise RuntimeError('Could not release idle Colab session: '+result.stderr[-300:])


def run_job(manifest, session, gpu):
    child=subprocess.Popen([sys.executable,str(ROOT/'colab_supervisor.py'),'--resume',str(manifest),
                            '--session',session,'--reuse-session','--keep-session','--gpu',gpu])
    while child.poll() is None:
        # Also keep the tunnel active during long verified archive downloads.
        try:
            subprocess.run([os.environ.get('YUE2_COLAB_PYTHON', sys.executable),
                            str(ROOT/'colab_tunnel_keepalive.py'),'--session',session],
                           capture_output=True,timeout=45)
        except (OSError,subprocess.TimeoutExpired):
            pass
        try:
            return child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    return child.returncode


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'queue')
    parser.add_argument('--session',default='yue2-directory-queue')
    parser.add_argument('--gpu',default='T4')
    parser.add_argument('--poll-seconds',type=float,default=10)
    parser.add_argument('--seedSweep','--seed-sweep',type=seed_sweep_count,default=1,
                        help='Total seeds per song for new jobs only (default: 1); existing runs stay frozen')
    args=parser.parse_args()
    queue=args.directory.expanduser().resolve()
    for name in ('pending','running','done','failed'):
        (queue/name).mkdir(parents=True,exist_ok=True)
    lock=(queue/'supervisor.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    idle_released=False
    while True:
        try:
            job=next_job(queue)
            if job is None:
                if not list((queue/'pending').glob('*.json')) and not idle_released:
                    release(args.session)
                    idle_released=True
                    print('Queue empty: Colab released; watching for new JSON configs',flush=True)
                write(queue/'state.json',{'status':'waiting_for_config','gpu_released':idle_released,'time':time.time()})
                time.sleep(args.poll_seconds)
                continue
            idle_released=False
            try:
                info=prepare_job(job, seed_sweep=args.seedSweep)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                write(job/'error.json',{'error':str(exc),'time':time.time()})
                job.rename(queue/'failed'/job.name)
                print(f'Invalid queued config: {exc}',flush=True)
                continue
            manifest=Path(info['manifest'])
            write(queue/'state.json',{'status':'running','manifest':str(manifest),'session':args.session,'time':time.time()})
            print(f'Processing {info["config"]}: {manifest.parent}',flush=True)
            code=run_job(manifest,args.session,args.gpu)
            info.update(finished_at=time.time(),return_code=code)
            write(job/'job.json',info)
            if code:
                retries=info.get('retries',0)+1
                info.update(retries=retries)
                write(job/'job.json',info)
                write(queue/'state.json',{'status':'retrying_current_config','manifest':str(manifest),
                                         'return_code':code,'retries':retries,'time':time.time()})
                print(f'{info["config"]} interrupted (exit {code}); retaining current job for resume',flush=True)
                time.sleep(min(60*retries,300))
                continue
            job.rename(queue/'done'/job.name)
            # Loop immediately: pending work reuses the same VM. Empty queue releases it.
        except Exception as exc:
            write(queue/'state.json',{'status':'retry_pending','error':str(exc),'time':time.time()})
            print(f'Queue supervisor: {exc}',flush=True)
            time.sleep(args.poll_seconds)


if __name__=='__main__':
    main()
