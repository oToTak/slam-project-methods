#!/usr/bin/env bash
# Acceptance test: DPVO on UCU seq01 must give ATE ~ 2.34 m with coverage 1.00 (midterm result on Colab T4).
#   bash scripts/accept_seq01.sh            # downloads seq01 if absent (needs `hf auth login`), runs DPVO, evaluates
# Pass criterion: coverage >= 0.99 and |ATE - 2.34| <= 0.30 m (GPU/driver differences allowed; see PLAN_local.md).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/setup/env.sh"
bash "$here/setup/10_verify.sh" >/dev/null || { echo "environment check failed: run bash scripts/setup/10_verify.sh" >&2; exit 1; }
bash "$here/data/fetch_ucu.sh" 1 --keep-mcap
before=$(wc -l < "$SLAM_REPO/results/summary.csv" 2>/dev/null || echo 0)
bash "$here/dpvo/run.sh" ucu seq01
row=$(tail -n 1 "$SLAM_REPO/results/summary.csv")
echo "summary row: $row"
"$SLAM_VENV/bin/python" - "$row" <<'PY'
import sys
f = sys.argv[1].split(",")
ate, cov, lost = float(f[5]) if f[5] else None, float(f[7]), f[8]
ok = ate is not None and abs(ate - 2.34) <= 0.30 and cov >= 0.99 and lost == "false"
print(f"ATE {ate} m (expected ~2.34), coverage {cov}, tracking_lost {lost} -> {'ACCEPTED' if ok else 'NOT ACCEPTED: investigate before running anything else'}")
sys.exit(0 if ok else 1)
PY
