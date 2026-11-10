#!/usr/bin/env bash
# Phase 4b: venv ~/venvs/dpvo (Python 3.10, torch 2.3.1/cu121, numpy 1.26.4), build DPVO, fetch weights.
# Mirrors DPVO's environment.yml. Portable build: no -march=native; CUDA archs 7.5 (1660S) / 8.6 (3060) / 8.9.
# Run after 01_system_root.sh and 04_dpvo_src.sh. No sudo. Prints timings.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-7.5;8.6;8.9}"
# nvcc with 3 archs needs ~3 GB per job: derive jobs from available RAM (WSL default RAM is ~half of the host)
if [ -z "${MAX_JOBS:-}" ]; then
  avail_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo)
  MAX_JOBS=$(( avail_gb / 3 )); [ "$MAX_JOBS" -ge 1 ] || MAX_JOBS=1; [ "$MAX_JOBS" -le 6 ] || MAX_JOBS=6
fi
export MAX_JOBS
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"   # large CUDA wheels; slow links time out at the 30 s default
export UV_HTTP_RETRIES="${UV_HTTP_RETRIES:-6}"
py="$DPVO_VENV/bin/python"
t0=$(date +%s)

[ -x "$py" ] || uv venv --python 3.10 "$DPVO_VENV"
uvp() { uv pip install --python "$py" "$@"; }
uvp torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cu121
uvp "numpy==1.26.4"
uvp torch-scatter==2.1.2 --find-links https://data.pyg.org/whl/torch-2.3.0+cu121.html
uvp tensorboard numba tqdm einops pypose kornia plyfile opencv-python yacs matplotlib scipy "numpy==1.26.4"
uvp setuptools wheel ninja
t1=$(date +%s)

cd "$DPVO_DIR"
if "$py" -c "import cuda_corr, cuda_ba, lietorch_backends" >/dev/null 2>&1; then
  echo "DPVO CUDA extensions already built, skipping build"
else
  uv pip install --python "$py" --no-build-isolation .
fi
t2=$(date +%s)

if [ ! -f dpvo.pth ]; then
  # Official weights (models.zip from DPVO's download_models_and_data.sh); data archive not needed.
  wget -q -O models.zip "https://www.dropbox.com/scl/fo/2g6e134ruh1acmaye5bmy/AK0DY9WLZ25elq-7N7sJcB0/models.zip?rlkey=4j336vcc4h324j4p4ji3ppl0t"
  unzip -o -q models.zip && rm models.zip
fi
ls -la dpvo.pth

"$py" - <<PY
import torch, numpy, platform
import cuda_corr, cuda_ba, lietorch_backends, dpvo.dpvo
print("python", platform.python_version(), "| torch", torch.__version__, "| CUDA", torch.version.cuda, "| numpy", numpy.__version__)
print("GPU:", torch.cuda.get_device_name(0), "| available:", torch.cuda.is_available())
PY
echo "env_install_s=$((t1-t0)) build_s=$((t2-t1))"
