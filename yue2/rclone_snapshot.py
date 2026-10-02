#!/usr/bin/python3
"""Upload immutable copies of YuE2 output files, never actively replaced paths.

Also serves as a scoped PATH shim for an already-running Colab worker. Calls
other than copying /content/yue2-outputs pass straight to the real rclone.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from pathlib import Path

OUTPUT = Path('/content/yue2-outputs')
REAL_RCLONE = '/usr/bin/rclone'


def snapshot(source, target, previous=None):
    count = 0
    for path in sorted(source.rglob('*')):
        if not path.is_file() or path.is_symlink() or path.name.endswith('.tmp'):
            continue
        if previous is not None:
            stat = path.stat()
            if previous.get(str(path.relative_to(source))) == [stat.st_size, stat.st_mtime_ns]:
                continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                # Holding the source descriptor across an atomic replacement
                # reads one complete version, including its matching metadata.
                with path.open('rb') as reader, destination.open('wb') as writer:
                    before = os.fstat(reader.fileno())
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                    after = os.fstat(reader.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise OSError(f'In-place write during snapshot: {path.name}')
                os.utime(destination, ns=(before.st_atime_ns, before.st_mtime_ns))
                count += 1
                break
            except FileNotFoundError:
                if not path.exists():
                    destination.unlink(missing_ok=True)
                    break
                if attempt == 2:
                    raise
            except OSError:
                if attempt == 2:
                    raise
    return count


def main(args):
    if 'copy' not in args:
        os.execv(REAL_RCLONE, [REAL_RCLONE, *args])
    index = args.index('copy') + 1
    source=Path(args[index]) if index < len(args) else None
    if source is None or (source != OUTPUT and source.parent != OUTPUT):
        os.execv(REAL_RCLONE, [REAL_RCLONE, *args])
    with tempfile.TemporaryDirectory(prefix='yue2-upload-', dir='/content') as temporary:
        cache_path = Path('/content') / ('yue2-uploaded-' + hashlib.sha256(str(source).encode()).hexdigest()[:16] + '.json')
        try:
            previous = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            previous = {}
        count = snapshot(source, Path(temporary), previous)
        if count == 0:
            return 0
        command = list(args)
        command[index] = temporary
        # The owning worker enforces the total deadline and kills the whole
        # process group on expiry, including this rclone child.
        result = subprocess.run([REAL_RCLONE, *command])
        if result.returncode == 0:
            for path in Path(temporary).rglob('*'):
                if path.is_file():
                    stat = path.stat()
                    previous[str(path.relative_to(temporary))] = [stat.st_size, stat.st_mtime_ns]
            staged_cache = cache_path.with_suffix('.tmp')
            staged_cache.write_text(json.dumps(previous)); staged_cache.replace(cache_path)
            receipt = {'status': 'verified_upload', 'files': count, 'time': time.time()}
            path = Path('/content/yue2-backup.json')
            staged = path.with_suffix('.tmp')
            staged.write_text(json.dumps(receipt))
            staged.replace(path)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
