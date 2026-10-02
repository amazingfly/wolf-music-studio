"""This local file is sent through colab exec. Setup runs detached."""
import json, subprocess, sys, time
from pathlib import Path
root = Path('/content/yue2')
root.mkdir(exist_ok=True)
old=Path('/content/yue2-exit.json')
if old.exists(): old.rename(old.with_name(f'yue2-exit-{time.time_ns()}.json'))
subprocess.run(['tar','xzf','/content/yue2-kit.tar.gz','-C',str(root)],check=True)
with open('/content/yue2-outputs.log','ab',buffering=0) as log:
    p = subprocess.Popen([sys.executable,str(root/'remote_worker.py')],stdout=log,
                         stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
context=json.loads((root/'run_context.json').read_text()) if (root/'run_context.json').exists() else {}
Path('/content/yue2-worker.json').write_text(json.dumps({'pid':p.pid,'run_name':context.get('run_name')}))
print(f'YuE2 bootstrap started: PID {p.pid}',flush=True)
