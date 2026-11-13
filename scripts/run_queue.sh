#!/usr/bin/env bash
# Queue worker: run methods over UCU sequences 1..13 from one end (see scripts/queue/run_queue.py --help).
#   main PC :  bash scripts/run_queue.sh --from 1  --dir up   --methods dpvo --runs 3
#   brother :  bash scripts/run_queue.sh --from 13 --dir down --methods dpvo --runs 3
#   where am I:  bash scripts/run_queue.sh --status           plan only:  add --dry-run
#   meeting point (while it runs):  bash scripts/run_queue.sh --set-stop 8      remove: --set-stop none
# Results go to $RESULTS_ROOT (scripts/setup/env.sh: <repo>/results in a development copy, $DATA_ROOT/results in the worker
# clone ~/slam-runner); an explicit --results-root later on the command line still wins.
# Stop with Ctrl-C (finished runs stay, the unfinished one is dropped); the same command resumes.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/setup/env.sh"
py="$SLAM_VENV/bin/python"
[ -x "$py" ] || py=$(command -v python3)
exec "$py" -u "$here/queue/run_queue.py" --results-root "$RESULTS_ROOT" "$@"
