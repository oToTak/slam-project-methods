#!/usr/bin/env bash
# One-time switch of a PC to the worker clone ~/slam-runner (git clone of GitHub main). Same script for both PCs; safe to re-run.
#   bash scripts/sync/migrate_to_git.sh --pc brother   old copy ~/slam-local (bundle); this PC works from seq13 down, keeps .mcap
#   bash scripts/sync/migrate_to_git.sh --pc main      old copy = the development repo; from seq01 up, deletes .mcap after a sequence
# Options: --old DIR      where the old results/ are (default above)
#                         (main: this repo, or /mnt/t/my_repos/slam-project-methods when run from the clone itself)
#          --no-windows   do not touch launchers on the Windows side
#          --dry-run      only print what would be done
# Steps: 1 refuse while a queue/method runs  2 clone or update main in ~/slam-runner (token: ~/.config/slam/github_token)
#   3 COPY old results/ to $DATA_ROOT/results (never deletes, never overwrites)  4 write ~/.config/slam/runner.conf if absent
#   5 installation check of the clone (installs nothing; the installed DPVO env/weights/CUDA are found through env.sh)
#   6 put START.bat and STATUS.bat on the Windows desktop and into C:\calculator-dire (if it exists); old
#     START_SLAM/STATUS_SLAM/START_MAIN/STATUS_MAIN.bat in those two folders are renamed to *.bat.old (not deleted).
# The old copy (~/slam-local or the development repo) is NOT changed. Data, logs, queue state and the report clone
# (~/slam-report) stay where they are ($DATA_ROOT does not move).
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
this_repo=$(cd "$here/../.." && pwd)
pc=""; old=""; windows=1; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pc) pc=${2:-}; shift ;;
    --old) old=${2:-}; shift ;;
    --no-windows) windows=0 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option '$1' (see --help)" >&2; exit 2 ;;
  esac
  shift
done
case "$pc" in main|brother|"") ;; *) echo "--pc must be main or brother" >&2; exit 2 ;; esac
unset RESULTS_ROOT
source "$this_repo/scripts/setup/env.sh"
source "$this_repo/scripts/sync/common.sh"
runner=$SLAM_RUNNER_DIR
new_results=$DATA_ROOT/results
warns=0
say() { echo "$*"; }
warn() { echo "  [warn] $*"; warns=1; }
die() { echo "!! $*" >&2; exit 1; }
act() { if [ "$dry" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

echo "== Перехід на git-клон воркера | host $(hostname) | клон $runner | дані $DATA_ROOT$([ "$dry" = 1 ] && echo ' | DRY RUN') =="

echo "== 1/6 Чи нічого не рахується =="
if busy=$(slam_busy); then
  printf '%s\n' "$busy" | sed 's/^/   /'
  die "Зараз працює черга або метод. Зупини її ОДНИМ Ctrl+C у вікні черги (краще одразу після завершення прогону: STATUS покаже), потім запусти це знову."
fi
echo "  нічого не працює"

echo "== 2/6 Клон main у $runner =="
slam_git_auth && echo "  токен GitHub: є" || echo "  токена GitHub нема (~/.config/slam/github_token): спробую без нього (спрацює, лише якщо репо публічне)"
if [ -d "$runner/.git" ]; then
  url=$(git -C "$runner" remote get-url origin 2>/dev/null)
  [ "$url" = "$SLAM_GIT_URL" ] || warn "origin клону $url, очікував $SLAM_GIT_URL"
  br=$(git -C "$runner" rev-parse --abbrev-ref HEAD)
  [ "$br" = main ] || die "$runner на гілці '$br', не main. Перевір, що там, і зроби: git -C $runner checkout main"
  if act timeout 300 git -C "$runner" pull --ff-only -q origin main; then echo "  клон уже був, оновлено: $(git -C "$runner" log -1 --format='%h %cd' --date=short)"
  else warn "клон уже є, але оновити не вдалось (мережа/токен/локальні зміни). START.bat спробує ще раз."; fi
elif [ -e "$runner" ]; then
  die "$runner існує, але це не git-клон. Перейменуй його (mv $runner ${runner}.old) і запусти знову."
else
  act timeout 900 git clone -q -b main "$SLAM_GIT_URL" "$runner" \
    || die "не вдалось склонувати $SLAM_GIT_URL. Нема мережі або токена: bash $this_repo/scripts/report/push_status.sh --token (вводиш сам), і знову."
  [ "$dry" = 1 ] || echo "  склоновано: $(git -C "$runner" log -1 --format='%h %cd' --date=short)"
fi
act touch "$runner/.git/slam-runner"      # marks the worker clone: env.sh then puts its results in $DATA_ROOT/results

echo "== 3/6 Результати: копія в $new_results (старе не чіпаю) =="
if [ -z "$old" ]; then
  case "$pc" in
    brother) old=$HOME/slam-local ;;
    main) old=$this_repo; [ "$old" = "$runner" ] && old=/mnt/t/my_repos/slam-project-methods ;;
    *) if [ -d "$HOME/slam-local/results" ]; then old=$HOME/slam-local; elif [ "$this_repo" != "$runner" ]; then old=$this_repo; fi ;;
  esac
fi
if [ "$old" = "$runner" ]; then old=""; fi
if [ -n "$old" ] && [ -d "$old/results" ]; then
  act mkdir -p "$new_results"
  n=0
  for e in "$old"/results/* "$old"/results/.[!.]*; do
    [ -e "$e" ] || continue
    case "$(basename "$e")" in README.md|experiments.md|metrics.json|figures|demo|.gitkeep) continue ;; esac   # repo template files, not runs
    act cp -rn --preserve=timestamps "$e" "$new_results/" && n=$((n + 1))
  done
  echo "  з $old/results скопійовано $n елементів (лише те, чого ще не було)"
  if [ "$dry" = 0 ]; then
    differ=$(cd "$old/results" && find . -type f | while read -r f; do [ -f "$new_results/$f" ] && ! cmp -s "$f" "$new_results/$f" && echo "$f"; done)
    [ -n "$differ" ] && warn "ці файли вже були в $new_results і відрізняються від старих; лишив нові, старі в $old/results:" \
      && printf '%s\n' "$differ" | head -n 20 | sed 's/^/      /'
    ls "$new_results"/summary*.csv 2>/dev/null | while read -r s; do echo "    $(basename "$s"): $(( $(wc -l < "$s") - 1 )) рядків"; done
  fi
else
  echo "  старих результатів нема (${old:-не задано}/results): нічого копіювати"
fi

echo "== 4/6 Налаштування цього ПК ($RUNNER_CONF) =="
if [ -f "$RUNNER_CONF" ]; then
  echo "  вже є, не змінюю:"; grep -v '^#' "$RUNNER_CONF" | sed 's/^/    /'
elif [ "$pc" = brother ]; then act conf_write 13 yes; echo "  записано: з 13 вниз, .mcap тримати"
elif [ "$pc" = main ]; then act conf_write 1 no; echo "  записано: з 1 вгору, .mcap видаляти"
else echo "  нема; START.bat спитає два питання при першому запуску"; fi

echo "== 5/6 Що встановлено (нічого не ставлю) =="
if [ -f "$runner/scripts/setup/setup_all_really_all.sh" ]; then
  out=$(env -u RESULTS_ROOT bash "$runner/scripts/setup/setup_all_really_all.sh" --check 2>&1)
  printf '%s\n' "$out" | grep -v '^CHECK '
  printf '%s\n' "$out" | grep -q '^CHECK method .* ok' || warn "жоден метод не знайдено встановленим: START.bat запустить рецепти (може знадобитись sudo-пароль)"
else
  echo "  (клону ще нема: пропускаю)"
fi

echo "== 6/6 Запускачі у Windows =="
if [ "$windows" = 0 ]; then
  echo "  --no-windows: пропускаю"
else
  manual="скопіюй руками START.bat і STATUS.bat з \\\\wsl\$\\Ubuntu-22.04\\home\\$(whoami)\\slam-runner\\scripts\\windows на робочий стіл"
  dirs=() d=""
  # 1) ask Windows (needs WSL interop); 2) else the only C:\Users\<name>\Desktop (or OneDrive\Desktop) there is
  desk=$(powershell.exe -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" 2>/dev/null | tr -d '\r')
  [ -n "$desk" ] && d=$(wslpath -u "$desk" 2>/dev/null) && [ -d "$d" ] || d=""
  if [ -z "$d" ]; then
    cands=()
    for c in /mnt/c/Users/*/OneDrive/Desktop /mnt/c/Users/*/Desktop; do
      case "$c" in /mnt/c/Users/Public/*|/mnt/c/Users/Default*|"/mnt/c/Users/All Users"/*) continue ;; esac
      [ -d "$c" ] && cands+=("$c")
    done
    users=$(for c in "${cands[@]}"; do echo "$c" | cut -d/ -f5; done | sort -u | wc -l)
    [ "$users" = 1 ] && d=${cands[0]}
  fi
  [ -n "$d" ] && dirs+=("$d") || warn "не знайшов робочого столу Windows: $manual"
  [ -d /mnt/c/calculator-dire ] && dirs+=(/mnt/c/calculator-dire)
  stamp=$(date +%Y%m%d_%H%M%S)
  for d in "${dirs[@]}"; do
    for o in START_SLAM STATUS_SLAM START_MAIN STATUS_MAIN; do
      if [ -f "$d/$o.bat" ]; then
        dst="$d/$o.bat.old"; [ -e "$dst" ] && dst="$d/$o.bat.old-$stamp"
        act mv "$d/$o.bat" "$dst" && echo "  старий $d/$o.bat -> $(basename "$dst")"
      fi
    done
    act cp "$runner/scripts/windows/START.bat" "$runner/scripts/windows/STATUS.bat" "$d/" && echo "  START.bat і STATUS.bat -> $d"
  done
fi

cat <<EOF

== Готово$([ "$dry" = 1 ] && echo ' (DRY RUN: нічого не змінено)') ==
$([ "$warns" = 0 ] && echo "Попереджень нема." || echo "Були попередження ([warn] вище): прочитай їх.")
Далі: подвійний клік START.bat на робочому столі (оновить код, перевірить, покаже стан, почне рахувати).
$(if [ "$old" = "$HOME/slam-local" ]; then echo "Старий $old лишився як резерв (не змінений): НЕ запускай звідти нічого, його результати вже скопійовано."
  elif [ -n "$old" ]; then echo "Розробка й далі в $old; рахує лише клон воркера $runner (START.bat; старі START_MAIN.bat/main_start.sh ведуть туди ж)."; fi)
EOF
