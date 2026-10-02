"""Nonblocking mpv playback and desktop/terminal clipboard support."""
import base64
import json
import os
from pathlib import Path
import queue
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time


class Player:
    def __init__(self, extra_args=()):
        self.commands = queue.Queue()
        self.state = {'path': '', 'position': 0, 'duration': 0, 'paused': False, 'idle': True, 'error': ''}
        self.extra_args = extra_args
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def command(self, *args):
        self.commands.put(list(args))

    def play(self, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError(f'Audio file is missing: {path}')
        self.command('loadfile', str(path), 'replace')
        self.command('set_property', 'pause', False)

    def seek(self, seconds):
        self.command('seek', seconds, 'relative+exact')

    def pause(self):
        self.command('cycle', 'pause')

    def stop(self):
        self.command('stop')

    def close(self):
        self.closed.set()
        self.thread.join(timeout=3)

    def _run(self):
        process = None
        channel = None
        pending = None
        with tempfile.TemporaryDirectory(prefix='yue2-player-') as directory:
            address = str(Path(directory) / 'mpv.sock')
            try:
                while not self.closed.is_set():
                    if process is None:
                        try:
                            pending = self.commands.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if pending[0] != 'loadfile':
                            continue
                        executable = shutil.which('mpv')
                        if not executable:
                            self.state['error'] = 'mpv is not installed; install mpv to play audio'
                            continue
                        process = subprocess.Popen([executable, '--no-config', '--idle=yes', '--no-video',
                            '--audio-display=no', '--no-terminal', '--volume=80',
                            f'--input-ipc-server={address}', *self.extra_args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        deadline = time.monotonic() + 5
                        while not Path(address).exists():
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError('mpv could not start its audio control socket')
                            if self.closed.wait(0.03):
                                return
                        channel = socket.socket(socket.AF_UNIX)
                        channel.connect(address)
                        channel.settimeout(0.2)
                        buffer = b''
                        for index, name in enumerate(('time-pos', 'duration', 'pause', 'idle-active'), 1):
                            channel.sendall((json.dumps({'command': ['observe_property', index, name]}) + '\n').encode())
                    if process.poll() is not None:
                        raise RuntimeError('Audio player exited; restart the browser to retry')
                    while pending is not None or not self.commands.empty():
                        command = pending if pending is not None else self.commands.get_nowait()
                        pending = None
                        if command[0] == 'loadfile':
                            self.state.update(path=command[1], error='', position=0, duration=0)
                        channel.sendall((json.dumps({'command': command}) + '\n').encode())
                    if not select.select([channel], [], [], 0.1)[0]:
                        continue
                    chunk = channel.recv(65536)
                    if not chunk:
                        raise RuntimeError('Lost connection to mpv')
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        message = json.loads(line)
                        if message.get('event') == 'property-change':
                            key = {'time-pos': 'position', 'duration': 'duration', 'pause': 'paused', 'idle-active': 'idle'}.get(message.get('name'))
                            if key:
                                self.state[key] = message.get('data') or 0
                        elif message.get('event') == 'end-file' and message.get('reason') == 'error':
                            self.state['error'] = f"Playback failed: {message.get('file_error', 'unsupported or unreadable audio')}"
                        elif message.get('error', 'success') != 'success':
                            self.state['error'] = 'Player: ' + message['error']
            except Exception as exc:
                self.state['error'] = str(exc)
            finally:
                if channel:
                    channel.close()
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def copy_clipboard(text):
    """Use the desktop clipboard when possible, otherwise request OSC 52 copying."""
    commands = []
    if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-copy'):
        commands.append(['wl-copy'])
    if os.environ.get('DISPLAY') and shutil.which('xclip'):
        commands.append(['xclip', '-selection', 'clipboard'])
    if os.environ.get('DISPLAY') and shutil.which('xsel'):
        commands.append(['xsel', '--clipboard', '--input'])
    if shutil.which('pbcopy'):
        commands.append(['pbcopy'])
    for command in commands:
        try:
            result = subprocess.run(command, input=text.encode('utf-8'), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=3)
            if result.returncode == 0:
                return 'Copied to clipboard'
        except (OSError, subprocess.TimeoutExpired):
            pass
    sequence = '\033]52;c;' + base64.b64encode(text.encode('utf-8')).decode('ascii') + '\a'
    if os.environ.get('TMUX'):
        sequence = '\033Ptmux;' + sequence.replace('\033', '\033\033') + '\033\\'
    try:
        with open('/dev/tty', 'w') as terminal:
            terminal.write(sequence)
            terminal.flush()
    except OSError as exc:
        raise RuntimeError('No desktop clipboard or terminal clipboard is available') from exc
    return 'Clipboard requested via OSC 52 (terminal must allow clipboard access)'
