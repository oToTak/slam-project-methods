#!/usr/bin/env bash
# Usage: scripts/wheel_odom/run.sh ucu <seq> [--run R]          (registry contract: scripts/queue/methods.conf)
# Wheel-odometry baseline: no SLAM, no GPU, seconds per sequence. Needs $DATA_ROOT/ucu_tum/<seq>/{rgb.txt,odom.txt}
# (odom.txt is made by scripts/data/fetch_ucu.sh). Writes $RESULTS_ROOT/wheel_odom/odom/ucu/<seq>/traj_run<R>.txt + meta.yaml,
# then scripts/eval.py appends the summary row (unless NO_EVAL=1, as the queue worker sets it).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
[ $# -ge 2 ] || { echo "Usage: $0 ucu <seq> [--run R]" >&2; exit 2; }
dataset=$1; seq=$2; shift 2
[ "$dataset" = ucu ] || { echo "wheel_odom exists only for the ucu dataset (got '$dataset')" >&2; exit 2; }
data_dir="$DATA_ROOT/ucu_tum/$seq"
[ -f "$data_dir/rgb.txt" ] || { echo "Missing $data_dir/rgb.txt" >&2; exit 1; }
[ -f "$data_dir/odom.txt" ] || { echo "Missing $data_dir/odom.txt: run  bash scripts/data/fetch_ucu.sh <N>  (it adds odom.txt to an existing export)" >&2; exit 1; }

mkdir -p "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/wheel_odom_${seq}_$(date +%Y%m%d_%H%M%S).log"
cd "$SLAM_REPO"
"$SLAM_VENV/bin/python" scripts/wheel_odom/run_wheel_odom.py --dataset "$dataset" --seq "$seq" \
  --data-dir "$data_dir" --results-root "$RESULTS_ROOT" "$@" 2>&1 | tee "$log"

if [ "${NO_EVAL:-0}" != 1 ]; then
  "$SLAM_VENV/bin/python" scripts/eval.py --method wheel_odom --mode odom --dataset "$dataset" --seq "$seq" --log "$log" \
    --results-root "$RESULTS_ROOT"
fi
