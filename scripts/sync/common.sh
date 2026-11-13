# Shared helpers of scripts/start.sh, scripts/sync/migrate_to_git.sh and scripts/report/push_status.sh.
# Source it AFTER scripts/setup/env.sh (needs SLAM_CONF_DIR).
export SLAM_GIT_URL="${SLAM_GIT_URL:-https://github.com/oToTak/slam-project-methods.git}"
RUNNER_CONF="${RUNNER_CONF:-$SLAM_CONF_DIR/runner.conf}"

# Use the GitHub token of this PC (~/.config/slam/github_token, stored by push_status.sh --token) for git over https.
# Never prompts. Returns 1 when there is no token (a public repo still works without it).
slam_git_auth() {
  local tok="$SLAM_CONF_DIR/github_token" askpass="$SLAM_CONF_DIR/git-askpass.sh"
  export GIT_TERMINAL_PROMPT=0
  [ -s "$tok" ] || return 1
  printf '#!/bin/sh\ncase "$1" in *sername*) echo x-access-token ;; *) cat "%s" ;; esac\n' "$tok" > "$askpass" && chmod 700 "$askpass" || return 1
  export GIT_ASKPASS="$askpass"
}

# Settings of THIS PC (not in git). Sets START_AT (1|13), DIRECTION (up|down), KEEP_DATA (yes|no), RUNS, METHODS, REPORT_EVERY,
# DATA_BUDGET_GB (empty = no limit) and BUDGET_SET (1 if the file has the key at all, so we do not ask again).
# Returns 0 = loaded, 1 = no file, 2 = file is broken (CONF_ERROR says why).
conf_load() {
  START_AT=""; DIRECTION=""; KEEP_DATA=no; RUNS=3; METHODS=all; REPORT_EVERY=10; DATA_BUDGET_GB=""; BUDGET_SET=0; CONF_ERROR=""
  [ -f "$RUNNER_CONF" ] || return 1
  local k v
  while IFS='=' read -r k v || [ -n "$k" ]; do
    k=$(printf '%s' "$k" | tr -d ' \t\r')
    v=$(printf '%s' "${v%%#*}" | tr -d ' \t\r"'"'")
    case "$k" in
      START_AT) START_AT=$v ;;
      KEEP_DATA) KEEP_DATA=$v ;;
      RUNS) RUNS=$v ;;
      METHODS) METHODS=$v ;;
      REPORT_EVERY) REPORT_EVERY=$v ;;
      DATA_BUDGET_GB) DATA_BUDGET_GB=$v; BUDGET_SET=1 ;;
    esac
  done < "$RUNNER_CONF"
  case "$START_AT" in 1) DIRECTION=up ;; 13) DIRECTION=down ;; *) CONF_ERROR="START_AT must be 1 or 13, got '$START_AT'"; return 2 ;; esac
  case "$KEEP_DATA" in yes|no) ;; *) CONF_ERROR="KEEP_DATA must be yes or no, got '$KEEP_DATA'"; return 2 ;; esac
  [[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { CONF_ERROR="RUNS must be a positive number, got '$RUNS'"; return 2; }
  [[ "$METHODS" =~ ^[A-Za-z0-9_:,]+$ ]] || { CONF_ERROR="METHODS must be 'all' or a comma list like dpvo,orbslam3:mono, got '$METHODS'"; return 2; }
  [[ "$REPORT_EVERY" =~ ^[0-9]+([.][0-9]+)?$ ]] || { CONF_ERROR="REPORT_EVERY must be minutes (0 = off), got '$REPORT_EVERY'"; return 2; }
  [ -z "$DATA_BUDGET_GB" ] || [[ "$DATA_BUDGET_GB" =~ ^[0-9]+([.][0-9]+)?$ && ! "$DATA_BUDGET_GB" =~ ^0+([.]0*)?$ ]] \
    || { CONF_ERROR="DATA_BUDGET_GB must be gigabytes > 0, or empty for no limit, got '$DATA_BUDGET_GB'"; return 2; }
  return 0
}

# conf_write START_AT [DATA_BUDGET_GB]   (second argument absent = no DATA_BUDGET_GB line: start.sh asks for it)
conf_write() {
  mkdir -p "$(dirname "$RUNNER_CONF")" && chmod 700 "$(dirname "$RUNNER_CONF")" 2>/dev/null
  cat > "$RUNNER_CONF" <<EOF
# Settings of THIS PC for scripts/start.sh (START.bat). Not in git. Edit by hand, or delete the file to be asked again.
# START_AT: 1 = this PC works from seq01 upwards (main PC), 13 = from seq13 downwards (brother's PC)
START_AT=$1
# KEEP_DATA: no = delete the .mcap and the export once all runs of a sequence are done (both PCs; 6-20 GB of .mcap each);
#            yes = keep them so a method added later does not download again (only with real free space for 13 sequences)
KEEP_DATA=no
# runs per method per sequence (a method may ask for fewer in scripts/queue/methods.conf)
RUNS=3
# all = every method of the registry that is installed; or a comma list (dpvo,orbslam3 or orbslam3:mono_inertial)
METHODS=all
# push progress to the branch runner-<host> every N minutes (0 = off; then the other PC does not see this one)
REPORT_EVERY=10
EOF
  [ $# -ge 2 ] && conf_set_budget "$2"
  return 0
}

# Append/replace DATA_BUDGET_GB (GB the data in DATA_ROOT may take; empty = no limit, only the real free space counts).
conf_set_budget() {
  local tmp="$RUNNER_CONF.tmp"
  grep -v -e '^DATA_BUDGET_GB=' -e '^# DATA_BUDGET_GB:' "$RUNNER_CONF" > "$tmp" 2>/dev/null
  {
    echo "# DATA_BUDGET_GB: at most this many GB of data in DATA_ROOT (the worker skips a sequence that does not fit); empty = no limit"
    echo "DATA_BUDGET_GB=$1"
  } >> "$tmp" && mv "$tmp" "$RUNNER_CONF"
}

# Ask for the disk budget (needs a terminal). Returns 1 if there is nobody to ask.
conf_ask_budget() {
  [ -t 0 ] || return 1
  local g
  echo "  Скільки місця можна зайняти даними на цьому ПК? Дивись реальне вільне місце диска у Windows (Провідник),"
  echo "  а не те, що показує WSL (там віртуальний розмір диска, ~1 ТБ). Найбільший сиквенс у дорозі займає ~26 ГБ."
  while :; do
    read -rp "  Бюджет у ГБ (ПК брата: 60; Enter = без ліміту): " g || return 1
    g=$(printf '%s' "$g" | tr -d ' ')
    if [ -z "$g" ] || [[ "$g" =~ ^[0-9]+([.][0-9]+)?$ && ! "$g" =~ ^0+([.]0*)?$ ]]; then break; fi
    echo "  Введи число гігабайтів більше 0 або просто Enter."
  done
  conf_set_budget "$g"
}

# Ask the questions once (needs a terminal). Returns 1 if there is nobody to ask.
conf_ask() {
  [ -t 0 ] || return 1
  local a
  echo "  Перший запуск на цьому ПК: два питання (відповіді запишу в $RUNNER_CONF, потім можна змінити в ньому)."
  while :; do
    read -rp "  Цей ПК копає з початку (1) чи з кінця (13)? Головний ПК: 1, ПК брата: 13. [1/13]: " a || return 1
    case "$a" in 1|13) break ;; esac; echo "  Введи 1 або 13."
  done
  conf_write "$a" && conf_ask_budget
}

# Methods of the registry (first column of scripts/queue/methods.conf), unique, in file order.
registry_methods() {
  awk -F'|' '/^[[:space:]]*#/ || NF < 3 {next} {gsub(/[[:space:]]/, "", $1); if ($1 != "" && !seen[$1]++) print $1}' "$1"
}

# Is a queue worker or a method process already running on this PC? Prints them. Pattern overridable for tests.
slam_busy() {
  local pat="${START_BUSY_PATTERN:-run_queue[.]py|run_dpvo[.]py}" out
  out=$(pgrep -af "$pat" || true)        # [.] keeps a shell whose command line merely quotes the pattern from matching
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}
