#!/usr/bin/env python3
"""Quiet persistent T4 allocation and detached-worker supervision."""
import argparse, configparser, json, subprocess, tempfile, time, tarfile, os, sys, shutil
from pathlib import Path
from archive_run import archive_if_complete
import archive_run
from run_config import create_run, load_run
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'colab_state.json'
MANIFEST=None
LOG=ROOT/'colab_run.log'
CLI_PYTHON=os.environ.get('YUE2_COLAB_PYTHON', sys.executable)

def call(*args,**kwargs):
    return subprocess.run(args,text=True,capture_output=True,timeout=kwargs.pop('timeout',90),**kwargs)

def state(**value):
    temp=STATE.with_suffix('.tmp'); temp.write_text(json.dumps({'updated_at':time.time(),**value},indent=2)); temp.replace(STATE)

def probe(session):
    code="""import json
from pathlib import Path
p=Path('/content/yue2-exit.json'); w=Path('/content/yue2-worker.json')
worker=json.loads(w.read_text()) if w.exists() else {}
pid=worker.get('pid')
print('YUE2_STATUS='+json.dumps({'exit':json.loads(p.read_text()) if p.exists() else None,'alive':bool(pid and Path(f'/proc/{pid}').exists()),'run_name':worker.get('run_name')}))
"""
    result=call('colab','exec','-s',session,'--timeout','30',input=code)
    for line in result.stdout.splitlines():
        if line.startswith('YUE2_STATUS='): return json.loads(line.split('=',1)[1])
    return None

def pack_run(archive):
    with tarfile.open(archive,'w:gz') as bundle:
        for name in ('src','pyproject.toml','README.md','LICENSE','MODEL_LICENSE',
                     'THIRD_PARTY_NOTICES.md','licenses','run_batch.py','remote_worker.py','rclone_snapshot.py','run_config.py'):
            bundle.add(ROOT/name,arcname=name,filter=lambda member: None if '__pycache__' in member.name else member)
        bundle.add(archive_run.REQUESTS,arcname='requests/batch.jsonl')
        if MANIFEST:
            bundle.add(MANIFEST,arcname='run_context.json')

def launch(session):
    with tempfile.TemporaryDirectory(prefix='yue2-kit-') as directory:
        archive=str(Path(directory)/'kit.tar.gz')
        pack_run(archive)
        source=configparser.RawConfigParser(); source.read(os.environ.get('RCLONE_CONFIG', str(Path.home()/'.config/rclone/rclone.conf')))
        from run_config import DRIVE_BASE
        drive_base=json.loads(MANIFEST.read_text()).get('drive_base', DRIVE_BASE) if MANIFEST else DRIVE_BASE
        remote_name=drive_base.split(':',1)[0].split(',',1)[0]
        if remote_name not in source: raise RuntimeError(f'Configure rclone remote {remote_name!r} before starting the queue')
        config=configparser.RawConfigParser(); config[remote_name]=dict(source[remote_name])
        credentials=Path(directory)/'rclone.conf'
        with credentials.open('w') as f: config.write(f)
        credentials.chmod(0o600)
        for local,remote in ((archive,'/content/yue2-kit.tar.gz'),(str(credentials),'/content/yue2-rclone.conf')):
            if call('colab','upload','-s',session,local,remote,timeout=180).returncode:
                raise RuntimeError('Colab kit upload failed')
        result=call('colab','exec','-s',session,'--timeout','30','--file',str(ROOT/'start_colab.py'))
        if result.returncode or not probe(session): raise RuntimeError('Remote bootstrap did not start')

def main():
    global MANIFEST,STATE,LOG
    ap=argparse.ArgumentParser(); ap.add_argument('--gpu',default='T4'); ap.add_argument('--session')
    select=ap.add_mutually_exclusive_group()
    select.add_argument('--config',type=Path,help='Start a new timestamped run from JSON')
    select.add_argument('--resume',type=Path,help='Resume an existing run.json without changing its timestamp')
    ap.add_argument('--retry-delay',type=float,default=30)
    ap.add_argument('--token-cycle',action='store_true')
    ap.add_argument('--keep-session',action='store_true',help='Queue controller releases the VM after archiving')
    ap.add_argument('--reuse-session',action='store_true',help='Allow a queue-owned session across configs')
    args=ap.parse_args(); attempt=0; startup_failures=0
    if args.token_cycle and not os.environ.get('YUE2_TOKEN_CYCLE_SCRIPT'):
        ap.error('Token cycling requires YUE2_TOKEN_CYCLE_SCRIPT; default authentication never cycles tokens')
    if args.config or args.resume:
        MANIFEST=create_run(args.config) if args.config else args.resume.expanduser().resolve()
        context=load_run(MANIFEST)
        archive_run.configure(MANIFEST)
        STATE=MANIFEST.parent/'colab_state.json'
        LOG=MANIFEST.parent/'colab_run.log'
        if args.session and args.session != context['session'] and not args.reuse_session:
            ap.error('--session must match the saved run session when using --config/--resume')
        args.session=args.session or context['session']
        print(f'YuE2 run: {context["run_name"]}; outputs: {MANIFEST.parent}',flush=True)
    else:
        args.session=args.session or 'yue2-t4-trial'
    def finish_queue():
        if args.keep_session and not archive_run.local_complete():
            remote=probe(args.session)
            # Wait for the final uploader to exit before removing its Drive files.
            if remote and remote['alive'] and not remote['exit']:
                return False
        def stop_session():
            if args.keep_session:
                remote=probe(args.session)
                if remote and remote['alive'] and not remote['exit']:
                    raise RuntimeError('Worker still writing final backup')
                return
            stopped=call('colab','stop','-s',args.session)
            if stopped.returncode and 'not found' not in (stopped.stdout+stopped.stderr).lower():
                raise RuntimeError('Could not stop queue before Drive cleanup')
        if archive_if_complete(before_delete=stop_session):
            state(status='complete',session=args.session,outputs=str(archive_run.LOCAL),drive_deleted=True)
            return True
        return False
    harvest_due=True  # Recover tracks from an allocation lost before a restart.
    while True:
        try:
            if finish_queue(): return 0
            if harvest_due:
                state(status='downloading_completed_tracks',session=args.session)
                archive_run.download_completed()
                harvest_due=False
            status=probe(args.session)
            if status and MANIFEST and status.get('run_name') != context['run_name']:
                if status['alive'] and not status['exit']:
                    raise RuntimeError('Shared Colab session is still running another config')
                launch(args.session)
                status=probe(args.session)
            if not status:
                attempt+=1; state(status='waiting_for_allocation',gpu=args.gpu,attempt=attempt)
                created=call('colab','new','--session',args.session,'--gpu',args.gpu)
                if created.returncode or 'rejected' in created.stdout.lower():
                    state(status='waiting_for_allocation',gpu=args.gpu,attempt=attempt,
                          last_error=(created.stderr or created.stdout)[-800:])
                    if args.token_cycle:
                        call(CLI_PYTHON,os.environ['YUE2_TOKEN_CYCLE_SCRIPT'],timeout=180)
                    time.sleep(args.retry_delay); continue
                print(f'ALLOCATED {args.session} {args.gpu}',flush=True); launch(args.session)
            elif not status['alive'] and not status['exit']:
                launch(args.session)
            harvest_due=True  # Harvest when monitoring returns after disconnect.
            failures=0
            while True:
                if finish_queue(): return 0
                status=probe(args.session)
                if status:
                    failures=0; state(status='running',gpu=args.gpu,session=args.session,remote=status)
                    call('colab','download','-s',args.session,'/content/yue2-outputs.log',str(LOG),timeout=60)
                    if status['exit']:
                        rc=status['exit']['return_code']
                        if rc==0:
                            state(status='awaiting_verified_archive',session=args.session)
                            time.sleep(30)
                            continue
                        if rc in {-9,-15,137,143,247}:
                            state(status='recovering_interruption',return_code=rc,session=args.session)
                            archive_run.download_completed()
                            print(f'Worker interrupted ({rc}); resuming current config from saved stages',flush=True)
                            time.sleep(30)
                            launch(args.session)
                            continue
                        if rc and status['exit'].get('phase','setup') in {'setup','backup'}:
                            startup_failures+=1
                            state(status='startup_recovery',session=args.session,failures=startup_failures,remote=status)
                            print(f'YuE2 {status["exit"].get("phase","setup")} failure; retrying with saved files',flush=True)
                            time.sleep(min(60*startup_failures,600))
                            launch(args.session)
                            continue
                        state(status='complete' if rc==0 else 'failed',return_code=rc,session=args.session)
                        archive_run.download_completed()
                        print(f'YuE2 worker exited: {rc}; see colab_run.log',flush=True)
                        return rc
                    if not status['alive']: raise RuntimeError('Colab worker disappeared')
                else:
                    failures+=1
                    if failures>=4: break
                call(CLI_PYTHON,str(ROOT/'colab_tunnel_keepalive.py'),'--session',args.session)
                time.sleep(30)
        except Exception as exc:
            state(status='retry_pending',reason=str(exc),gpu=args.gpu); time.sleep(args.retry_delay)

if __name__=='__main__': raise SystemExit(main())
