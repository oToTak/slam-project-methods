# Shared environment for the local SLAM setup. Source it: `source scripts/setup/env.sh`.
# Everything is overridable from the caller's environment.
export SLAM_REPO="${SLAM_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Large data lives outside git. Layout mirrors CONVENTIONS.md section 3:
#   $DATA_ROOT/ucu/sequence-N   raw .mcap     $DATA_ROOT/ucu_tum/seqNN   TUM export
#   $DATA_ROOT/tum/fr1_xyz
if [ -z "${DATA_ROOT:-}" ]; then
  # main PC keeps data on T: (the WSL disk is also on T:); elsewhere fall back to the Linux home
  if [ -d /mnt/t ]; then DATA_ROOT=/mnt/t/slam-data; else DATA_ROOT="$HOME/slam-data"; fi
fi
export DATA_ROOT
export SLAM_VENV="${SLAM_VENV:-$HOME/venvs/slam}"        # evo + exporter (Python 3.12, numpy 2.x)
export DPVO_VENV="${DPVO_VENV:-$HOME/venvs/dpvo}"        # torch 2.3.1/cu121 + DPVO (numpy 1.26.4)
export DPVO_DIR="${DPVO_DIR:-$HOME/src/DPVO}"
export DATASET_REPO_DIR="${DATASET_REPO_DIR:-$HOME/src/ucu-slam-dataset}"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.1}"
export PATH="$HOME/.local/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
