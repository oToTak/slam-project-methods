#!/usr/bin/env python3
"""EuRoC MAV sequence (e.g. MH_01_easy, unzipped 'mav0/' layout) -> the TUM-style folder the ORB-SLAM3 driver reads. Optional:
only needed for the inertial sanity run on EuRoC (TASK_all_methods.md T3: 'EuRoC MH_01 passes'). Nothing is downloaded here.

  python scripts/orbslam3/euroc_to_tum.py <euroc_seq_dir_with_mav0> <out_dir>      # out: $DATA_ROOT/euroc_tum/MH_01

Writes in <out_dir>:
  rgb.txt          '#timestamp path'; the path points into <euroc>/mav0/cam0/data (no image is copied; absolute path)
  imu.txt          '#timestamp ax ay az gx gy gz'  (EuRoC CSV order is gyro THEN accel: reordered here), seconds
  groundtruth.txt  '#timestamp x y z qx qy qz qw'  from state_groundtruth_estimate0 (EuRoC CSV quaternion order w x y z:
                   reordered). It is the pose of the BODY (IMU) frame, so evaluate ORB-SLAM3 with --body-frame
                   (scripts/orbslam3/run.sh does that for dataset 'euroc').
Timestamps: EuRoC nanoseconds -> seconds with 9 decimals, computed on integers (exact).
"""
import csv
import sys
from pathlib import Path


def ns_to_s(ns):
    ns = int(ns)
    return f"{ns // 10**9}.{ns % 10**9:09d}"


def rows(path):
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if not r or r[0].lstrip().startswith("#"):
                continue
            yield [c.strip() for c in r]


def convert(euroc_dir, out_dir):
    mav = Path(euroc_dir) / "mav0"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cam = list(rows(mav / "cam0" / "data.csv"))
    with open(out / "rgb.txt", "w", newline="\n") as fh:
        fh.write("#timestamp path\n")
        for r in cam:
            fh.write(f"{ns_to_s(r[0])} {(mav / 'cam0' / 'data' / r[1]).resolve().as_posix()}\n")
    n_imu = 0
    with open(out / "imu.txt", "w", newline="\n") as fh:
        fh.write("#timestamp ax ay az gx gy gz\n")
        for r in rows(mav / "imu0" / "data.csv"):
            w, a = r[1:4], r[4:7]                       # csv: wx wy wz ax ay az
            fh.write(f"{ns_to_s(r[0])} {a[0]} {a[1]} {a[2]} {w[0]} {w[1]} {w[2]}\n")
            n_imu += 1
    n_gt = 0
    gt_path = mav / "state_groundtruth_estimate0" / "data.csv"
    with open(out / "groundtruth.txt", "w", newline="\n") as fh:
        fh.write("#timestamp x y z qx qy qz qw\n")
        if gt_path.exists():
            for r in rows(gt_path):
                p, q = r[1:4], r[4:8]                   # csv: px py pz qw qx qy qz
                fh.write(f"{ns_to_s(r[0])} {p[0]} {p[1]} {p[2]} {q[1]} {q[2]} {q[3]} {q[0]}\n")
                n_gt += 1
    return len(cam), n_imu, n_gt


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    n = convert(argv[0], argv[1])
    print(f"frames {n[0]}, imu {n[1]}, groundtruth {n[2]} -> {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
