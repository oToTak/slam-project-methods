#!/usr/bin/env python3
"""Shared evaluation of one SLAM run (CONVENTIONS.md sections 4-6, CLAUDE_teodor.md).

For one trajectory (TUM format, per-frame) it computes
  * ATE RMSE with evo (association --t_max_diff 0.02; Sim(3) `-as` for mono, SE(3) `-a` otherwise),
  * coverage by time: (t_last - t_first of trajectory) / (t_last - t_first of rgb.txt),
  * position drift over 50 m and 100 m GT-path segments (see ``drift_position``),
  * tracking_lost (full failure: no valid trajectory or evo cannot align it),
  * RPE (translation, delta = 1 frame) for TUM only; never for UCU (GT has no orientation),
then APPENDS one row to results/summary.csv (never rewrites it) and one to results/summary_extra.csv,
and refreshes coverage/tracking_lost in the run's meta.yaml.

Needs the evo==1.37.1 venv (CONVENTIONS section 5); has no GPU/method dependencies.

Example:
  python scripts/eval.py --method dpvo --mode mono --dataset ucu --seq seq01 [--run 1]
"""
import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FIELDS = ["method", "mode", "dataset", "seq", "run", "ate_rmse_m", "rpe_trans_m",
                  "coverage", "tracking_lost", "commit"]
# Not part of the team's summary.csv schema (CONVENTIONS section 6): kept in a sidecar table.
EXTRA_FIELDS = ["method", "mode", "dataset", "seq", "run", "n_matched", "n_possible",
                "drift50_m", "drift100_m", "n_seg50", "n_seg100", "max_gap_s", "n_poses", "note"]
SIM3_MODES = {"mono"}                                   # no metric scale -> -as
SE3_MODES = {"mono_inertial", "rgbd", "rgbd_inertial", "stereo_inertial", "odom"}
T_MAX_DIFF = 0.02
DRIFT_LENGTHS_M = (50.0, 100.0)


# ----------------------------------------------------------------------------- pure helpers

def read_times(path):
    """First column (seconds) of a TUM-like text file; '#' and blank lines skipped."""
    ts = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ts.append(float(line.split()[0]))
    return np.asarray(ts)


def read_traj_array(path):
    """Return (N,8) array [t x y z qx qy qz qw] or raise ValueError if unusable."""
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        vals = line.split()
        if len(vals) != 8:
            raise ValueError(f"{path}: expected 8 columns, got {len(vals)}")
        rows.append([float(v) for v in vals])
    arr = np.asarray(rows, dtype=float).reshape(-1, 8)
    if len(arr) < 2:
        raise ValueError(f"{path}: fewer than 2 poses")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{path}: non-finite values")
    return arr


def compute_coverage(traj_t, rgb_t):
    """CONVENTIONS section 5: time span of the trajectory over time span of rgb.txt, clipped to [0, 1]."""
    if len(traj_t) < 2 or len(rgb_t) < 2:
        return 0.0
    span = float(np.max(rgb_t) - np.min(rgb_t))
    if span <= 0:
        return 0.0
    return float(min(1.0, max(0.0, (np.max(traj_t) - np.min(traj_t)) / span)))


def max_gap(traj_t):
    t = np.sort(np.asarray(traj_t, dtype=float))
    return float(np.max(np.diff(t))) if len(t) > 1 else float("inf")


def drift_position(gt_xyz, est_xyz, length_m):
    """Position drift on GT-path segments of ``length_m`` metres.

    ``gt_xyz``/``est_xyz``: matched positions (N,3) in the same frame (estimate already aligned).
    For every start index i the end index j is the first with GT path length s[j] - s[i] >= length_m;
    the segment error is |(est[j] - est[i]) - (gt[j] - gt[i])|. Returns (rmse_m, n_segments)
    or (nan, 0) if the path is shorter than ``length_m``. GT is position-only (no orientation),
    so only translations are compared. This definition is our proposal: drift_position.py
    from the task description was not available.
    """
    gt = np.asarray(gt_xyz, dtype=float)
    est = np.asarray(est_xyz, dtype=float)
    if len(gt) < 2:
        return float("nan"), 0
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(gt, axis=0), axis=1))])
    errs = []
    for i in range(len(gt) - 1):
        j = int(np.searchsorted(s, s[i] + length_m, side="left"))
        if j >= len(gt):
            break  # start indices only increase s[i]; no later start can have a full segment
        errs.append(np.linalg.norm((est[j] - est[i]) - (gt[j] - gt[i])))
    if not errs:
        return float("nan"), 0
    return float(np.sqrt(np.mean(np.square(errs)))), len(errs)


def fmt(x, nd=6):
    return "" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{nd}f}"


def next_run(res_dir):
    nums = [int(m.group(1)) for p in Path(res_dir).glob("traj_run*.txt")
            if (m := re.fullmatch(r"traj_run(\d+)\.txt", p.name))]
    return max(nums) if nums else None


# ----------------------------------------------------------------------------- evo part

def evo_metrics(gt_path, traj_path, mode, rpe_delta_frames=None):
    """ATE (and optionally RPE) with the evo API, equivalent to
    `evo_ape tum gt traj -as|-a --t_max_diff 0.02` / `evo_rpe ... --delta 1 --delta_unit f`.
    Returns dict with ate, n_matched, n_possible, gt_xyz, est_xyz (aligned), rpe."""
    from evo.core import metrics, sync
    from evo.tools import file_interface

    traj_ref = file_interface.read_tum_trajectory_file(str(gt_path))
    traj_est = file_interface.read_tum_trajectory_file(str(traj_path))
    n_possible = min(traj_ref.num_poses, traj_est.num_poses)  # same "max. M possible" as evo CLI
    ref, est = sync.associate_trajectories(traj_ref, traj_est, max_diff=T_MAX_DIFF)
    n_matched = ref.num_poses
    if n_matched < 3:
        raise RuntimeError(f"only {n_matched} timestamps matched within {T_MAX_DIFF} s")
    correct_scale = mode in SIM3_MODES
    est.align(ref, correct_scale=correct_scale)
    ape = metrics.APE(metrics.PoseRelation.translation_part)
    ape.process_data((ref, est))
    out = {"ate": float(ape.get_statistic(metrics.StatisticsType.rmse)),
           "n_matched": n_matched, "n_possible": n_possible,
           "gt_xyz": ref.positions_xyz, "est_xyz": est.positions_xyz, "rpe": None}
    if rpe_delta_frames:
        rpe = metrics.RPE(metrics.PoseRelation.translation_part, float(rpe_delta_frames),
                          metrics.Unit.frames, 0.0, False)
        rpe.process_data((ref, est))
        out["rpe"] = float(rpe.get_statistic(metrics.StatisticsType.rmse))
    return out


def save_plot(gt_path, traj_path, mode, out_pdf):
    """xy plot via evo_traj CLI, as in CONVENTIONS section 7 (GT grey by evo default)."""
    exe = Path(sys.executable).parent / "evo_traj"
    flag = "-as" if mode in SIM3_MODES else "-a"
    env = dict(os.environ, MPLBACKEND="Agg")
    r = subprocess.run([str(exe), "tum", str(traj_path), "--ref", str(gt_path), flag,
                        "--t_max_diff", str(T_MAX_DIFF), "--plot_mode", "xy", "--no_warnings",
                        "--save_plot", str(out_pdf)], capture_output=True, text=True, env=env)
    return r.returncode == 0


# ----------------------------------------------------------------------------- outputs

def append_row(path, fields, row, key_fields):
    """Append ``row`` to a CSV; create with header if missing; never rewrite. Refuses duplicates of the key."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            if header != fields:
                raise SystemExit(f"{path}: header {header} differs from expected {fields}; refusing to append")
            for old in csv.DictReader(f, fieldnames=header):
                if all(old[k] == str(row[k]) for k in key_fields):
                    raise SystemExit(f"{path}: row for {[row[k] for k in key_fields]} already exists "
                                     "(summary.csv is append-only; use a new run number)")
        needs_nl = not path.read_bytes().endswith(b"\n")
        with open(path, "a", newline="") as f:
            if needs_nl:
                f.write("\n")
            csv.DictWriter(f, fieldnames=fields, lineterminator="\n").writerow(row)
    else:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerow(row)


def read_meta(path):
    meta = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            m = re.match(r"^(\w+):[ \t]*(.*)$", line)
            if m:
                meta[m.group(1)] = m.group(2).strip().strip('"')
    return meta


def update_meta(path, run, coverage, tracking_lost, note, create_with):
    """Refresh coverage/tracking_lost (and a note) in meta.yaml only if it describes this run."""
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    if text:
        m = re.search(r"^run:[ \t]*(\d+)", text, re.M)
        if m and int(m.group(1)) != run:
            return False  # meta.yaml describes a later run (CONVENTIONS section 4)
    else:
        text = (f"method: {create_with['method']}\nmode: {create_with['mode']}\n"
                f"dataset: {create_with['dataset']}\nseq: {create_with['seq']}\nrun: {run}\n"
                f"commit: {create_with['commit']}\nconfig: \"\"\ncoverage: 0.00\n"
                f"tracking_lost: false\nnotes: \"\"\n")

    def setf(txt, key, val):
        line = f"{key}: {val}"
        if re.search(rf"^{key}:.*$", txt, re.M):
            return re.sub(rf"^{key}:.*$", lambda _m: line, txt, flags=re.M)
        return txt.rstrip("\n") + "\n" + line + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    text = setf(text, "coverage", f"{coverage:.2f}")
    text = setf(text, "tracking_lost", "true" if tracking_lost else "false")
    if note:
        mnote = re.search(r"^notes:[ \t]*(.*)$", text, re.M)
        old = (mnote.group(1).strip().strip('"') if mnote else "")
        old = re.sub(r"\s*eval:[^.]*\.", "", old).strip()
        text = setf(text, "notes", json.dumps(f"{old} eval: {note}.".strip(), ensure_ascii=False))
    path.write_text(text)
    return True


def git_short(repo):
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        f = Path(repo) / "BUNDLE_COMMIT"  # unpacked bundle without .git (see make_bundle.sh)
        return f.read_text().strip()[:7] if f.exists() else ""


# ----------------------------------------------------------------------------- main

def evaluate(args):
    data_root = Path(os.environ.get("DATA_ROOT", "/mnt/t/slam-data"))
    data_dir = data_root / ("tum" if args.dataset == "tum" else "ucu_tum") / args.seq
    res_dir = Path(args.results_root) / args.method / args.mode / args.dataset / args.seq
    run = args.run if args.run is not None else next_run(res_dir)
    if run is None and not args.traj:
        raise SystemExit(f"no traj_run<N>.txt in {res_dir}")
    traj = Path(args.traj) if args.traj else res_dir / f"traj_run{run}.txt"
    if run is None:
        run = 1
    gt = Path(args.gt) if args.gt else data_dir / "groundtruth.txt"
    rgb = Path(args.rgb) if args.rgb else data_dir / "rgb.txt"
    for p in (gt, rgb):
        if not p.exists():
            raise SystemExit(f"missing: {p}")
    meta_path = res_dir / "meta.yaml"
    meta = read_meta(meta_path)
    meta_same_run = meta.get("run") == str(run)
    commit = args.commit or (meta.get("commit", "")[:7] if meta_same_run else "") or git_short(REPO_ROOT)

    notes = []
    result = {"ate": None, "rpe": None, "n_matched": "", "n_possible": "",
              "drift": {L: (float("nan"), 0) for L in DRIFT_LENGTHS_M}}
    coverage, gap, n_poses, lost = 0.0, float("nan"), 0, False
    log_hit = False
    if args.log and Path(args.log).exists():
        pat = re.compile(args.log_pattern, re.I)
        log_hit = any(pat.search(l) for l in Path(args.log).read_text(errors="replace").splitlines())
        if log_hit:
            notes.append(f"log matches /{args.log_pattern}/")

    try:
        arr = read_traj_array(traj)
        n_poses = len(arr)
        coverage = compute_coverage(arr[:, 0], read_times(rgb))
        gap = max_gap(arr[:, 0])
        rpe_delta = 1 if (args.dataset == "tum") else None  # never RPE on UCU
        res = evo_metrics(gt, traj, args.mode, rpe_delta)
        result.update({k: res[k] for k in ("ate", "rpe", "n_matched", "n_possible")})
        for L in DRIFT_LENGTHS_M:
            result["drift"][L] = drift_position(res["gt_xyz"], res["est_xyz"], L)
        if res["n_matched"] < 0.9 * res["n_possible"]:
            notes.append(f"ATE matched {res['n_matched']}/{res['n_possible']} timestamps")
    except Exception as e:  # evo raises its own exception types; any failure here is a full failure
        lost = True
        coverage = 0.0 if n_poses < 2 else coverage
        result["ate"] = None
        notes.append(f"full failure: {type(e).__name__}: {e}")
    if args.lost_gap_s is not None and math.isfinite(gap) and gap > args.lost_gap_s:
        lost = True
        notes.append(f"trajectory gap {gap:.1f}s > {args.lost_gap_s}s")

    row = {"method": args.method, "mode": args.mode, "dataset": args.dataset, "seq": args.seq,
           "run": run, "ate_rmse_m": fmt(result["ate"], 3),
           "rpe_trans_m": fmt(result["rpe"], 3) if args.dataset == "tum" else "",
           "coverage": f"{coverage:.2f}", "tracking_lost": "true" if lost else "false",
           "commit": commit}
    extra = {k: row[k] for k in ("method", "mode", "dataset", "seq", "run")}
    d50, d100 = result["drift"][50.0], result["drift"][100.0]
    extra.update({"n_matched": result["n_matched"], "n_possible": result["n_possible"],
                  "drift50_m": fmt(d50[0], 3), "drift100_m": fmt(d100[0], 3),
                  "n_seg50": d50[1], "n_seg100": d100[1],
                  "max_gap_s": fmt(gap, 3), "n_poses": n_poses, "note": "; ".join(notes)})

    print(f"== {args.method} {args.mode} {args.dataset}/{args.seq} run{run} ==")
    print(f"ATE RMSE   : {row['ate_rmse_m'] or '-'} m   (matched {extra['n_matched']}/{extra['n_possible']})")
    if args.dataset == "tum":
        print(f"RPE (1 fr) : {row['rpe_trans_m'] or '-'} m")
    print(f"coverage   : {row['coverage']}   tracking_lost: {row['tracking_lost']}   poses: {n_poses}   max gap: {extra['max_gap_s'] or '-'} s")
    print(f"drift 50 m : {extra['drift50_m'] or '-'} m ({d50[1]} seg)   100 m: {extra['drift100_m'] or '-'} m ({d100[1]} seg)")
    for n in notes:
        print("note:", n)

    if args.dry_run:
        print("(dry run: nothing written)")
        return row
    summary = Path(args.summary) if args.summary else Path(args.results_root) / "summary.csv"
    extra_path = summary.with_name("summary_extra.csv")
    append_row(summary, SUMMARY_FIELDS, row, ["method", "mode", "dataset", "seq", "run"])
    append_row(extra_path, EXTRA_FIELDS, extra, ["method", "mode", "dataset", "seq", "run"])
    update_meta(meta_path, int(run), coverage, lost, "; ".join(notes) if notes else "",
                create_with={"method": args.method, "mode": args.mode, "dataset": args.dataset,
                             "seq": args.seq, "commit": commit})
    if not args.no_plot and not lost:
        ok = save_plot(gt, traj, args.mode, traj.parent / "traj_xy.pdf")
        print("plot:", traj.parent / "traj_xy.pdf" if ok else "evo_traj failed (plot skipped)")
    print(f"appended to {summary} and {extra_path}")
    return row


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", required=True, help="orbslam3, dpvo, dso, openvins, dpvslam, kimera, wheel_odom ...")
    ap.add_argument("--mode", required=True, choices=sorted(SIM3_MODES | SE3_MODES))
    ap.add_argument("--dataset", required=True, choices=["tum", "ucu", "euroc"])
    ap.add_argument("--seq", required=True)
    ap.add_argument("--run", type=int, default=None, help="default: highest traj_run<N>.txt")
    ap.add_argument("--traj", help="override trajectory path")
    ap.add_argument("--gt", help="override ground-truth path")
    ap.add_argument("--rgb", help="override rgb.txt path (coverage reference)")
    ap.add_argument("--log", help="method log; scanned for --log-pattern")
    ap.add_argument("--log-pattern", default=r"tracking (is )?lost|lost tracking|tracking failed",
                    help="regex for signs of tracking loss in the log (recorded in note)")
    ap.add_argument("--lost-gap-s", type=float, default=None,
                    help="if set, a trajectory gap longer than this also sets tracking_lost (rule to be agreed)")
    ap.add_argument("--commit", help="commit hash for the row (default: meta.yaml / git HEAD)")
    ap.add_argument("--results-root", default=str(REPO_ROOT / "results"))
    ap.add_argument("--summary", help="summary.csv path (default: <results-root>/summary.csv)")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print metrics, write nothing")
    return ap


if __name__ == "__main__":
    evaluate(build_parser().parse_args())
