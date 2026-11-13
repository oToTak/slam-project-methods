"""Tests of the queue worker with stubs: a fake fetch, a fake method and a fake eval (no GPU, no evo, no network).

Run in WSL:  bash scripts/setup/run_queue_tests.sh      (or: python3 -I scripts/queue/test_run_queue.py)
Everything happens in a temporary directory; nothing is downloaded, DATA_ROOT and results are redirected.
"""
import csv
import os
import subprocess
import sys
import tempfile
import textwrap
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
# fake run.sh: <ucu> <seqNN> --run R
set -e
seq=$2; r=$4
echo "$seq run$r NO_EVAL=$NO_EVAL" >> "$FAKE_CALLS/run.calls"
[ "${FAKE_RUN_FAIL:-}" = "$seq" ] && { echo "boom"; exit 1; }
d="$RESULTS_ROOT/fake/mono/ucu/$seq"; mkdir -p "$d"
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
        (self.root / "methods.conf").write_text(f"fake | mono | {self.root / 'run.sh'} | cpu\n")
        self.env = dict(os.environ, DATA_ROOT=str(self.data), QUEUE_HOST="T1", FAKE_CALLS=str(self.calls),
                        QUEUE_FETCH_CMD=f"bash {self.root / 'fetch.sh'}")
        for k in ("FAKE_FETCH_FAIL", "FAKE_RUN_FAIL", "FAKE_SHORT"):
            self.env.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self, *args, **extra_env):
        cmd = [sys.executable, "-u", str(HERE / "run_queue.py"), "--registry", str(self.root / "methods.conf"),
               "--results-root", str(self.res), "--eval-script", str(self.root / "eval.py"),
               "--methods", "fake", "--no-disk-check", "--report-every", "0", "--no-keepawake", *args]
        return subprocess.run(cmd, env=dict(self.env, **extra_env), capture_output=True, text=True, timeout=120)

    def lines(self, name):
        p = self.calls / name
        return p.read_text().split() if p.exists() and p.read_text() else []

    def rows(self, name="summary_T1.csv"):
        with open(self.res / name, newline="") as f:
            return list(csv.DictReader(f))

    # --- pure helpers

    def test_sequence_order(self):
        self.assertEqual(rq.sequence_order(1, "up", 4), [1, 2, 3])
        self.assertEqual(rq.sequence_order(13, "down", 10), [13, 12, 11])
        self.assertEqual(rq.sequence_order(12, "up"), [12, 13])
        self.assertEqual(rq.sequence_order(2, "down"), [2, 1])
        self.assertEqual(rq.sequence_order(5, "up", 5), [])

    def test_registry_parse_and_errors(self):
        reg = rq.load_registry(self.root / "methods.conf")
        self.assertEqual(reg["fake"].mode, "mono")
        self.assertFalse(reg["fake"].gpu)
        self.assertTrue(rq.load_registry(HERE / "methods.conf")["dpvo"].gpu)
        (self.root / "bad.conf").write_text("x | mono | run.sh\n")
        with self.assertRaises(SystemExit):
            rq.load_registry(self.root / "bad.conf")

    def test_disk_need_rule(self):
        os.environ["DATA_ROOT"] = str(self.data)
        ns =type("N", (), {"results_root": str(self.res), "registry": str(self.root / "methods.conf"), "methods": "fake"})()
        ctx = rq.Ctx(ns)
        self.assertAlmostEqual(rq.disk_need_gb(ctx, 10), 19.5 * 1.35 + rq.DISK_RESERVE_GB, places=6)
        ctx.export_dir(10).mkdir(parents=True)
        (ctx.export_dir(10) / "rgb.txt").write_text("1.0 a\n")
        self.assertEqual(rq.disk_need_gb(ctx, 10), 0.0)

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
        (self.res / "summary.csv").write_text("method,mode,dataset,seq,run,ate_rmse_m,rpe_trans_m,coverage,tracking_lost,commit\n"
                                              "fake,mono,ucu,seq01,1,1.0,,1.00,false,abc\n")
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

    def test_short_trajectory_is_marked_bad_and_blocks_cleanup(self):
        r = self.queue("--only", "1", FAKE_SHORT="seq01")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue((self.res / "fake/mono/ucu/seq01/.bad_run1").exists())
        self.assertFalse((self.res / "fake/mono/ucu/seq01/.done_run1").exists())
        self.assertTrue((self.data / "ucu_tum" / "seq01").exists())
        st = self.queue("--status", "--only", "1").stdout
        self.assertIn("ПЕРЕВІР", st)

    def test_fetch_failure_stops_the_queue(self):
        r = self.queue("--from", "1", "--stop-before", "4", FAKE_FETCH_FAIL="seq02")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.lines("fetch.calls"), ["1", "2"])                # seq03 never tried

    def test_dry_run_changes_nothing(self):
        r = self.queue("--dry-run", "--from", "1", "--stop-before", "4", "--runs", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("seq01", r.stdout)
        self.assertEqual(self.lines("fetch.calls") + self.lines("run.calls"), [])
        self.assertEqual(list(self.res.iterdir()), [])
        self.assertFalse((self.data / "ucu").exists())

    def test_status_shows_position_and_frontier(self):
        self.queue("--from", "1", "--stop-before", "3")
        r = self.queue("--status", "--from", "1", "--dir", "up")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ти тут: 3", r.stdout)
        self.assertIn("seq01 … seq02", r.stdout)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
