#!/usr/bin/env python3
"""Validate and clean imu.txt for the ORB-SLAM3 driver (scripts/orbslam3/driver/orbslam3_tum.cc).

Input  (exported by the official UCU exporter, TUM style):  '#timestamp ax ay az gx gy gz'
         seconds (9 decimals, same clock as rgb.txt), accelerometer in m/s^2 (specific force: z reads about -9.8 at rest because the
         IMU z axis points down), gyroscope in rad/s.
Output (the driver's IMU file): the same 7 columns, same order, same units, same clock, but
         * non-finite rows and rows whose timestamp does not increase are dropped (counted and reported, never 'repaired'),
         * timestamps printed with 9 decimals, values with 9 significant digits (float32 is what ORB-SLAM3 stores).
No time shift: the exporter already moved timestamps to the base clock (old-dvpo/CONVENTIONS.md section 3, Kalibr shift
0.0149 s is applied). No axis permutation: ORB-SLAM3 takes the IMU frame as it is; IMU.T_b_c1 in the settings carries the
camera-IMU geometry. The ORB-SLAM3 EuRoC example reads gyro before accel from its CSV; our file order is accel then gyro and
the driver reads exactly that (no swap anywhere).

Exit codes: 0 usable; 1 unusable (too few rows, or the IMU span does not cover the frames and --allow-partial was not given).
The last stdout line is 'IMU_STATS {json}' (rate, max gap, drops, coverage of the frame span).
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orb3_lib as lib  # noqa: E402


def read_imu_txt(path):
    """-> list of (t, ax, ay, az, gx, gy, gz) floats in file order; '#'/blank skipped; a row with != 7 numbers is an error."""
    rows = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 7:
            raise ValueError(f"{path}:{n}: expected 7 columns 'timestamp ax ay az gx gy gz', got {len(parts)}")
        rows.append(tuple(float(v) for v in parts))
    return rows


def sanitize(rows):
    """Drop non-finite rows and rows with non-increasing time. Returns (clean_rows, dropped dict)."""
    clean, bad_value, bad_time = [], 0, 0
    last = -math.inf
    for r in rows:
        if not all(math.isfinite(v) for v in r):
            bad_value += 1
        elif r[0] <= last:
            bad_time += 1
        else:
            clean.append(r)
            last = r[0]
    return clean, {"non_finite": bad_value, "non_increasing_time": bad_time}


def stats_of(rows, dropped, frame_t0=None, frame_t1=None, max_gap_s=0.05):
    st = {"n": len(rows), "dropped": dropped}
    if len(rows) >= 2:
        dts = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        med = statistics.median(dts)
        st.update(t_first=rows[0][0], t_last=rows[-1][0], span_s=rows[-1][0] - rows[0][0], median_dt_s=med,
                  rate_hz=(1.0 / med if med > 0 else None), max_gap_s=max(dts),
                  n_gaps_over_limit=sum(1 for d in dts if d > max_gap_s), gap_limit_s=max_gap_s)
    if frame_t0 is not None and rows:
        st["covers_frames"] = bool(rows[0][0] <= frame_t0 and rows[-1][0] >= frame_t1)
        st["starts_before_first_frame_s"] = frame_t0 - rows[0][0]
        st["ends_after_last_frame_s"] = rows[-1][0] - frame_t1
    return st


def format_rows(rows):
    return "".join(f"{r[0]:.9f} " + " ".join(f"{v:.9g}" for v in r[1:]) + "\n" for r in rows)


def convert(imu_path, out_path, rgb_path=None, max_frames=0, max_gap_s=0.05, allow_partial=False):
    """Returns (ok, stats). Writes out_path only when ok."""
    rows, dropped = sanitize(read_imu_txt(imu_path))
    f0 = f1 = None
    if rgb_path:
        ts = [t for _, t, _ in lib.read_rgb_txt(rgb_path)]
        if max_frames > 0:
            ts = ts[:max_frames]
        if ts:
            f0, f1 = ts[0], ts[-1]
    st = stats_of(rows, dropped, f0, f1, max_gap_s)
    ok = len(rows) >= 2
    if ok and f0 is not None and not st.get("covers_frames", False) and not allow_partial:
        ok = False
        st["error"] = "IMU time span does not cover the frame span (IMU must start before the first frame and end after the last)"
    if len(rows) < 2:
        st["error"] = "fewer than 2 usable IMU rows"
    if ok:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#timestamp ax ay az gx gy gz\n")
            fh.write(format_rows(rows))
    return ok, st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imu", required=True, type=Path, help="imu.txt of the exported sequence")
    ap.add_argument("--out", required=True, type=Path, help="cleaned file for the driver")
    ap.add_argument("--rgb", type=Path, help="rgb.txt: check that the IMU span covers the frames")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--max-gap", type=float, default=0.05, help="gap [s] counted as an IMU dropout in the stats")
    ap.add_argument("--allow-partial", action="store_true", help="do not fail when the IMU does not cover all frames")
    args = ap.parse_args(argv)
    ok, st = convert(args.imu, args.out, args.rgb, args.max_frames, args.max_gap, args.allow_partial)
    print("IMU_STATS " + json.dumps(st, sort_keys=True))
    if not ok:
        print(f"ERROR: {st.get('error', 'unusable IMU data')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
