#!/usr/bin/env bash
# Usage: scripts/dso/run.sh <ucu|tum> <seq> [--run R] [--max-frames N]        (registry contract: scripts/queue/methods.conf)
# One mono DSO run (JakobEngel/dso, pinned commit), CPU only, WITHOUT photometric calibration (mode=1: the UCU camera has no gamma /
# vignette calibration and uses auto-exposure; DSO was designed for calibrated global-shutter cameras: expect worse results than DPVO).
#   ucu  $DATA_ROOT/ucu_tum/<seq>  (rgb.txt + rgb/*.png)   camera configs/dso/camera_ucu.txt (RadTan from configs/dpvo/calib_ucu.txt)
#   tum  $DATA_ROOT/tum/<seq>      sanity (fr1_xyz)         camera configs/dso/camera_tum_fr1.txt (k3 of the calibration cannot be expressed: dropped)
# Writes $RESULTS_ROOT/dso/mono/<dataset>/<seq>/traj_run<R>.txt + meta.yaml: TUM format, ONE LINE PER FRAME THAT HAS A POSE. DSO needs
# an initialisation phase and may get lost, so frames without a pose are absent (nothing is interpolated or invented); the share is
# written to meta notes and to coverage. Never overwrites an existing traj_run<R>.txt. Exit code 0 only if a trajectory was written
# (and evaluated, unless NO_EVAL=1); non-zero on any failure or when DSO never initialised.
# Headless (no Pangolin, no GUI), no real-time throttling, no sleeps (scripts/dso/driver/dso_headless.cc). DSO is not deterministic
# across runs (threads; the registry asks for 3 runs).
# Log: $DATA_ROOT/logs/dso_<seq>_run<R>_<timestamp>.log   Raw driver output: $DATA_ROOT/dso_work/...
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
source "$here/env_dso.sh"
[ $# -ge 2 ] || { echo "Usage: $0 <ucu|tum> <seq> [--run R] [--max-frames N]" >&2; exit 2; }
dataset=$1; seq=$2; shift 2
run=""; max_frames=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run) run=${2:?--run needs a number}; shift 2 ;;
    --max-frames) max_frames=${2:?--max-frames needs a number}; shift 2 ;;
    *) echo "unknown argument '$1'" >&2; exit 2 ;;
  esac
done
mode=${QUEUE_MODE:-mono}
[ "$mode" = mono ] || { echo "QUEUE_MODE must be mono (got '$mode')" >&2; exit 2; }

py="$SLAM_VENV/bin/python"; [ -x "$py" ] || py=$(command -v python3)   # the helper scripts are stdlib-only
out_dir="$RESULTS_ROOT/dso/mono/$dataset/$seq"
if [ -z "$run" ]; then                                                  # by hand without --run: next free number
  run=1; while [ -e "$out_dir/traj_run$run.txt" ]; do run=$((run + 1)); done
fi
[[ "$run" =~ ^[1-9][0-9]*$ ]] || { echo "bad run number '$run'" >&2; exit 2; }
[ ! -e "$out_dir/traj_run$run.txt" ] || { echo "$out_dir/traj_run$run.txt already exists; refusing to overwrite a run" >&2; exit 1; }

case "$dataset" in
  ucu) data_dir="$DATA_ROOT/ucu_tum/$seq"; config="configs/dso/camera_ucu.txt" ;;
  tum) data_dir="$DATA_ROOT/tum/$seq";     config="configs/dso/camera_tum_fr1.txt" ;;
  *) echo "Unknown dataset '$dataset' (expected ucu or tum)" >&2; exit 2 ;;
esac
camera="$SLAM_REPO/$config"
[ -f "$data_dir/rgb.txt" ] || { echo "Missing $data_dir/rgb.txt" >&2; exit 1; }
[ -f "$camera" ] || { echo "Missing camera file $camera" >&2; exit 1; }
[ -x "$DSO_BIN" ] || { echo "DSO is not installed ($DSO_BIN): bash scripts/setup/dso.sh" >&2; exit 1; }

work="$DATA_ROOT/dso_work/mono/$dataset/$seq/run$run"
mkdir -p "$work" "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/dso_${seq}_run${run}_$(date +%Y%m%d_%H%M%S).log"
rm -f "$work/poses_refined.txt" "$work/poses_tracked.txt" "$work/poses_keyframes.txt" "$work/result_stock_format.txt"   # never pick up a stale file
cd "$SLAM_REPO"

mf=(); [ "$max_frames" -gt 0 ] && mf=(--max-frames "$max_frames")
"$py" scripts/dso/dso_tools.py prep --rgb "$data_dir/rgb.txt" --out "$work/images.txt" "${mf[@]}" 2>&1 | tee "$log"
[ "${PIPESTATUS[0]}" = 0 ] || { echo "cannot build the image list (see above)" >&2; exit 1; }

drv=("$DSO_BIN" "$camera" "$work/images.txt" "$work" "${mf[@]}")
[ "$DSO_NOMT" = 1 ] && drv+=(--nomt)
[ "$DSO_QUIET" = 1 ] && drv+=(--quiet)
t_start=$SECONDS
set +e
( cd "$work" && "${drv[@]}" ) 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
set -e
wall=$((SECONDS - t_start))
stats=$(grep -h 'DSO_STATS' "$log" | tail -n 1 || true)
note="DSO ${DSO_COMMIT:0:7} mono, NO photometric calibration (mode=1: affine brightness a,b optimised, exposure 1.0), preset 0 (2000 points, 5-7 keyframes), RadTan distortion passed as in the camera file ('crop' rectification), global-shutter assumption violated by the rolling-shutter sensor, nogui, no sleeping (linearizeOperation), multithreading=$([ "$DSO_NOMT" = 1 ] && echo off || echo on), non-keyframe poses re-expressed against final keyframe poses, wall ${wall}s. $stats"

pp=("$py" scripts/dso/dso_tools.py post --poses "$work/poses_refined.txt" --rgb "$data_dir/rgb.txt" --results-root "$RESULTS_ROOT"
    --dataset "$dataset" --seq "$seq" --run "$run" --config "$config" --note "$note")
[ "$max_frames" -gt 0 ] && pp+=(--max-frames "$max_frames")
if [ "$rc" -gt 128 ] && [ "$rc" != 137 ] && [ "$rc" != 143 ] && [ "$rc" != 130 ]; then
  # killed by a signal (SIGSEGV 139, SIGABRT 134, ...): a crash inside the method = a result without a trajectory (exit 4), not a broken PC
  # (137 = SIGKILL: OOM or our timeout, 143/130 = interrupt are NOT crashes)
  echo "DSO driver crashed (signal $((rc - 128))). Recorded as a result without a trajectory (exit 4)." | tee -a "$log"
  "${pp[@]}" --driver-failed "DSO driver crashed (signal $((rc - 128)))" || true
  exit 4
fi
if [ "$rc" != 0 ] && [ "$rc" != 4 ]; then
  echo "DSO driver exited with code $rc" | tee -a "$log"       # 2 usage, 3 bad calibration / unreadable image: not a tracking result
  exit "$rc"
fi
set +e
"${pp[@]}" 2>&1 | tee -a "$log"
prc=${PIPESTATUS[0]}
set -e
[ "$prc" = 0 ] || { echo "no trajectory produced (postprocess exit $prc)" >&2; exit "$prc"; }

if [ "${NO_EVAL:-0}" != 1 ] && [ "$max_frames" = 0 ]; then
  "$py" scripts/eval.py --method dso --mode mono --dataset "$dataset" --seq "$seq" --run "$run" --log "$log" --results-root "$RESULTS_ROOT"
fi
