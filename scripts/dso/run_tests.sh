#!/usr/bin/env bash
# Stub tests of the pure-python parts of the DSO integration (seconds, CPU only, no DSO / data / GPU needed):
#   bash scripts/dso/run_tests.sh
# Plus a syntax check of the shell scripts of the method (nothing is executed).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
py="$SLAM_VENV/bin/python"; [ -x "$py" ] || py=$(command -v python3)
for f in "$here/run.sh" "$here/env_dso.sh" "$here/../setup/dso.sh" "$here/run_tests.sh"; do bash -n "$f" && echo "bash -n ok: ${f#$SLAM_REPO/}"; done
exec "$py" -I "$here/test_dso.py"
