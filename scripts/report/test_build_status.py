"""Tests of build_status.py on synthetic runner-<host> branches in a temporary git repo.

Run:  python3 -I scripts/report/test_build_status.py      (needs only git and the stdlib)
No network; nothing outside a temporary directory is touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_status as bs  # noqa: E402

HEADER = "method,mode,dataset,seq,run,ate_rmse_m,rpe_trans_m,coverage,tracking_lost,commit\n"
CONF_OLD = "# old format\ndpvo | mono | scripts/dpvo/run.sh | gpu\n"
CONF_NEW = (
    "# new format: method | mode | script | gpu|cpu | runs\n"
    "dpvo | mono | scripts/dpvo/run.sh | gpu\n"
    "orbslam3 | mono | scripts/orbslam3/run.sh | cpu | 3\n"
    "orbslam3 | mono_inertial | scripts/orbslam3/run.sh | cpu |\n"
    "wheel_odom | odom | scripts/wheel_odom/run.sh | cpu | 1\n"
)
NOW = "2026-11-13T12:00:00+00:00"


def row(method, mode, seq, run, dataset="ucu"):
    return f"{method},{mode},{dataset},{seq},{run},1.0,,1.00,false,abc1234\n"


class Repo:
    """A bare-bones repo with orphan runner-<host> branches committed at chosen times."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args, date=None):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
        env.update(
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@invalid",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@invalid",
        )
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        r = subprocess.run(
            ["git", "-C", str(self.root), *args], env=env, capture_output=True, text=True
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {args}: {r.stderr}")
        return r.stdout

    def commit_branch(self, host, files, date):
        """Make runner-<host> contain exactly ``files`` ({path: text}) in a new commit at ``date``."""
        branch = f"runner-{host}"
        exists = self.git("branch", "--list", branch).strip()
        if exists:
            self.git("checkout", "-q", branch)
        else:
            self.git("checkout", "-q", "--orphan", branch)
        self.git("rm", "-rqf", "--ignore-unmatch", ".")
        for path, text in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", f"status {host}", date=date)


def status_txt(running):
    return "host: X\n\n" + ("Воркер ПРАЦЮЄ (pid 1): seq12\n" if running else "Воркер не працює\n")


class BuildStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = Repo(self.base / "repo")
        self.conf = self.base / "methods.conf"
        self.conf.write_text(CONF_NEW, encoding="utf-8")
        self.out = self.base / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_build(self, now=NOW, conf=None, extra=()):
        args = ["--repo", str(self.repo.root), "--refs", "refs/heads/runner-*"]
        args += ["--methods-conf", str(conf or self.conf), "--out", str(self.out), "--now", now]
        bs.main([*args, *extra])
        return (
            (self.out / "STATUS.md").read_text(encoding="utf-8"),
            json.loads((self.out / "status.json").read_text(encoding="utf-8")),
        )

    def seed(self):
        # StarDestroyer: seq13 all 3 dpvo runs, seq05 run1, now computing seq12 dpvo run 1
        sd = HEADER + "".join(row("dpvo", "mono", "seq13", r) for r in (1, 2, 3))
        sd += row("dpvo", "mono", "seq05", 1)
        self.repo.commit_branch(
            "StarDestroyer",
            {
                "status.txt": status_txt(True),
                "queue_state.json": json.dumps(
                    {"pid": 1, "host": "StarDestroyer", "seq": "seq12", "method": "dpvo",
                     "run": 1, "since": "2026-11-13T13:00:00", "dir": "down"}
                ),
                "results/summary_StarDestroyer.csv": sd,
                "results/summary_extra.csv": "method,mode\n",
                "results/dpvo/mono/ucu/seq13/traj_run1.txt": "0 0 0 0 0 0 0 1\n",
            },
            "2026-11-13T11:50:00+00:00",
        )
        # TEODOR: legacy summary.csv (seq01 x3, a TUM row), own summary (seq05 run1 = duplicate,
        # seq05 run2), a .done marker for orbslam3 mono seq02 run1, a .bad marker for seq03;
        # stale (last report 5 h before NOW) although its state says running
        legacy = HEADER + "".join(row("dpvo", "mono", "seq01", r) for r in (1, 2, 3))
        legacy += row("dpvo", "mono", "fr1_xyz", 1, dataset="tum")
        own = HEADER + row("dpvo", "mono", "seq05", 1) + row("dpvo", "mono", "seq05", 2)
        own += row("dso", "mono", "seq01", 1)
        self.repo.commit_branch(
            "TEODOR",
            {
                "status.txt": status_txt(True),
                "queue_state.json": json.dumps({"pid": 7, "seq": "seq03", "method": "dpvo", "run": 1}),
                "results/summary.csv": legacy,
                "results/summary_TEODOR.csv": own,
                "results/orbslam3/mono/ucu/seq02/.done_run1": "",
                "results/dpvo/mono/ucu/seq03/.bad_run2": "",
            },
            "2026-11-13T07:00:00+00:00",
        )

    # ------------------------------------------------------------------ registry

    def test_registry_old_and_new_formats(self):
        self.conf.write_text(CONF_OLD, encoding="utf-8")
        self.assertEqual(bs.load_registry(self.conf), [("dpvo:mono", "dpvo", "mono", 3)])
        self.conf.write_text(CONF_NEW + "dso | mono | x.sh | gpu | runs=2\n", encoding="utf-8")
        reg = bs.load_registry(self.conf, default_runs=3)
        self.assertEqual(
            reg,
            [
                ("dpvo:mono", "dpvo", "mono", 3),
                ("orbslam3:mono", "orbslam3", "mono", 3),
                ("orbslam3:mono_inertial", "orbslam3", "mono_inertial", 3),
                ("wheel_odom:odom", "wheel_odom", "odom", 1),
                ("dso:mono", "dso", "mono", 2),
            ],
        )

    def test_registry_bad_line_is_skipped(self):
        self.conf.write_text(CONF_OLD + "broken line\n", encoding="utf-8")
        self.assertEqual(len(bs.load_registry(self.conf)), 1)

    def test_real_methods_conf_parses(self):
        self.assertTrue(bs.load_registry(bs.DEFAULT_CONF))

    # ------------------------------------------------------------------ merging

    def test_merge_two_hosts(self):
        self.seed()
        md, js = self.run_build()
        seqs = {s["seq"]: s["cells"] for s in js["sequences"]}
        dp = {k: v["dpvo:mono"] for k, v in seqs.items()}
        self.assertEqual(dp["seq13"]["done"], 3)
        self.assertEqual(dp["seq01"]["done"], 3)  # legacy summary.csv counts, attributed to TEODOR
        self.assertEqual(dp["seq01"]["runs"]["1"], ["TEODOR"])
        self.assertEqual(dp["seq05"]["done"], 2)  # run1 done by both PCs counts once
        self.assertEqual(dp["seq05"]["runs"]["1"], ["StarDestroyer", "TEODOR"])
        self.assertEqual(dp["seq03"]["bad"], [2])
        self.assertEqual(seqs["seq02"]["orbslam3:mono"]["done"], 1)  # .done marker
        self.assertEqual(seqs["seq02"]["orbslam3:mono_inertial"]["done"], 0)  # modes are separate
        self.assertEqual(seqs["seq01"]["wheel_odom:odom"]["needed"], 1)
        # 3+3+2 dpvo, 1 orbslam3; needed 13*(3+3+3+1)
        self.assertEqual((js["total_done"], js["total_needed"]), (9, 130))
        self.assertIn("**9 з 130**", md)
        self.assertIn("| 13 | seq13 | ✅ 3/3 StarDestroyer |", md)
        self.assertIn("| 5 | seq05 | 2/3 StarDestroyer 1 + TEODOR 2", md)
        self.assertIn("✗1", md)
        # rows outside the table are listed, not silently dropped
        self.assertTrue(any("tum" in x for x in js["other_results"]))
        self.assertTrue(any(x.startswith("dso:mono ucu") for x in js["other_results"]))
        # ATE is not in the table
        self.assertNotIn("ATE", md)
        self.assertNotIn("1.0", md)  # every synthetic row has ate_rmse_m = 1.0

    def test_now_fresh_and_stale(self):
        self.seed()
        md, js = self.run_build()
        sd, te = js["hosts"]["StarDestroyer"], js["hosts"]["TEODOR"]
        self.assertTrue(sd["busy"])
        self.assertEqual((sd["seq"], sd["method"], sd["run"]), ("seq12", "dpvo", 1))
        self.assertTrue(te["stale"])
        self.assertFalse(te["busy"])
        cell12 = js["sequences"][11]["cells"]["dpvo:mono"]
        self.assertEqual(cell12["in_progress"], ["StarDestroyer"])
        self.assertEqual(js["sequences"][2]["cells"]["dpvo:mono"]["in_progress"], [])  # stale claim
        self.assertIn("рахує seq12 · dpvo · прогін 1", md)
        self.assertIn("⚠ звітів нема", md)
        self.assertIn("⏳StarDestroyer", md)

    def test_state_with_method_mode_key(self):
        self.repo.commit_branch(
            "B",
            {
                "status.txt": status_txt(True),
                "queue_state.json": json.dumps({"pid": 1, "seq": "seq04", "method": "orbslam3:mono"}),
            },
            "2026-11-13T11:59:00+00:00",
        )
        _, js = self.run_build()
        cells = js["sequences"][3]["cells"]
        self.assertEqual(cells["orbslam3:mono"]["in_progress"], ["B"])
        self.assertEqual(cells["orbslam3:mono_inertial"]["in_progress"], [])

    def test_idle_worker_and_empty_branch(self):
        # like the real runner-StarDestroyer today: only status.txt, no state, no results
        self.repo.commit_branch("SD", {"status.txt": status_txt(False)}, "2026-11-13T11:59:00+00:00")
        md, js = self.run_build()
        self.assertEqual(js["hosts"]["SD"]["worker"], "idle")
        self.assertEqual(js["total_done"], 0)
        self.assertIn("| SD | не рахує |", md)
        self.assertEqual(list((self.out / "summaries").iterdir()), [])

    def test_no_branches(self):
        md, js = self.run_build()
        self.assertEqual(js["hosts"], {})
        self.assertIn("Жодної гілки", md)

    # ------------------------------------------------------------------ files and change detection

    def test_summaries_copied(self):
        self.seed()
        self.run_build()
        names = sorted(p.name for p in (self.out / "summaries").iterdir())
        self.assertEqual(
            names,
            ["summary_StarDestroyer.csv", "summary_TEODOR.csv", "summary_TEODOR__summary.csv"],
        )
        text = (self.out / "summaries" / "summary_StarDestroyer.csv").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(HEADER) and "seq13" in text)

    def test_rewrite_only_on_content_change(self):
        self.seed()
        md1, js1 = self.run_build()
        # later run, nothing new: files untouched (generation time stays)
        md2, js2 = self.run_build(now="2026-11-13T12:20:00+00:00")
        self.assertEqual((md1, js1), (md2, js2))
        # runner pushes again, only the report time changes: still no change
        sd = self.repo.git("show", "runner-StarDestroyer:results/summary_StarDestroyer.csv")
        state = self.repo.git("show", "runner-StarDestroyer:queue_state.json")
        files = {
            "status.txt": status_txt(True) + "updated later\n",
            "queue_state.json": state,
            "results/summary_StarDestroyer.csv": sd,
        }
        self.repo.commit_branch("StarDestroyer", files, "2026-11-13T12:25:00+00:00")
        md3, js3 = self.run_build(now="2026-11-13T12:30:00+00:00")
        self.assertEqual(md3, md1)
        # a new run finished: rewritten, new report time and new generation time
        files["results/summary_StarDestroyer.csv"] = sd + row("dpvo", "mono", "seq12", 1)
        self.repo.commit_branch("StarDestroyer", files, "2026-11-13T12:35:00+00:00")
        md4, js4 = self.run_build(now="2026-11-13T12:40:00+00:00")
        self.assertNotEqual(js4["content_hash"], js1["content_hash"])
        self.assertEqual(js4["total_done"], js1["total_done"] + 1)
        self.assertIn("2026-11-13 12:40 UTC", md4)
        self.assertIn("2026-11-13 12:35 +0000", md4)

    def test_staleness_flip_is_a_change(self):
        self.repo.commit_branch("SD", {"status.txt": status_txt(False)}, "2026-11-13T11:00:00+00:00")
        _, js1 = self.run_build(now="2026-11-13T12:00:00+00:00")
        _, js2 = self.run_build(now="2026-11-13T13:00:00+00:00")
        self.assertEqual(js1["content_hash"], js2["content_hash"])
        _, js3 = self.run_build(now="2026-11-13T15:00:01+00:00")
        self.assertTrue(js3["hosts"]["SD"]["stale"])
        self.assertNotEqual(js1["content_hash"], js3["content_hash"])

    def test_dir_source(self):
        d = self.base / "dir"
        (d / "results").mkdir(parents=True)
        (d / "status.txt").write_text(status_txt(False), encoding="utf-8")
        (d / "results" / "summary_X.csv").write_text(HEADER + row("dpvo", "mono", "seq07", 2))
        bs.main(
            ["--dir", f"X={d}", "--methods-conf", str(self.conf), "--out", str(self.out), "--now", NOW]
        )
        js = json.loads((self.out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(js["sequences"][6]["cells"]["dpvo:mono"]["runs"], {"2": ["X"]})

    def test_cli_as_subprocess(self):
        self.seed()
        r = subprocess.run(
            [sys.executable, "-I", str(HERE / "build_status.py"), "--repo", str(self.repo.root),
             "--refs", "refs/heads/runner-*", "--methods-conf", str(self.conf),
             "--out", str(self.out), "--now", NOW],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("written", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
