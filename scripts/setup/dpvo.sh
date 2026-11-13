#!/usr/bin/env bash
# Recipe of the method `dpvo` (the contract every method recipe follows, TASK_all_methods.md T0.1b/T0.3):
#   bash scripts/setup/dpvo.sh --check   fast check, installs nothing; exit 0 = installed and usable
#   bash scripts/setup/dpvo.sh           install (idempotent, safe to re-run): DPVO is part of the base recipe setup_all.sh
#                                        (apt + CUDA 12.1 [sudo, asks once], evo/hf, DPVO @0ac95b6 build, weights, TUM, verify)
# setup_all_really_all.sh --check and scripts/start.sh call this for the registry line `dpvo | mono | ...`.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
case "${1:-}" in
  --check)
    fail=0
    [ -x "$DPVO_VENV/bin/python" ] || { echo "no venv $DPVO_VENV"; fail=1; }
    git -C "$DPVO_DIR" rev-parse --short HEAD 2>/dev/null | grep -q '^0ac95b6' || { echo "no DPVO sources @0ac95b6 in $DPVO_DIR"; fail=1; }
    [ -f "$DPVO_DIR/dpvo.pth" ] || { echo "no weights $DPVO_DIR/dpvo.pth"; fail=1; }
    [ "$fail" = 0 ] || exit 1
    out=$("$DPVO_VENV/bin/python" -c "
import sys, os; sys.path.insert(0, os.environ['DPVO_DIR'])
import torch, cuda_corr, cuda_ba, lietorch_backends, dpvo.dpvo
print('DPVO 0ac95b6, torch', torch.__version__)" 2>&1) || { echo "DPVO does not import (CUDA extensions not built?):"; echo "$out" | tail -n 2; exit 1; }
    echo "$out" | tail -n 1
    ;;
  "") exec bash "$here/setup_all.sh" ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac
