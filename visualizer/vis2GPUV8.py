#!/usr/bin/env python3
"""V7 visuals/karaoke with a shared local hardware lease for Gemma coexistence."""
from contextlib import nullcontext
from pathlib import Path
import sys
import os

sys.path.insert(0, os.environ.get('YUE2_ROOT', str(Path(__file__).resolve().parent.parent / 'yue2')))
from local_compute import compute_lease, inherited_lease

def main(argv=None):
    with nullcontext() if inherited_lease() else compute_lease('visualizer', 'manual V8 render'):
        from vis2GPUV7 import main as render
        return render(argv)

if __name__ == '__main__':
    raise SystemExit(main())
