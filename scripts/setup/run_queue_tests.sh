#!/usr/bin/env bash
# Stub tests of the queue worker (no GPU, no network, no downloads; temporary directory only).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
exec "$SLAM_VENV/bin/python" -I "$SLAM_REPO/scripts/queue/test_run_queue.py"
