#!/usr/bin/env bash
# Recipe of the method `dpvslam` (contract: scripts/setup/dpvo.sh; TASK_all_methods.md T0.1b/T0.3):
#   bash scripts/setup/dpvslam.sh --check   fast check, installs nothing; exit 0 = usable
#   bash scripts/setup/dpvslam.sh           installs ONLY missing pure-python packages (pypose numba scipy einops) into the dpvo venv
#                                           with uv, torch/numpy pinned by constraints: no apt, no sudo, no compilation.
# DPV-SLAM is the DPVO repo itself (~/src/DPVO @0ac95b6) with cfg LOOP_CLOSURE=True (proximity loop closure), so it needs the
# DPVO recipe (scripts/setup/dpvo.sh) first. The classic loop closure (DBoW2 + DPRetrieval, cmake/apt) is not used and not checked.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
PKGS="pypose numba scipy einops"        # imported by dpvo/loop_closure/optim_utils.py (all already installed by 05_dpvo_env.sh)

missing_pkgs() {   # prints the pip names that do not import in the dpvo venv
  "$DPVO_VENV/bin/python" - $PKGS <<'PY'
import importlib.util, sys
print(" ".join(p for p in sys.argv[1:] if importlib.util.find_spec(p) is None))
PY
}

check() {
  out=$(bash "$here/dpvo.sh" --check 2>&1) || { echo "DPVO itself is not usable (scripts/setup/dpvo.sh): $(echo "$out" | tail -n 1)"; return 1; }
  miss=$(missing_pkgs 2>&1 | tail -n 1)
  [ -z "$miss" ] || { echo "missing in $DPVO_VENV: $miss (bash scripts/setup/dpvslam.sh installs them)"; return 1; }
  out=$("$DPVO_VENV/bin/python" -c "
import sys, os; sys.path.insert(0, os.environ['DPVO_DIR'])
import torch
from dpvo.config import cfg
assert 'LOOP_CLOSURE' in cfg and 'BACKEND_THRESH' in cfg, 'this DPVO has no LOOP_CLOSURE option'
import dpvo.patchgraph, dpvo.loop_closure.optim_utils
print('DPV-SLAM (DPVO 0ac95b6, LOOP_CLOSURE=True, proximity backend), torch', torch.__version__)" 2>&1) \
    || { echo "DPV-SLAM backend does not import: $(echo "$out" | tail -n 1)"; return 1; }
  echo "$out" | tail -n 1
}

case "${1:-}" in
  --check) check; exit $? ;;
  "")
    [ -x "$DPVO_VENV/bin/python" ] || { echo "no venv $DPVO_VENV: install DPVO first (bash scripts/setup/dpvo.sh)" >&2; exit 1; }
    miss=$(missing_pkgs | tail -n 1)
    if [ -n "$miss" ]; then
      command -v uv >/dev/null || { echo "uv not found (PATH=$HOME/.local/bin): cannot install $miss" >&2; exit 1; }
      cons=$(mktemp); printf 'torch==2.3.1\nnumpy==1.26.4\n' > "$cons"      # never let a dependency move the CUDA build
      echo "installing into $DPVO_VENV: $miss"
      uv pip install --python "$DPVO_VENV/bin/python" --constraint "$cons" $miss; rc=$?
      rm -f "$cons"
      [ "$rc" = 0 ] || { echo "uv pip install failed" >&2; exit 1; }
    fi
    check; exit $? ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac
