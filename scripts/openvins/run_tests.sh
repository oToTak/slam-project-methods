#!/usr/bin/env bash
# Stub tests of the pure-python parts of the OpenVINS integration (seconds, CPU only, no OpenVINS / ROS / data / GPU needed):
#   bash scripts/openvins/run_tests.sh
# Plus a syntax check of the shell scripts of the method (nothing is executed).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
py="$SLAM_VENV/bin/python"; [ -x "$py" ] || py=$(command -v python3)
for f in "$here/run.sh" "$here/env_openvins.sh" "$here/../setup/openvins.sh" "$here/run_tests.sh"; do bash -n "$f" && echo "bash -n ok: ${f#$SLAM_REPO/}"; done
exec "$py" -I "$here/test_openvins.py"
