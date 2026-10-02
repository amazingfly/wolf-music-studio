#!/usr/bin/env python3
"""Start a JSON song config as a persistent Colab service with a dated output folder."""
import argparse
import subprocess
import os
import sys
from pathlib import Path
from run_config import create_run, load_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument('--config', type=Path, help='JSON song object, array, or {"songs": [...]}')
    select.add_argument('--resume', type=Path, help='Existing outputs/<run>/run.json')
    parser.add_argument('--gpu', default='T4')
    parser.add_argument('--token-cycle', action='store_true')
    parser.add_argument('--prepare-only', action='store_true', help='Validate and prepare without starting Colab')
    args = parser.parse_args()
    manifest = create_run(args.config) if args.config else args.resume.expanduser().resolve()
    context = load_run(manifest)
    print(f'Output: {manifest.parent}', flush=True)
    if args.prepare_only:
        print(f'Ready; start with: python start_run.py --resume {manifest}')
        return
    unit = context['session']
    cmd = ['systemd-run', '--user', '--collect', '--unit', unit,
           '--property=Restart=on-abnormal', '--property=RestartSec=20s',
           '--property=MemoryHigh=256M', '--property=MemoryMax=512M',
           '--setenv=PYTHONUNBUFFERED=1',
           '--setenv=PATH=' + os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
           sys.executable, str(Path(__file__).with_name('colab_supervisor.py')),
           '--resume', str(manifest), '--gpu', args.gpu]
    for key,value in os.environ.items():
        if key.startswith('YUE2_') or key == 'RCLONE_CONFIG':
            cmd.insert(cmd.index(sys.executable), '--setenv=' + key + '=' + value)
    if args.token_cycle:
        cmd.append('--token-cycle')
    subprocess.run(cmd, check=True)
    print(f'Service: {unit}\nLog: tail -F {manifest.parent}/colab_run.log')


if __name__ == '__main__':
    main()
