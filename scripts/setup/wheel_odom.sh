#!/usr/bin/env bash
# Recipe of the method `wheel_odom` (contract: scripts/setup/dpvo.sh; TASK_all_methods.md T0.1b/T0.3):
#   bash scripts/setup/wheel_odom.sh --check   fast check, installs nothing; exit 0 = usable
#   bash scripts/setup/wheel_odom.sh           nothing to install beyond the base environment (setup_all.sh): numpy + rosbags in
#                                              the slam venv and the pinned official exporter repo (for odom.txt via fetch_ucu.sh)
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
case "${1:-}" in
  --check)
    [ -x "$SLAM_VENV/bin/python" ] || { echo "no venv $SLAM_VENV (base environment: scripts/setup/setup_all.sh)"; exit 1; }
    out=$("$SLAM_VENV/bin/python" -c "import numpy, rosbags; print('wheel_odom: numpy', numpy.__version__, '+ rosbags')" 2>&1) \
      || { echo "numpy/rosbags missing in $SLAM_VENV: $(echo "$out" | tail -n 1)"; exit 1; }
    echo "$out" | tail -n 1
    ;;
  "") exec bash "$here/setup_all.sh" ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac
