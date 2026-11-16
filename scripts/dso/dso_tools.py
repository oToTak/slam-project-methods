#!/usr/bin/env python3
"""Pure-python helpers of the DSO integration (stdlib only; reuses scripts/orbslam3/orb3_lib.py and scripts/dpvo/run_dpvo.py helpers).

  python scripts/dso/dso_tools.py camera --dataset ucu --write     (re)write configs/dso/camera_ucu.txt from configs/dpvo/calib_ucu.txt
  python scripts/dso/dso_tools.py camera --dataset ucu --check     exit 1 if the committed file differs from the generated text
  python scripts/dso/dso_tools.py prep  --rgb <rgb.txt> --out <images.txt> [--max-frames N]
  python scripts/dso/dso_tools.py post  --poses <poses_refined.txt> --rgb <rgb.txt> --results-root R --dataset ucu --seq seq01 --run 1 ...

CAMERA FILE (DSO @7b0c99f, src/util/Undistort.cpp, read from the source, not from memory):
  DSO's own README formats: line 1 'RadTan fx fy cx cy k1 k2 r1 r2', line 2 input size, line 3 'crop', line 4 output size.
  * RadTan is the OpenCV model (distortCoordinates(): x_d = x(1+k1 r^2+k2 r^4) + 2 r1 x y + r2 (r^2+2x^2), y_d likewise with r1/r2
    swapped), so (k1, k2, r1, r2) = (k1, k2, p1, p2) of configs/dpvo/calib_ucu.txt: the distortion is passed honestly, no k3 needed for UCU.
  * fx fy cx cy are pixels when cx or cy >= 1 (readFromFile: values < 1 would be taken as 'relative to the image size' and shifted by
    0.5). Kalibr/OpenCV principal points are pixel-centre based like DSO's internal convention, so they are written unchanged.
  * 'crop' rectifies to a pinhole camera of the output size 640x480 (narrower field of view than the raw image; DSO has no
    distortion model inside the optimisation, it works on the rectified image). That is DSO's intended way for RadTan cameras.
  * The 8 numbers must be the only tokens of line 1 (the parser's sscanf format has 10 conversions for 8 pointers).
  * TUM fr1: the calibration has a k3 term (1.1633) that DSO's RadTan cannot express; it is DROPPED for the sanity run (documented in notes).

POST-PROCESSING (honest gaps): the driver writes '<frame_index> x y z qx qy qz qw' (camToWorld, cam->world) only for frames that got a valid
pose. Each index is mapped to its rgb.txt row (exact timestamp text, 9 decimals). Frames without a pose get NO line (no interpolation, no
copies); frames before DSO's initialisation, frames after DSO got lost, frames of a discarded pre-reset system are absent.
Exit codes: 0 trajectory written; 4 no usable trajectory (meta.yaml says tracking_lost: true, NO traj file); 1 usage/other error.
"""
import argparse
import math
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orbslam3"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dpvo"))
import orb3_lib as lib  # noqa: E402
import run_dpvo as common  # noqa: E402  (pure helpers: compute_coverage, git_commit)

METHOD, MODE = "dso", "mono"
MIN_POSES = 2
EXIT_LOST = 4
CONFIG_DIR = REPO_ROOT / "configs" / "dso"
CALIBS = {   # dataset -> (calibration file of the repo, image size, name of the generated DSO camera file)
    "ucu": (REPO_ROOT / "configs" / "dpvo" / "calib_ucu.txt", (640, 480), CONFIG_DIR / "camera_ucu.txt"),
    "tum": (REPO_ROOT / "configs" / "dpvo" / "calib_tum_fr1.txt", (640, 480), CONFIG_DIR / "camera_tum_fr1.txt"),
}


# ------------------------------------------------------------------ camera file

def render_camera(vals, size):
    """vals = [fx fy cx cy k1 k2 p1 p2 (k3)] as in configs/dpvo/calib_*.txt -> DSO camera file text (4 lines)."""
    if len(vals) < 8:
        raise ValueError(f"need fx fy cx cy k1 k2 p1 p2, got {len(vals)} numbers")
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("non-finite calibration value")
    fx, fy, cx, cy, k1, k2, p1, p2 = vals[:8]
    if cx < 1 or cy < 1:
        raise ValueError("cx, cy must be pixel values (>= 1); DSO would read smaller values as 'relative to image size'")
    w, h = size
    if w % 32 or h % 32:
        raise ValueError(f"DSO needs an output size divisible by 32 (6 pyramid levels), got {w}x{h}")
    nums = " ".join(repr(float(v)) for v in (fx, fy, cx, cy, k1, k2, p1, p2))
    return f"RadTan {nums}\n{w} {h}\ncrop\n{w} {h}\n"


def read_calib_numbers(path):
    return [float(v) for v in Path(path).read_text().split()]


def camera_text(dataset):
    calib, size, _ = CALIBS[dataset]
    return render_camera(read_calib_numbers(calib), size)


def cmd_camera(a):
    calib, size, target = CALIBS[a.dataset]
    text = camera_text(a.dataset)
    if a.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {target}")
    elif a.check:
        have = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else None
        if have != text:
            print(f"DIFFERS: {target} (run: python scripts/dso/dso_tools.py camera --dataset {a.dataset} --write)")
            return 1
        print(f"ok: {target}")
    else:
        sys.stdout.write(text)
    return 0


# ------------------------------------------------------------------ image list

def make_image_list(rgb_txt, max_frames=0):
    """rgb.txt -> list of absolute image paths in frame order (relative paths are relative to the folder of rgb.txt)."""
    rgb_txt = Path(rgb_txt)
    out = []
    for _, _, rel in lib.read_rgb_txt(rgb_txt):
        p = Path(rel)
        out.append(str(p if p.is_absolute() else (rgb_txt.parent / p).resolve()))
    if max_frames > 0:
        out = out[:max_frames]
    return out


def cmd_prep(a):
    paths = make_image_list(a.rgb, a.max_frames)
    if len(paths) < 2:
        print(f"ERROR: {a.rgb}: fewer than 2 frames", file=sys.stderr)
        return 1
    for p in (paths[0], paths[len(paths) // 2], paths[-1]):
        if not Path(p).is_file():
            print(f"ERROR: image missing: {p}", file=sys.stderr)
            return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(paths) + "\n")
    print(f"wrote {a.out} ({len(paths)} images)")
    return 0


# ------------------------------------------------------------------ driver output -> TUM

def read_poses(path):
    """-> (rows [(frame_index, [x y z qx qy qz qw])], n_malformed). Missing file == no rows."""
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
            vals = [float(v) for v in parts[1:]]
        except ValueError:
            bad += 1
            continue
        if len(vals) != 7 or not all(math.isfinite(v) for v in vals):
            bad += 1
            continue
        try:
            q = lib.quat_normalize(vals[3:7])
        except ValueError:
            bad += 1
            continue
        rows.append((idx, vals[:3] + q))
    return rows, bad


def map_to_frames(rows, n_frames):
    """{frame_index: pose}, stats. First pose of an index wins; indices outside the sequence are dropped and counted."""
    kept, outside, dup = {}, 0, 0
    for idx, pose in rows:
        if not 0 <= idx < n_frames:
            outside += 1
        elif idx in kept:
            dup += 1
        else:
            kept[idx] = pose
    return kept, {"raw": len(rows), "kept": len(kept), "outside": outside, "duplicate": dup}


def format_pose_line(ts_token, pose):
    return f"{lib.ts_9dec(ts_token)} " + " ".join(f"{v:.9f}" for v in pose)


def format_meta(*, dataset, seq, run, commit, config, coverage, tracking_lost, notes, method=METHOD, mode=MODE):
    import json
    return (f"method: {method}\nmode: {mode}\ndataset: {dataset}\nseq: {seq}\nrun: {run}\ncommit: {commit}\n"
            f"config: {config}\ncoverage: {coverage:.2f}\ntracking_lost: {'true' if tracking_lost else 'false'}\n"
            f"notes: {json.dumps(notes, ensure_ascii=False)}\n")


def process(*, poses, rgb, out_dir, run, dataset, seq, config, max_frames=0, note="", driver_failed=None, commit=None,
            method=METHOD, mode=MODE, label="DSO", rows=None, malformed=0):
    """Returns the exit code. Writes traj_run<run>.txt (unless lost) and meta.yaml. Never overwrites a run.
    `rows` (list of (frame_index, pose7)) replaces reading `poses`: scripts/openvins/openvins_tools.py composes its own poses and reuses this."""
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
    notes = note.strip() + f" host={os.environ.get('QUEUE_HOST') or socket.gethostname()}."
    lost, kept, tstamps, selected = False, {}, [], []
    if driver_failed:
        lost, notes = True, f"{notes} Driver failed: {driver_failed}."
    else:
        if rows is None:
            rows, malformed = read_poses(poses)
        kept, st = map_to_frames(rows, len(frames))
        if len(kept) < MIN_POSES:
            lost = True
            notes += f" No usable trajectory: {len(rows)} raw poses, {len(kept)} usable ({label} never initialised or lost tracking at once)."
        else:
            selected = sorted(kept)
            tstamps = [frames[j][0] for j in selected]
            notes += (f" {len(selected)} of {len(frames)} frames have a pose ({100.0 * len(selected) / len(frames):.1f}% of frames; "
                      f"outside-sequence {st['outside']}, duplicate {st['duplicate']}, malformed {malformed}); frames without a pose "
                      f"(before initialisation, after {label} got lost, before a reset) are NOT interpolated.")
    coverage = 0.0
    if not lost:
        tmp = out_dir / f".traj_run{run}.txt.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for j, tok in zip(selected, tstamps):
                fh.write(format_pose_line(tok, kept[j]) + "\n")
        os.replace(tmp, traj)
        coverage = common.compute_coverage([float(t) for t in tstamps], ftimes)
    (out_dir / "meta.yaml").write_text(format_meta(dataset=dataset, seq=seq, run=run, commit=commit, config=config, coverage=coverage,
                                                   tracking_lost=lost, notes=notes, method=method, mode=mode), encoding="utf-8")
    if lost:
        print(f"No trajectory written for {seq} run {run}: {notes.strip()}")
        return EXIT_LOST
    print(f"Wrote {traj} ({len(selected)} poses of {len(frames)} frames, coverage {coverage:.2f}) and {out_dir / 'meta.yaml'}")
    return 0


def cmd_post(a):
    out_dir = a.results_root / METHOD / MODE / a.dataset / a.seq
    return process(poses=a.poses, rgb=a.rgb, out_dir=out_dir, run=a.run, dataset=a.dataset, seq=a.seq, config=a.config,
                   max_frames=a.max_frames, note=a.note, driver_failed=a.driver_failed, commit=a.commit)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("camera")
    c.add_argument("--dataset", choices=sorted(CALIBS), required=True)
    g = c.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    c.set_defaults(fn=cmd_camera)
    p = sub.add_parser("prep")
    p.add_argument("--rgb", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--max-frames", type=int, default=0)
    p.set_defaults(fn=cmd_prep)
    q = sub.add_parser("post")
    q.add_argument("--poses", required=True, type=Path, help="poses_refined.txt of the driver")
    q.add_argument("--rgb", required=True, type=Path)
    q.add_argument("--results-root", required=True, type=Path)
    q.add_argument("--dataset", required=True)
    q.add_argument("--seq", required=True)
    q.add_argument("--run", required=True, type=int)
    q.add_argument("--config", default="")
    q.add_argument("--max-frames", type=int, default=0)
    q.add_argument("--note", default="")
    q.add_argument("--driver-failed", default=None)
    q.add_argument("--commit", default=None)
    q.set_defaults(fn=cmd_post)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
