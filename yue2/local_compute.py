"""FIFO leases for local Gemma and karaoke rendering, with no priority."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
import uuid
from track_registry import atomic_json

COMPUTE_ROOT = Path(os.environ.get('YUE2_COMPUTE_ROOT', str(Path.home() / '.cache/wolf-music-studio/compute'))).expanduser().resolve()
VIS_ROOT = Path(os.environ.get('YUE2_VIS_ROOT', str(Path(__file__).resolve().parent.parent / 'visualizer'))).expanduser().resolve()

def process_stamp(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        fields = text[text.rfind(')') + 2:].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None

def external_users(owner):
    """Honor already running legacy commands even if they do not use our lease."""
    found = []
    waiting_pids = set()
    for pending in (COMPUTE_ROOT / 'waiting').glob('*.json'):
        try:
            waiting_pids.add(json.loads(pending.read_text())['pid'])
        except FileNotFoundError:
            pass
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid() or int(proc.name) in waiting_pids:
            continue
        try:
            args = proc.joinpath('cmdline').read_bytes().decode(errors='replace').split('\0')[:-1]
            if not args:
                continue
            cwd = proc.joinpath('cwd').resolve()
            if Path(args[0]).name == 'llama-server' and '-m' in args:
                if 'gemma' in Path(args[args.index('-m') + 1]).name.casefold():
                    found.append(int(proc.name))
            script = next((a for a in args[1:] if Path(a).name in {
                'vis2GPUV6.py', 'vis2GPUV7.py', 'vis2GPUV8.py', 'main.py'}), None)
            if script and (str(script).startswith(str(VIS_ROOT) + '/') or cwd == VIS_ROOT):
                found.append(int(proc.name))
            if Path(args[0]).name == 'whisper-cli' and str(VIS_ROOT) + '/' in args[0]:
                found.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            continue
    return sorted(set(found))

@contextmanager
def compute_lease(owner, detail='', root=COMPUTE_ROOT, blockers=external_users):
    root = Path(root)
    tickets = root / 'waiting'
    tickets.mkdir(parents=True, exist_ok=True)
    ticket = tickets / f'{time.time_ns():020d}_{uuid.uuid4().hex}.json'
    identity = {'owner': owner, 'detail': detail, 'pid': os.getpid(),
                'process_stamp': process_stamp(os.getpid()), 'queued_at': time.time()}
    atomic_json(ticket, identity)
    hardware = (root / 'hardware.lock').open('a')
    granted = announced = False
    try:
        while not granted:
            with (root / 'admission.lock').open('a') as admission:
                fcntl.flock(admission, fcntl.LOCK_EX)
                for pending in tickets.glob('*.json'):
                    try:
                        row = json.loads(pending.read_text())
                    except FileNotFoundError:
                        continue
                    if process_stamp(row['pid']) != row['process_stamp']:
                        pending.unlink(missing_ok=True)
                waiting = sorted(tickets.glob('*.json'))
                if waiting and waiting[0] == ticket and not blockers(owner):
                    try:
                        fcntl.flock(hardware, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        granted = True
                    except BlockingIOError:
                        pass
                    if granted:
                        ticket.unlink(missing_ok=True)
                        atomic_json(root / 'holder.json', {**identity, 'acquired_at': time.time()})
            if not granted:
                if not announced:
                    print(f'Waiting for local hardware: {owner}: {detail}', flush=True)
                    announced = True
                time.sleep(1)
        print(f'Local hardware acquired: {owner}: {detail}', flush=True)
        yield hardware.fileno()
    finally:
        ticket.unlink(missing_ok=True)
        if granted:
            with (root / 'admission.lock').open('a') as admission:
                fcntl.flock(admission, fcntl.LOCK_EX)
                (root / 'holder.json').unlink(missing_ok=True)
            # Closing the descriptor rather than explicit LOCK_UN preserves the
            # child's inherited lease if a controller dies unexpectedly.
            print(f'Local hardware released by controller: {owner}', flush=True)
        hardware.close()

def inherited_lease():
    value = os.environ.get('YUE2_COMPUTE_FD')
    if not value:
        return False
    try:
        return os.fstat(int(value)).st_ino == (COMPUTE_ROOT / 'hardware.lock').stat().st_ino
    except (OSError, ValueError):
        return False

if __name__ == '__main__':
    holder = COMPUTE_ROOT / 'holder.json'
    print(holder.read_text() if holder.exists() else 'Local hardware idle')
    for path in sorted((COMPUTE_ROOT / 'waiting').glob('*.json')):
        print('Waiting:', path.read_text())
