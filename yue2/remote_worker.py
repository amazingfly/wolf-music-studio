"""Detached setup, direct Drive restore/backup and GPU worker lifecycle."""
import json, os, subprocess, sys, threading, time, tarfile, urllib.request, shutil, signal, hashlib
from pathlib import Path
ROOT=Path('/content/yue2')
OUTPUT=Path('/content/yue2-outputs')
DRIVE=os.environ.get('YUE2_DRIVE_BASE', 'gDrive:yue2/').rstrip('/') + '/industrial_360s_t4'
if (ROOT/'run_context.json').is_file():
    from run_config import component, DRIVE_BASE
    context=json.loads((ROOT/'run_context.json').read_text())
    run_name=component(context['run_name'])
    DRIVE=context.get('drive_base', DRIVE_BASE).rstrip('/') + '/' + run_name
    OUTPUT=OUTPUT/run_name
CONFIG='/content/yue2-rclone.conf'
stop=threading.Event()
phase='setup'
TRANSFER_TIMEOUT=1800

def run_transfer(command):
    process=subprocess.Popen(command,start_new_session=True)
    try:
        return process.wait(timeout=TRANSFER_TIMEOUT)
    except BaseException:
        # subprocess.run(timeout=...) killed only the wrapper and could leave
        # rclone uploading in the background alongside the next retry.
        try: os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
        raise

def create_environment():
    # Colab's /usr/bin/python can lack working ensurepip. uv supplies its own
    # environment creation and a managed Python, independent of distro venv.
    uv=Path('/content/yue2-uv')
    if not uv.is_file():
        archive=Path('/content/yue2-uv.tar.gz')
        urllib.request.urlretrieve(
            'https://github.com/astral-sh/uv/releases/download/0.11.19/uv-x86_64-unknown-linux-gnu.tar.gz', archive)
        with tarfile.open(archive) as bundle:
            with bundle.extractfile('uv-x86_64-unknown-linux-gnu/uv') as source, uv.open('wb') as target:
                shutil.copyfileobj(source,target)
        uv.chmod(0o755)
    destination=Path('/content/yue2-python312')
    if not (destination/'bin/python').is_file():
        subprocess.run([str(uv),'venv','--python','3.12','--managed-python','--seed',str(destination)],check=True)
    python=str(destination/'bin/python')
    subprocess.run([python,'-m','pip','--version'],check=True)
    return python

def sync(direction):
    source,target=(str(OUTPUT),DRIVE) if direction=='push' else (DRIVE,str(OUTPUT))
    return run_transfer([sys.executable,str(ROOT/'rclone_snapshot.py'),'--config',CONFIG,'copy',source,target,'--exclude','*.tmp',
                         '--transfers','2','--checkers','2','--retries','2','--timeout','120s'])

def backup():
    while not stop.wait(45):
        try:
            if sync('push'): print('Drive backup pending: transfer failed',flush=True)
        except Exception as exc: print(f'Drive backup pending: {exc}',flush=True)

def main():
    global phase
    OUTPUT.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.update(PYTHONUNBUFFERED='1',PIP_NO_CACHE_DIR='1',TOKENIZERS_PARALLELISM='false')
    python=create_environment()
    environment_key=hashlib.sha256((ROOT/'pyproject.toml').read_bytes()).hexdigest()
    ready=Path('/content/yue2-environment-ready.json')
    reuse=ready.exists() and ready.read_text()==environment_key
    if reuse:
        reuse=subprocess.run([python,'-c','import torch, transformers, yue2; assert torch.cuda.is_available()'],env=env).returncode==0
    if not reuse:
        print('Installing isolated YuE2 environment',flush=True)
        subprocess.run([python,'-m','pip','install','torch==2.10.0','--index-url',
                        'https://download.pytorch.org/whl/cu128'],check=True,env=env)
        subprocess.run([python,'-m','pip','install','-e',str(ROOT)],check=True,env=env)
        subprocess.run(['apt-get','update','-qq'],check=True)
        subprocess.run(['apt-get','install','-y','-qq','rclone'],check=True)
        ready.write_text(environment_key)
    else:
        print('Reusing installed environment and cached model files on this Colab VM',flush=True)
    if not Path(CONFIG).is_file(): raise RuntimeError('Missing Drive credentials')
    subprocess.run(['rclone','--config',CONFIG,'mkdir',DRIVE],check=True)
    if sync('pull'): raise RuntimeError('Drive restore failed')
    thread=threading.Thread(target=backup,daemon=True); thread.start()
    try:
        phase='generation'
        rows=[json.loads(line) for line in (ROOT/'requests/batch.jsonl').read_text().splitlines() if line.strip()]
        for index,row in enumerate(rows,1):
            print(f'Isolated song process {index}/{len(rows)}: {row["id"]}',flush=True)
            song_env={**env,'YUE2_SONG_ID':row['id']}
            code=subprocess.run([python,str(ROOT/'run_batch.py'),'--output',str(OUTPUT)],env=song_env).returncode
            if code: return code
        return 0
    finally:
        stop.set(); thread.join()
        if sync('push'):
            phase='backup'
            raise RuntimeError('Final Drive backup failed')

if __name__=='__main__':
    code=1
    try: code=main()
    finally: Path('/content/yue2-exit.json').write_text(json.dumps({'return_code':code,'time':time.time(),'phase':phase}))
    raise SystemExit(code)
