#!/usr/bin/env bash
# One-command start for the MAIN PC (called by START_MAIN.bat). Queue from sequence 1 upwards, 3 runs, ALL methods of the registry.
# Same worker as on the brother's PC, other end of the range. Stop it with ONE Ctrl+C.
#   bash scripts/main_start.sh          start
#   bash scripts/main_start.sh --check  only check that it is safe to start
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if pgrep -af "run_queue.py|run_dpvo.py" | grep -v pgrep >/dev/null; then
  echo "!! Уже щось працює (черга або DPVO):"; pgrep -af "run_queue.py|run_dpvo.py"
  echo "Не запускаю другу копію. Якщо ти її не запускав і ПК щойно вимикався аварійно — зачекай 2 хв і спробуй знову."
  exit 1
fi
[ -x "$HOME/.local/bin/hf" ] && ! "$HOME/.local/bin/hf" auth whoami >/dev/null 2>&1 && echo "УВАГА: не залогінений у Hugging Face: ~/.local/bin/hf auth login"
[ "${1:-}" = "--check" ] && { echo "OK: можна стартувати."; exit 0; }
echo "=== Старт черги з seq01 вгору. Зупинка: ОДИН раз Ctrl+C і зачекати. Вікно не закривай. ==="
echo "=== Межу зустрічі з братом виставляє:  bash scripts/run_queue.sh --set-stop N  (працює, поки черга йде) ==="
exec bash scripts/run_queue.sh --from 1 --dir up --methods all --runs 3 --report-every 0
