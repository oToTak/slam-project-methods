"""Tests of the queue worker with stubs: a fake fetch, a fake method, a fake eval and a fake push (no GPU, no evo, no network).

Run in WSL:  bash scripts/setup/run_queue_tests.sh      (or: python3 -I scripts/queue/test_run_queue.py)
Everything happens in a temporary directory; nothing is downloaded, DATA_ROOT and results are redirected.
The other PCs' runner-<host> branches are given as a directory (QUEUE_PEERS_DIR/runner-<host>/...); one test uses
real git with a local file:// repository (skipped when git is missing).
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_queue as rq  # noqa: E402

FAKE_FETCH = """#!/usr/bin/env bash
# fake fetch_ucu.sh: $1 = N. Creates a tiny .mcap and a 10-frame TUM export.
set -e
n=$(( 10#$1 )); seq=$(printf 'seq%02d' "$n")
echo "$n" >> "$FAKE_CALLS/fetch.calls"
[ "${FAKE_FETCH_FAIL:-}" = "$seq" ] && { echo "network down"; exit 1; }
mkdir -p "$DATA_ROOT/ucu/sequence-$n" "$DATA_ROOT/ucu_tum/$seq"
echo x > "$DATA_ROOT/ucu/sequence-$n/data.mcap"
for i in 1 2 3 4 5 6 7 8 9 10; do echo "$i.0 rgb/$i.png"; done > "$DATA_ROOT/ucu_tum/$seq/rgb.txt"
"""

FAKE_RUN = """#!/usr/bin/env bash
# fake run.sh: <ucu> <seqNN> --run R  (method and mode come from QUEUE_METHOD / QUEUE_MODE)
set -e
seq=$2; r=$4
echo "$seq run$r NO_EVAL=$NO_EVAL $QUEUE_METHOD:$QUEUE_MODE" >> "$FAKE_CALLS/run.calls"
[ "${FAKE_RUN_FAIL:-}" = "$seq" ] && { echo "boom"; exit 1; }
d="$RESULTS_ROOT/$QUEUE_METHOD/$QUEUE_MODE/ucu/$seq"; mkdir -p "$d"
[ -e "$d/traj_run$r.txt" ] && { echo "refusing to overwrite"; exit 1; }
n=$(grep -vc '^#' "$DATA_ROOT/ucu_tum/$seq/rgb.txt")
[ "${FAKE_SHORT:-}" = "$seq" ] && n=2
for i in $(seq 1 "$n"); do echo "$i.0 0 0 0 0 0 0 1"; done > "$d/traj_run$r.txt"
"""

FAKE_EVAL = '''
import argparse, csv, os, sys
from pathlib import Path
ap = argparse.ArgumentParser()
for k in ("method", "mode", "dataset", "seq", "run", "summary", "results-root", "log"):
    ap.add_argument("--" + k)
a = ap.parse_args()
open(os.path.join(os.environ["FAKE_CALLS"], "eval.calls"), "a").write(f"{a.seq} run{a.run}\\n")
p = Path(a.summary); new = not p.exists()
with open(p, "a", newline="") as f:
    w = csv.writer(f, lineterminator="\\n")
    if new:
        w.writerow(["method", "mode", "dataset", "seq", "run", "ate_rmse_m", "rpe_trans_m", "coverage", "tracking_lost", "commit"])
    w.writerow([a.method, a.mode, a.dataset, a.seq, a.run, "1.0", "", "1.00", "false", "abc1234"])
'''

FAKE_PUSH = r"""#!/usr/bin/env bash
# fake push_status.sh: logs the call; with PEER_ON_PUSH=<host> that peer claims the same sequence (once),
# as if the other PC had taken it at the same moment.
seq=$(grep -o '"seq": "seq[0-9]*"' "$DATA_ROOT/logs/queue_$QUEUE_HOST.state.json" 2>/dev/null | grep -o 'seq[0-9][0-9]')
echo "force=$PUSH_FORCE seq=${seq:-none}" >> "$FAKE_CALLS/push.calls"
if [ -n "${PEER_ON_PUSH:-}" ] && [ -n "$seq" ] && [ ! -e "$FAKE_CALLS/peer_claimed" ]; then
  b="$QUEUE_PEERS_DIR/runner-$PEER_ON_PUSH"; mkdir -p "$b"
  printf '{"pid": 4242, "host": "%s", "seq": "%s"}\n' "$PEER_ON_PUSH" "$seq" > "$b/queue_state.json"
  date +%s > "$b/.commit_time"; touch "$FAKE_CALLS/peer_claimed"
fi
echo "pushed -> runner-$QUEUE_HOST"
"""

HEADER = "method,mode,dataset,seq,run,ate_rmse_m,rpe_trans_m,coverage,tracking_lost,commit\n"


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data, self.res, self.calls = self.root / "data", self.root / "results", self.root / "calls"
        for d in (self.data, self.res, self.calls):
            d.mkdir()
        (self.root / "fetch.sh").write_text(FAKE_FETCH)
        (self.root / "run.sh").write_text(FAKE_RUN)
        (self.root / "eval.py").write_text(FAKE_EVAL)
        (self.root / "push.sh").write_text(FAKE_PUSH)
        (self.root / "methods.conf").write_text(f"fake | mono | {self.root / 'run.sh'} | cpu\n")
        self.peers = self.root / "peers_src"                  # stands for origin's runner-* branches
        self.peers.mkdir()
        self.env = dict(os.environ, DATA_ROOT=str(self.data), QUEUE_HOST="T1", FAKE_CALLS=str(self.calls),
                        QUEUE_FETCH_CMD=f"bash {self.root / 'fetch.sh'}", QUEUE_PEERS_DIR=str(self.peers),
                        QUEUE_PUSH_CMD=f"bash {self.root / 'push.sh'}", REPORT_REPO=str(self.root / "no-remote.git"),
                        QUEUE_WIN_FREE_GB="10000")             # no powershell.exe in tests
        for k in ("FAKE_FETCH_FAIL", "FAKE_RUN_FAIL", "FAKE_SHORT", "PEER_ON_PUSH", "DATA_BUDGET_GB"):
            self.env.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self, *args, methods="fake", disk_check=False, **extra_env):
        cmd = [sys.executable, "-u", str(HERE / "run_queue.py"), "--registry", str(self.root / "methods.conf"),
               "--results-root", str(self.res), "--eval-script", str(self.root / "eval.py"),
               "--methods", methods, "--report-every", "0", "--no-keepawake", *args]
        if not disk_check:
            cmd.append("--no-disk-check")
        env = dict(self.env, **extra_env)
        for k in [k for k, v in env.items() if v is None]:
            del env[k]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)

    def lines(self, name):
        p = self.calls / name
        return p.read_text().split() if p.exists() and p.read_text() else []

    def run_calls(self):
        p = self.calls / "run.calls"
        return p.read_text().splitlines() if p.exists() else []

    def rows(self, name="summary_T1.csv"):
        with open(self.res / name, newline="") as f:
            return list(csv.DictReader(f))

    def peer(self, host, rows=(), seq=None, age_s=60):
        """Branch runner-<host>: summary rows (method, mode, seq, run), optional claim of `seq`, last commit age_s ago."""
        b = self.peers / f"runner-{host}"
        (b / "results").mkdir(parents=True, exist_ok=True)
        (b / "results" / f"summary_{host}.csv").write_text(
            HEADER + "".join(f"{m},{mode},ucu,{s},{r},1.0,,1.00,false,abc\n" for m, mode, s, r in rows))
        if seq:
            (b / "queue_state.json").write_text(json.dumps({"pid": 99, "host": host, "seq": seq, "since": "x"}))
        (b / ".commit_time").write_text(str(int(time.time() - age_s)))
        return b

    # --- pure helpers

    def test_sequence_order(self):
        self.assertEqual(rq.sequence_order(1, "up", 4), [1, 2, 3])
        self.assertEqual(rq.sequence_order(13, "down", 10), [13, 12, 11])
        self.assertEqual(rq.sequence_order(12, "up"), [12, 13])
        self.assertEqual(rq.sequence_order(2, "down"), [2, 1])
        self.assertEqual(rq.sequence_order(5, "up", 5), [])

    def test_registry_parse_and_errors(self):
        reg = rq.load_registry(self.root / "methods.conf")
        self.assertEqual(reg["fake:mono"].mode, "mono")
        self.assertFalse(reg["fake:mono"].gpu)
        self.assertIsNone(reg["fake:mono"].runs)
        self.assertTrue(rq.load_registry(HERE / "methods.conf")["dpvo:mono"].gpu)
        for bad in ("x | mono | run.sh\n", "x | mono | run.sh | cpu | 0\n", "x | mono | run.sh | cpu | two\n",
                    "x:y | mono | run.sh | cpu\n", "x | mono | a.sh | cpu\nx | mono | b.sh | gpu\n"):
            (self.root / "bad.conf").write_text(bad)
            with self.assertRaises(SystemExit, msg=bad):
                rq.load_registry(self.root / "bad.conf")

    def test_registry_two_modes_and_method_selection(self):
        (self.root / "two.conf").write_text("a | mono | r.sh | gpu\na | inertial | r.sh | cpu | 1\nb | mono | r.sh | cpu\n")
        reg = rq.load_registry(self.root / "two.conf")
        self.assertEqual(list(reg), ["a:mono", "a:inertial", "b:mono"])
        self.assertEqual(reg["a:inertial"].runs, 1)
        self.assertIsNone(reg["a:inertial"].pose_tol)
        (self.root / "tol.conf").write_text("a | mono | r.sh | cpu | - | 0.2\na | x | r.sh | cpu | 2 | 0.5\n")
        tol = rq.load_registry(self.root / "tol.conf")
        self.assertEqual((tol["a:mono"].runs, tol["a:mono"].pose_tol, tol["a:x"].runs, tol["a:x"].pose_tol), (None, 0.2, 2, 0.5))
        (self.root / "badtol.conf").write_text("a | mono | r.sh | cpu | 3 | 0\n")
        with self.assertRaises(SystemExit):
            rq.load_registry(self.root / "badtol.conf")
        self.assertEqual(rq.select_methods(reg, "a"), ["a:mono", "a:inertial"])
        self.assertEqual(rq.select_methods(reg, "a:inertial"), ["a:inertial"])
        self.assertEqual(rq.select_methods(reg, "b,a:mono"), ["a:mono", "b:mono"])          # registry order
        self.assertEqual(rq.select_methods(reg, "all"), list(reg))
        for bad in ("c", "a:stereo", "", " , "):
            with self.assertRaises(SystemExit, msg=bad):
                rq.select_methods(reg, bad)

    def test_disk_need_rule(self):
        os.environ["DATA_ROOT"] = str(self.data)
        ns = type("N", (), {"results_root": str(self.res), "registry": str(self.root / "methods.conf"), "methods": "fake"})()
        ctx = rq.Ctx(ns)
        self.assertAlmostEqual(rq.disk_need_gb(ctx, 10), 19.5 * 1.35 + rq.DISK_RESERVE_GB, places=6)
        ctx.export_dir(10).mkdir(parents=True)
        (ctx.export_dir(10) / "rgb.txt").write_text("1.0 a\n")
        self.assertEqual(rq.disk_need_gb(ctx, 10), 0.0)

    def test_method_needing_odom_fetches_an_export_without_it(self):
        """wheel_odom needs odom.txt: an old export (rgb.txt only) is completed by fetch; a complete one is not fetched again."""
        run = self.root / "run.sh"
        (self.root / "methods.conf").write_text(f"wheel_odom | odom | {run} | cpu | 1\nfake | mono | {run} | cpu\n")

        def export(n, odom):
            d = self.data / "ucu_tum" / f"seq{n:02d}"
            d.mkdir(parents=True)
            (d / "rgb.txt").write_text("".join(f"{i}.0 rgb/{i}.png\n" for i in range(1, 11)))
            if odom:
                (d / "odom.txt").write_text("1.0 0 0 0 0 0 0 1\n")
        export(1, odom=False)
        export(2, odom=True)
        export(3, odom=False)
        r = self.queue("--only", "1", "--keep-data", methods="wheel_odom")        # odom.txt missing -> fetched
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1"])
        r = self.queue("--only", "2", "--keep-data", methods="wheel_odom")        # complete export -> not fetched
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1"])
        r = self.queue("--only", "3", "--keep-data", methods="fake")              # method without that need -> not fetched
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1"])

    # --- worker behaviour

    def test_up_from_one_stops_before_boundary_and_cleans_up(self):
        r = self.queue("--from", "1", "--dir", "up", "--stop-before", "3", "--runs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])
        self.assertEqual(len(self.rows()), 4)                                  # 2 seqs x 2 runs
        self.assertEqual(sorted({x["seq"] for x in self.rows()}), ["seq01", "seq02"])
        self.assertIn("NO_EVAL=1", (self.calls / "run.calls").read_text())
        for n in (1, 2):
            self.assertFalse((self.data / "ucu" / f"sequence-{n}").exists())   # data removed
            self.assertFalse((self.data / "ucu_tum" / f"seq{n:02d}").exists())
            for run in (1, 2):
                self.assertTrue((self.res / "fake/mono/ucu" / f"seq{n:02d}" / f".done_run{run}").exists())
        self.assertFalse((self.res / "fake/mono/ucu/seq03").exists())          # boundary respected
        self.assertTrue(list((self.data / "logs").glob("queue_T1_seq01_*.log")))
        self.assertFalse((self.data / "logs" / "queue_T1.state.json").exists())  # state cleared on exit
        pushes = self.lines("push.calls")
        self.assertIn("seq=seq01", pushes)                                     # claim of each sequence published
        self.assertIn("seq=seq02", pushes)
        self.assertEqual(self.queue("--status").returncode, 0)

    def test_rerun_skips_finished_work_without_fetching(self):
        self.queue("--from", "1", "--stop-before", "3", "--runs", "2")
        n_fetch, n_run = len(self.lines("fetch.calls")), len(self.lines("run.calls"))
        r = self.queue("--from", "1", "--stop-before", "3", "--runs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((len(self.lines("fetch.calls")), len(self.lines("run.calls"))), (n_fetch, n_run))

    def test_down_from_thirteen(self):
        r = self.queue("--from", "13", "--dir", "down", "--stop-before", "11")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["13", "12"])

    def test_legacy_summary_row_counts_as_done(self):
        self.res.mkdir(exist_ok=True)
        (self.res / "summary.csv").write_text(HEADER + "fake,mono,ucu,seq01,1,1.0,,1.00,false,abc\n")
        r = self.queue("--only", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), [])

    def test_failed_run_keeps_data_and_exit_code_2(self):
        r = self.queue("--from", "1", "--stop-before", "3", FAKE_RUN_FAIL="seq02")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue((self.res / "fake/mono/ucu/seq01/.done_run1").exists())
        self.assertFalse((self.data / "ucu" / "sequence-1").exists())
        self.assertTrue((self.data / "ucu" / "sequence-2").exists())           # kept for investigation
        self.assertFalse((self.res / "fake/mono/ucu/seq02/.done_run1").exists())
        r2 = self.queue("--from", "1", "--stop-before", "3")                   # fixed: resumes only seq02
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])                # seq02 export still there: no second fetch
        self.assertFalse((self.data / "ucu" / "sequence-2").exists())

    def test_short_trajectory_is_marked_bad_but_does_not_block_the_queue(self):
        """A method that lost tracking on a hard sequence is a result: flagged, but the sequence completes, data is cleaned up
        and the worker goes on (an unattended night must not stop at the first dusk sequence)."""
        r = self.queue("--from", "1", "--stop-before", "4", FAKE_SHORT="seq01")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.res / "fake/mono/ucu/seq01/.bad_run1").exists())
        self.assertFalse((self.res / "fake/mono/ucu/seq01/.done_run1").exists())
        self.assertFalse((self.data / "ucu_tum" / "seq01").exists())           # cleaned up like any finished sequence
        self.assertEqual(self.lines("fetch.calls"), ["1", "2", "3"])           # went on to the next sequences
        self.assertIn("ПЕРЕВІР", r.stdout)
        st = self.queue("--status", "--only", "1").stdout
        self.assertIn("ПЕРЕВІР", st)
        r2 = self.queue("--from", "1", "--stop-before", "4")                   # nothing to redo
        self.assertEqual(self.lines("fetch.calls"), ["1", "2", "3"])

    def test_fetch_failure_stops_the_queue(self):
        r = self.queue("--from", "1", "--stop-before", "4", FAKE_FETCH_FAIL="seq02")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])                # seq03 never tried

    def test_dry_run_changes_nothing(self):
        r = self.queue("--dry-run", "--from", "1", "--stop-before", "4", "--runs", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("seq01", r.stdout)
        self.assertEqual(self.lines("fetch.calls") + self.lines("run.calls") + self.lines("push.calls"), [])
        self.assertEqual(list(self.res.iterdir()), [])
        self.assertFalse((self.data / "ucu").exists())

    def test_status_shows_position_and_frontier(self):
        self.queue("--from", "1", "--stop-before", "3")
        r = self.queue("--status", "--from", "1", "--dir", "up")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ти тут: 3", r.stdout)
        self.assertIn("seq01 … seq02", r.stdout)
        self.assertIn("fake 1/1 T1", r.stdout)
        for n in range(1, 14):                                                  # the 13-row table is printed
            self.assertIn(f"seq{n:02d}", r.stdout)

    def test_set_stop_file_roundtrip_and_validation(self):
        r = self.queue("--set-stop", "8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.data / "logs" / "queue_T1.stop").read_text().strip(), "8")
        r = self.queue("--from", "1", "--dir", "up", "--dry-run")              # boundary file applies
        self.assertIn("seq07", r.stdout)
        self.assertNotIn("seq08 ", r.stdout.split("порядок:")[1].splitlines()[0])
        self.assertEqual(self.queue("--set-stop", "none").returncode, 0)
        self.assertFalse((self.data / "logs" / "queue_T1.stop").exists())
        bad = self.queue("--from", "5", "--dir", "up", "--stop-before", "3")
        self.assertNotEqual(bad.returncode, 0)

    def test_second_worker_on_same_host_is_refused(self):
        (self.data / "logs").mkdir()
        (self.data / "logs" / "queue_T1.state.json").write_text(f'{{"pid": {os.getpid()}, "seq": "seq05"}}')
        r = self.queue("--only", "1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("уже працює", r.stderr + r.stdout)

    # --- S1.1: registry keyed by method:mode, runs per entry

    def test_two_modes_of_one_method(self):
        run = self.root / "run.sh"
        (self.root / "methods.conf").write_text(f"fake | mono | {run} | cpu\nfake | inertial | {run} | cpu | 1\n")
        r = self.queue("--only", "1", "--runs", "2")                          # 'fake' = both modes
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = sorted((x["mode"], x["run"]) for x in self.rows())
        self.assertEqual(got, [("inertial", "1"), ("mono", "1"), ("mono", "2")])  # inertial: runs column = 1
        for mode, run_no in got:
            self.assertTrue((self.res / "fake" / mode / "ucu/seq01" / f".done_run{run_no}").exists())
        self.assertFalse((self.data / "ucu" / "sequence-1").exists())          # complete in both modes: cleaned
        r = self.queue("--only", "2", methods="fake:inertial")                 # one mode only
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x for x in self.run_calls() if x.startswith("seq02")], ["seq02 run1 NO_EVAL=1 fake:inertial"])
        st = self.queue("--status", "--runs", "2", methods="all").stdout
        self.assertIn("fake:mono 2/2 T1, fake:inertial 1/1 T1", st)
        self.assertNotEqual(self.queue("--status", methods="fake:stereo").returncode, 0)

    # --- S1.2 - S1.5: the other PC

    def test_peer_done_runs_are_skipped_and_cached_outside_results(self):
        self.peer("SD", rows=[("fake", "mono", "seq01", "1"), ("fake", "mono", "seq02", "1")])
        r = self.queue("--from", "1", "--stop-before", "4", "--runs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2", "3"])
        calls = self.run_calls()
        self.assertNotIn("seq01 run1 NO_EVAL=1 fake:mono", calls)              # done on SD
        self.assertIn("seq01 run2 NO_EVAL=1 fake:mono", calls)                 # only the missing run
        self.assertIn("seq03 run1 NO_EVAL=1 fake:mono", calls)
        self.assertTrue((self.data / "peers" / "SD" / "summary_SD.csv").exists())
        self.assertEqual(sorted(p.name for p in self.res.glob("summary*")), ["summary_T1.csv"])  # never in results/
        self.assertFalse((self.res / "fake/mono/ucu/seq01/.done_run1").exists())
        st = self.queue("--status", "--runs", "2").stdout
        self.assertIn("fake 2/2 SD+T1", st)
        self.assertIn("Інший ПК SD", st)
        r = self.queue("--from", "1", "--stop-before", "3")                    # with 1 run everything is done
        self.assertIn("є в SD", r.stdout)

    def test_fresh_peer_claim_is_skipped(self):
        self.peer("SD", seq="seq01", age_s=600)
        dry = self.queue("--dry-run", "--from", "1", "--stop-before", "3")
        self.assertIn("пропустити (рахує SD)", dry.stdout)
        r = self.queue("--from", "1", "--stop-before", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["2"])
        self.assertIn("зараз рахує SD", r.stdout)
        st = self.queue("--status", "--from", "1").stdout
        self.assertIn("рахує SD", st)
        self.assertIn("Інший ПК SD", st)
        self.assertIn("Ти тут: 3", st)                                          # seq01 is SD's, seq02 is done

    def test_stale_peer_claim_does_not_block(self):
        self.peer("SD", rows=[("fake", "mono", "seq01", "1")], seq="seq01", age_s=4 * 3600)
        r = self.queue("--only", "1", "--runs", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1"])
        self.assertEqual(self.run_calls(), ["seq01 run2 NO_EVAL=1 fake:mono"])  # only what SD did not finish
        self.assertIn("вважаю зупиненим", self.queue("--status").stdout)
        self.peer("SD", seq="seq03", age_s=4 * 3600)                           # the same 4 h claim is fresh
        r = self.queue("--only", "3", "--peer-stale-h", "5")                   # when the limit is 5 h
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("3", self.lines("fetch.calls"))

    def test_simultaneous_claim_lower_host_name_keeps_the_sequence(self):
        # A0 sorts before T1: we lose seq01 and go on with seq02
        r = self.queue("--from", "1", "--stop-before", "3", PEER_ON_PUSH="A0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["2"])
        self.assertIn("лишається йому", r.stdout)
        self.assertIn("force=1", self.lines("push.calls"))

    def test_simultaneous_claim_higher_host_name_yields(self):
        # Z9 sorts after T1: the sequence stays ours (Z9 skips it by the same rule)
        r = self.queue("--from", "1", "--stop-before", "3", PEER_ON_PUSH="Z9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])
        self.assertTrue((self.peers / "runner-Z9" / "queue_state.json").exists())

    def test_peer_fetch_failure_uses_last_cache(self):
        self.peer("SD", rows=[("fake", "mono", "seq01", "1")])
        self.assertEqual(self.queue("--status").returncode, 0)                 # fills the cache
        shutil.rmtree(self.peers)                                              # "network down"
        r = self.queue("--from", "1", "--stop-before", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("не вдалось оновити стан інших ПК", r.stdout)
        self.assertEqual(self.lines("fetch.calls"), ["2"])                     # cache still says seq01 is done
        st = self.queue("--status").stdout
        self.assertIn("показую кеш від", st)
        self.assertIn("fake 1/1 SD", st)

    def test_no_peers_flag_ignores_the_other_pc(self):
        self.peer("SD", rows=[("fake", "mono", "seq01", "1")], seq="seq02")
        r = self.queue("--from", "1", "--stop-before", "3", "--no-peers")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])
        self.assertEqual(self.lines("push.calls"), [])                         # --report-every 0 + --no-peers: no push

    # --- disk: DATA_BUDGET_GB and the Windows drive under the WSL vhdx (seq01 needs 6.5 * 1.35 + 5 = 13.8 GB)

    def test_budget_blocks_a_sequence_that_does_not_fit(self):
        r = self.queue("--only", "1", disk_check=True, DATA_BUDGET_GB="10")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("DATA_BUDGET_GB 10 GB", r.stdout)
        self.assertEqual(self.lines("fetch.calls"), [])
        r = self.queue("--only", "1", disk_check=True, DATA_BUDGET_GB="20")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1"])
        self.assertIn("місце: треба ≈ 13.8 GB", r.stdout)

    def test_budget_counts_what_is_already_in_data_root(self):
        (self.data / "other").mkdir()
        with open(self.data / "other" / "big.bin", "wb") as f:              # sparse: 20 GB apparent, ~0 on disk
            f.truncate(20 * 10**9)
        r = self.queue("--only", "1", disk_check=True, DATA_BUDGET_GB="30")  # 30 - 20 = 10 < 13.8
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("зайнято 20.0 GB", r.stdout)
        r = self.queue("--only", "1", disk_check=True, DATA_BUDGET_GB="40")  # 40 - 20 = 20 >= 13.8
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_windows_drive_under_vhdx_limits_and_failure_falls_back_to_df(self):
        r = self.queue("--only", "1", disk_check=True, QUEUE_WIN_FREE_GB="10")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("диск Windows", r.stdout)
        r = self.queue("--only", "1", disk_check=True, QUEUE_WIN_FREE_GB="none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("не вдалось дізнатися вільне місце диска Windows", r.stdout)

    def test_status_and_dry_run_explain_where_the_number_comes_from(self):
        env = dict(DATA_BUDGET_GB="40", QUEUE_WIN_FREE_GB="123")
        st = self.queue("--status", **env).stdout
        self.assertIn("мінімум із: df", st)
        self.assertIn("диск Windows тест під vhdx 123.0 GB", st)
        self.assertIn("бюджет DATA_BUDGET_GB 40 GB − зайнято", st)
        dry = self.queue("--dry-run", "--from", "1", "--stop-before", "5", "--keep-data", **env).stdout
        self.assertIn("місце для даних: доступно 40.0 GB", dry)
        plan = {ln.split()[0]: ln for ln in dry.splitlines() if ln.startswith("seq0")}
        self.assertIn("качати + рахувати", plan["seq01"])                      # 13.8 of 40.0
        self.assertIn("качати + рахувати", plan["seq02"])                      # 18.8 of 31.2 (seq01 data kept)
        self.assertIn("СТОП: не вміститься", plan["seq03"])                    # 18.6 > 17.4
        self.assertIn("не вдалось дізнатися", self.queue("--status", QUEUE_WIN_FREE_GB="none").stdout)
        self.assertEqual(self.queue("--status", DATA_BUDGET_GB="abc").returncode, 1)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_peers_through_real_git_from_a_local_repository(self):
        origin = self.root / "origin"
        g = ["git", "-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@invalid"]
        subprocess.run(["git", "init", "-q", str(origin)], check=True)

        def branch(name, files):
            subprocess.run(g + ["checkout", "-q", "--orphan", name], check=True)
            subprocess.run(g + ["rm", "-rfq", "--ignore-unmatch", "."], check=True)
            for rel, text in files.items():
                (origin / rel).parent.mkdir(parents=True, exist_ok=True)
                (origin / rel).write_text(text)
            subprocess.run(g + ["add", "-A"], check=True)
            subprocess.run(g + ["commit", "-qm", name], check=True)

        branch("runner-SD", {"results/summary_SD.csv": HEADER + "fake,mono,ucu,seq01,1,1.0,,1.00,false,abc\n",
                             "results/fake/mono/ucu/seq01/traj_run1.txt": "1 0 0 0 0 0 0 1\n",
                             "queue_state.json": json.dumps({"pid": 7, "host": "SD", "seq": "seq03"}),
                             "status.txt": "x\n"})
        branch("runner-T1", {"results/summary_T1.csv": HEADER + "fake,mono,ucu,seq02,1,1.0,,1.00,false,abc\n"})
        branch("main-ish", {"README": "not a runner branch\n"})
        env = dict(QUEUE_PEERS_DIR=None, REPORT_REPO=f"file://{origin}")
        r = self.queue("--dry-run", "--from", "1", "--stop-before", "5", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = {ln.split()[0]: ln for ln in r.stdout.splitlines() if ln.startswith("seq0")}
        self.assertIn("пропустити (готово)", plan["seq01"])                    # done on SD
        self.assertIn("качати + рахувати", plan["seq02"])                      # own branch is not a peer
        self.assertIn("пропустити (рахує SD)", plan["seq03"])                  # fresh claim (commit just made)
        self.assertEqual(sorted(p.name for p in (self.data / "peers").iterdir() if not p.name.startswith(".")), ["SD"])
        self.assertEqual(sorted(p.name for p in (self.data / "peers" / "SD").iterdir()),
                         ["meta.json", "queue_state.json", "summary_SD.csv"])
        bad = self.queue("--dry-run", "--only", "1", QUEUE_PEERS_DIR=None, REPORT_REPO=f"file://{self.root}/missing")
        self.assertEqual(bad.returncode, 0, bad.stdout + bad.stderr)           # fetch failed: warning, cache used
        self.assertIn("не вдалось оновити стан інших ПК", bad.stdout)
        self.assertIn("пропустити (готово)", bad.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
