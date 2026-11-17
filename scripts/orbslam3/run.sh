#!/usr/bin/env bash
# Usage: scripts/orbslam3/run.sh <ucu|tum|euroc> <seq> [--run R] [--max-frames N]     (registry contract: scripts/queue/methods.conf)
# One ORB-SLAM3 run, CPU only. The mode comes from the environment: QUEUE_MODE=mono (default) | mono_inertial
# (the queue worker sets QUEUE_METHOD / QUEUE_MODE / NO_EVAL / RESULTS_ROOT; run by hand: QUEUE_MODE=mono_inertial bash ...).
#   ucu   $DATA_ROOT/ucu_tum/<seq>  (rgb.txt, imu.txt for mono_inertial)   settings configs/orbslam3/UCU_<mode>.yaml
#   tum   $DATA_ROOT/tum/<seq>      mono only, sanity (fr1_xyz)             settings = upstream Examples/Monocular/TUM1.yaml
#   euroc $DATA_ROOT/euroc_tum/<seq> (made by euroc_to_tum.py), sanity       settings = upstream Examples/*/EuRoC.yaml, body-frame output
# Writes $RESULTS_ROOT/orbslam3/<mode>/<dataset>/<seq>/traj_run<R>.txt + meta.yaml: TUM format, one line per frame that HAS a pose
# (ORB-SLAM3 SaveTrajectoryEuRoC, never keyframes only; frames without a pose are absent, nothing is interpolated or invented).
# Never overwrites an existing traj_run<R>.txt. Exit code 0 only if a trajectory was written (and evaluated, unless NO_EVAL=1);
# non-zero on any failure, on tracking that never initialised, or when the driver produced no usable poses.
# ORB-SLAM3 is NOT deterministic: the registry asks for 3 runs. ORB3_PACE=1 would run in real time (default 0 = no sleeping).
# Log: $DATA_ROOT/logs/orbslam3_<mode>_<seq>_run<R>_<timestamp>.log   Raw driver output: $DATA_ROOT/orbslam3_work/...
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
source "$here/env_orbslam3.sh"
[ $# -ge 2 ] || { echo "Usage: $0 <ucu|tum|euroc> <seq> [--run R] [--max-frames N]" >&2; exit 2; }
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
case "$mode" in mono|mono_inertial) ;; *) echo "QUEUE_MODE must be mono or mono_inertial (got '$mode')" >&2; exit 2 ;; esac

py="$SLAM_VENV/bin/python"; [ -x "$py" ] || py=$(command -v python3)   # the helper scripts are stdlib-only
out_dir="$RESULTS_ROOT/orbslam3/$mode/$dataset/$seq"
if [ -z "$run" ]; then                                                  # by hand without --run: next free number
  run=1; while [ -e "$out_dir/traj_run$run.txt" ]; do run=$((run + 1)); done
fi
[[ "$run" =~ ^[1-9][0-9]*$ ]] || { echo "bad run number '$run'" >&2; exit 2; }
[ ! -e "$out_dir/traj_run$run.txt" ] || { echo "$out_dir/traj_run$run.txt already exists; refusing to overwrite a run" >&2; exit 1; }

postargs=(--dataset "$dataset")
case "$dataset" in
  ucu)
    data_dir="$DATA_ROOT/ucu_tum/$seq"; settings="$SLAM_REPO/configs/orbslam3/UCU_$mode.yaml"
    config="configs/orbslam3/UCU_$mode.yaml"
    if [ -n "${ORB3_SETTINGS:-}" ]; then settings=$ORB3_SETTINGS; config="override:$(basename "$ORB3_SETTINGS")"; fi ;;   # ablations only
  tum)
    [ "$mode" = mono ] || { echo "tum sanity exists for mono only" >&2; exit 2; }
    data_dir="$DATA_ROOT/tum/$seq"; settings="$ORB3_DIR/Examples/Monocular/TUM1.yaml"     # fr1 calibration (fr1_xyz)
    config="orbslam3-upstream:Examples/Monocular/TUM1.yaml" ;;
  euroc)
    data_dir="$DATA_ROOT/euroc_tum/$seq"
    if [ "$mode" = mono ]; then settings="$ORB3_DIR/Examples/Monocular/EuRoC.yaml"; config="orbslam3-upstream:Examples/Monocular/EuRoC.yaml"
    else settings="$ORB3_DIR/Examples/Monocular-Inertial/EuRoC.yaml"; config="orbslam3-upstream:Examples/Monocular-Inertial/EuRoC.yaml"; fi
    postargs+=(--body-frame) ;;                                         # EuRoC ground truth is the IMU body pose
  *) echo "Unknown dataset '$dataset' (expected ucu, tum or euroc)" >&2; exit 2 ;;
esac
[ -f "$data_dir/rgb.txt" ] || { echo "Missing $data_dir/rgb.txt" >&2; exit 1; }
[ -f "$settings" ] || { echo "Missing settings $settings" >&2; exit 1; }
[ -x "$ORB3_BIN" ] && [ -f "$ORB3_VOCAB" ] || { echo "ORB-SLAM3 is not installed ($ORB3_BIN / $ORB3_VOCAB): bash scripts/setup/orbslam3.sh" >&2; exit 1; }

work="$DATA_ROOT/orbslam3_work/$mode/$dataset/$seq/run$run"
mkdir -p "$work" "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/orbslam3_${mode}_${seq}_run${run}_$(date +%Y%m%d_%H%M%S).log"
raw="$work/raw_trajectory_euroc.txt"
rm -f "$raw"                                                            # a stale raw file of an earlier attempt must never be picked up
cd "$SLAM_REPO"

rgb_used="$data_dir/rgb.txt"                                            # inertial mode: a copy trimmed to the IMU span (see convert_imu.py)
drv=("$ORB3_BIN" "$ORB3_VOCAB" "$settings" "$data_dir" "$raw" --kf-out "$work/raw_keyframes_euroc.txt" --pace "$ORB3_PACE")
mf=(); [ "$max_frames" -gt 0 ] && mf=(--max-frames "$max_frames")
if [ "$mode" = mono_inertial ]; then
  [ -f "$data_dir/imu.txt" ] || { echo "Missing $data_dir/imu.txt (inertial mode)" >&2; exit 1; }
  set +e
  "$py" scripts/orbslam3/convert_imu.py --imu "$data_dir/imu.txt" --rgb "$data_dir/rgb.txt" --out "$work/imu.txt" \
        --rgb-out "$work/rgb.txt" "${mf[@]}" 2>&1 | tee "$log"
  irc=${PIPESTATUS[0]}
  set -e
  [ "$irc" = 0 ] || { echo "IMU data unusable (see above)" >&2; exit 1; }
  drv[3]="$work"; rgb_used="$work/rgb.txt"                              # the driver reads SEQ_DIR/rgb.txt (absolute image paths inside)
  drv+=(--imu "$work/imu.txt")
fi
drv+=("${mf[@]}")

t_start=$SECONDS
set +e
"${drv[@]}" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
set -e
wall=$((SECONDS - t_start))
stats=$(grep -h 'ORB3_STATS' "$log" | tail -n 1 || true)
trim=$(grep -h '^IMU_TRIM' "$log" | tail -n 1 || true)
[ -n "$trim" ] && stats="$stats $trim"
note="ORB-SLAM3 ${ORB3_COMMIT:0:7} (-std=$ORB3_CXX_STD), Pangolin $ORB3_PANGOLIN_TAG, viewer off, pace=$ORB3_PACE, stride 1, wall ${wall}s incl. vocabulary load. $stats"

pp=("$py" scripts/orbslam3/postprocess.py --raw "$raw" --rgb "$rgb_used" --mode "$mode" --results-root "$RESULTS_ROOT"
    --seq "$seq" --run "$run" --config "$config" --note "$note" "${postargs[@]}")
[ "$max_frames" -gt 0 ] && pp+=(--max-frames "$max_frames")
if [ "$rc" != 0 ]; then
  echo "ORB-SLAM3 driver exited with code $rc" | tee -a "$log"
  # rc 3 = bad input data / rc 255 = bad settings: not a tracking result, do not write meta saying 'tracking lost'
  if [ "$rc" = 4 ]; then "${pp[@]}" --driver-failed "tracking never initialised (driver exit 4)" || true; fi
  exit "$rc"
fi
set +e
"${pp[@]}" 2>&1 | tee -a "$log"
prc=${PIPESTATUS[0]}
set -e
[ "$prc" = 0 ] || { echo "no trajectory produced (postprocess exit $prc)" >&2; exit "$prc"; }

if [ "${NO_EVAL:-0}" != 1 ] && [ "$max_frames" = 0 ]; then
  evalargs=(--method orbslam3 --mode "$mode" --dataset "$dataset" --seq "$seq" --run "$run" --log "$log" --results-root "$RESULTS_ROOT")
  if [ "$dataset" = euroc ]; then evalargs+=(--gt "$data_dir/groundtruth.txt" --rgb "$data_dir/rgb.txt"); fi
  "$py" scripts/eval.py "${evalargs[@]}"      # needs evo: the slam venv (python above); the stdlib fallback cannot run it
fi
