#!/usr/bin/env python3
"""Install, inspect and operate the complete wolf music workflow from one checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT/'local/settings.json'
DEFAULTS = json.loads((ROOT/'studio.json.example').read_text())
WORKERS = {'queue': 'queue_supervisor.py', 'visualizer': 'visualizer_queue.py',
           'songwriter': 'gemma_songwriter.py', 'report': 'compareSongwriters.py'}
TOOLS = {'library':'trackLibrary.py', 'make-config':'makeConfig.py', 'trim':'processTracks.py',
         'songwriter':'gemma_songwriter.py', 'compare':'compareSongwriters.py',
         'visualize':'visualizer_queue.py', 'validate':'checkSongPrompts.py',
         'tag':'outputs/trackTags.py', 'start-run':'start_run.py'}


def settings():
    value = dict(DEFAULTS)
    if CONFIG.exists():
        custom = json.loads(CONFIG.read_text())
        if not isinstance(custom,dict) or set(custom)-set(DEFAULTS):
            raise ValueError('Unknown settings; use studio.json.example as the template')
        if any(not isinstance(v,str) or not v for v in custom.values()):
            raise ValueError('Settings must be nonempty strings')
        value.update(custom)
    if not value['drive_base'].count(':') or '\n' in value['drive_base']:
        raise ValueError('drive_base must be an rclone remote path, e.g. gDrive:yue2/')
    if int(value['gemma_gpu_layers']) < 0 or int(value['gemma_context']) < 8192:
        raise ValueError('Gemma layers must be nonnegative; context must be at least 8192 for the references')
    if not value['campaign'].replace('-','').replace('_','').isalnum():
        raise ValueError('Campaign name must use letters, digits, underscores or hyphens')
    return value


def path(value):
    value = Path(value).expanduser()
    # Interpreter symlinks MUST stay inside their venv path; resolving them runs base Python.
    return Path(os.path.abspath(value if value.is_absolute() else ROOT/value))


def environment(config=None):
    config = config or settings()
    env = dict(os.environ)
    values = {'YUE2_ROOT':str(ROOT/'yue2'), 'YUE2_DRIVE_BASE':config['drive_base'],
        'YUE2_GEMMA_ROOT':str(path(config['gemma_root'])),
        'YUE2_GEMMA_GPU_LAYERS':config['gemma_gpu_layers'], 'YUE2_GEMMA_CONTEXT':config['gemma_context'],
        'YUE2_VIS_ROOT':str(path(config['visualizer_root'])),
        'YUE2_VIS_PYTHON':str(path(config['visualizer_python'])),
        'YUE2_TAG_PYTHON':str(path(config['controller_python'])),
        'YUE2_COLAB_PYTHON':str(path(config['colab_python'])),
        'YUE2_COMPUTE_ROOT':str(path(config['compute_root'])),
        'RCLONE_CONFIG':str(path(config['rclone_config']))}
    env.update(values)
    env['PATH'] = str(path(config['controller_python']).parent)+os.pathsep+env.get('PATH','')
    env['PYTHONUNBUFFERED']='1'
    return env


def execute(arguments,config=None,cwd=None):
    return subprocess.run([str(v) for v in arguments],check=True,
                          cwd=cwd or ROOT,env=environment(config))


def initialize():
    CONFIG.parent.mkdir(exist_ok=True)
    if not CONFIG.exists():
        # Exclusive creation avoids replacing an operator's changes.
        with CONFIG.open('x') as handle:json.dump(DEFAULTS,handle,indent=2);handle.write('\n')
    for area in ['pending','running','done','failed','cancelled']:
        (ROOT/'yue2/queue'/area).mkdir(parents=True,exist_ok=True)
    for directory in ['yue2/outputs','yue2/songwriter/runs','gemma/models','visualizer/models']:
        (ROOT/directory).mkdir(parents=True,exist_ok=True)
    print('Local settings:',CONFIG)


def pip_install(python,arguments):
    probe=subprocess.run([str(python),'-c','import sys; assert sys.prefix != sys.base_prefix, \"Refusing to install into base Python\"'],capture_output=True,text=True)
    if probe.returncode:raise ValueError('Installer requires an isolated virtualenv interpreter: '+str(python))
    execute([python,'-m','pip','install',*arguments])


def setup(args):
    if sys.version_info[:2] != (3,12):
        raise ValueError('Run setup with Python 3.12: STUDIO_BOOTSTRAP_PYTHON=python3.12 ./studio setup ...')
    initialize();config=settings()
    if args.profile in {'controller','all'}:
        target=path(config['controller_python']).parent.parent
        if not (target/'pyvenv.cfg').exists():execute([sys.executable,'-m','venv',target])
        python=path(config['controller_python'])
        pip_install(python,['-c',ROOT/'locks/controller.txt','-r',ROOT/('requirements-dev.txt' if args.dev else 'requirements-controller.txt')])
        if args.tags:pip_install(python,['-r',ROOT/'requirements-tags.txt'])
    elif args.tags:
        raise ValueError('--tags requires the controller or all profile')
    if args.profile in {'visualizer','all'}:
        target=path(config['visualizer_python']).parent.parent
        if not (target/'pyvenv.cfg').exists():execute([sys.executable,'-m','venv',target])
        python=path(config['visualizer_python'])
        pip_install(python,['torch==2.8.0','torchaudio==2.8.0','--index-url','https://download.pytorch.org/whl/cpu'])
        pip_install(python,['-r',ROOT/'visualizer/requirements.txt'])
        if args.dev:pip_install(python,['pytest==9.0.3'])
    if args.engines:
        execute(['bash',ROOT/'gemma/setupGemma12B.sh','--build'])
        execute(['bash',ROOT/'visualizer/scripts/setup_karaoke.sh','--build-only'])
    if args.models:
        execute(['bash',ROOT/'gemma/setupGemma12B.sh','--download-model'])
        execute(['bash',ROOT/'visualizer/scripts/setup_karaoke.sh','--models-only'])
    print('Setup finished. Run ./studio doctor, configure Drive/Colab, then install services.')


def diagnostics(config=None):
    config=config or settings()
    checks={}
    for name in ['ffmpeg','ffprobe','rclone','mpv','cmake','git','glslc']:
        checks[name]=shutil.which(name) is not None
    for name in ['controller_python','visualizer_python']:
        checks[name]=path(config[name]).is_file()
    gemma=path(config['gemma_root']);vis=path(config['visualizer_root'])
    for name,p in [('gemma_server',gemma/'llama.cpp/build/bin/llama-server'),
                   ('gemma_model',gemma/'models/gemma-4-12B-it-Q6_K.gguf'),
                   ('whisper_cli',vis/'tools/whisper.cpp/build/bin/whisper-cli'),
                   ('whisper_model',vis/'models/ggml-large-v3-q5_0.bin'),
                   ('rclone_config',path(config['rclone_config']))]:
        checks[name]=p.is_file()
    if checks['controller_python']:
        code='import numpy, scipy, soundfile, colab_cli'
        result=subprocess.run([str(path(config['controller_python'])),'-c',code],capture_output=True)
        checks['controller_imports']=result.returncode==0
    if checks['visualizer_python']:
        code='import torch, torchaudio, demucs, librosa, moderngl, PIL; assert torch.__version__.startswith("2.8.")'
        result=subprocess.run([str(path(config['visualizer_python'])),'-c',code],capture_output=True)
        checks['visualizer_imports']=result.returncode==0
    checks['systemd_user']=shutil.which('systemctl') is not None
    checks['vulkan_render_device']=any(Path('/dev/dri').glob('renderD*'))
    return checks


def unit_quote(value):
    value=str(value)
    if '\n' in value or '\r' in value:raise ValueError('Newlines cannot be used in service paths')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def unit_text(worker,config=None):
    config=config or settings()
    unit_quote(ROOT)  # reject newlines; WorkingDirectory itself is not a shell-quoted field
    command=' '.join(unit_quote(p) for p in [path(config['controller_python']),ROOT/'studio.py','_worker',worker])
    service_type='Type=oneshot\n' if worker=='report' else 'Restart=on-failure\nRestartSec=30s\n'
    return ('[Unit]\nDescription=Wolf Music Studio '+worker+'\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n'
        '[Service]\n'+service_type+'WorkingDirectory='+str(ROOT).replace('%','%%')+'\n'
        'Environment=PYTHONUNBUFFERED=1\nExecStart='+command+'\nNice=10\nTimeoutStopSec=30s\n\n'
        '[Install]\nWantedBy=default.target\n')


def service_names(selection):
    return [f'wolfstudio-{w}.service' for w in selection.split(',') if w]


def services(args):
    selected=args.only.split(',')
    if any(w not in WORKERS for w in selected):raise ValueError('Unknown service selection')
    if args.action=='install':
        initialize();target=Path.home()/'.config/systemd/user';target.mkdir(parents=True,exist_ok=True)
        for worker in selected:
            dest=target/f'wolfstudio-{worker}.service'
            if dest.exists() and unit_quote(ROOT) not in dest.read_text():
                raise ValueError(f'{dest.name} belongs to another checkout; remove or rename it first')
            dest.write_text(unit_text(worker))
        timer=target/'wolfstudio-report.timer'
        timer.write_text('[Unit]\nDescription=Wolf Studio comparison refresh\n\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=60s\nUnit=wolfstudio-report.service\n\n[Install]\nWantedBy=timers.target\n')
        execute(['systemctl','--user','daemon-reload'])
        print('Installed user services; none were started. Use ./studio services start after authentication and model setup.')
    elif args.action=='start':
        checks=diagnostics();required={'controller_python','controller_imports'}
        if 'queue' in selected:required|={'rclone','rclone_config'}
        if 'songwriter' in selected:required|={'gemma_model','gemma_server'}
        if 'visualizer' in selected:required|={'visualizer_python','visualizer_imports','whisper_cli','whisper_model','ffmpeg'}
        missing=sorted(n for n in required if not checks.get(n))
        if missing:raise ValueError('Finish setup before starting services: '+', '.join(missing))
        units=[u for u in service_names(args.only) if 'report' not in u]
        if 'report' in selected:units+=['wolfstudio-report.timer']
        if units:execute(['systemctl','--user','enable','--now',*units])
    else:
        units=[u for u in service_names(args.only) if 'report' not in u]
        if 'report' in selected:units+=['wolfstudio-report.timer','wolfstudio-report.service']
        if units:execute(['systemctl','--user',args.action,*units])


def dispatch(tool,arguments):
    config=settings();python=path(config['controller_python'])
    if not python.is_file():raise ValueError('Run ./studio setup --profile controller first')
    arguments=list(arguments)
    if tool=='songwriter' and '--campaign' not in arguments:
        arguments=['--campaign',str(ROOT/'yue2/songwriter/runs'/config['campaign']),*arguments]
    os.execvpe(str(python),[str(python),str(ROOT/'yue2'/TOOLS[tool]),*arguments],environment(config))


def worker(name):
    arguments=[]
    if name=='queue':arguments=['--session','wolfstudio-directory-queue']
    if name=='songwriter':arguments=['--campaign',str(ROOT/'yue2/songwriter/runs'/settings()['campaign'])]
    tool={'queue':None,'visualizer':'visualize','songwriter':'songwriter','report':'compare'}[name]
    if tool:dispatch(tool,arguments)
    config=settings();python=path(config['controller_python'])
    os.execvpe(str(python),[str(python),str(ROOT/'yue2'/WORKERS[name]),*arguments],environment(config))


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    # Preserve component CLIs, including their own --help and short aliases.
    if argv and argv[0] in TOOLS:
        dispatch(argv[0],argv[1:]);return 0
    if argv and argv[0]=='_worker':
        if len(argv)!=2 or argv[1] not in WORKERS:raise ValueError('Unknown worker')
        worker(argv[1]);return 0
    parser=argparse.ArgumentParser(description=__doc__, epilog='Workflow commands: '+', '.join(TOOLS)+'. Each supports its own --help.')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init',help='Create ignored local settings and empty queues')
    p=sub.add_parser('setup',help='Install isolated controller/visualizer environments')
    p.add_argument('--profile',choices=['controller','visualizer','all'],default='controller')
    p.add_argument('--dev',action='store_true');p.add_argument('--tags',action='store_true')
    p.add_argument('--engines',action='store_true');p.add_argument('--models',action='store_true')
    sub.add_parser('doctor',help='Inspect dependencies without loading models or allocating a GPU')
    p=sub.add_parser('services',help='Install or control user systemd services')
    p.add_argument('action',choices=['install','start','stop','status'])
    p.add_argument('--only',default='queue,visualizer,songwriter,report')
    p=sub.add_parser('logs',help='Follow one worker journal')
    p.add_argument('worker',choices=list(WORKERS),default='songwriter',nargs='?')
    args=parser.parse_args(argv)
    if args.command=='init':initialize()
    elif args.command=='setup':setup(args)
    elif args.command=='doctor':
        checks=diagnostics()
        for name,ok in checks.items():print(f'{"OK     " if ok else "MISSING"} {name}')
        print('Drive:',settings()['drive_base']);print('Config:',CONFIG)
        return 0 if all(checks.values()) else 1
    elif args.command=='services':services(args)
    elif args.command=='logs':execute(['journalctl','--user','-u',f'wolfstudio-{args.worker}.service','-f'])
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}',file=sys.stderr);raise SystemExit(1)
