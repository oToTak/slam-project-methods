#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$SLAM_REPO"
exec "$SLAM_VENV/bin/python" -I -m unittest discover -s scripts -p 'test_eval.py' -v
