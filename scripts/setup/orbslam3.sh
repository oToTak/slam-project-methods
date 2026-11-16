#!/usr/bin/env bash
# Recipe of the method `orbslam3` (contract: scripts/setup/dpvo.sh; TASK_all_methods.md T0.1b/T0.3).
#   bash scripts/setup/orbslam3.sh --check      fast check, installs nothing; exit 0 = driver binary, ORB-SLAM3 library and
#                                               vocabulary exist, sources are at the pinned commit, no shared library is missing
#   bash scripts/setup/orbslam3.sh              install everything (idempotent: finished steps are skipped, re-run is safe)
#   bash scripts/setup/orbslam3.sh --apt-only   ONLY the apt step (the only step that needs sudo)
#   bash scripts/setup/orbslam3.sh --no-apt     everything except apt (same as ORB3_SKIP_APT=1): no sudo, for PCs where the
#                                               packages are already there. The apt step also skips itself when nothing is missing.
# Environment knobs: see scripts/orbslam3/env_orbslam3.sh (ORB3_DIR, ORB3_PREFIX, ORB3_CXX_STD, ...) and
#   ORB3_JOBS=<n> parallel compile jobs (default: min(cores, free RAM in GB / 2), ORB-SLAM3 needs ~2 GB per job)
#   ORB3_NICE=<n> nice level of the builds (default 10)      ORB3_FORCE=1 redo finished steps
#
# What it does (ORB-SLAM3 upstream has no Ubuntu 22.04 support; the recipe follows the public reports listed in
# scripts/orbslam3/PREP_NOTES.md and is NOT TESTED YET: it was written without building anything):
#   1 apt      build tools + libs (OpenCV 4.5.4 from Ubuntu satisfies find_package(OpenCV 4.4); Eigen 3.4; Boost serialization;
#              OpenSSL (-lcrypto); GL/Wayland/xkb headers that Pangolin v0.9 needs)       [sudo]
#   2 sources  ORB-SLAM3 @4452a3c (fetched by commit) into $ORB3_DIR, Pangolin v0.9.1 into $ORB3_PANGOLIN_SRC
#   3 patch    ONE source change: CMakeLists.txt  -std=c++11 -> -std=$ORB3_CXX_STD (Pangolin's sigslot needs C++14)
#   4 pangolin built and installed under $ORB3_PANGOLIN_PREFIX (no sudo, nothing in /usr/local)
#   5 ORB-SLAM3 third party (DBoW2, g2o, Sophus) like upstream build.sh, then ONLY the library target (the upstream examples,
#              stereo/RGB-D/RealSense, are not built: they only add time and risk)
#   6 vocabulary  Vocabulary/ORBvoc.txt.tar.gz -> ORBvoc.txt
#   7 driver   scripts/orbslam3/driver/orbslam3_tum.cc -> $ORB3_BIN (headless, per-frame trajectory)
# setup_all_really_all.sh --check and scripts/start.sh call this for the registry lines `orbslam3 | mono|mono_inertial | ...`.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
source "$here/../orbslam3/env_orbslam3.sh"
drv_src="$SLAM_REPO/scripts/orbslam3/driver"
stamps="$ORB3_PREFIX/.stamps"

APT_PKGS=(build-essential cmake git pkg-config
          libopencv-dev libeigen3-dev libboost-serialization-dev libssl-dev
          libglew-dev libgl1-mesa-dev libegl1-mesa-dev libwayland-dev libxkbcommon-dev wayland-protocols
          libjpeg-dev libpng-dev)

check() {
  local fail=0 missing
  [ -x "$ORB3_BIN" ] || { echo "no driver binary $ORB3_BIN (bash scripts/setup/orbslam3.sh)"; fail=1; }
  [ -f "$ORB3_VOCAB" ] || { echo "no vocabulary $ORB3_VOCAB"; fail=1; }
  [ -f "$ORB3_DIR/lib/libORB_SLAM3.so" ] || { echo "no $ORB3_DIR/lib/libORB_SLAM3.so (ORB-SLAM3 not built)"; fail=1; }
  git -C "$ORB3_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$ORB3_COMMIT" || { echo "no ORB-SLAM3 sources @${ORB3_COMMIT:0:7} in $ORB3_DIR"; fail=1; }
  [ "$fail" = 0 ] || return 1
  missing=$(ldd "$ORB3_BIN" 2>&1 | grep 'not found' | head -n 3 | tr '\n' ' ')
  [ -z "$missing" ] || { echo "driver has unresolved shared libraries: $missing"; return 1; }
  echo "ORB-SLAM3 ${ORB3_COMMIT:0:7} + Pangolin $ORB3_PANGOLIN_TAG, -std=$ORB3_CXX_STD, driver $ORB3_BIN"
}

stamp_ok()  { [ "${ORB3_FORCE:-0}" != 1 ] && [ -f "$stamps/$1" ] && [ "$(cat "$stamps/$1")" = "$2" ]; }
stamp_set() { mkdir -p "$stamps" && printf '%s' "$2" > "$stamps/$1"; }

# ---- 1 apt (the only step that needs sudo) ------------------------------------------------------------------------------
step_apt() {
  local miss=() p
  for p in "${APT_PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || miss+=("$p")
  done
  if [ ${#miss[@]} -eq 0 ]; then echo "[1 apt] all ${#APT_PKGS[@]} packages are installed: no sudo needed"; return 0; fi
  echo "[1 apt] missing packages: ${miss[*]}"
  if [ "${ORB3_SKIP_APT:-0}" = 1 ]; then
    echo "[1 apt] skipped (ORB3_SKIP_APT=1 / --no-apt). Install them yourself:  sudo apt-get install -y ${miss[*]}"
    return 0
  fi
  local sudo=""; [ "$(id -u)" = 0 ] || sudo=sudo
  echo "[1 apt] needs root: $sudo apt-get install -y ${miss[*]}   (the sudo password is typed by you, once)"
  $sudo apt-get update && $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${miss[@]}"
}

# ---- 2 sources ---------------------------------------------------------------------------------------------------------
step_sources() {
  mkdir -p "$(dirname "$ORB3_DIR")" "$(dirname "$ORB3_PANGOLIN_SRC")" "$ORB3_PREFIX"
  if ! git -C "$ORB3_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$ORB3_COMMIT"; then
    echo "[2 sources] ORB-SLAM3 @${ORB3_COMMIT:0:7} -> $ORB3_DIR"
    [ -d "$ORB3_DIR/.git" ] || { git init -q "$ORB3_DIR" && git -C "$ORB3_DIR" remote add origin https://github.com/UZ-SLAMLab/ORB_SLAM3.git; } || return 1
    git -C "$ORB3_DIR" fetch --depth 1 origin "$ORB3_COMMIT" || git -C "$ORB3_DIR" fetch origin || return 1   # by commit; full fetch as fallback
    git -C "$ORB3_DIR" checkout -q -f "$ORB3_COMMIT" || return 1
  fi
  if [ ! -d "$ORB3_PANGOLIN_SRC/.git" ]; then
    echo "[2 sources] Pangolin $ORB3_PANGOLIN_TAG -> $ORB3_PANGOLIN_SRC"
    git clone --depth 1 --branch "$ORB3_PANGOLIN_TAG" --recursive --shallow-submodules \
      https://github.com/stevenlovegrove/Pangolin.git "$ORB3_PANGOLIN_SRC" || return 1
  fi
  if [ -n "$ORB3_PANGOLIN_SHORT" ]; then
    local got; got=$(git -C "$ORB3_PANGOLIN_SRC" rev-parse --short=7 HEAD)
    [ "$got" = "$ORB3_PANGOLIN_SHORT" ] || { echo "Pangolin $ORB3_PANGOLIN_TAG is at $got, expected $ORB3_PANGOLIN_SHORT: refusing (set ORB3_PANGOLIN_SHORT= to skip this check)"; return 1; }
  fi
  echo "[2 sources] ok"
}

# ---- 3 patch -----------------------------------------------------------------------------------------------------------
step_patch() {
  local f="$ORB3_DIR/CMakeLists.txt"
  [ -f "$f" ] || { echo "no $f"; return 1; }
  grep -q -- "-std=$ORB3_CXX_STD" "$f" || sed -E -i "s/-std=c\+\+(11|14|17)/-std=$ORB3_CXX_STD/g" "$f"
  grep -q -- "-std=$ORB3_CXX_STD" "$f" || { echo "[3 patch] could not set -std=$ORB3_CXX_STD in $f"; return 1; }
  # C++17 forbids ++ on a bool (upstream LoopClosing.cc: mnFullBAIdx++ on a bool, same effect as "= true" under C++14); sets the flag only.
  sed -i 's/mnFullBAIdx++;/mnFullBAIdx = true;/' "$ORB3_DIR/src/LoopClosing.cc"
  git -C "$ORB3_DIR" diff > "$ORB3_PREFIX/orbslam3_local_patches.diff" 2>/dev/null
  echo "[3 patch] CMakeLists.txt uses -std=$ORB3_CXX_STD (diff saved in $ORB3_PREFIX/orbslam3_local_patches.diff)"
}

# ---- 4 pangolin --------------------------------------------------------------------------------------------------------
step_pangolin() {
  if stamp_ok pangolin "$ORB3_PANGOLIN_TAG" && [ -d "$ORB3_PANGOLIN_PREFIX/lib" ]; then echo "[4 pangolin] already installed in $ORB3_PANGOLIN_PREFIX"; return 0; fi
  echo "[4 pangolin] build + install into $ORB3_PANGOLIN_PREFIX (jobs $JOBS)"
  cmake -S "$ORB3_PANGOLIN_SRC" -B "$ORB3_PANGOLIN_SRC/build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$ORB3_PANGOLIN_PREFIX" -DBUILD_EXAMPLES=OFF -DBUILD_TOOLS=OFF -DBUILD_TESTS=OFF \
        -DBUILD_PANGOLIN_PYTHON=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 || return 1
  $NICE cmake --build "$ORB3_PANGOLIN_SRC/build" -j "$JOBS" || return 1
  cmake --install "$ORB3_PANGOLIN_SRC/build" || return 1
  stamp_set pangolin "$ORB3_PANGOLIN_TAG"
}

# ---- 5 ORB-SLAM3 -------------------------------------------------------------------------------------------------------
step_orb3() {
  local d key
  for d in DBoW2 g2o Sophus; do       # same order and flags as upstream build.sh
    key="$ORB3_COMMIT"
    if stamp_ok "tp-$d" "$key"; then echo "[5 orb3] Thirdparty/$d already built"; continue; fi
    echo "[5 orb3] Thirdparty/$d (jobs $JOBS)"
    mkdir -p "$ORB3_DIR/Thirdparty/$d/build"
    ( cd "$ORB3_DIR/Thirdparty/$d/build" && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 && $NICE make -j"$JOBS" ) || return 1
    stamp_set "tp-$d" "$key"
  done
  [ -f "$ORB3_DIR/Thirdparty/DBoW2/lib/libDBoW2.so" ] && [ -f "$ORB3_DIR/Thirdparty/g2o/lib/libg2o.so" ] || { echo "DBoW2/g2o libraries missing after build"; return 1; }
  key="$ORB3_COMMIT/$ORB3_CXX_STD/$ORB3_PANGOLIN_TAG"
  if stamp_ok orb3 "$key" && [ -f "$ORB3_DIR/lib/libORB_SLAM3.so" ]; then echo "[5 orb3] libORB_SLAM3.so already built"; return 0; fi
  echo "[5 orb3] libORB_SLAM3.so (jobs $JOBS; Tracking.cc is the slow one)"
  cmake -S "$ORB3_DIR" -B "$ORB3_DIR/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$ORB3_PANGOLIN_PREFIX" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 || return 1
  $NICE cmake --build "$ORB3_DIR/build" --target ORB_SLAM3 -j "$JOBS" || return 1
  [ -f "$ORB3_DIR/lib/libORB_SLAM3.so" ] || { echo "build finished but lib/libORB_SLAM3.so is missing"; return 1; }
  stamp_set orb3 "$key"
}

# ---- 6 vocabulary ------------------------------------------------------------------------------------------------------
step_vocab() {
  if [ -f "$ORB3_VOCAB" ]; then echo "[6 vocab] present"; return 0; fi
  echo "[6 vocab] unpacking ORBvoc.txt.tar.gz"
  tar -xf "$ORB3_DIR/Vocabulary/ORBvoc.txt.tar.gz" -C "$ORB3_DIR/Vocabulary" || return 1
  [ -f "$ORB3_VOCAB" ] || { echo "ORBvoc.txt not produced"; return 1; }
}

# ---- 7 driver ----------------------------------------------------------------------------------------------------------
step_driver() {
  local key
  key="$ORB3_COMMIT/$ORB3_CXX_STD/$(cat "$drv_src/orbslam3_tum.cc" "$drv_src/CMakeLists.txt" | sha1sum | cut -c1-12)"
  if stamp_ok driver "$key" && [ -x "$ORB3_BIN" ]; then echo "[7 driver] up to date: $ORB3_BIN"; return 0; fi
  echo "[7 driver] build orbslam3_tum"
  cmake -S "$drv_src" -B "$ORB3_PREFIX/build-driver" -DCMAKE_BUILD_TYPE=Release -DORB3_DIR="$ORB3_DIR" \
        -DORB3_CXX_STD="$ORB3_CXX_STD" -DCMAKE_PREFIX_PATH="$ORB3_PANGOLIN_PREFIX" -DCMAKE_INSTALL_PREFIX="$ORB3_PREFIX" || return 1
  $NICE cmake --build "$ORB3_PREFIX/build-driver" -j "$JOBS" || return 1
  cmake --install "$ORB3_PREFIX/build-driver" || return 1
  [ -x "$ORB3_BIN" ] || { echo "driver not installed at $ORB3_BIN"; return 1; }
  stamp_set driver "$key"
}

case "${1:-}" in
  --check) check; exit $? ;;
  --apt-only) step_apt; exit $? ;;
  --no-apt) export ORB3_SKIP_APT=1 ;;
  "") ;;
  *) echo "usage: $0 [--check | --apt-only | --no-apt]" >&2; exit 2 ;;
esac

mem_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null); mem_gb=${mem_gb:-4}
cores=$(nproc 2>/dev/null || echo 2)
JOBS=${ORB3_JOBS:-$(( mem_gb / 2 < cores ? mem_gb / 2 : cores ))}
[ "$JOBS" -ge 1 ] 2>/dev/null || JOBS=1
NICE="nice -n ${ORB3_NICE:-10}"
echo "== orbslam3 recipe: ORB-SLAM3 @${ORB3_COMMIT:0:7}, Pangolin $ORB3_PANGOLIN_TAG, -std=$ORB3_CXX_STD, jobs $JOBS (free RAM ${mem_gb} GB, $cores cores) =="
for step in step_apt step_sources step_patch step_pangolin step_orb3 step_vocab step_driver; do
  $step || { echo "ERROR: $step failed (see the output above). Fix and re-run: finished steps are skipped." >&2; exit 1; }
done
check || exit 1
echo "== orbslam3 recipe finished =="
