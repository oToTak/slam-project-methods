#!/usr/bin/env bash
# Usage: scripts/openvins/run.sh <ucu|euroc> <seq> [--run R] [--max-frames N]        (registry contract: scripts/queue/methods.conf)
# One mono + IMU OpenVINS run (rpng/open_vins, pinned commit, ROS-FREE build: no ROS, no rosbag, no colcon), CPU only. Mode: mono_inertial.
#   ucu    $DATA_ROOT/ucu_tum/<seq>      (rgb.txt + rgb/*.png 30 Hz, imu.txt ~125 Hz)    config configs/openvins/ucu/ (generated, Kalibr T_ic)
#   euroc  $DATA_ROOT/euroc_tum/<seq>    sanity (MH_01): made by scripts/orbslam3/euroc_to_tum.py (cam0 only); config = upstream
#          config/euroc_mav/ with max_cameras 1 / use_stereo false; the output is the IMU (body) pose like the EuRoC ground truth
# Writes $RESULTS_ROOT/openvins/mono_inertial/<dataset>/<seq>/traj_run<R>.txt + meta.yaml: TUM format, ONE LINE PER FRAME THAT HAS A POSE.
# The filter needs an initialisation phase (>= 2 s of data), so the first frames have no pose; nothing is interpolated or invented, the share
# is in meta notes and coverage. Never overwrites an existing traj_run<R>.txt. Exit code 0 only if a trajectory was written (and
# evaluated, unless NO_EVAL=1); non-zero on any failure or when the filter never initialised.
# Headless (no ROS, no viewer), no real-time throttling, no sleeps: the driver feeds IMU and images as fast as the filter runs (single thread).
# Env: OV_CONFIG_DIR=<folder> uses another config (ablations: scripts/openvins/make_config.py --out DIR --init dynamic | --no-online-calib).
# Log: $DATA_ROOT/logs/openvins_<seq>_run<R>_<timestamp>.log   Raw driver output: $DATA_ROOT/openvins_work/...
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
source "$here/env_openvins.sh"
[ $# -ge 2 ] || { echo "Usage: $0 <ucu|euroc> <seq> [--run R] [--max-frames N]" >&2; exit 2; }
dataset=$1; seq=$2; shift 2
run=""; max_frames=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run) run=${2:?--run needs a number}; shift 2 ;;
    --max-frames) max_frames=${2:?--max-frames needs a number}; shift 2 ;;
    *) echo "unknown argument '$1'" >&2; exit 2 ;;
  esac
done
mode=${QUEUE_MODE:-mono_inertial}
[ "$mode" = mono_inertial ] || { echo "QUEUE_MODE must be mono_inertial (got '$mode')" >&2; exit 2; }

py="$SLAM_VENV/bin/python"; [ -x "$py" ] || py=$(command -v python3)   # the helper scripts are stdlib-only
out_dir="$RESULTS_ROOT/openvins/mono_inertial/$dataset/$seq"
if [ -z "$run" ]; then                                                  # by hand without --run: next free number
  run=1; while [ -e "$out_dir/traj_run$run.txt" ]; do run=$((run + 1)); done
fi
[[ "$run" =~ ^[1-9][0-9]*$ ]] || { echo "bad run number '$run'" >&2; exit 2; }
[ ! -e "$out_dir/traj_run$run.txt" ] || { echo "$out_dir/traj_run$run.txt already exists; refusing to overwrite a run" >&2; exit 1; }

work="$DATA_ROOT/openvins_work/mono_inertial/$dataset/$seq/run$run"
postargs=()
case "$dataset" in
  ucu)
    data_dir="$DATA_ROOT/ucu_tum/$seq"; cfg_dir="$SLAM_REPO/configs/openvins/ucu"; config="configs/openvins/ucu/estimator_config.yaml" ;;
  euroc)
    data_dir="$DATA_ROOT/euroc_tum/$seq"; cfg_dir="$work/euroc_cfg"; config="openvins-upstream:config/euroc_mav (max_cameras 1)"
    postargs+=(--body-frame)                                            # EuRoC ground truth is the IMU body pose
    [ -d "$OV_DIR/config/euroc_mav" ] || { echo "Missing $OV_DIR/config/euroc_mav (OpenVINS sources): bash scripts/setup/openvins.sh" >&2; exit 1; }
    mkdir -p "$cfg_dir"
    cp "$OV_DIR"/config/euroc_mav/kalibr_imu_chain.yaml "$OV_DIR"/config/euroc_mav/kalibr_imucam_chain.yaml "$cfg_dir/"
    sed -E -e 's/^max_cameras:.*/max_cameras: 1/' -e 's/^use_stereo:.*/use_stereo: false/' -e 's/^num_opencv_threads:.*/num_opencv_threads: 0/' \
      "$OV_DIR/config/euroc_mav/estimator_config.yaml" > "$cfg_dir/estimator_config.yaml" ;;
  *) echo "Unknown dataset '$dataset' (expected ucu or euroc)" >&2; exit 2 ;;
esac
if [ -n "$OV_CONFIG_DIR" ]; then cfg_dir=$OV_CONFIG_DIR; config="override:$(basename "$OV_CONFIG_DIR")"; fi
cfg="$cfg_dir/estimator_config.yaml"
[ -f "$data_dir/rgb.txt" ] || { echo "Missing $data_dir/rgb.txt" >&2; exit 1; }
[ -f "$data_dir/imu.txt" ] || { echo "Missing $data_dir/imu.txt (this method needs the IMU)" >&2; exit 1; }
[ -f "$cfg" ] && [ -f "$cfg_dir/kalibr_imu_chain.yaml" ] && [ -f "$cfg_dir/kalibr_imucam_chain.yaml" ] || { echo "Incomplete config folder $cfg_dir" >&2; exit 1; }
[ -x "$OV_BIN" ] || { echo "OpenVINS is not installed ($OV_BIN): bash scripts/setup/openvins.sh" >&2; exit 1; }

mkdir -p "$work" "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/openvins_${seq}_run${run}_$(date +%Y%m%d_%H%M%S).log"
raw="$work/raw_poses.txt"
rm -f "$raw"                                                            # a stale raw file of an earlier attempt must never be picked up
cd "$SLAM_REPO"

mf=(); [ "$max_frames" -gt 0 ] && mf=(--max-frames "$max_frames")
set +e
"$py" scripts/orbslam3/convert_imu.py --imu "$data_dir/imu.txt" --rgb "$data_dir/rgb.txt" --out "$work/imu.txt" "${mf[@]}" 2>&1 | tee "$log"
irc=${PIPESTATUS[0]}
set -e
[ "$irc" = 0 ] || { echo "IMU data unusable (see above)" >&2; exit 1; }

t_start=$SECONDS
set +e
"$OV_BIN" "$cfg" "$data_dir/rgb.txt" "$work/imu.txt" "$raw" "${mf[@]}" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
set -e
wall=$((SECONDS - t_start))
stats=$(grep -h 'OV_STATS' "$log" | tail -n 1 || true)
note="OpenVINS ${OV_COMMIT:0:7} ROS-free build (ENABLE_ROS=OFF, ENABLE_ARUCO_TAGS=OFF), own driver, one camera + IMU, config $config (upstream EuRoC values, not tuned; Kalibr T_ic and IMU noise from the dataset calibration, time shift already applied by the exporter), single thread, every frame fed (no track_frequency dropping), no sleeping, wall ${wall}s. $stats"

pp=("$py" scripts/openvins/openvins_tools.py post --poses "$raw" --rgb "$data_dir/rgb.txt" --results-root "$RESULTS_ROOT"
    --dataset "$dataset" --seq "$seq" --run "$run" --config "$config" --note "$note" "${postargs[@]}")
[ "$max_frames" -gt 0 ] && pp+=(--max-frames "$max_frames")
if [ "$rc" != 0 ] && [ "$rc" != 4 ]; then
  echo "OpenVINS driver exited with code $rc" | tee -a "$log"     # 2 usage, 3 bad config / data: not a tracking result
  exit "$rc"
fi
set +e
"${pp[@]}" 2>&1 | tee -a "$log"
prc=${PIPESTATUS[0]}
set -e
[ "$prc" = 0 ] || { echo "no trajectory produced (postprocess exit $prc)" >&2; exit "$prc"; }

if [ "${NO_EVAL:-0}" != 1 ] && [ "$max_frames" = 0 ]; then
  evalargs=(--method openvins --mode mono_inertial --dataset "$dataset" --seq "$seq" --run "$run" --log "$log" --results-root "$RESULTS_ROOT")
  if [ "$dataset" = euroc ]; then evalargs+=(--gt "$data_dir/groundtruth.txt" --rgb "$data_dir/rgb.txt"); fi
  "$py" scripts/eval.py "${evalargs[@]}"      # needs evo: the slam venv (python above); the stdlib fallback cannot run it
fi
