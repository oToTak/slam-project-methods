#!/usr/bin/env bash
# Usage: scripts/dpvslam/run.sh <tum|ucu> <seq> [extra run_dpvo.py args]    e.g. tum fr1_xyz | ucu seq01 --run 2
# Mono DPV-SLAM (stride 1) = the SAME code as scripts/dpvo/run.sh (scripts/dpvo/run_dpvo.py --method dpvslam), i.e. DPVO 0ac95b6
# with the proximity loop closure (cfg LOOP_CLOSURE=True, README "SLAM Backends"); the classic image-retrieval loop closure is NOT
# used (it needs DBoW2 + DPRetrieval built with cmake/apt). Same calibration files as dpvo (configs/dpvo/calib_*.txt).
# -> $RESULTS_ROOT/dpvslam/mono/<dataset>/<seq>/traj_run<N>.txt + meta.yaml (RESULTS_ROOT: env.sh), then scripts/eval.py appends
# the summary.csv row (unless NO_EVAL=1; the queue worker sets it). Needs the CUDA GPU + ~/venvs/dpvo.
# Log: $DATA_ROOT/logs/dpvslam_<seq>_<timestamp>.log
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
[ $# -ge 2 ] || { echo "Usage: $0 <tum|ucu> <seq> [run_dpvo.py args]" >&2; exit 2; }
dataset=$1; seq=$2; shift 2
case "$dataset" in
  tum) data_dir="$DATA_ROOT/tum/$seq"; calib="configs/dpvo/calib_tum_fr1.txt" ;;
  ucu) data_dir="$DATA_ROOT/ucu_tum/$seq"; calib="configs/dpvo/calib_ucu.txt" ;;
  *) echo "Unknown dataset '$dataset' (expected tum or ucu)" >&2; exit 2 ;;
esac
[ -f "$data_dir/rgb.txt" ] || { echo "Missing $data_dir/rgb.txt (see CONVENTIONS.md section 3)" >&2; exit 1; }

mkdir -p "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/dpvslam_${seq}_$(date +%Y%m%d_%H%M%S).log"
cd "$SLAM_REPO"
"$DPVO_VENV/bin/python" scripts/dpvo/run_dpvo.py --method dpvslam --dataset "$dataset" --seq "$seq" \
  --data-dir "$data_dir" --calib "$calib" --dpvo-dir "$DPVO_DIR" --results-root "$RESULTS_ROOT" "$@" 2>&1 | tee "$log"

if [ "${NO_EVAL:-0}" != 1 ] && ! printf '%s ' "$@" | grep -q -- '--max-frames'; then
  "$SLAM_VENV/bin/python" scripts/eval.py --method dpvslam --mode mono --dataset "$dataset" --seq "$seq" --log "$log" \
    --results-root "$RESULTS_ROOT"
fi
