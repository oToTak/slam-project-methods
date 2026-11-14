"""CPU-only checks of wheel_odom on synthetic odometry (no data, no GPU). Run: python test_run_wheel_odom.py"""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_wheel_odom as w

sys.path.insert(0, str(HERE.parent / "data"))
import export_odom as eo


def yaw_q(a):
    return (0.0, 0.0, math.sin(a / 2), math.cos(a / 2))


def make_odom(path, t0=1000.0, n=125, hz=25.0):
    """Robot drives 1 m/s along x while yawing 0.1 rad/s; odometry at 25 Hz."""
    lines = ["#timestamp x y z qx qy qz qw"]
    for i in range(n):
        t = i / hz
        lines.append(eo.format_line(int((t0 + t) * 1e9), (t, 0.0, 0.0, *yaw_q(0.1 * t))))
    Path(path).write_text("\n".join(lines) + "\n")


def test_all():
    # export_odom formatting equals the official exporter's (9 decimals, bag time in seconds)
    line = eo.format_line(1305031102175304000, (1, 2, 3, 0, 0, 0, 1)).split()
    assert len(line[0].split(".")[1]) == 9 and abs(float(line[0]) - 1305031102.175304) < 1e-6 and line[1:4] == ["1.0", "2.0", "3.0"]

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        data = d / "ucu_tum" / "seq99"
        data.mkdir(parents=True)
        t0 = 1000.0
        make_odom(data / "odom.txt", t0)
        # 30 Hz frames from 0.3 s before the odometry starts to 0.5 s after it ends (odometry spans 0..4.96 s)
        ft = [t0 - 0.3 + i / 30 for i in range(int(5.8 * 30))]
        (data / "rgb.txt").write_text("# h\n" + "".join(f"{t:.6f} rgb/{t:.6f}.png\n" for t in ft))

        # interpolation: x == t, yaw == 0.1 t at every kept frame, also between odometry samples (frames are not on its grid)
        ot, oxyz, oq = w.read_odom_txt(data / "odom.txt")
        kt, poses = w.resample(ot, oxyz, oq, ft)
        assert 0 < len(kt) < len(ft)
        assert kt[0] >= ot[0] and kt[-1] <= ot[-1]
        rel = kt - t0
        assert np.allclose(poses[:, 0], rel, atol=1e-6)
        yaw = 2 * np.arctan2(poses[:, 5], poses[:, 6])
        assert np.allclose(yaw, 0.1 * rel, atol=1e-6)
        assert np.allclose(np.linalg.norm(poses[:, 3:], axis=1), 1.0)

        # slerp takes the short way round (q and -q are the same rotation)
        q = np.array([yaw_q(0.2)]); assert np.allclose(w.slerp(q, -q, np.array([0.5])), q) or np.allclose(w.slerp(q, -q, np.array([0.5])), -q)

        # end to end through the CLI: run 1, then 2 explicitly; an existing run is refused
        res = d / "results"
        cmd = [sys.executable, str(HERE / "run_wheel_odom.py"), "--dataset", "ucu", "--seq", "seq99",
               "--data-dir", str(data), "--results-root", str(res)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        out = res / "wheel_odom" / "odom" / "ucu" / "seq99"
        lines = (out / "traj_run1.txt").read_text().splitlines()
        assert len(lines) == len(kt) and len(lines[0].split()) == 8 and not lines[0].startswith("#")
        meta = (out / "meta.yaml").read_text()
        assert "method: wheel_odom" in meta and "mode: odom" in meta and "run: 1" in meta and "tracking_lost: false" in meta
        cov = float(next(l for l in meta.splitlines() if l.startswith("coverage:")).split()[1])
        assert abs(cov - (kt[-1] - kt[0]) / (ft[-1] - ft[0])) < 0.01 and cov < 1.0   # honest coverage: frames outside are dropped
        assert subprocess.run(cmd + ["--run", "2"], capture_output=True, text=True).returncode == 0
        assert (out / "traj_run2.txt").exists()
        assert subprocess.run(cmd + ["--run", "2"], capture_output=True, text=True).returncode != 0   # no overwrite

        # missing odom.txt -> clear failure, nothing written
        (data / "odom.txt").unlink()
        r = subprocess.run(cmd + ["--run", "3"], capture_output=True, text=True)
        assert r.returncode != 0 and "odom.txt" in r.stderr and not (out / "traj_run3.txt").exists()

        # frames entirely outside the odometry span -> tracking_lost, no trajectory, non-zero exit
        make_odom(data / "odom.txt", t0)
        (data / "rgb.txt").write_text("".join(f"{t0 + 100 + i / 30:.6f} rgb/x.png\n" for i in range(50)))
        r = subprocess.run(cmd + ["--run", "4"], capture_output=True, text=True)
        assert r.returncode != 0 and not (out / "traj_run4.txt").exists()
        assert "tracking_lost: true" in (out / "meta.yaml").read_text()
    print("OK: wheel_odom tests passed")


if __name__ == "__main__":
    test_all()
