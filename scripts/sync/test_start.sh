#!/usr/bin/env bash
# Tests of scripts/start.sh, scripts/sync/migrate_to_git.sh and the --check mode of setup_all_really_all.sh on STUBS:
# a local bare repo instead of GitHub, a fake venv whose python only logs the worker's arguments, a fake dpvo recipe.
# No network, no GPU, nothing installed; everything lives in a temp dir (KEEP_TMP=1 keeps it).
#   bash scripts/sync/test_start.sh        PASS/FAIL per case; exit 0 = all passed
set -uo pipefail
src=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tmp=$(mktemp -d)
[ "${KEEP_TMP:-0}" = 1 ] && echo "temp: $tmp" || trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }
check() { local name=$1; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }
has() { grep -qF -- "$2" "$1"; }

# ---------------------------------------------------------------- isolated environment
export HOME=$tmp/home DATA_ROOT=$tmp/data SLAM_VENV=$tmp/venv QUEUE_HOST=testhost
export SLAM_GIT_URL=$tmp/origin.git START_BUSY_PATTERN='^no-such-process-for-test$'
unset SLAM_REPO RESULTS_ROOT SLAM_RUNNER_DIR SLAM_CONF_DIR RUNNER_CONF DPVO_VENV DPVO_DIR QUEUE_FROM GIT_ASKPASS
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@invalid
mkdir -p "$HOME/.local/bin" "$SLAM_VENV/bin" "$DATA_ROOT"
calls=$tmp/calls.log; : > "$calls"
cat > "$SLAM_VENV/bin/python" <<EOF
#!/bin/sh
echo "python budget=\${DATA_BUDGET_GB:-none} \$*" >> "$calls"
case "\$*" in *--status*) echo "STATUS-TABLE" ;; esac
EOF
printf '#!/bin/sh\nexit 0\n' > "$SLAM_VENV/bin/evo"
cp "$SLAM_VENV/bin/evo" "$SLAM_VENV/bin/hf"; cp "$SLAM_VENV/bin/evo" "$HOME/.local/bin/hf"
chmod +x "$SLAM_VENV/bin/"* "$HOME/.local/bin/hf"

# ---------------------------------------------------------------- "GitHub": work copy with the real scripts + stubs, bare origin
work=$tmp/work
mkdir -p "$work/scripts/setup" "$work/scripts/sync" "$work/scripts/queue" "$work/scripts/windows" "$work/results"
cp "$src/scripts/start.sh" "$src/scripts/run_queue.sh" "$work/scripts/"
cp "$src/scripts/setup/env.sh" "$src/scripts/setup/setup_all_really_all.sh" "$work/scripts/setup/"
cp "$src/scripts/sync/common.sh" "$src/scripts/sync/migrate_to_git.sh" "$work/scripts/sync/"
cp "$src/scripts/windows/"*.bat "$work/scripts/windows/"
cat > "$work/scripts/queue/methods.conf" <<'EOF'
# test registry
dpvo | mono | scripts/dpvo/run.sh | gpu
fakem | mono | scripts/fakem/run.sh | cpu
EOF
cat > "$work/scripts/setup/dpvo.sh" <<EOF
#!/usr/bin/env bash
case "\${1:-}" in
  --check) [ -f "\$DATA_ROOT/dpvo_installed" ] && echo "fake dpvo ok" || { echo "fake dpvo missing"; exit 1; } ;;
  "") echo "recipe dpvo install" >> "$calls"; touch "\$DATA_ROOT/dpvo_installed" ;;
esac
EOF
echo "results of the dev copy" > "$work/results/README.md"
git -C "$work" init -q -b main && git -C "$work" add -A && git -C "$work" commit -qm init
git clone -q --bare "$work" "$tmp/origin.git"
git -C "$work" remote add origin "$tmp/origin.git"
publish() { echo "$1" >> "$work/CHANGES"; git -C "$work" add -A; git -C "$work" commit -qm "$1"; git -C "$work" push -q origin main; }

# ---------------------------------------------------------------- old bundle copy of the brother
old=$HOME/slam-local
mkdir -p "$old/results/dpvo/mono/ucu/seq13"
printf 'method,mode,dataset,seq,run\ndpvo,mono,ucu,seq13,1\n' > "$old/results/summary_testhost.csv"
echo "0 0 0 0 0 0 0 1" > "$old/results/dpvo/mono/ucu/seq13/traj_run1.txt"
echo "template" > "$old/results/README.md"
old_sum=$(cd "$old" && find . -type f -exec md5sum {} + | sort | md5sum)

runner=$HOME/slam-runner
res=$DATA_ROOT/results
conf=$HOME/.config/slam/runner.conf

# ---------------------------------------------------------------- 1. migration (brother)
out=$(bash "$work/scripts/sync/migrate_to_git.sh" --pc brother --no-windows 2>&1); rc=$?
check "migrate: exit 0" [ "$rc" = 0 ]
check "migrate: clone of main" [ -f "$runner/scripts/start.sh" ]
check "migrate: clone marked as worker" [ -f "$runner/.git/slam-runner" ]
check "migrate: summary copied" [ -f "$res/summary_testhost.csv" ]
check "migrate: trajectory copied" [ -f "$res/dpvo/mono/ucu/seq13/traj_run1.txt" ]
check "migrate: template README not copied" [ ! -e "$res/README.md" ]
check "migrate: runner.conf brother preset" grep -qx 'START_AT=13' "$conf"
check "migrate: brother does not keep data" grep -qx 'KEEP_DATA=no' "$conf"
check "migrate: brother disk budget 60" grep -qx 'DATA_BUDGET_GB=60' "$conf"
check "migrate: check ran, installs nothing" bash -c "printf '%s' \"\$1\" | grep -q 'fake dpvo missing' && [ ! -f '$DATA_ROOT/dpvo_installed' ]" _ "$out"
echo "dpvo,mono,ucu,seq12,1" >> "$res/summary_testhost.csv"
out=$(bash "$work/scripts/sync/migrate_to_git.sh" --pc brother --no-windows 2>&1); rc=$?
check "migrate again: exit 0" [ "$rc" = 0 ]
check "migrate again: newer result kept" grep -q seq12 "$res/summary_testhost.csv"
check "migrate again: warns about the differing file" has <(printf '%s' "$out") "summary_testhost.csv"
check "migrate: old copy unchanged" [ "$(cd "$old" && find . -type f -exec md5sum {} + | sort | md5sum)" = "$old_sum" ]

# ---------------------------------------------------------------- 2. env.sh: where results go
check "env: worker clone -> DATA_ROOT/results" [ "$(bash -c "source '$runner/scripts/setup/env.sh'; echo \$RESULTS_ROOT")" = "$res" ]
check "env: dev copy -> its own results/" [ "$(bash -c "source '$work/scripts/setup/env.sh'; echo \$RESULTS_ROOT")" = "$work/results" ]

# ---------------------------------------------------------------- 3. full start: pull + re-exec, install missing, run
publish "second commit"
: > "$calls"
out=$(bash "$runner/scripts/start.sh" < /dev/null 2>&1); rc=$?
check "start: exit 0" [ "$rc" = 0 ]
check "start: pulled main" [ "$(git -C "$runner" rev-parse HEAD)" = "$(git -C "$work" rev-parse HEAD)" ]
check "start: says code updated" has <(printf '%s' "$out") "код оновлено"
check "start: ran the missing recipe" has "$calls" "recipe dpvo install"
check "start: method without recipe switched off" has <(printf '%s' "$out") "fakem: нема рецепта"
check "start: status shown" has "$calls" "--status --from 13 --dir down --methods dpvo --runs 3"
check "start: queue started from 13 down" has "$calls" "--from 13 --dir down --methods dpvo --runs 3 --report-every 10"
check "start: budget 60 passed to the worker" has "$calls" "python budget=60 "
check "start: brother does not keep data" bash -c "! grep -q -- '--keep-data' '$calls'"
check "start: results outside git" has "$calls" "--results-root $res "

# ---------------------------------------------------------------- 4. --check installs and starts nothing
rm -f "$DATA_ROOT/dpvo_installed"; : > "$calls"
out=$(bash "$runner/scripts/start.sh" --check < /dev/null 2>&1); rc=$?
check "check: exit 0" [ "$rc" = 0 ]
check "check: no recipe run" [ ! -f "$DATA_ROOT/dpvo_installed" ]
check "check: queue not started" bash -c "! grep -q -- '--report-every' '$calls'"
check "check: says what it would install" has <(printf '%s' "$out") "START.bat запустить bash scripts/setup/dpvo.sh"
touch "$DATA_ROOT/dpvo_installed"

# ---------------------------------------------------------------- 5. --status: no pull, no questions
publish "third commit"; : > "$calls"
before=$(git -C "$runner" rev-parse HEAD)
out=$(bash "$runner/scripts/start.sh" --status < /dev/null 2>&1); rc=$?
check "status: exit 0" [ "$rc" = 0 ]
check "status: did not pull" [ "$(git -C "$runner" rev-parse HEAD)" = "$before" ]
check "status: table printed" has <(printf '%s' "$out") "STATUS-TABLE"

# ---------------------------------------------------------------- 6. old wrapper / dev copy forwards to the worker clone
out=$(bash "$work/scripts/start.sh" --status < /dev/null 2>&1)
check "forward: dev copy hands over to ~/slam-runner" has <(printf '%s' "$out") "не клон воркера"

# ---------------------------------------------------------------- 7. network down: warn and keep working
mv "$tmp/origin.git" "$tmp/origin.away"; : > "$calls"
out=$(bash "$runner/scripts/start.sh" < /dev/null 2>&1); rc=$?
check "no network: still runs" has "$calls" "--report-every"
check "no network: warns" has <(printf '%s' "$out") "не вдалось оновити код"
mv "$tmp/origin.away" "$tmp/origin.git"

# ---------------------------------------------------------------- 8. all methods installed -> --methods all; main PC preset
printf '#!/bin/sh\n[ "$1" = --check ] && echo fake fakem ok\n' > "$runner/scripts/setup/fakem.sh"
cat > "$conf" <<'EOF'
START_AT=1
KEEP_DATA=no
EOF
: > "$calls"
bash "$runner/scripts/start.sh" --no-pull < /dev/null > /dev/null 2>&1
check "all installed: --methods all, from 1 up, data deleted" has "$calls" "--from 1 --dir up --methods all --runs 3 --report-every 10"
check "main preset: no --keep-data" bash -c "! grep -q -- '--keep-data' '$calls'"
check "no budget key, no tty: runs without a budget" has "$calls" "python budget=none "
printf 'START_AT=1\nKEEP_DATA=no\nDATA_BUDGET_GB=\n' > "$conf"; : > "$calls"
out=$(bash "$runner/scripts/start.sh" --no-pull < /dev/null 2>&1)
check "empty budget = no limit, not asked again" bash -c "grep -q 'python budget=none ' '$calls' && ! printf '%s' \"\$1\" | grep -q 'нема DATA_BUDGET_GB'" _ "$out"
printf 'START_AT=1\nKEEP_DATA=no\nDATA_BUDGET_GB=0\n' > "$conf"
out=$(bash "$runner/scripts/start.sh" --no-pull < /dev/null 2>&1); rc=$?
check "budget 0 refused" bash -c "[ $rc != 0 ] && printf '%s' \"\$1\" | grep -q 'DATA_BUDGET_GB must be'" _ "$out"
rm -f "$runner/scripts/setup/fakem.sh"

# ---------------------------------------------------------------- 9. no settings and nobody to ask: refuse
rm -f "$conf"; : > "$calls"
out=$(bash "$runner/scripts/start.sh" --no-pull < /dev/null 2>&1); rc=$?
check "no conf, no tty: refuses" [ "$rc" != 0 ]
check "no conf, no tty: queue not started" bash -c "! grep -q -- '--report-every' '$calls'"
printf 'START_AT=7\nKEEP_DATA=yes\n' > "$conf"
out=$(bash "$runner/scripts/start.sh" --no-pull < /dev/null 2>&1); rc=$?
check "broken conf: refuses with reason" bash -c "[ $rc != 0 ] && printf '%s' \"\$1\" | grep -q 'START_AT must be 1 or 13'" _ "$out"

# ---------------------------------------------------------------- 9b. first start in a terminal asks direction and budget
if command -v script > /dev/null; then
  rm -f "$conf"
  printf '13\n60\n' | script -qec "bash '$runner/scripts/start.sh' --check --no-pull" /dev/null > /dev/null 2>&1
  check "first start: asks direction and budget" bash -c "grep -qx 'START_AT=13' '$conf' && grep -qx 'DATA_BUDGET_GB=60' '$conf' && grep -qx 'KEEP_DATA=no' '$conf'"
fi

# ---------------------------------------------------------------- 10. syntax of every shell script touched by S3
for f in "$src"/scripts/start.sh "$src"/scripts/*_start.sh "$src"/scripts/*_status.sh "$src"/scripts/run_queue.sh \
         "$src"/scripts/accept_seq01.sh "$src"/scripts/dpvo/run.sh "$src"/scripts/sync/*.sh "$src"/scripts/report/push_status.sh \
         "$src"/scripts/setup/env.sh "$src"/scripts/setup/dpvo.sh "$src"/scripts/setup/setup_all_really_all.sh; do
  check "bash -n ${f#$src/}" bash -n "$f"
done

echo; [ "$fails" = 0 ] && echo "OK: all passed" || echo "FAILED: $fails"
[ "$fails" = 0 ]
