#!/usr/bin/env bash
# ONE start for BOTH PCs (called by scripts/windows/START.bat). Always runs in the worker clone ~/slam-runner.
#   bash scripts/start.sh             1 update code (git pull --ff-only)  2 settings of this PC (~/.config/slam/runner.conf)
#                                     3 installation check (+ recipe of a missing method)  4 logins  5 what is done  6 queue
#   bash scripts/start.sh --check     steps 1-5 only: installs nothing, starts nothing
#   bash scripts/start.sh --status    step 5 only (STATUS.bat): no pull, no questions
#   --no-pull                         skip step 1
# The only difference between the PCs is runner.conf (direction, keep .mcap). Stop the queue with ONE Ctrl+C.
# Called from another copy (the development repo, old main_start.sh/brother_start.sh): forwards to ~/slam-runner.
set -uo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mode=run; pull=1
for a in "$@"; do
  case "$a" in
    --check) mode=check ;;
    --status) mode=status ;;
    --no-pull) pull=0 ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option '$a' (see --help)" >&2; exit 2 ;;
  esac
done

unset RESULTS_ROOT                       # the worker clone always keeps results in $DATA_ROOT/results (env.sh decides)
source "$repo/scripts/setup/env.sh"
source "$repo/scripts/sync/common.sh"
warn() { echo "  [warn] $*"; }
die() { echo "!! $*" >&2; exit 1; }

if [ ! -f "$repo/.git/slam-runner" ]; then
  if [ -f "$SLAM_RUNNER_DIR/.git/slam-runner" ] && [ -f "$SLAM_RUNNER_DIR/scripts/start.sh" ]; then
    echo "($repo — не клон воркера; запускаю $SLAM_RUNNER_DIR/scripts/start.sh)"
    exec bash "$SLAM_RUNNER_DIR/scripts/start.sh" "$@"
  fi
  die "Нема клону воркера $SLAM_RUNNER_DIR. Один раз: bash $repo/scripts/sync/migrate_to_git.sh --pc main|brother  (scripts/RUNBOOK.md)"
fi
cd "$repo" || exit 1

if [ "$mode" != status ] && busy=$(slam_busy); then
  echo "!! На цьому ПК уже працює черга або метод:"; printf '%s\n' "$busy" | sed 's/^/   /'
  [ "$mode" = run ] && die "Другу копію не запускаю. Подивитись стан: STATUS.bat. Якщо ПК щойно аварійно вимикався — зачекай 2 хв і спробуй знову."
  warn "(--check: продовжую лише перевірки, код не оновлюю)"; pull=0
fi

# ---------------------------------------------------------------- 1. code
if [ "$mode" != status ]; then
  echo "== 1/6 Оновлення коду =="
  head_now=$(git rev-parse --short HEAD 2>/dev/null)
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ -n "${SLAM_START_UPDATED:-}" ]; then
    echo "  код: $head_now (щойно оновлено)"
  elif [ "$pull" = 0 ]; then
    echo "  код: $head_now ($(git log -1 --format=%cd --date=format:'%d.%m.%Y %H:%M' 2>/dev/null)), без git pull"
  elif [ "$branch" != main ]; then
    warn "клон воркера на гілці '$branch', не main: не оновлюю (git -C $repo checkout main)"
  else
    slam_git_auth || true
    if out=$(timeout 180 git pull --ff-only -q origin main 2>&1); then
      head_new=$(git rev-parse --short HEAD)
      if [ "$head_new" != "$head_now" ]; then
        echo "  код оновлено: $head_now -> $head_new"
        git log --oneline "$head_now..$head_new" | head -n 15 | sed 's/^/    /'
        exec env SLAM_START_UPDATED=1 bash "$repo/scripts/start.sh" "$@"     # continue with the NEW start.sh
      fi
      echo "  код уже свіжий: $head_new"
    else
      warn "не вдалось оновити код (мережа, токен або хтось змінив файли в $repo); працюю на поточному $head_now:"
      printf '%s\n' "$out" | tail -n 3 | sed 's/^/    /'
    fi
  fi
fi

# ---------------------------------------------------------------- 2. settings of this PC
[ "$mode" != status ] && echo "== 2/6 Налаштування ПК ($RUNNER_CONF) =="
conf_load; rc=$?
if [ "$rc" = 1 ]; then
  if [ "$mode" = status ]; then
    echo "  (налаштувань $RUNNER_CONF ще нема: показую для напрямку 1 -> 13)"; START_AT=1; DIRECTION=up
  else
    conf_ask || die "Нема $RUNNER_CONF і нема кого спитати (не термінал). Запусти START.bat у вікні або: bash scripts/sync/migrate_to_git.sh --pc main|brother"
    conf_load || die "$RUNNER_CONF: $CONF_ERROR"
  fi
elif [ "$rc" = 2 ]; then
  die "$RUNNER_CONF зіпсований: $CONF_ERROR. Виправ або видали файл (тоді спитаю знову)."
fi
summary="з seq$(printf %02d "$START_AT") $([ "$DIRECTION" = up ] && echo 'вгору' || echo 'вниз'), тримати .mcap: $KEEP_DATA, прогонів: $RUNS, методи: $METHODS"
[ "$mode" != status ] && echo "  $summary"
echo "  результати: $RESULTS_ROOT | дані й логи: $DATA_ROOT"

# ---------------------------------------------------------------- 3. installation
ready=""
if [ "$mode" != status ]; then
  echo "== 3/6 Перевірка встановлення (нічого не ставить) =="
  run_check() { chk_out=$(bash "$repo/scripts/setup/setup_all_really_all.sh" --check 2>&1); printf '%s\n' "$chk_out" | grep -v '^CHECK '; }
  field() { printf '%s\n' "$chk_out" | awk -v k="$1" '$1 == "CHECK" && $2 == k {print (NF > 3 ? $3 " " $4 : $3)}'; }
  run_check
  if [ "$(field base)" != "ok" ]; then
    [ "$mode" = check ] && die "Базове середовище не встановлене. START.bat запустить scripts/setup/setup_all.sh (sudo-пароль вводиш сам)."
    echo "  Ставлю базове середовище: scripts/setup/setup_all.sh (може спитати sudo-пароль: вводиш сам)."
    bash "$repo/scripts/setup/setup_all.sh" || die "setup_all.sh не вдався (лог у $DATA_ROOT/logs/setup_all_*.log). Без нього рахувати нічим."
    run_check; [ "$(field base)" = "ok" ] || die "базове середовище все ще не знаходиться після setup_all.sh"
  fi
  mapfile -t mlines < <(field method)      # not `while read`: a recipe may read stdin (sudo, prompts)
  for line in "${mlines[@]}"; do
    read -r m st <<< "$line"; [ -n "$m" ] || continue
    if [ "$st" = ok ]; then ready="$ready,$m"; continue; fi
    if [ "$st" = norecipe ]; then warn "$m: нема рецепта scripts/setup/$m.sh, цього разу без $m"; continue; fi
    if [ "$mode" = check ]; then echo "  $m не встановлений: START.bat запустить bash scripts/setup/$m.sh"; continue; fi
    echo "  Ставлю $m: bash scripts/setup/$m.sh (може спитати sudo-пароль: вводиш сам)."
    if bash "$repo/scripts/setup/$m.sh" && bash "$repo/scripts/setup/$m.sh" --check >/dev/null 2>&1; then
      echo "  $m: встановлено"; ready="$ready,$m"
    else
      warn "$m не встановився (лог вище), цього разу рахую без нього"
    fi
  done
  ready=${ready#,}
  # what to give the worker: 'all' when nothing is switched off, else only installed methods (keeps a 'method:mode' filter)
  sel=""
  all_methods=$(registry_methods "$repo/scripts/queue/methods.conf" | paste -sd, -)
  if [ "$METHODS" = all ] && [ "$ready" = "$all_methods" ]; then sel=all
  else
    want=$([ "$METHODS" = all ] && echo "$all_methods" || echo "$METHODS")
    for w in ${want//,/ }; do case ",$ready," in *",${w%%:*},"*) sel="$sel,$w" ;; *) warn "$w не готовий: пропускаю" ;; esac; done
    sel=${sel#,}
  fi
  [ "$mode" = run ] && [ -z "$sel" ] && die "Нема жодного встановленого методу з '$METHODS': рахувати нічим."
  echo "  рахуватиму: ${sel:-нічого}"
else
  sel=$METHODS
fi

# ---------------------------------------------------------------- 4. logins
if [ "$mode" != status ]; then
  echo "== 4/6 Входи =="
  if hf auth whoami >/dev/null 2>&1; then echo "  Hugging Face: так"
  elif [ "$mode" = run ] && [ -t 0 ]; then
    echo "  Не залогінений у Hugging Face. Вхід зараз (токен вводиш сам; на 'add token as git credential?' відповідай n):"
    hf auth login || warn "вхід у HF не вдався: черга зупиниться на завантаженні"
  else warn "не залогінений у Hugging Face: ~/.local/bin/hf auth login"; fi
  if [ -s "$SLAM_CONF_DIR/github_token" ]; then echo "  GitHub-токен: є"
  else warn "нема GitHub-токена: інший ПК не бачитиме прогресу цього (bash scripts/report/push_status.sh --token)"; fi
  [ "$REPORT_EVERY" = 0 ] && warn "REPORT_EVERY=0 у $RUNNER_CONF: звіти в гілку runner-$(hostname) вимкнені"
  host=$(echo "${QUEUE_HOST:-$(hostname)}" | tr -c 'A-Za-z0-9_.\n-' '_')
  [ -s "$DATA_ROOT/logs/queue_$host.stop" ] && warn "стоїть ручна межа stop-before = $(cat "$DATA_ROOT/logs/queue_$host.stop"); зняти: bash $repo/scripts/run_queue.sh --set-stop none"
fi

# ---------------------------------------------------------------- 5. what is done (both PCs once the worker reads the other PC's branch)
[ "$mode" != status ] && echo "== 5/6 Що пораховано =="
[ "$mode" = status ] && echo "код: $(git rev-parse --short HEAD 2>/dev/null) ($(git log -1 --format=%cd --date=format:'%d.%m.%Y %H:%M' 2>/dev/null)) | $summary"
qargs=(--from "$START_AT" --dir "$DIRECTION" --methods "${sel:-$METHODS}" --runs "$RUNS")
bash "$repo/scripts/run_queue.sh" --status "${qargs[@]}" || warn "--status не вдався (див. вище)"
if [ "$mode" = status ]; then
  echo; nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || echo "(nvidia-smi недоступний)"
  exit 0
fi

# ---------------------------------------------------------------- 6. run
qargs+=(--report-every "$REPORT_EVERY")
[ "$KEEP_DATA" = yes ] && qargs+=(--keep-data)
if [ "$mode" = check ]; then
  echo "== 6/6 (--check) Черга НЕ запущена. START.bat запустив би:"
  echo "  bash scripts/run_queue.sh ${qargs[*]}"
  exit 0
fi
echo "== 6/6 Рахую. Зупинка: ОДИН раз Ctrl+C і зачекати. Вікно не закривай. =="
exec bash "$repo/scripts/run_queue.sh" "${qargs[@]}"
