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

Frames outside the IMU span. Both inertial drivers need IMU data before the first frame and after the last one. On the real
sequences the IMU stream may start a little later than the camera one (seq12: 0.13 s; seq13: one stray first frame stamped 18 s
before everything else, see PIPELINE_DEBT D1). Such leading (and trailing) frames cannot be used by an inertial method, so with
--rgb-out this script writes a copy of rgb.txt WITHOUT them (same timestamp text, absolute image paths, nothing shifted or
invented) and the run uses that copy. It prints 'IMU_TRIM leading=N trailing=M ...' and reports the numbers in IMU_STATS. More than
--max-trim-frac (default 2 %) of the frames outside the IMU span is not a small start-up offset but broken data: refused.

Exit codes: 0 usable; 1 unusable (too few rows, no frame left inside the IMU span, or too many frames outside it and
--allow-partial was not given). The last stdout line is 'IMU_STATS {json}' (rate, max gap, drops, coverage of the frame span).
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


def trim_to_span(frames, t_first, t_last):
    """frames: [(token, t, path)] sorted by time. -> (kept, n_leading, n_trailing): frames with t_first <= t <= t_last."""
    kept = [f for f in frames if t_first <= f[1] <= t_last]
    n_lead = sum(1 for f in frames if f[1] < t_first)
    return kept, n_lead, len(frames) - len(kept) - n_lead


def write_rgb(path, frames, base_dir):
    """rgb.txt copy with the original timestamp text and ABSOLUTE image paths (the copy lives in another folder)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# timestamp path   (trimmed to the IMU span by scripts/orbslam3/convert_imu.py)\n")
        for tok, _, rel in frames:
            img = Path(rel)
            if not img.is_absolute():
                img = (Path(base_dir) / img).resolve()
            fh.write(f"{tok} {img.as_posix()}\n")


def convert(imu_path, out_path, rgb_path=None, max_frames=0, max_gap_s=0.05, allow_partial=False, rgb_out=None, max_trim_frac=0.02):
    """Returns (ok, stats). Writes out_path (and rgb_out, if given) only when ok."""
    rows, dropped = sanitize(read_imu_txt(imu_path))
    f0 = f1 = None
    kept = []
    trim = None
    if rgb_path:
        frames = lib.read_rgb_txt(rgb_path)
        ts = [t for _, t, _ in frames]
        if max_frames > 0:
            ts = ts[:max_frames]
        if ts:
            f0, f1 = ts[0], ts[-1]
        if len(rows) >= 2:
            kept, n_lead, n_trail = trim_to_span(frames, rows[0][0], rows[-1][0])
            trim = {"leading": n_lead, "trailing": n_trail, "frames_in": len(frames), "frames_kept": len(kept)}
    st = stats_of(rows, dropped, f0, f1, max_gap_s)
    ok = len(rows) >= 2
    if trim is not None:
        st["trim"] = trim
        n_out = trim["leading"] + trim["trailing"]
        if n_out and kept:
            st["first_kept_frame_after_imu_start_s"] = kept[0][1] - rows[0][0]
        if not kept:
            ok = False
            st["error"] = "no frame lies inside the IMU time span"
        elif n_out and not allow_partial and n_out > max_trim_frac * len(frames):
            ok = False
            st["error"] = (f"IMU time span does not cover the frame span ({n_out} of {len(frames)} frames lie outside it, "
                           f"more than {max_trim_frac:.0%}: not a start-up offset; --allow-partial trims them anyway)")
    if len(rows) < 2:
        st["error"] = "fewer than 2 usable IMU rows"
    if ok:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#timestamp ax ay az gx gy gz\n")
            fh.write(format_rows(rows))
        if rgb_out and rgb_path:
            write_rgb(rgb_out, kept, Path(rgb_path).resolve().parent)
    return ok, st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imu", required=True, type=Path, help="imu.txt of the exported sequence")
    ap.add_argument("--out", required=True, type=Path, help="cleaned file for the driver")
    ap.add_argument("--rgb", type=Path, help="rgb.txt: check that the IMU span covers the frames")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--max-gap", type=float, default=0.05, help="gap [s] counted as an IMU dropout in the stats")
    ap.add_argument("--allow-partial", action="store_true", help="trim frames outside the IMU span however many there are")
    ap.add_argument("--rgb-out", type=Path, help="write rgb.txt without the frames outside the IMU span (absolute image paths)")
    ap.add_argument("--max-trim-frac", type=float, default=0.02, help="largest share of frames that may lie outside the IMU span")
    args = ap.parse_args(argv)
    ok, st = convert(args.imu, args.out, args.rgb, args.max_frames, args.max_gap, args.allow_partial, args.rgb_out, args.max_trim_frac)
    tr = st.get("trim")
    if tr:
        print(f"IMU_TRIM leading={tr['leading']} trailing={tr['trailing']} of {tr['frames_in']} frames outside the IMU span, "
              f"{tr['frames_kept']} kept" + (" (rgb.txt trimmed copy used)" if args.rgb_out and ok else ""))
    print("IMU_STATS " + json.dumps(st, sort_keys=True))
    if not ok:
        print(f"ERROR: {st.get('error', 'unusable IMU data')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
