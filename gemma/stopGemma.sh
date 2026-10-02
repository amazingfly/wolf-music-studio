#!/usr/bin/env bash
set -euo pipefail
gemma_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
studio_root="$(cd "$gemma_root/.." && pwd)"
export YUE2_GEMMA_ROOT="$gemma_root"
exec "${YUE2_CONTROLLER_PYTHON:-$studio_root/.venv/bin/python}" "$studio_root/yue2/gemma_runtime.py" stop "$@"
