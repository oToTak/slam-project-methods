#!/usr/bin/env bash
# Read-only probe of the WSL environment (no changes).
echo "== os =="; lsb_release -ds; uname -r
echo "== mem =="; free -h | head -2
echo "== cpu =="; nproc
echo "== disk =="; df -h / /mnt/t 2>/dev/null
echo "== gpu =="; nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1
echo "== tools =="
for t in python3 gcc g++ cmake ninja unzip git curl wget uv hf nvcc evo_ape; do printf '%s: ' "$t"; command -v "$t" || echo MISSING; done
python3 --version
echo "== user =="; id -un; sudo -n true 2>&1 | head -1
echo "== cuda dirs =="; ls /usr/local | grep -i cuda || echo none
