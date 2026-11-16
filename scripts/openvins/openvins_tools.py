#!/usr/bin/env python3
"""Pure-python helpers of the OpenVINS integration (stdlib only; reuses scripts/orbslam3/orb3_lib.py and scripts/dso/dso_tools.py).

  python scripts/openvins/openvins_tools.py post --poses <driver output> --rgb <rgb.txt> --results-root R --dataset ucu --seq seq01 --run 1 ...

The driver (scripts/openvins/driver/ov_ucu_driver.cc) writes, for each camera frame that got a pose after its filter update,
  <frame_index> px py pz qx qy qz qw  cqx cqy cqz cqw  cpx cpy cpz
  (p, q) = pose of the IMU in the global frame: p_IinG and the Hamilton quaternion of R_ItoG;
  (cq, cp) = camera -> IMU transform in use at that moment: Hamilton quaternion of R_CtoI and p_CinI.
CONVENTIONS wants the pose of the colour camera, so  T_G_C = T_G_I * T_I_C  is composed here (with the filter's own current
extrinsics estimate, which equals the configured Kalibr T_ic when online calibration is off). The result is in OpenVINS' global frame:
gravity-aligned, origin at the filter's initial IMU position, metric scale (eval.py aligns with SE(3) for mono_inertial).
Honest gaps (same rules as scripts/dso/dso_tools.py): every index is mapped to its rgb.txt row (exact timestamp text); frames before the
filter initialised (>= init_window_time, normally 2 s) and frames after the last IMU sample have NO line; nothing is interpolated.
Exit codes: 0 trajectory written; 4 no usable trajectory (meta.yaml says tracking_lost: true, NO traj file); 1 usage/other error.
"""
import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dso"))
import dso_tools as dt  # noqa: E402  (also puts scripts/orbslam3 and scripts/dpvo on sys.path)

lib = dt.lib
METHOD, MODE = "openvins", "mono_inertial"


def compose_camera_pose(imu_pose, t_i_c):
    """imu_pose = [x y z qx qy qz qw] of the IMU in G (T_G_I); t_i_c = [qx qy qz qw  px py pz] of the camera in the IMU frame (T_I_C).
    Returns the camera pose in G as [x y z qx qy qz qw]."""
    T_gi = lib.pose_to_mat(imu_pose)
    T_ic = lib.pose_to_mat([t_i_c[4], t_i_c[5], t_i_c[6], t_i_c[0], t_i_c[1], t_i_c[2], t_i_c[3]])
    return lib.mat_to_pose(lib.mat_mul(T_gi, T_ic))


def read_vio_poses(path, body_frame=False):
    """-> (rows [(frame_index, pose7)], n_malformed). Missing file == no rows. A row needs 15 finite numbers and unit-able quaternions.
    pose7 is the camera pose T_G_C, or with body_frame=True the IMU pose T_G_I (EuRoC ground truth is the body pose)."""
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
            idx = int(parts[0])
            v = [float(x) for x in parts[1:]]
        except ValueError:
            bad += 1
            continue
        if len(v) != 14 or not all(math.isfinite(x) for x in v):
            bad += 1
            continue
        try:
            lib.quat_normalize(v[3:7])
            lib.quat_normalize(v[7:11])
        except ValueError:
            bad += 1
            continue
        rows.append((idx, lib.mat_to_pose(lib.pose_to_mat(v[0:7])) if body_frame else compose_camera_pose(v[0:7], v[7:14])))
    return rows, bad


def process(*, poses, rgb, out_dir, run, dataset, seq, config, max_frames=0, note="", driver_failed=None, commit=None, body_frame=False):
    rows, malformed = ([], 0) if driver_failed else read_vio_poses(poses, body_frame)
    return dt.process(poses=poses, rgb=rgb, out_dir=out_dir, run=run, dataset=dataset, seq=seq, config=config, max_frames=max_frames,
                      note=note, driver_failed=driver_failed, commit=commit, method=METHOD, mode=MODE, label="OpenVINS",
                      rows=rows, malformed=malformed)


def cmd_post(a):
    out_dir = a.results_root / METHOD / MODE / a.dataset / a.seq
    return process(poses=a.poses, rgb=a.rgb, out_dir=out_dir, run=a.run, dataset=a.dataset, seq=a.seq, config=a.config,
                   max_frames=a.max_frames, note=a.note, driver_failed=a.driver_failed, commit=a.commit, body_frame=a.body_frame)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("post")
    q.add_argument("--poses", required=True, type=Path, help="output of ov_ucu_driver")
    q.add_argument("--rgb", required=True, type=Path)
    q.add_argument("--results-root", required=True, type=Path)
    q.add_argument("--dataset", required=True)
    q.add_argument("--seq", required=True)
    q.add_argument("--run", required=True, type=int)
    q.add_argument("--config", default="")
    q.add_argument("--max-frames", type=int, default=0)
    q.add_argument("--note", default="")
    q.add_argument("--body-frame", action="store_true", help="write the IMU (body) pose instead of the camera pose (EuRoC sanity)")
    q.add_argument("--driver-failed", default=None)
    q.add_argument("--commit", default=None)
    q.set_defaults(fn=cmd_post)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
