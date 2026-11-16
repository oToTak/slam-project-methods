#!/usr/bin/env bash
# Recipe of the method `openvins` (contract: scripts/setup/dpvo.sh; TASK_all_methods.md T0.1b/T0.3/T4).
#   bash scripts/setup/openvins.sh --check      fast check, installs nothing; exit 0 = driver binary and libov_msckf_lib.so exist, sources are at
#                                               the pinned commit, no shared library is missing
#   bash scripts/setup/openvins.sh              install everything (idempotent: finished steps are skipped, re-run is safe)
#   bash scripts/setup/openvins.sh --apt-only   ONLY the apt step (the only step that needs sudo)
#   bash scripts/setup/openvins.sh --no-apt     everything except apt (same as OV_SKIP_APT=1): no sudo, for PCs where the packages exist.
#                                               The apt step also skips itself when nothing is missing.
# Environment knobs: see scripts/openvins/env_openvins.sh (OV_DIR, OV_PREFIX, OV_COMMIT) and
#   OV_JOBS=<n> parallel compile jobs (default: min(cores, free RAM in GB / 2); the Ceres/Eigen templates need ~2 GB per job)
#   OV_NICE=<n> nice level (default 10)      OV_FORCE=1 redo finished steps
#
# DECISION (docs: https://docs.openvins.com/gs-installing-free.html): the ROS-FREE build is real and supported ("-DENABLE_ROS=OFF"; the
# docs call it 'limited support'). It needs only OpenCV, Eigen3, Boost and Ceres from apt: NO ROS 2 Humble, NO rosbag/mcap conversion,
# NO ros-humble-* packages, NO colcon, NO extra apt repository. ov_core and ov_init sources are compiled into libov_msckf_lib.so by
# ov_msckf's own CMake (ov_msckf/cmake/ROS1.cmake, "MANUALLY LINKING TO OV_CORE LIBRARY"), so nothing has to be installed with sudo.
# What it does (NOT TESTED YET: written without building anything, from the sources of the pinned commit):
#   1 apt      build tools + libeigen3-dev libboost-{system,filesystem,thread,date-time}-dev libceres-dev libopencv-dev      [sudo]
#   2 sources  rpng/open_vins @d0075dd fetched by commit into $OV_DIR
#   3 library  cmake -S $OV_DIR/ov_msckf -DENABLE_ROS=OFF -DENABLE_ARUCO_TAGS=OFF (no opencv-contrib needed), target ov_msckf_lib only
#              (run_simulation / test_sim_* are skipped: not needed)
#   4 driver   scripts/openvins/driver/ov_ucu_driver.cc -> $OV_BIN (headless, per-frame trajectory)
# setup_all_really_all.sh --check and scripts/start.sh call this for the registry line `openvins | mono_inertial | ...`.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
source "$here/../openvins/env_openvins.sh"
drv_src="$SLAM_REPO/scripts/openvins/driver"
stamps="$OV_PREFIX/.stamps"

APT_PKGS=(build-essential cmake git pkg-config
          libeigen3-dev libboost-system-dev libboost-filesystem-dev libboost-thread-dev libboost-date-time-dev
          libceres-dev libopencv-dev)

check() {
  local fail=0 missing
  [ -x "$OV_BIN" ] || { echo "no driver binary $OV_BIN (bash scripts/setup/openvins.sh)"; fail=1; }
  [ -f "$OV_DIR/ov_msckf/build/libov_msckf_lib.so" ] || { echo "no $OV_DIR/ov_msckf/build/libov_msckf_lib.so (OpenVINS not built)"; fail=1; }
  git -C "$OV_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$OV_COMMIT" || { echo "no OpenVINS sources @${OV_COMMIT:0:7} in $OV_DIR"; fail=1; }
  [ "$fail" = 0 ] || return 1
  missing=$(ldd "$OV_BIN" 2>&1 | grep 'not found' | head -n 3 | tr '\n' ' ')
  [ -z "$missing" ] || { echo "driver has unresolved shared libraries: $missing"; return 1; }
  echo "OpenVINS ${OV_COMMIT:0:7} (ROS-free, no aruco), driver $OV_BIN"
}

stamp_ok()  { [ "${OV_FORCE:-0}" != 1 ] && [ -f "$stamps/$1" ] && [ "$(cat "$stamps/$1")" = "$2" ]; }
stamp_set() { mkdir -p "$stamps" && printf '%s' "$2" > "$stamps/$1"; }

# ---- 1 apt (the only step that needs sudo) ------------------------------------------------------------------------------
step_apt() {
  local miss=() p
  for p in "${APT_PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || miss+=("$p")
  done
  if [ ${#miss[@]} -eq 0 ]; then echo "[1 apt] all ${#APT_PKGS[@]} packages are installed: no sudo needed"; return 0; fi
  echo "[1 apt] missing packages: ${miss[*]}"
  if [ "${OV_SKIP_APT:-0}" = 1 ]; then
    echo "[1 apt] skipped (OV_SKIP_APT=1 / --no-apt). Install them yourself:  sudo apt-get install -y ${miss[*]}"
    return 0
  fi
  local sudo=""; [ "$(id -u)" = 0 ] || sudo=sudo
  echo "[1 apt] needs root: $sudo apt-get install -y ${miss[*]}   (the sudo password is typed by you, once)"
  $sudo apt-get update && $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${miss[@]}"
}

# ---- 2 sources ---------------------------------------------------------------------------------------------------------
step_sources() {
  mkdir -p "$(dirname "$OV_DIR")" "$OV_PREFIX"
  if ! git -C "$OV_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$OV_COMMIT"; then
    echo "[2 sources] OpenVINS @${OV_COMMIT:0:7} -> $OV_DIR"
    [ -d "$OV_DIR/.git" ] || { git init -q "$OV_DIR" && git -C "$OV_DIR" remote add origin https://github.com/rpng/open_vins.git; } || return 1
    git -C "$OV_DIR" fetch --depth 1 origin "$OV_COMMIT" || git -C "$OV_DIR" fetch origin || return 1   # by commit; full fetch as fallback
    git -C "$OV_DIR" checkout -q -f "$OV_COMMIT" || return 1
  fi
  echo "[2 sources] ok"
}

# ---- 3 library ---------------------------------------------------------------------------------------------------------
step_lib() {
  local key="$OV_COMMIT/noros/noaruco"
  if stamp_ok lib "$key" && [ -f "$OV_DIR/ov_msckf/build/libov_msckf_lib.so" ]; then echo "[3 lib] libov_msckf_lib.so already built"; return 0; fi
  echo "[3 lib] cmake + build target ov_msckf_lib (jobs $JOBS)"
  cmake -S "$OV_DIR/ov_msckf" -B "$OV_DIR/ov_msckf/build" -DCMAKE_BUILD_TYPE=Release -DENABLE_ROS=OFF -DENABLE_ARUCO_TAGS=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 || return 1
  $NICE cmake --build "$OV_DIR/ov_msckf/build" --target ov_msckf_lib -j "$JOBS" || return 1
  [ -f "$OV_DIR/ov_msckf/build/libov_msckf_lib.so" ] || { echo "build finished but ov_msckf/build/libov_msckf_lib.so is missing"; return 1; }
  stamp_set lib "$key"
}

# ---- 4 driver ----------------------------------------------------------------------------------------------------------
step_driver() {
  local key
  key="$OV_COMMIT/$(cat "$drv_src/ov_ucu_driver.cc" "$drv_src/CMakeLists.txt" | sha1sum | cut -c1-12)"
  if stamp_ok driver "$key" && [ -x "$OV_BIN" ]; then echo "[4 driver] up to date: $OV_BIN"; return 0; fi
  echo "[4 driver] build ov_ucu_driver"
  cmake -S "$drv_src" -B "$OV_PREFIX/build-driver" -DCMAKE_BUILD_TYPE=Release -DOV_DIR="$OV_DIR" -DCMAKE_INSTALL_PREFIX="$OV_PREFIX" || return 1
  $NICE cmake --build "$OV_PREFIX/build-driver" -j "$JOBS" || return 1
  cmake --install "$OV_PREFIX/build-driver" || return 1
  [ -x "$OV_BIN" ] || { echo "driver not installed at $OV_BIN"; return 1; }
  stamp_set driver "$key"
}

case "${1:-}" in
  --check) check; exit $? ;;
  --apt-only) step_apt; exit $? ;;
  --no-apt) export OV_SKIP_APT=1 ;;
  "") ;;
  *) echo "usage: $0 [--check | --apt-only | --no-apt]" >&2; exit 2 ;;
esac

mem_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null); mem_gb=${mem_gb:-4}
cores=$(nproc 2>/dev/null || echo 2)
JOBS=${OV_JOBS:-$(( mem_gb / 2 < cores ? mem_gb / 2 : cores ))}
[ "$JOBS" -ge 1 ] 2>/dev/null || JOBS=1
NICE="nice -n ${OV_NICE:-10}"
echo "== openvins recipe: OpenVINS @${OV_COMMIT:0:7} ROS-free, jobs $JOBS (free RAM ${mem_gb} GB, $cores cores) =="
for step in step_apt step_sources step_lib step_driver; do
  $step || { echo "ERROR: $step failed (see the output above). Fix and re-run: finished steps are skipped." >&2; exit 1; }
done
check || exit 1
echo "== openvins recipe finished =="
