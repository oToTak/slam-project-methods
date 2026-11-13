#!/usr/bin/env bash
# One-command start for the brother's PC (called by START_SLAM.bat). Queue from 13 downwards, 3 runs, DPVO.
# The queue itself keeps Windows awake and pushes progress to GitHub; stop it with ONE Ctrl+C.
#   bash scripts/brother_start.sh          start
#   bash scripts/brother_start.sh --check  only check that it is safe to start
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if pgrep -af "run_queue.py|run_dpvo.py" | grep -v pgrep >/dev/null; then
  echo "!! Уже щось працює (черга або DPVO):"; pgrep -af "run_queue.py|run_dpvo.py"
  echo "Не запускаю другу копію. Якщо ти її не запускав і ПК щойно вимикався аварійно — зачекай 2 хв і спробуй знову."
  exit 1
fi
[ -x "$HOME/.local/bin/hf" ] && ! "$HOME/.local/bin/hf" auth whoami >/dev/null 2>&1 && echo "УВАГА: не залогінений у Hugging Face: ~/.local/bin/hf auth login"
[ -s "$HOME/.config/slam/github_token" ] || echo "УВАГА: нема токена GitHub, звіти не підуть (bash scripts/report/push_status.sh --token)"
[ "${1:-}" = "--check" ] && { echo "OK: можна стартувати."; exit 0; }
echo "=== Старт черги. Зупинка: ОДИН раз Ctrl+C і зачекати. Вікно не закривай. ==="
exec bash scripts/run_queue.sh --from 13 --dir down --methods dpvo --runs 3
