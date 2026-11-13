#!/usr/bin/env bash
# Stub tests of the queue worker (no GPU, no network, no downloads; temporary directory only).
# The other PC's runner-* branch is faked with a directory and with a local file:// git repository.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
py="$SLAM_VENV/bin/python"
[ -x "$py" ] || py=$(command -v python3)
exec "$py" -I "$SLAM_REPO/scripts/queue/test_run_queue.py"
