#!/usr/bin/env bash
# Recipe of the method `dso` (contract: scripts/setup/dpvo.sh; TASK_all_methods.md T0.1b/T0.3/T5).
#   bash scripts/setup/dso.sh --check      fast check, installs nothing; exit 0 = driver binary and libdso.a exist, sources are at the
#                                          pinned commit, no shared library is missing
#   bash scripts/setup/dso.sh              install everything (idempotent: finished steps are skipped, re-run is safe)
#   bash scripts/setup/dso.sh --apt-only   ONLY the apt step (the only step that needs sudo)
#   bash scripts/setup/dso.sh --no-apt     everything except apt (same as DSO_SKIP_APT=1): no sudo, for PCs where the packages exist.
#                                          The apt step also skips itself when nothing is missing.
# Environment knobs: see scripts/dso/env_dso.sh (DSO_DIR, DSO_PREFIX, DSO_CXX_STD, DSO_MARCH, ...) and
#   DSO_JOBS=<n> parallel compile jobs (default: min(cores, free RAM in GB))   DSO_NICE=<n> nice level (default 10)
#   DSO_FORCE=1 redo finished steps
#
# What it does (NOT TESTED YET: written without building anything, from the sources of the pinned commit):
#   1 apt      build tools + libsuitesparse-dev libeigen3-dev boost system/thread libopencv-dev            [sudo]
#              NO Pangolin, NO libzip: upstream's viewer and zip support are not needed (we have our own headless driver)
#   2 sources  JakobEngel/dso @7b0c99f fetched by commit into $DSO_DIR
#   3 patch    ONE source change: src/IOWrapper/OpenCV/ImageRW_OpenCV.cpp  CV_LOAD_IMAGE_* -> cv::IMREAD_* (OpenCV 4 removed the old
#              names); optional flag rewrites -std= / -march= in CMakeLists.txt when DSO_CXX_STD / DSO_MARCH differ from upstream
#   4 library  cmake + build ONLY the `dso` target (libdso.a) with Pangolin and LibZip switched off (upstream's dso_dataset executable
#              is skipped by its own CMake when Pangolin is missing: that is intended)
#   5 driver   scripts/dso/driver/dso_headless.cc -> $DSO_BIN (headless, per-frame trajectory)
# setup_all_really_all.sh --check and scripts/start.sh call this for the registry line `dso | mono | ...`.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
source "$here/../dso/env_dso.sh"
drv_src="$SLAM_REPO/scripts/dso/driver"
stamps="$DSO_PREFIX/.stamps"

APT_PKGS=(build-essential cmake git pkg-config
          libsuitesparse-dev libeigen3-dev libboost-system-dev libboost-thread-dev libopencv-dev)

check() {
  local fail=0 missing
  [ -x "$DSO_BIN" ] || { echo "no driver binary $DSO_BIN (bash scripts/setup/dso.sh)"; fail=1; }
  [ -f "$DSO_DIR/build/lib/libdso.a" ] || { echo "no $DSO_DIR/build/lib/libdso.a (DSO not built)"; fail=1; }
  git -C "$DSO_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$DSO_COMMIT" || { echo "no DSO sources @${DSO_COMMIT:0:7} in $DSO_DIR"; fail=1; }
  [ "$fail" = 0 ] || return 1
  missing=$(ldd "$DSO_BIN" 2>&1 | grep 'not found' | head -n 3 | tr '\n' ' ')
  [ -z "$missing" ] || { echo "driver has unresolved shared libraries: $missing"; return 1; }
  echo "DSO ${DSO_COMMIT:0:7} (no Pangolin), -std=$DSO_CXX_STD -march=$DSO_MARCH, driver $DSO_BIN"
}

stamp_ok()  { [ "${DSO_FORCE:-0}" != 1 ] && [ -f "$stamps/$1" ] && [ "$(cat "$stamps/$1")" = "$2" ]; }
stamp_set() { mkdir -p "$stamps" && printf '%s' "$2" > "$stamps/$1"; }

# ---- 1 apt (the only step that needs sudo) ------------------------------------------------------------------------------
step_apt() {
  local miss=() p
  for p in "${APT_PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || miss+=("$p")
  done
  if [ ${#miss[@]} -eq 0 ]; then echo "[1 apt] all ${#APT_PKGS[@]} packages are installed: no sudo needed"; return 0; fi
  echo "[1 apt] missing packages: ${miss[*]}"
  if [ "${DSO_SKIP_APT:-0}" = 1 ]; then
    echo "[1 apt] skipped (DSO_SKIP_APT=1 / --no-apt). Install them yourself:  sudo apt-get install -y ${miss[*]}"
    return 0
  fi
  local sudo=""; [ "$(id -u)" = 0 ] || sudo=sudo
  echo "[1 apt] needs root: $sudo apt-get install -y ${miss[*]}   (the sudo password is typed by you, once)"
  $sudo apt-get update && $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${miss[@]}"
}

# ---- 2 sources ---------------------------------------------------------------------------------------------------------
step_sources() {
  mkdir -p "$(dirname "$DSO_DIR")" "$DSO_PREFIX"
  if ! git -C "$DSO_DIR" rev-parse HEAD 2>/dev/null | grep -q "^$DSO_COMMIT"; then
    echo "[2 sources] DSO @${DSO_COMMIT:0:7} -> $DSO_DIR"
    [ -d "$DSO_DIR/.git" ] || { git init -q "$DSO_DIR" && git -C "$DSO_DIR" remote add origin https://github.com/JakobEngel/dso.git; } || return 1
    git -C "$DSO_DIR" fetch --depth 1 origin "$DSO_COMMIT" || git -C "$DSO_DIR" fetch origin || return 1   # by commit; full fetch as fallback
    git -C "$DSO_DIR" checkout -q -f "$DSO_COMMIT" || return 1
  fi
  echo "[2 sources] ok"
}

# ---- 3 patch -----------------------------------------------------------------------------------------------------------
step_patch() {
  local rw="$DSO_DIR/src/IOWrapper/OpenCV/ImageRW_OpenCV.cpp" cm="$DSO_DIR/CMakeLists.txt"
  [ -f "$rw" ] && [ -f "$cm" ] || { echo "[3 patch] DSO sources incomplete in $DSO_DIR"; return 1; }
  sed -i -e 's/CV_LOAD_IMAGE_GRAYSCALE/cv::IMREAD_GRAYSCALE/g' -e 's/CV_LOAD_IMAGE_COLOR/cv::IMREAD_COLOR/g' \
         -e 's/CV_LOAD_IMAGE_UNCHANGED/cv::IMREAD_UNCHANGED/g' "$rw"
  if grep -q 'CV_LOAD_IMAGE' "$rw"; then echo "[3 patch] CV_LOAD_IMAGE_* still present in $rw"; return 1; fi
  sed -E -i -e "s/-std=c\+\+[0-9a-z]+/-std=$DSO_CXX_STD/" -e "s/-march=[A-Za-z0-9_.-]+/-march=$DSO_MARCH/" "$cm"
  grep -q -- "-std=$DSO_CXX_STD" "$cm" && grep -q -- "-march=$DSO_MARCH" "$cm" || { echo "[3 patch] could not set the compiler flags in $cm"; return 1; }
  git -C "$DSO_DIR" diff > "$DSO_PREFIX/dso_local_patches.diff" 2>/dev/null
  echo "[3 patch] OpenCV 4 image flags fixed; -std=$DSO_CXX_STD -march=$DSO_MARCH (diff saved in $DSO_PREFIX/dso_local_patches.diff)"
}

# ---- 4 library ---------------------------------------------------------------------------------------------------------
step_lib() {
  local key="$DSO_COMMIT/$DSO_CXX_STD/$DSO_MARCH"
  if stamp_ok lib "$key" && [ -f "$DSO_DIR/build/lib/libdso.a" ]; then echo "[4 lib] libdso.a already built"; return 0; fi
  echo "[4 lib] cmake + build target dso (jobs $JOBS)"
  cmake -S "$DSO_DIR" -B "$DSO_DIR/build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_DISABLE_FIND_PACKAGE_Pangolin=ON -DCMAKE_DISABLE_FIND_PACKAGE_LibZip=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 || return 1
  $NICE cmake --build "$DSO_DIR/build" --target dso -j "$JOBS" || return 1
  [ -f "$DSO_DIR/build/lib/libdso.a" ] || { echo "build finished but build/lib/libdso.a is missing"; return 1; }
  stamp_set lib "$key"
}

# ---- 5 driver ----------------------------------------------------------------------------------------------------------
step_driver() {
  local key
  key="$DSO_COMMIT/$DSO_CXX_STD/$DSO_MARCH/$(cat "$drv_src/dso_headless.cc" "$drv_src/CMakeLists.txt" | sha1sum | cut -c1-12)"
  if stamp_ok driver "$key" && [ -x "$DSO_BIN" ]; then echo "[5 driver] up to date: $DSO_BIN"; return 0; fi
  echo "[5 driver] build dso_headless"
  cmake -S "$drv_src" -B "$DSO_PREFIX/build-driver" -DCMAKE_BUILD_TYPE=Release -DDSO_DIR="$DSO_DIR" -DDSO_CXX_STD="$DSO_CXX_STD" \
        -DDSO_MARCH="$DSO_MARCH" -DCMAKE_INSTALL_PREFIX="$DSO_PREFIX" || return 1
  $NICE cmake --build "$DSO_PREFIX/build-driver" -j "$JOBS" || return 1
  cmake --install "$DSO_PREFIX/build-driver" || return 1
  [ -x "$DSO_BIN" ] || { echo "driver not installed at $DSO_BIN"; return 1; }
  stamp_set driver "$key"
}

case "${1:-}" in
  --check) check; exit $? ;;
  --apt-only) step_apt; exit $? ;;
  --no-apt) export DSO_SKIP_APT=1 ;;
  "") ;;
  *) echo "usage: $0 [--check | --apt-only | --no-apt]" >&2; exit 2 ;;
esac

mem_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null); mem_gb=${mem_gb:-2}
cores=$(nproc 2>/dev/null || echo 2)
JOBS=${DSO_JOBS:-$(( mem_gb < cores ? mem_gb : cores ))}
[ "$JOBS" -ge 1 ] 2>/dev/null || JOBS=1
NICE="nice -n ${DSO_NICE:-10}"
echo "== dso recipe: DSO @${DSO_COMMIT:0:7}, -std=$DSO_CXX_STD -march=$DSO_MARCH, jobs $JOBS (free RAM ${mem_gb} GB, $cores cores) =="
for step in step_apt step_sources step_patch step_lib step_driver; do
  $step || { echo "ERROR: $step failed (see the output above). Fix and re-run: finished steps are skipped." >&2; exit 1; }
done
check || exit 1
echo "== dso recipe finished =="
