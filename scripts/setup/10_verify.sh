#!/usr/bin/env bash
# Fast checks that the environment is complete. Exit code 0 = all good.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
fail=0
ok()   { printf '  [ok]   %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=1; }
chk()  { local what=$1; shift; if out=$("$@" 2>&1); then ok "$what: $(echo "$out" | head -1)"; else bad "$what"; fi; }

chk "GPU"              nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
chk "nvcc 12.1"        bash -c "$CUDA_HOME/bin/nvcc --version | grep -q 'release 12.1' && $CUDA_HOME/bin/nvcc --version | tail -1"
chk "evo 1.37.1"       bash -c "$SLAM_VENV/bin/evo pkg --version | grep -q 1.37.1 && echo 1.37.1"
chk "hf CLI"           "$SLAM_VENV/bin/hf" version
chk "torch+DPVO import" "$DPVO_VENV/bin/python" -c "
import sys, os; sys.path.insert(0, os.environ['DPVO_DIR'])  # sources: pip install . omits dpvo.loop_closure
import cv2, torch, numpy, cuda_corr, cuda_ba, lietorch_backends, dpvo.dpvo
assert torch.__version__.startswith('2.3.1'), torch.__version__
assert numpy.__version__ == '1.26.4', numpy.__version__
assert torch.cuda.is_available()
print('torch', torch.__version__, 'cuda', torch.version.cuda, 'numpy', numpy.__version__, torch.cuda.get_device_name(0))"
[ -f "$DPVO_DIR/dpvo.pth" ] && ok "weights dpvo.pth" || bad "weights dpvo.pth missing"
git -C "$DPVO_DIR" rev-parse --short HEAD | grep -q 0ac95b6 && ok "DPVO commit 0ac95b6" || bad "DPVO commit differs"
[ -f "$DATA_ROOT/tum/fr1_xyz/rgb.txt" ] && ok "TUM fr1_xyz" || bad "TUM fr1_xyz missing"
chk "eval tests"       bash -c "cd '$SLAM_REPO' && '$SLAM_VENV/bin/python' -I -m unittest discover -s scripts -p test_eval.py 2>&1 | grep -E '^(OK|FAILED)' | head -1 | grep -q '^OK' && echo unit tests OK"
df -h "$DATA_ROOT" 2>/dev/null | tail -1 | awk '{print "  [info] free on data disk: "$4}'
hf auth whoami >/dev/null 2>&1 && ok "HF login" || echo "  [todo] HF not logged in: run 'hf auth login' (you type the token yourself)"
[ $fail -eq 0 ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
exit $fail
