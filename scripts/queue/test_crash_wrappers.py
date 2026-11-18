"""Tests of how the run scripts treat a crashing / diverging method binary (stub binaries, no real ORB-SLAM3 / OpenVINS, no data download).

Why: on the brother's PC seq10-13 stopped the whole queue because
  * ORB-SLAM3 mono_inertial segfaulted in SaveTrajectoryEuRoC (the final atlas map was empty after a reset: exit 139) and
  * OpenVINS aborted itself with std::exit(1) ('StateHelper::EKFUpdate() - diagonal at N is -0.00') when the filter diverged.
Both are results OF THE METHOD, so the run scripts must turn them into exit 4 ('no usable trajectory', recorded by the worker) or, when
poses exist, into a normal trajectory; anything else that fails must stay a failure (exit != 0, != 4).

Run in WSL:  python3 -I scripts/queue/test_crash_wrappers.py      (POSIX + bash only; skipped elsewhere)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

STUBS = {
    # ORB-SLAM3 driver stubs (args are ignored); the real one prints ORB3_STATS when the tracking loop is finished
    "orb_segv_end": 'echo "ORB3_STATS frames=10 ever_ok=1 state_hist=1:8,2:2 track_s=1 wall_s=1"\necho "Saving trajectory ..."\nkill -SEGV $$\n',
    "orb_segv_mid": 'echo "tracking..."\nkill -SEGV $$\n',
    "orb_oom": 'echo "tracking..."\nkill -KILL $$\n',
    "orb_fail1": 'echo boom\nexit 1\n',
    # OpenVINS driver stubs: args  cfg rgb imu raw
    "ov_diverge": ('raw=$4\nfor i in 3 4 5 6; do echo "$i 1.0 2.0 3.0 0 0 0 1 0 0 0 1 0.05 0.05 0.0" >> "$raw"; done\n'
                   "printf '7 1.0 2.0' >> \"$raw\"\n"                                  # last line cut by the lost output buffer
                   'echo "q_GtoI = 0.9 | p_IinG = -3.5e4 | dist = 42494.91 (meters)"\n'
                   "printf '\\033[31mStateHelper::EKFUpdate() - diagonal at 90 is -0.00\\n\\033[0m'\nexit 1\n"),
    "ov_diverge_nopose": "printf '\\033[31mStateHelper::EKFUpdate() - diagonal at 90 is -0.00\\n\\033[0m'\nexit 1\n",
    "ov_cfgerr": 'echo "unable to parse all parameters"\nexit 1\n',
}


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "needs POSIX + bash (run it in WSL)")
class CrashWrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.root = Path(self.tmp.name)
        self.data, self.res, self.bin = t / "data", t / "res", t / "bin"
        seq = self.data / "ucu_tum" / "seq99"
        for d in (seq, self.res, self.bin):
            d.mkdir(parents=True)
        (seq / "rgb.txt").write_text("".join(f"{100 + i}.0 rgb/{i}.png\n" for i in range(10)))
        with open(seq / "imu.txt", "w") as f:                                     # 125 Hz, covers all frames
            for k in range(int((110.5 - 99.9) / 0.008) + 1):
                f.write(f"{99.9 + k * 0.008:.6f} 0.0 0.0 -9.8 0.0 0.0 0.0\n")
        (self.bin / "ORBvoc.txt").write_text("vocab\n")
        for name, body in STUBS.items():
            p = self.bin / name
            p.write_text("#!/usr/bin/env bash\n" + body)
            p.chmod(0o755)
        self.env = dict(os.environ, DATA_ROOT=str(self.data), RESULTS_ROOT=str(self.res), NO_EVAL="1", QUEUE_HOST="test",
                        ORB3_VOCAB=str(self.bin / "ORBvoc.txt"))

    def tearDown(self):
        self.tmp.cleanup()

    def orb(self, stub):
        env = dict(self.env, ORB3_BIN=str(self.bin / stub), QUEUE_MODE="mono_inertial")
        r = subprocess.run(["bash", str(REPO / "scripts/orbslam3/run.sh"), "ucu", "seq99", "--run", "1"], env=env,
                           capture_output=True, text=True, timeout=120)
        return r, self.res / "orbslam3/mono_inertial/ucu/seq99"

    def ov(self, stub):
        env = dict(self.env, OV_BIN=str(self.bin / stub))
        r = subprocess.run(["bash", str(REPO / "scripts/openvins/run.sh"), "ucu", "seq99", "--run", "1"], env=env,
                           capture_output=True, text=True, timeout=120)
        return r, self.res / "openvins/mono_inertial/ucu/seq99"

    def test_orbslam3_segfault_at_the_end_is_a_result(self):
        r, d = self.orb("orb_segv_end")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("while saving the trajectory", r.stdout)
        self.assertNotIn("second attempt", r.stdout)                              # the end-of-run crash is not retried: it would repeat
        self.assertFalse((d / "traj_run1.txt").exists())
        meta = (d / "meta.yaml").read_text()
        self.assertIn("tracking_lost: true", meta)
        self.assertIn("crashed", meta)

    def test_orbslam3_crash_during_tracking_gets_one_more_attempt_then_is_a_result(self):
        r, d = self.orb("orb_segv_mid")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("second attempt"), 1)
        self.assertIn("tracking_lost: true", (d / "meta.yaml").read_text())

    def test_orbslam3_sigkill_and_plain_errors_stay_failures(self):
        for stub in ("orb_oom", "orb_fail1"):                                     # OOM kill / ordinary failure: not the method's result
            r, d = self.orb(stub)
            self.assertNotIn(r.returncode, (0, 4), (stub, r.stdout + r.stderr))
            self.assertFalse((d / "meta.yaml").exists(), stub)

    def test_openvins_divergence_keeps_the_poses_written_before_the_abort(self):
        r, d = self.ov("ov_diverge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("diverged", r.stdout)
        self.assertEqual(len((d / "traj_run1.txt").read_text().splitlines()), 4)  # the cut 5th line is dropped, nothing invented
        self.assertIn("FILTER DIVERGED", (d / "meta.yaml").read_text())

    def test_openvins_divergence_without_poses_is_a_result_without_trajectory(self):
        r, d = self.ov("ov_diverge_nopose")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertFalse((d / "traj_run1.txt").exists())
        self.assertIn("tracking_lost: true", (d / "meta.yaml").read_text())

    def test_openvins_config_error_stays_a_failure(self):
        r, d = self.ov("ov_cfgerr")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse((d / "meta.yaml").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
