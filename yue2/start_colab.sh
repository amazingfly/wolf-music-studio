#!/bin/bash
set -euo pipefail
mkdir -p /content/yue2 /content/yue2-outputs
tar xzf /content/yue2-kit.tar.gz -C /content/yue2
cd /content/yue2
python -m pip install -q -e .
nohup python run_batch.py --requests /content/yue2/requests/batch.jsonl --output /content/yue2-outputs >/content/yue2-outputs.log 2>&1 &
echo "YuE2 detached worker PID $!; log=/content/yue2-outputs.log"
