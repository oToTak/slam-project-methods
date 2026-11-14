#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
exec "$SLAM_VENV/bin/python" -I "$SLAM_REPO/scripts/wheel_odom/test_run_wheel_odom.py"
