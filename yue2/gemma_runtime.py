"""Scoped Gemma lifecycle; stop the model before releasing its hardware lease."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from local_compute import compute_lease, process_stamp
from track_registry import atomic_json

GEMMA_ROOT = Path(os.environ.get('YUE2_GEMMA_ROOT', str(Path(__file__).resolve().parent.parent / 'gemma'))).expanduser().resolve()

class GemmaSession:
    def __init__(self, root=GEMMA_ROOT, model=None, host='127.0.0.1', port=8080,
                 layers=None, context=None, ctk='q8_0', ctv='q8_0', log=None, detail='songwriting', cache_directory=None):
        self.root = Path(root).resolve()
        self.model = Path(model).resolve() if model else self.root / 'models/gemma-4-12B-it-Q6_K.gguf'
        self.host, self.port = host, port
        self.layers = layers if layers is not None else int(os.environ.get('YUE2_GEMMA_GPU_LAYERS', 16))
        self.context = context if context is not None else int(os.environ.get('YUE2_GEMMA_CONTEXT', 12288))
        self.ctk, self.ctv = ctk, ctv
        self.log = Path(log) if log else self.root / 'gemma_server.log'
        self.detail = detail
        self.cache_directory = Path(cache_directory).resolve() if cache_directory else None
        self.prompt_cache = None
        self.proc = None
        self.stack = ExitStack()
        self.url = f'http://{host}:{port}'

    def __enter__(self):
        try:
            fd = self.stack.enter_context(compute_lease('gemma', self.detail))
            binary = self.root / 'llama.cpp/build/bin/llama-server'
            if not self.model.is_file() or not binary.is_file():
                raise FileNotFoundError(f'Missing Gemma model or server: {self.model}, {binary}')
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((self.host, self.port))
            command = [str(binary), '-m', str(self.model), '-ngl', str(self.layers), '-c', str(self.context),
                '-ctk', self.ctk, '-ctv', self.ctv, '--host', self.host, '--port', str(self.port),
                '-np', '1', '-t', '6', '-tb', '6', '--jinja', '--reasoning', 'off',
                '--chat-template-kwargs', '{"enable_thinking":false}']
            if self.cache_directory:
                self.cache_directory.mkdir(parents=True, exist_ok=True)
                command += ['--cache-ram', '512', '--ctx-checkpoints', '16']
            self.log.parent.mkdir(parents=True, exist_ok=True)
            stream = self.stack.enter_context(self.log.open('a'))
            stream.write('\n=== Scoped songwriting Gemma session ===\n'); stream.flush()
            self.proc = subprocess.Popen(command, cwd=self.root, stdout=stream, stderr=subprocess.STDOUT,
                                         pass_fds=(fd,), start_new_session=True)
            (self.root / 'gemma_server.pid').write_text(str(self.proc.pid) + '\n')
            atomic_json(self.root / 'gemma_runtime.json', {'pid': self.proc.pid,
                'process_stamp': process_stamp(self.proc.pid), 'controller_pid': os.getpid(),
                'model': str(self.model), 'url': self.url, 'purpose': self.detail})
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f'Gemma startup exited {self.proc.returncode}; see {self.log}')
                try:
                    with urllib.request.urlopen(self.url + '/health', timeout=2) as response:
                        if response.status == 200:
                            if self.cache_directory:
                                from gemma_prompt_cache import PromptCache
                                model_stat = self.model.stat()
                                self.prompt_cache = PromptCache(self.url, self.cache_directory, {
                                    'model':str(self.model), 'model_size':model_stat.st_size,
                                    'model_mtime_ns':model_stat.st_mtime_ns,
                                    'server_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                                    'layers':self.layers,'context':self.context,'ctk':self.ctk,'ctv':self.ctv})
                            print(f'Gemma ready: {self.url}; log: {self.log}', flush=True)
                            return self
                except (OSError, urllib.error.HTTPError):
                    pass
                time.sleep(1)
            raise TimeoutError(f'Gemma did not become ready; see {self.log}')
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *exc):
        try:
            if self.proc:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        self.proc.kill(); self.proc.wait()
                pidfile = self.root / 'gemma_server.pid'
                if pidfile.exists() and pidfile.read_text().strip() == str(self.proc.pid):
                    pidfile.unlink()
                runtime = self.root / 'gemma_runtime.json'
                if runtime.exists() and json.loads(runtime.read_text()).get('pid') == self.proc.pid:
                    runtime.unlink()
                self.proc = None
        finally:
            self.stack.close()

def scoped_stop(root=GEMMA_ROOT):
    root = Path(root)
    guard = root / 'gemma_guard.json'
    if guard.exists():
        row = json.loads(guard.read_text())
        if process_stamp(row['pid']) == row['process_stamp']:
            os.kill(row['pid'], signal.SIGTERM)
            return
        guard.unlink()
    runtime = root / 'gemma_runtime.json'
    if runtime.exists():
        row = json.loads(runtime.read_text())
        if process_stamp(row['pid']) == row['process_stamp']:
            args = Path(f'/proc/{row["pid"]}/cmdline').read_bytes().split(b'\0')
            if args and Path(args[0].decode()).resolve() == root.resolve() / 'llama.cpp/build/bin/llama-server':
                os.kill(row['pid'], signal.SIGTERM)
                print('Stopped this project\'s Gemma server; controller will release its lease.')
                return
    print('No owned Gemma server running; other llama-server instances left intact.')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'stop'])
    parser.add_argument('-m', '--model', type=Path)
    parser.add_argument('-p', '--port', type=int, default=8080)
    parser.add_argument('-H', '--host', default='127.0.0.1')
    parser.add_argument('-g', '--ngl', type=int, default=int(os.environ.get('YUE2_GEMMA_GPU_LAYERS', 16)))
    parser.add_argument('-c', '--ctx', type=int, default=int(os.environ.get('YUE2_GEMMA_CONTEXT', 12288)))
    parser.add_argument('-k', '--ctk', default='q8_0')
    parser.add_argument('-v', '--ctv', default='q8_0')
    parser.add_argument('-l', '--log', type=Path, default=GEMMA_ROOT / 'gemma_server.log')
    parser.add_argument('-f', '--foreground', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'stop':
        scoped_stop(); return 0
    guard = GEMMA_ROOT / 'gemma_guard.json'
    if not args.foreground:
        if guard.exists():
            row = json.loads(guard.read_text())
            if process_stamp(row['pid']) == row['process_stamp']:
                raise RuntimeError('Gemma controller is already running or waiting; use stopGemma.sh first')
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open('a') as log:
            command = [sys.executable, str(Path(__file__).resolve()), *(argv or sys.argv[1:]), '--foreground']
            proc = subprocess.Popen(command, cwd=GEMMA_ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(f'Gemma controller started/waiting: PID {proc.pid}; tail -f {args.log}')
        return 0
    atomic_json(guard, {'pid': os.getpid(), 'process_stamp': process_stamp(os.getpid())})
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with GemmaSession(model=args.model, host=args.host, port=args.port, layers=args.ngl,
                          context=args.ctx, ctk=args.ctk, ctv=args.ctv, log=args.log, detail='manual Gemma server') as session:
            session.proc.wait()
    finally:
        if guard.exists() and json.loads(guard.read_text()).get('pid') == os.getpid():
            guard.unlink()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
