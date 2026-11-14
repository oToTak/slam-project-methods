#!/usr/bin/env python3
"""wheel_odom baseline: the robot's wheel odometry (/odom, exported to odom.txt) resampled to the camera frames.

No SLAM, no GPU: for every frame time of rgb.txt that lies inside the odometry time span, position is interpolated
linearly and orientation by slerp. Frames outside the span get no pose (coverage then says so honestly).
The result is deterministic, so the registry asks for a single run (scripts/queue/methods.conf).
Output (CONVENTIONS.md section 4): <results-root>/wheel_odom/odom/<dataset>/<seq>/traj_run<N>.txt + meta.yaml (mode 'odom', SE(3) -a).
Only numpy is needed. Helpers shared with DPVO are imported from scripts/dpvo/run_dpvo.py (pure functions, no torch).
"""
import argparse
import json
import os
import socket
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dpvo"))
import run_dpvo as common  # noqa: E402  (read_rgb_txt, write_tum_trajectory, compute_coverage, git_commit, ...)

METHOD, MODE = "wheel_odom", "odom"


def read_odom_txt(path):
    """odom.txt -> (t[N], xyz[N,3], quat_xyzw[N,4]); '#' and blank lines skipped, time must be strictly increasing."""
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append([float(v) for v in line.split()[:8]])
    a = np.asarray(rows, dtype=float)
    if a.ndim != 2 or a.shape[0] < 2 or a.shape[1] != 8:
        raise ValueError(f"{path}: need at least 2 lines 'timestamp x y z qx qy qz qw', got {a.shape}")
    keep = np.concatenate([[True], np.diff(a[:, 0]) > 0])  # duplicates / time going back are dropped, not fixed
    a = a[keep]
    q = a[:, 4:8]
    norm = np.linalg.norm(q, axis=1)
    if not np.all(norm > 1e-9):
        raise ValueError(f"{path}: zero quaternion in odometry")
    return a[:, 0], a[:, 1:4], q / norm[:, None]


def slerp(q0, q1, u):
    """Spherical interpolation of unit quaternions (xyzw), vectorised: q0, q1 [M,4], u [M]."""
    dot = np.sum(q0 * q1, axis=1)
    q1 = np.where((dot < 0)[:, None], -q1, q1)       # shortest arc
    dot = np.abs(dot)
    theta = np.arccos(np.clip(dot, -1.0, 1.0))
    sin = np.sin(theta)
    small = sin < 1e-8                                # nearly equal: plain lerp
    w0 = np.where(small, 1 - u, np.sin((1 - u) * theta) / np.where(small, 1, sin))
    w1 = np.where(small, u, np.sin(u * theta) / np.where(small, 1, sin))
    q = w0[:, None] * q0 + w1[:, None] * q1
    return q / np.linalg.norm(q, axis=1)[:, None]


def resample(odom_t, odom_xyz, odom_q, frame_t):
    """Poses at frame_t inside [odom_t[0], odom_t[-1]]. Returns (kept frame times, poses[M,7] = xyz + quat xyzw)."""
    frame_t = np.asarray(frame_t, dtype=float)
    inside = (frame_t >= odom_t[0]) & (frame_t <= odom_t[-1])
    ft = frame_t[inside]
    i = np.clip(np.searchsorted(odom_t, ft, side="right") - 1, 0, len(odom_t) - 2)
    u = (ft - odom_t[i]) / (odom_t[i + 1] - odom_t[i])
    xyz = odom_xyz[i] + u[:, None] * (odom_xyz[i + 1] - odom_xyz[i])
    q = slerp(odom_q[i], odom_q[i + 1], u)
    return ft, np.hstack([xyz, q])


def format_meta(*, dataset, seq, run, commit, config, coverage, tracking_lost, notes):
    return (f"method: {METHOD}\nmode: {MODE}\ndataset: {dataset}\nseq: {seq}\nrun: {run}\ncommit: {commit}\n"
            f"config: {config}\ncoverage: {coverage:.2f}\ntracking_lost: {'true' if tracking_lost else 'false'}\n"
            f"notes: {json.dumps(notes, ensure_ascii=False)}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, choices=["ucu"])
    ap.add_argument("--seq", required=True)
    ap.add_argument("--data-dir", required=True, type=Path, help="exported sequence: rgb.txt + odom.txt")
    ap.add_argument("--results-root", type=Path, default=REPO_ROOT / "results")
    ap.add_argument("--run", type=int, default=None, help="run number N for traj_run<N>.txt (default: next free)")
    args = ap.parse_args()

    odom_path = args.data_dir / "odom.txt"
    if not odom_path.is_file():
        raise SystemExit(f"Missing {odom_path}: run scripts/data/fetch_ucu.sh <N> (it adds odom.txt to an old export)")
    frames = common.read_rgb_txt(args.data_dir / "rgb.txt")
    rgb_t = [t for t, _ in frames]
    out_dir = args.results_root / METHOD / MODE / args.dataset / args.seq
    out_dir.mkdir(parents=True, exist_ok=True)
    run = args.run if args.run is not None else common.next_run_number(out_dir)
    traj = out_dir / f"traj_run{run}.txt"
    if traj.exists():
        raise SystemExit(f"{traj} already exists; refusing to overwrite a run")

    odom_t, odom_xyz, odom_q = read_odom_txt(odom_path)
    ft, poses = resample(odom_t, odom_xyz, odom_q, rgb_t)
    sha, dirty = common.git_commit()
    notes = (f"wheel odometry (/odom) resampled to rgb.txt times: linear position, slerp orientation; "
             f"{len(odom_t)} odometry poses, {len(ft)} of {len(rgb_t)} frames inside the odometry time span. "
             f"Deterministic: 1 run. Pose frame = odom frame (evaluation aligns SE(3)).")
    if dirty:
        notes += " Repo had uncommitted changes."
    notes += f" host={os.environ.get('QUEUE_HOST') or socket.gethostname()}."
    lost = not common.poses_are_valid(poses)
    if lost:
        notes += " No valid poses."
    else:
        common.write_tum_trajectory(traj, ft, poses)
    cov = 0.0 if lost else common.compute_coverage(ft, rgb_t)
    (out_dir / "meta.yaml").write_text(format_meta(dataset=args.dataset, seq=args.seq, run=run, commit=sha,
                                                   config="", coverage=cov, tracking_lost=lost, notes=notes))
    print(f"Wrote {traj} ({len(ft)} poses of {len(rgb_t)} frames, coverage {cov:.2f})" if not lost
          else f"No trajectory written for {args.seq}")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
