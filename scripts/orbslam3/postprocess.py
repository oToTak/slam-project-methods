#!/usr/bin/env python3
"""ORB-SLAM3 raw trajectory -> CONVENTIONS.md section 4 outputs (traj_run<N>.txt + meta.yaml).

Input: the file the driver wrote with System::SaveTrajectoryEuRoC (src/System.cc @4452a3c): one line per frame that has a pose,
  '<1e9 * t_rel> x y z qx qy qz qw'   (t_rel = frame time minus the first frame time, printed in nanoseconds with 6 decimals)
  mono          : pose of the camera in the frame of the first keyframe of the biggest atlas map
  mono_inertial : pose of the BODY (IMU) in the frame of the first keyframe's IMU pose  -> converted here to the camera pose
                  T_w_c = T_w_b * T_b_c  (T_b_c = Kalibr T_ic = inverse of T_cam_imu, see make_settings.py); CONVENTIONS wants the
                  pose of camera_color. --body-frame keeps the body pose (EuRoC sanity: its ground truth is the body pose).
ORB-SLAM3 never writes frames where tracking is lost / before initialisation / in other atlas maps, and in a lost-tracking
corner case it can repeat the previous frame time. Honest handling:
  * every raw line is matched to the NEAREST rgb.txt time within --tol (default 1 ms; frame spacing is 33 ms);
  * lines that match no frame, or a frame that already has a pose, are dropped and counted (first pose of a frame wins);
  * frames without a pose get NO line (no interpolation, no copies of the previous pose); eval.py reports coverage.
The output keeps the exact timestamp text of rgb.txt (normalised to 9 decimals) and is written atomically, never over an
existing traj_run<N>.txt.

Exit codes: 0 trajectory written; 4 no usable trajectory (tracking never initialised / lost / driver failed): meta.yaml says
tracking_lost: true and NO traj file is written; 1 usage or other error (e.g. the run already exists).
"""
import argparse
import bisect
import json
import math
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orb3_lib as lib  # noqa: E402

sys.path.insert(0, str(lib.REPO_ROOT / "scripts" / "dpvo"))
import run_dpvo as common  # noqa: E402  (pure helpers: compute_coverage, git_commit)

METHOD = "orbslam3"
MIN_POSES = 2
EXIT_LOST = 4


def read_raw(path):
    """-> (rows [(t_ns, [x,y,z,qx,qy,qz,qw])], n_malformed). Missing file == no rows."""
    rows, bad = [], 0
    p = Path(path)
    if not p.is_file():
        return rows, bad
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        try:
            vals = [float(v) for v in parts]
        except ValueError:
            bad += 1
            continue
        if len(vals) != 8 or not all(math.isfinite(v) for v in vals):
            bad += 1
            continue
        try:
            lib.quat_normalize(vals[4:8])
        except ValueError:
            bad += 1
            continue
        rows.append((vals[0], vals[1:8]))
    return rows, bad


def match_to_frames(rows, frame_times, t0, tol=1e-3):
    """Match raw rows to frames. Returns ({frame_index: pose7}, stats). t_abs = t0 + t_ns * 1e-9."""
    kept, unmatched, dup = {}, 0, 0
    for t_ns, pose in rows:
        t = t0 + t_ns * 1e-9
        i = bisect.bisect_left(frame_times, t)
        cand = [j for j in (i - 1, i) if 0 <= j < len(frame_times)]
        j = min(cand, key=lambda k: abs(frame_times[k] - t)) if cand else None
        if j is None or abs(frame_times[j] - t) > tol:
            unmatched += 1
        elif j in kept:
            dup += 1
        else:
            kept[j] = pose
    return kept, {"raw": len(rows), "matched": len(kept), "unmatched": unmatched, "duplicate_frame": dup}


def body_to_camera(pose, T_b_c):
    """pose = body pose in the world (x y z qx qy qz qw); returns the camera pose T_w_c = T_w_b * T_b_c."""
    T = lib.mat_mul(lib.pose_to_mat(pose), T_b_c)
    return lib.mat_to_pose(T)


def format_pose_line(ts_token, pose):
    return f"{lib.ts_9dec(ts_token)} " + " ".join(f"{v:.9f}" for v in pose)


def format_meta(*, dataset, seq, run, mode, commit, config, coverage, tracking_lost, notes):
    return (f"method: {METHOD}\nmode: {mode}\ndataset: {dataset}\nseq: {seq}\nrun: {run}\ncommit: {commit}\n"
            f"config: {config}\ncoverage: {coverage:.2f}\ntracking_lost: {'true' if tracking_lost else 'false'}\n"
            f"notes: {json.dumps(notes, ensure_ascii=False)}\n")


def process(*, raw, rgb, mode, out_dir, run, dataset, seq, config, calib_path=lib.DEFAULT_CALIB, max_frames=0, tol=1e-3,
            body_frame=False, note="", driver_failed=None, commit=None):
    """Returns exit code. Writes traj_run<run>.txt (unless lost) and meta.yaml."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj = out_dir / f"traj_run{run}.txt"
    if traj.exists():
        print(f"ERROR: {traj} already exists; refusing to overwrite a run", file=sys.stderr)
        return 1
    frames = lib.read_rgb_txt(rgb)
    if max_frames > 0:
        frames = frames[:max_frames]
    if len(frames) < 2:
        print(f"ERROR: {rgb}: fewer than 2 frames", file=sys.stderr)
        return 1
    ftimes = [t for _, t, _ in frames]
    if any(b <= a for a, b in zip(ftimes, ftimes[1:])):
        print(f"ERROR: {rgb}: timestamps are not strictly increasing", file=sys.stderr)
        return 1
    if commit is None:
        commit, dirty = common.git_commit()
        if dirty:
            note = (note + " " if note else "") + "Repo had uncommitted changes."
    notes = note.strip()
    notes += f" host={os.environ.get('QUEUE_HOST') or socket.gethostname()}."

    lost, poses, tstamps, stats = False, [], [], {}
    if driver_failed:
        lost, notes = True, f"{notes} Driver failed: {driver_failed}."
    else:
        rows, malformed = read_raw(raw)
        kept, stats = match_to_frames(rows, ftimes, ftimes[0], tol)
        stats["malformed_raw_lines"] = malformed
        if len(kept) < MIN_POSES:
            lost = True
            notes += f" No usable trajectory: {len(rows)} raw poses, {len(kept)} matched frames (tracking never initialised or lost)."
        else:
            T_b_c = None
            if mode == "mono_inertial" and not body_frame:
                cal = lib.load_calibration(calib_path)
                T_b_c = lib.se3_inverse(cal["T_cam_imu"])
            for j in sorted(kept):
                p = kept[j]
                poses.append(body_to_camera(p, T_b_c) if T_b_c is not None else _norm_pose(p))
                tstamps.append(frames[j][0])
            notes += (f" {len(poses)} of {len(frames)} frames have a pose (raw {stats['raw']}, unmatched {stats['unmatched']}, "
                      f"duplicate-frame {stats['duplicate_frame']}, malformed {malformed}); missing frames are NOT interpolated.")
            if mode == "mono_inertial":
                notes += (" Pose = body (IMU) pose" if body_frame else " Pose = camera pose from body pose via T_b_c = Kalibr T_ic") + "."
    coverage = 0.0
    if not lost:
        tmp = out_dir / f".traj_run{run}.txt.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for tok, pose in zip(tstamps, poses):
                fh.write(format_pose_line(tok, pose) + "\n")
        os.replace(tmp, traj)
        coverage = common.compute_coverage([float(t) for t in tstamps], ftimes)
    (out_dir / "meta.yaml").write_text(format_meta(dataset=dataset, seq=seq, run=run, mode=mode, commit=commit, config=config,
                                                   coverage=coverage, tracking_lost=lost, notes=notes), encoding="utf-8")
    if lost:
        print(f"No trajectory written for {seq} run {run}: {notes.strip()}")
        return EXIT_LOST
    print(f"Wrote {traj} ({len(poses)} poses of {len(frames)} frames, coverage {coverage:.2f}) and {out_dir / 'meta.yaml'}")
    return 0


def _norm_pose(p):
    q = lib.quat_normalize(p[3:7])
    return list(p[0:3]) + q


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", required=True, type=Path, help="driver output (SaveTrajectoryEuRoC format)")
    ap.add_argument("--rgb", required=True, type=Path, help="rgb.txt of the sequence")
    ap.add_argument("--mode", required=True, choices=["mono", "mono_inertial"])
    ap.add_argument("--results-root", required=True, type=Path)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seq", required=True)
    ap.add_argument("--run", required=True, type=int)
    ap.add_argument("--config", default="", help="repo-relative settings file recorded in meta.yaml")
    ap.add_argument("--calib", type=Path, default=lib.DEFAULT_CALIB)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--tol", type=float, default=1e-3, help="max |raw time - frame time| [s] for a match")
    ap.add_argument("--body-frame", action="store_true", help="mono_inertial: keep the IMU body pose (EuRoC sanity)")
    ap.add_argument("--note", default="", help="free text appended to notes")
    ap.add_argument("--driver-failed", default=None, help="the driver failed: write meta (tracking_lost) and no trajectory")
    ap.add_argument("--commit", default=None)
    a = ap.parse_args(argv)
    out_dir = a.results_root / METHOD / a.mode / a.dataset / a.seq
    return process(raw=a.raw, rgb=a.rgb, mode=a.mode, out_dir=out_dir, run=a.run, dataset=a.dataset, seq=a.seq,
                   config=a.config, calib_path=a.calib, max_frames=a.max_frames, tol=a.tol, body_frame=a.body_frame,
                   note=a.note, driver_failed=a.driver_failed, commit=a.commit)


if __name__ == "__main__":
    sys.exit(main())
