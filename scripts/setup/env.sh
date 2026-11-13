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
# Worker clone (scripts/start.sh, START.bat): a plain clone of GitHub main where nobody commits, so `git pull --ff-only` always works.
# It is marked by the file .git/slam-runner (scripts/sync/migrate_to_git.sh creates it). Its results live OUTSIDE the git tree,
# so pulls never touch them; a development copy keeps writing to its own results/ as before.
export SLAM_RUNNER_DIR="${SLAM_RUNNER_DIR:-$HOME/slam-runner}"
export SLAM_CONF_DIR="${SLAM_CONF_DIR:-$HOME/.config/slam}"     # github_token, runner.conf (settings of this PC); never in git
if [ -z "${RESULTS_ROOT:-}" ]; then
  if [ -f "$SLAM_REPO/.git/slam-runner" ]; then RESULTS_ROOT="$DATA_ROOT/results"; else RESULTS_ROOT="$SLAM_REPO/results"; fi
fi
export RESULTS_ROOT
export SLAM_VENV="${SLAM_VENV:-$HOME/venvs/slam}"        # evo + exporter (Python 3.12, numpy 2.x)
export DPVO_VENV="${DPVO_VENV:-$HOME/venvs/dpvo}"        # torch 2.3.1/cu121 + DPVO (numpy 1.26.4)
export DPVO_DIR="${DPVO_DIR:-$HOME/src/DPVO}"
export DATASET_REPO_DIR="${DATASET_REPO_DIR:-$HOME/src/ucu-slam-dataset}"
export DATASET_REPO_COMMIT="${DATASET_REPO_COMMIT:-f46dfd6135a75af9da64a759c44928856a3d5b7d}"  # official exporter, pinned so exports stay identical
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.1}"
export PATH="$HOME/.local/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
