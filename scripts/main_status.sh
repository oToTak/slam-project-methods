#!/usr/bin/env bash
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash scripts/run_queue.sh --from 1 --dir up --methods all --runs 3 --status
echo; nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader
