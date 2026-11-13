"""Synthetic tests for scripts/eval.py (no SLAM method needed).

Run in the evo venv:  ~/venvs/slam/bin/python -m unittest scripts/test_eval.py -v
"""
import csv
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval as ev  # noqa: E402  (module named eval.py; shadows nothing we use)


def curve(t):
    """Smooth ~400 m path in metres: gentle S-curve at 1.5 m/s."""
    return np.stack([1.5 * t, 20.0 * np.sin(t / 40.0), 0.5 * np.sin(t / 25.0)], axis=1)


def qwrite(path, t, xyz, header=None):
    with open(path, "w") as f:
        if header:
            f.write(header + "\n")
        for ti, p in zip(t, xyz):
            f.write(f"{ti:.9f} {p[0]:.9f} {p[1]:.9f} {p[2]:.9f} 0 0 0 1\n")


def rot_z(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data" / "ucu_tum" / "seqS"
        self.data.mkdir(parents=True)
        os.environ["DATA_ROOT"] = str(self.root / "data")
        self.res = self.root / "results"
        rng = np.random.default_rng(0)
        self.t0 = 1_700_000_000.0
        self.t_gt = self.t0 + np.arange(0, 270, 1.0)             # 1 Hz, 270 s -> ~405 m
        self.t_cam = self.t0 + np.arange(0, 269.99, 1 / 30)       # 30 Hz camera
        self.sigma = 1.0
        self.gt_noise = rng.normal(0, self.sigma, (len(self.t_gt), 3))
        qwrite(self.data / "groundtruth.txt", self.t_gt, curve(self.t_gt - self.t0) + self.gt_noise,
               "#timestamp x y z qx qy qz qw")
        (self.data / "rgb.txt").write_text("#timestamp path\n" + "".join(
            f"{t:.9f} rgb/{i}.png\n" for i, t in enumerate(self.t_cam)))

    def tearDown(self):
        self.tmp.cleanup()

    def write_traj(self, run, scale=0.37, deg=33.0, keep=1.0, noise=0.0):
        """Estimate = similarity transform of the true curve, per frame (what a mono method would give)."""
        n = int(len(self.t_cam) * keep)
        t = self.t_cam[:n]
        pts = curve(t - self.t0)
        est = scale * (pts @ rot_z(deg).T) + np.array([5.0, -3.0, 2.0])
        if noise:
            est = est + np.random.default_rng(1).normal(0, noise, est.shape)
        d = self.res / "testm" / "mono" / "ucu" / "seqS"
        d.mkdir(parents=True, exist_ok=True)
        qwrite(d / f"traj_run{run}.txt", t, est)
        return d

    def run_eval(self, *extra, mode="mono"):
        args = ev.build_parser().parse_args(
            ["--method", "testm", "--mode", mode, "--dataset", "ucu", "--seq", "seqS",
             "--results-root", str(self.res), "--no-plot", "--commit", "abc1234", *extra])
        return ev.evaluate(args)

    def rows(self, name="summary.csv"):
        with open(self.res / name, newline="") as f:
            return list(csv.DictReader(f))

    # --- helpers
    def test_drift_zero_for_identical(self):
        p = curve(np.arange(0, 270.0))
        d, n = ev.drift_position(p, p, 50.0)
        self.assertEqual(d, 0.0)
        self.assertGreater(n, 100)

    def test_drift_too_short_path(self):
        p = curve(np.arange(0, 10.0))
        d, n = ev.drift_position(p, p, 100.0)
        self.assertTrue(math.isnan(d) and n == 0)

    def test_drift_constant_offset_cancels(self):
        p = curve(np.arange(0, 270.0))
        d, _ = ev.drift_position(p, p + np.array([3.0, 1.0, 0.0]), 50.0)
        self.assertAlmostEqual(d, 0.0, places=9)

    # --- end to end
    def test_perfect_similarity_mono_gives_zero_ate_on_clean_gt(self):
        # GT without noise -> a Sim(3)-transformed estimate must align exactly
        qwrite(self.data / "groundtruth.txt", self.t_gt, curve(self.t_gt - self.t0))
        self.write_traj(1)
        row = self.run_eval()
        self.assertLess(float(row["ate_rmse_m"]), 1e-3)
        self.assertEqual(row["coverage"], "1.00")
        self.assertEqual(row["tracking_lost"], "false")
        self.assertEqual(row["rpe_trans_m"], "")  # never RPE on UCU

    def test_noisy_gt_matches_expected_floor(self):
        # Only GT noise (sigma=1 per axis) -> ATE ~ sigma*sqrt(3), drift ~ sqrt(2)*ATE
        self.write_traj(1)
        row = self.run_eval()
        ate = float(row["ate_rmse_m"])
        self.assertAlmostEqual(ate, self.sigma * math.sqrt(3), delta=0.3)
        ex = self.rows("summary_extra.csv")[0]
        self.assertAlmostEqual(float(ex["drift50_m"]), math.sqrt(2) * ate, delta=0.4)
        self.assertGreater(int(ex["n_seg100"]), 50)
        self.assertEqual(ex["n_matched"], ex["n_possible"])

    def test_se3_mode_does_not_fix_scale(self):
        qwrite(self.data / "groundtruth.txt", self.t_gt, curve(self.t_gt - self.t0))
        self.write_traj(1)
        d = self.write_traj(1)
        row = self.run_eval("--traj", str(d / "traj_run1.txt"), mode="mono_inertial")
        self.assertGreater(float(row["ate_rmse_m"]), 10.0)

    def test_coverage_partial(self):
        self.write_traj(1, keep=0.5)
        row = self.run_eval()
        self.assertAlmostEqual(float(row["coverage"]), 0.5, delta=0.01)

    def test_append_only_and_duplicate_refused(self):
        self.write_traj(1)
        self.run_eval()
        before = (self.res / "summary.csv").read_text()
        self.write_traj(2)
        self.run_eval("--run", "2")
        after = (self.res / "summary.csv").read_text()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(self.rows()), 2)
        with self.assertRaises(SystemExit):
            self.run_eval("--run", "2")
        self.assertEqual((self.res / "summary.csv").read_text(), after)

    def test_default_run_is_latest_and_meta_follows_run(self):
        d = self.write_traj(1)
        self.write_traj(2)
        (d / "meta.yaml").write_text('method: testm\nrun: 2\ncommit: deadbeefcafe\ncoverage: 0.00\n'
                                     'tracking_lost: false\nnotes: ""\n')
        row = self.run_eval("--commit", "")
        self.assertEqual(row["run"], 2)
        self.assertEqual(row["commit"], "deadbee")
        self.assertIn("coverage: 1.00", (d / "meta.yaml").read_text())
        # evaluating the older run must not touch meta.yaml of run 2
        before = (d / "meta.yaml").read_text()
        self.run_eval("--run", "1")
        self.assertEqual((d / "meta.yaml").read_text(), before)

    def test_per_host_summary_has_its_own_sidecar(self):
        self.assertEqual(ev.extra_path_for("r/summary.csv"), Path("r/summary_extra.csv"))
        self.assertEqual(ev.extra_path_for("r/summary_PC1.csv"), Path("r/summary_extra_PC1.csv"))
        self.assertEqual(ev.extra_path_for("r/other.csv"), Path("r/other_extra.csv"))
        self.write_traj(1)
        self.run_eval("--summary", str(self.res / "summary_PC1.csv"))
        self.assertEqual(len(self.rows("summary_PC1.csv")), 1)
        self.assertEqual(len(self.rows("summary_extra_PC1.csv")), 1)
        self.assertFalse((self.res / "summary.csv").exists())
        self.assertFalse((self.res / "summary_extra.csv").exists())

    def test_empty_trajectory_is_full_failure(self):
        d = self.res / "testm" / "mono" / "ucu" / "seqS"
        d.mkdir(parents=True)
        (d / "traj_run1.txt").write_text("")
        row = self.run_eval()
        self.assertEqual(row["tracking_lost"], "true")
        self.assertEqual(row["ate_rmse_m"], "")
        self.assertEqual(row["coverage"], "0.00")

    def test_gap_rule_optional(self):
        d = self.write_traj(1)
        lines = (d / "traj_run1.txt").read_text().splitlines()
        (d / "traj_run1.txt").write_text("\n".join(lines[:100] + lines[400:]) + "\n")  # ~10 s hole
        row = self.run_eval()
        self.assertEqual(row["tracking_lost"], "false")
        ex = self.rows("summary_extra.csv")[0]
        self.assertGreater(float(ex["max_gap_s"]), 5.0)
        self.write_traj(2)
        (d / "traj_run2.txt").write_text("\n".join(lines[:100] + lines[400:]) + "\n")
        row2 = self.run_eval("--run", "2", "--lost-gap-s", "1.0")
        self.assertEqual(row2["tracking_lost"], "true")


if __name__ == "__main__":
    unittest.main()
