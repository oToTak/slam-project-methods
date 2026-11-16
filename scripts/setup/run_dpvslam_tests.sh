#!/usr/bin/env bash
# Stub test of the dpvslam wrapper (no GPU, no data, no torch).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
py="$SLAM_VENV/bin/python"
[ -x "$py" ] || py=$(command -v python3)
exec "$py" -I "$SLAM_REPO/scripts/dpvslam/test_dpvslam.py"
