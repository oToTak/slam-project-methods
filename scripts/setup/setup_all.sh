#!/usr/bin/env bash
# One-shot setup of the GPU environment inside WSL Ubuntu 22.04 (idempotent: safe to re-run).
#   bash scripts/setup/setup_all.sh            # asks for the sudo password once (apt + CUDA toolkit)
# Steps: 01 system+CUDA 12.1 (sudo) -> 02 uv/evo/hf -> 04 DPVO sources -> 05 DPVO env+build+weights
#        -> TUM fr1_xyz -> 10_verify.sh.  Log: $DATA_ROOT/logs/setup_all_<time>.log
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
mkdir -p "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/setup_all_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$log") 2>&1
echo "== setup_all started $(date) | repo $SLAM_REPO | data $DATA_ROOT =="

if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L >/dev/null 2>&1; then
  echo "ERROR: nvidia-smi does not see a GPU in WSL. Update the NVIDIA driver on WINDOWS (not inside WSL) and retry." >&2
  exit 1
fi
nvidia-smi -L

echo "== 1/6 system packages + CUDA toolkit 12.1 (sudo) =="
if [ -x /usr/local/cuda-12.1/bin/nvcc ] && command -v cmake >/dev/null && command -v ninja >/dev/null && command -v unzip >/dev/null \
   && [ -f /usr/include/python3.10/Python.h ]; then
  echo "already installed, skipping"
else
  if [ "$(id -u)" -eq 0 ]; then bash "$here/01_system_root.sh"; else sudo bash "$here/01_system_root.sh"; fi
fi
echo "== 2/6 uv, Python 3.12 venv (evo 1.37.1), hf CLI =="
bash "$here/02_python.sh"
echo "== 3/6 DPVO sources @ 0ac95b6 =="
bash "$here/04_dpvo_src.sh"
echo "== 4/6 DPVO venv (torch 2.3.1/cu121) + CUDA extensions + weights =="
bash "$here/05_dpvo_env.sh"
echo "== 5/6 TUM fr1_xyz (sanity data) =="
bash "$here/../data/fetch_tum.sh"
echo "== 6/6 verify =="
bash "$here/10_verify.sh"
echo "== setup_all finished $(date) =="
echo "Next: ~/.local/bin/hf auth login   (once; you type the token), then: bash scripts/accept_seq01.sh"
