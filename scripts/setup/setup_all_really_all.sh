#!/usr/bin/env bash
# ONE setup script for BOTH PCs (run inside WSL Ubuntu 22.04, after the Windows part: WSL, NVIDIA driver, .wslconfig).
# Safe to re-run: every step checks what is already done.
#   bash scripts/setup/setup_all_really_all.sh            # the whole setup + checks, prints the exact queue commands
#   bash scripts/setup/setup_all_really_all.sh --accept   # ... and also the seq01 acceptance test (6.5 GB download, ~1.5 h)
# Steps:
#   1/5 machine check (WSL, RAM, free disk, GPU)
#   2/5 scripts/setup/setup_all.sh   (apt + CUDA 12.1 [sudo, once], uv/evo/hf, DPVO @0ac95b6 + CUDA build, weights, TUM sanity, verify)
#   3/5 Hugging Face login (you type the token yourself; skipped when already logged in) + dataset access check
#   4/5 unit tests (run_dpvo, eval, queue with stubs: CPU only, nothing downloaded)
#   5/5 queue dry-run for both ends + the commands to start the work
# Log: $DATA_ROOT/logs/setup_really_all_<time>.log.  New methods get their own recipe next to this file and one call here.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"
accept=0
case "${1:-}" in
  "") ;;
  --accept) accept=1 ;;
  *) echo "usage: $0 [--accept]" >&2; exit 2 ;;
esac
mkdir -p "$DATA_ROOT/logs"
log="$DATA_ROOT/logs/setup_really_all_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$log") 2>&1
warn=0
todo() { echo "  [warn] $*"; warn=1; }
echo "== setup_all_really_all started $(date) | host $(hostname) | repo $SLAM_REPO | data $DATA_ROOT =="

echo "== 1/5 machine check =="
if grep -qi microsoft /proc/version 2>/dev/null; then echo "  WSL: yes"; else todo "this does not look like WSL (native Ubuntu also works, but the guide assumes WSL2)"; fi
if [ "$(. /etc/os-release && echo "$VERSION_ID")" != "22.04" ]; then todo "Ubuntu $(. /etc/os-release && echo "$VERSION_ID"), the recipe is tested on 22.04"; fi
mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
echo "  RAM visible to WSL: ${mem_gb} GB"
[ "$mem_gb" -ge 9 ] || todo "WSL sees only ${mem_gb} GB RAM: the CUDA build will be slow / may be killed. Set memory= in C:\\Users\\<name>\\.wslconfig (scripts/setup/wslconfig.example), then 'wsl --shutdown' in PowerShell"
free_gb=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc '0-9')
echo "  free on the data disk ($DATA_ROOT): ${free_gb} GB"
[ "$free_gb" -ge 50 ] || todo "less than 50 GB free for data: the biggest sequence needs ~26 GB in transit, the environment ~17 GB"
if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then nvidia-smi -L; else
  echo "ERROR: nvidia-smi does not see a GPU in WSL. Update the NVIDIA driver on WINDOWS (not inside WSL), 'wsl --shutdown', retry." >&2; exit 1; fi

echo "== 2/5 environment (setup_all.sh; asks for the sudo password once) =="
bash "$here/setup_all.sh" || { echo "ERROR: setup_all.sh failed (see the log above). Fix and run this script again: it continues where it stopped." >&2; exit 1; }

echo "== 3/5 Hugging Face =="
if hf auth whoami >/dev/null 2>&1; then
  echo "  logged in as: $(hf auth whoami 2>&1 | head -1)"
elif [ -t 0 ]; then
  echo "  Not logged in. Log in now: you type the token (fine-grained read-only; answer 'n' to 'add token as git credential')."
  hf auth login || todo "hf auth login failed or was cancelled"
else
  todo "not logged in to Hugging Face and no terminal to ask: run  ~/.local/bin/hf auth login  yourself, then this script again"
fi
if hf auth whoami >/dev/null 2>&1; then
  "$SLAM_VENV/bin/python" - <<'PY' || todo "no access to the dataset ucu-autonomous-ugv/ucu-slam-dataset-v1 with this account (accept its terms on huggingface.co, or the file name changed)"
from huggingface_hub import get_hf_file_metadata, hf_hub_url
url = hf_hub_url("ucu-autonomous-ugv/ucu-slam-dataset-v1", "sequence-1/metadata.yaml", repo_type="dataset")
m = get_hf_file_metadata(url)          # HEAD request only: nothing is downloaded
print("  dataset access OK (sequence-1/metadata.yaml, %s bytes)" % m.size)
PY
fi

echo "== 4/5 unit tests =="
tests_ok=1
bash "$here/run_dpvo_tests.sh" 2>&1 | tail -n 3 || tests_ok=0
bash "$here/run_queue_tests.sh" 2>&1 | tail -n 6 || tests_ok=0
[ "$tests_ok" = 1 ] && echo "  tests: finished (look for OK above)" || todo "some unit tests failed: scroll up and send the output to whoever helps you"

echo "== 5/5 queue plan (dry run: nothing is downloaded or computed) =="
plan_out=$(bash "$SLAM_REPO/scripts/run_queue.sh" --dry-run --from 1 --dir up --methods dpvo --runs 3 2>&1) || todo "queue dry-run failed"
printf '%s\n' "$plan_out" | head -n 8

if [ "$accept" = 1 ]; then
  echo "== acceptance: seq01 =="
  bash "$SLAM_REPO/scripts/accept_seq01.sh" || todo "acceptance NOT ACCEPTED: DPVO is non-deterministic, repeat per scripts/setup/README.md before worrying"
fi

cat <<EOF

== setup_all_really_all finished $(date) ==
$( [ "$warn" = 0 ] && echo "No warnings." || echo "There were warnings ([warn] lines above): fix them before the long run." )

Start the work (the same command can be re-run after Ctrl-C or a reboot: finished runs are skipped):
  main PC (from the start):   cd $SLAM_REPO && bash scripts/run_queue.sh --from 1  --dir up   --methods dpvo --runs 3
  brother's PC (from the end): cd $SLAM_REPO && bash scripts/run_queue.sh --from 13 --dir down --methods dpvo --runs 3
Where am I / what to tell the other side:  bash scripts/run_queue.sh --status
When you learn where the other PC is (say it is on 8): bash scripts/run_queue.sh --set-stop 8   (works while the worker runs)
Give back at the end: results/summary_$(hostname).csv, results/summary_extra_$(hostname).csv, results/dpvo/, $DATA_ROOT/logs/fetch_timings.csv
Log of this script: $log
EOF
[ "$warn" = 0 ]
