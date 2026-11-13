#!/usr/bin/env bash
# Push the queue progress and results of THIS PC to a git branch, so the other side can follow the work.
#   bash scripts/report/push_status.sh              one push now
#   bash scripts/report/push_status.sh --loop 600   push every 600 s until Ctrl-C (run it in its own window)
#   bash scripts/report/push_status.sh --token      store the token (you type it; it is never echoed)
# Pushes ONLY to branch  runner-<host>  (never main). Single writer per branch => no conflicts, no force-push.
# Contents: status.txt (queue --status + GPU), results/ (summary csv, trajectories), fetch_timings.csv, queue state, tail of the newest queue log.
# NOT pushed: hugging-face token, ~/.cache, data (.mcap), full logs.
# Config (env): REPORT_REPO (default below), REPORT_DIR (clone dir), QUEUE_METHODS/QUEUE_FROM/QUEUE_DIR/QUEUE_RUNS (for the status table).
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
REPORT_REPO=${REPORT_REPO:-https://github.com/oToTak/slam-project-methods.git}
REPORT_DIR=${REPORT_DIR:-$HOME/slam-report}
cfg=$HOME/.config/slam; tokfile=$cfg/github_token; askpass=$cfg/git-askpass.sh
host=$(echo "${QUEUE_HOST:-$(hostname)}" | tr -c 'A-Za-z0-9_.\n-' '_')
branch="runner-$host"

if [ "${1:-}" = "--token" ]; then
  mkdir -p "$cfg"; chmod 700 "$cfg"
  read -rsp "GitHub token (fine-grained, ONLY this repo, Contents: read+write; input is hidden): " t; echo
  [ -n "$t" ] || { echo "empty, nothing saved" >&2; exit 1; }
  ( umask 077; printf '%s\n' "$t" > "$tokfile" ); unset t
  echo "saved to $tokfile (chmod 600). Test:  bash $0"; exit 0
fi

use_token=0
case "$REPORT_REPO" in https://*) use_token=1 ;; esac
if [ "$use_token" = 1 ]; then
  [ -s "$tokfile" ] || { echo "No token. Run:  bash $0 --token" >&2; exit 1; }
  printf '#!/bin/sh\ncase "$1" in *sername*) echo x-access-token ;; *) cat "%s" ;; esac\n' "$tokfile" > "$askpass"; chmod 700 "$askpass"
  export GIT_ASKPASS="$askpass"
fi
export GIT_TERMINAL_PROMPT=0
g() { git -C "$REPORT_DIR" "$@"; }

prepare() {
  if [ ! -d "$REPORT_DIR/.git" ]; then
    mkdir -p "$REPORT_DIR" && git -C "$REPORT_DIR" init -q || return 1
    g remote add origin "$REPORT_REPO"
    g config user.name "slam-runner $host"; g config user.email "runner@invalid"; g config gc.auto 0
  fi
  if g ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    g fetch -q origin "$branch" || return 1
    g checkout -q -B "$branch" FETCH_HEAD || return 1
  else
    g checkout -q --orphan "$branch" 2>/dev/null || g checkout -q "$branch" || return 1
  fi
}

snapshot() {
  local d=$REPORT_DIR
  find "$d" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  mkdir -p "$d/results"
  [ -d "$SLAM_REPO/results" ] && cp -r "$SLAM_REPO/results/." "$d/results/"
  [ -f "$DATA_ROOT/logs/fetch_timings.csv" ] && cp "$DATA_ROOT/logs/fetch_timings.csv" "$d/"
  [ -f "$DATA_ROOT/logs/queue_$host.state.json" ] && cp "$DATA_ROOT/logs/queue_$host.state.json" "$d/queue_state.json"
  local newest; newest=$(ls -t "$DATA_ROOT"/logs/queue_"$host"_seq*_*.log 2>/dev/null | head -1)
  [ -n "$newest" ] && tail -n 60 "$newest" > "$d/queue_log_tail.txt"
  {
    echo "host: $host   updated: $(date -Is)   bundle: $(cut -c1-7 "$SLAM_REPO/BUNDLE_COMMIT" 2>/dev/null)"
    echo; bash "$SLAM_REPO/scripts/run_queue.sh" --status \
      --from "${QUEUE_FROM:-13}" --dir "${QUEUE_DIR:-down}" --methods "${QUEUE_METHODS:-dpvo}" --runs "${QUEUE_RUNS:-3}" 2>&1
    echo; nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv 2>&1
  } > "$d/status.txt"
}

push_once() {
  prepare || { echo "$(date +%T) prepare failed (network/token?)" >&2; return 1; }
  snapshot
  g add -A
  if g rev-parse -q --verify HEAD >/dev/null 2>&1; then
    if g diff --cached --quiet; then echo "$(date +%T) no changes"; return 0; fi
    # only volatile status files changed (timestamp, tail of the log): commit at most once per hour
    local real; real=$(g diff --cached --name-only | grep -vE '^(status\.txt|queue_log_tail\.txt|queue_state\.json)$' | head -1)
    local age=$(( $(date +%s) - $(g log -1 --format=%ct) ))
    if [ -z "$real" ] && [ "$age" -lt 3600 ] && [ "${PUSH_FORCE:-0}" != 1 ]; then g reset -q; echo "$(date +%T) only status changed, last push ${age}s ago: skipped"; return 0; fi
  fi
  g commit -q -m "status $host $(date -Is)" || return 1
  if g push -q origin "$branch" 2>&1; then echo "$(date +%T) pushed -> $branch"; else echo "$(date +%T) push FAILED" >&2; return 1; fi
}

if [ "${1:-}" = "--loop" ]; then
  every=${2:-600}; [[ "$every" =~ ^[0-9]+$ ]] && [ "$every" -ge 60 ] || { echo "interval must be >= 60 s" >&2; exit 2; }
  trap 'echo; echo stopped; exit 0' INT TERM HUP
  while true; do push_once || true; sleep "$every" & wait $!; done
fi
push_once
