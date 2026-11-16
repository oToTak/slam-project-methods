#!/usr/bin/env python3
"""Run mono DPVO on a TUM-format sequence and write CONVENTIONS.md section 4 outputs.

Pure helpers (rgb.txt parsing, trajectory/meta writing, coverage, run numbering) need
no torch/dpvo and are unit-tested on a CPU-only machine. DPVO is imported lazily in
``run_slam`` and is never modified.
"""
import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METHOD, MODE = "dpvo", "mono"       # METHOD is the default; --method dpvslam reuses everything below (scripts/dpvslam/run.sh)
# (top/bottom, left/right) crop in pixels after undistortion, as in DPVO evaluate_tum.py
CROP_AFTER_UNDISTORT = {"tum": (8, 16)}
# Fixed cfg options of each method on top of config/default.yaml (yacs "KEY VALUE ..." list, like DPVO's own --opts).
# DPV-SLAM = the same DPVO repo/commit with the proximity loop closure (README "SLAM Backends": --opts LOOP_CLOSURE True).
# CLASSIC_LOOP_CLOSURE (image retrieval, needs DBoW2 + DPRetrieval built with cmake/apt) is deliberately NOT used.
METHOD_OPTS = {"dpvo": [], "dpvslam": ["LOOP_CLOSURE", "True"]}


# ---------------------------------------------------------------- pure helpers

def read_rgb_txt(path):
    """Parse TUM rgb.txt -> list of (timestamp_seconds, image_path). Skips '#' and blank lines."""
    path = Path(path)
    frames = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ts, rel = line.split()[:2]
        frames.append((float(ts), path.parent / rel))
    return frames


def read_calib(path):
    """Single-line calib file: fx fy cx cy [k1 k2 p1 p2 ...]."""
    vals = [float(v) for v in Path(path).read_text().split()]
    if len(vals) < 4:
        raise ValueError(f"{path}: expected at least 4 numbers, got {len(vals)}")
    return vals


def format_tum_line(t, pose):
    """pose = (x, y, z, qx, qy, qz, qw) as returned by DPVO.terminate()."""
    return f"{t:.9f} " + " ".join(f"{v:.9f}" for v in pose)


def write_tum_trajectory(path, tstamps, poses):
    """TUM: 'timestamp tx ty tz qx qy qz qw', spaces, no header."""
    if len(tstamps) != len(poses):
        raise ValueError("tstamps and poses differ in length")
    with open(path, "w") as f:
        for t, p in zip(tstamps, poses):
            f.write(format_tum_line(float(t), [float(v) for v in p]) + "\n")


def compute_coverage(traj_tstamps, rgb_tstamps):
    """(t_last - t_first of trajectory) / (t_last - t_first of rgb.txt), CONVENTIONS section 5."""
    if len(traj_tstamps) < 2 or len(rgb_tstamps) < 2:
        return 0.0
    span = max(rgb_tstamps) - min(rgb_tstamps)
    if span <= 0:
        return 0.0
    cov = (max(traj_tstamps) - min(traj_tstamps)) / span
    return max(0.0, min(1.0, cov))


def next_run_number(out_dir):
    """Next free N for traj_run<N>.txt; existing runs are never overwritten."""
    out_dir = Path(out_dir)
    nums = [int(m.group(1)) for p in out_dir.glob("traj_run*.txt")
            if (m := re.fullmatch(r"traj_run(\d+)\.txt", p.name))]
    return max(nums, default=0) + 1


def check_tstamps(tstamps, rgb_tstamps, tol=1e-6):
    """Guard: DPVO must return the exact timestamps we passed (one pose per frame)."""
    if len(tstamps) == len(rgb_tstamps):
        err = max((abs(float(a) - float(b)) for a, b in zip(tstamps, rgb_tstamps)), default=0.0)
        if err >= tol:
            raise RuntimeError(f"DPVO timestamps differ from rgb.txt (max abs diff {err:.3g} s >= {tol}); "
                               "refusing to write a trajectory with wrong time")


def poses_are_valid(poses):
    return len(poses) >= 2 and all(math.isfinite(float(v)) for p in poses for v in p)


def effective_opts(method, extra_opts=None):
    """cfg option list for ``method``: its fixed options, then the caller's extra ones (later keys win in yacs)."""
    return list(METHOD_OPTS[method]) + list(extra_opts or [])


def base_notes(method, extra_opts, stride, n_frames):
    """First part of meta.yaml `notes`. For plain DPVO it is exactly the text used before --method existed."""
    if method == "dpvslam":
        what = ("DPV-SLAM = DPVO 0ac95b6 + proximity loop closure (cfg LOOP_CLOSURE=True), "
                "classic image-retrieval loop closure NOT used")
    else:
        what = "DPVO 0ac95b6"
    notes = f"{what}, config/default.yaml"
    if extra_opts:
        notes += f", extra opts {' '.join(extra_opts)}"
    return notes + f", stride={stride}, {n_frames} frames."


def lc_note(stats):
    """Loop-closure facts of a run (empty for plain DPVO): how often the backend really fired."""
    if "lc_cfg" not in stats:
        return ""
    return (f"Loop closure: {stats['lc_cfg']}; {stats['lc_edge_batches']} loop-edge batches "
            f"({stats['lc_edges']} edges), {stats['global_ba_calls']} global BA calls "
            f"({stats['global_ba_s']:.0f}s of {stats['wall_s']:.0f}s); {stats['n_keyframes']} keyframes kept; "
            f"BA failures swallowed by DPVO: {stats['ba_failures']}"
            + (f" (first: {stats['ba_first_error']})" if stats["ba_failures"] else "") + ".")


def format_meta(*, dataset, seq, run, commit, config, coverage, tracking_lost, notes, method=METHOD):
    """meta.yaml text per CONVENTIONS section 4 (hand-written: no PyYAML needed in Colab)."""
    return (
        f"method: {method}\n"
        f"mode: {MODE}\n"
        f"dataset: {dataset}\n"
        f"seq: {seq}\n"
        f"run: {run}\n"
        f"commit: {commit}\n"
        f"config: {config}\n"
        f"coverage: {coverage:.2f}\n"
        f"tracking_lost: {'true' if tracking_lost else 'false'}\n"
        f"notes: {json.dumps(notes, ensure_ascii=False)}\n"
    )


def write_outputs(out_dir, *, dataset, seq, config, commit, rgb_tstamps, tstamps, poses,
                  tracking_lost=False, notes="", run=None, method=METHOD):
    """Write traj_run<N>.txt (unless tracking lost) and meta.yaml. Returns (run, traj_path or None).

    ``run`` is chosen by the caller (the queue worker passes it explicitly); default = next free number.
    An existing traj_run<run>.txt is never overwritten.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if run is None:
        run = next_run_number(out_dir)
    elif (out_dir / f"traj_run{run}.txt").exists():
        raise FileExistsError(f"{out_dir / f'traj_run{run}.txt'} already exists (runs are never overwritten)")
    if not tracking_lost and not poses_are_valid(poses):
        tracking_lost = True
        notes = (notes + " " if notes else "") + "invalid or empty pose output."
    traj_path = None
    coverage = 0.0
    if not tracking_lost:
        traj_path = out_dir / f"traj_run{run}.txt"
        write_tum_trajectory(traj_path, tstamps, poses)
        coverage = compute_coverage(tstamps, rgb_tstamps)
    (out_dir / "meta.yaml").write_text(format_meta(
        dataset=dataset, seq=seq, run=run, commit=commit, config=config,
        coverage=coverage, tracking_lost=tracking_lost, notes=notes, method=method))
    return run, traj_path


def git_commit(repo=REPO_ROOT):
    try:
        sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                      text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                                        text=True).strip()
        return sha, bool(dirty)
    except Exception:
        # unpacked bundle (make_bundle.sh) has no .git: it carries the source commit in BUNDLE_COMMIT
        f = Path(repo) / "BUNDLE_COMMIT"
        return (f.read_text().strip() if f.exists() else "unknown"), False


# ---------------------------------------------------------------- GPU part

def frame_stream(frames, calib, dataset, stride=1):
    """Yield (timestamp, CHW BGR uint8 image, intrinsics[4]) with undistort/crop as in DPVO."""
    import cv2
    import numpy as np

    fx, fy, cx, cy = calib[:4]
    dist = np.array(calib[4:]) if len(calib) > 4 else None
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
    crop_v, crop_h = CROP_AFTER_UNDISTORT.get(dataset, (0, 0))

    for ts, imfile in frames[::stride]:
        image = cv2.imread(str(imfile))
        if image is None:
            raise FileNotFoundError(imfile)
        intr = np.array([fx, fy, cx, cy], dtype=np.float64)
        if dist is not None:
            image = cv2.undistort(image, K, dist)
        if crop_v or crop_h:
            h, w = image.shape[:2]
            image = image[crop_v:h - crop_v, crop_h:w - crop_h]
            intr[2] -= crop_h
            intr[3] -= crop_v
        h, w = image.shape[:2]
        image = image[:h - h % 16, :w - w % 16]  # DPVO needs multiples of 16
        yield ts, image.transpose(2, 0, 1), intr


class _GpuSampler:
    """Polls device memory (total - free, i.e. what nvidia-smi shows incl. other processes) in a thread."""

    def __init__(self, period=0.5):
        import threading
        self.period, self.peak, self.base, self._stop = period, 0, 0, threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _used(self):
        import torch
        free, total = torch.cuda.mem_get_info()
        return total - free

    def _loop(self):
        while not self._stop.wait(self.period):
            self.peak = max(self.peak, self._used())

    def __enter__(self):
        self.base = self.peak = self._used()
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, self._used())


def _count_loop_closure(slam, lc):
    """Count what the DPV-SLAM backend does, without changing it: loop-edge batches returned by
    PatchGraph.edges_loop() and global-BA calls (+ their wall time). Never breaks the run."""
    import time

    import torch

    try:
        orig_edges = slam.pg.edges_loop

        def edges_loop():
            res = orig_edges()
            n = int(res[0].numel()) // slam.M if len(res) == 2 else 0     # (kk, jj) of M patches per loop edge
            if n:
                lc["lc_edge_batches"] += 1
                lc["lc_edges"] += n
            return res

        slam.pg.edges_loop = edges_loop
        orig_ba = slam._DPVO__run_global_BA        # name-mangled private method of DPVO

        def global_ba():
            torch.cuda.synchronize()
            t = time.time()
            try:
                return orig_ba()
            finally:
                torch.cuda.synchronize()
                lc["global_ba_calls"] += 1
                lc["global_ba_s"] += time.time() - t

        slam._DPVO__run_global_BA = global_ba
        # DPVO.update() swallows every BA exception ("Warning BA failed...", e.g. CUDA OOM of the dense 6N x 6N global BA):
        # count them so a run whose backend silently did nothing is visible in meta notes. The error is re-raised unchanged.
        ba_mod = sys.modules[type(slam).__module__].fastba
        orig_fastba = ba_mod.BA

        def fastba_counted(*a, **k):
            try:
                return orig_fastba(*a, **k)
            except BaseException as e:
                lc["ba_failures"] += 1
                lc.setdefault("ba_first_error", f"{type(e).__name__}: {str(e).splitlines()[0][:100] if str(e) else ''}")
                raise

        ba_mod.BA = fastba_counted
    except Exception as e:                          # instrumentation is optional
        print(f"WARNING: loop-closure counters disabled: {e}")


def run_slam(frames, calib, dataset, dpvo_dir, network=None, stride=1, cfg_opts=None):
    """Feed frames with their rgb.txt timestamps to DPVO (``cfg_opts``: yacs KEY VALUE list, e.g. LOOP_CLOSURE True).

    Returns (poses Nx7, tstamps N, stats) where stats holds wall time, GPU name and memory peaks.
    """
    import resource
    import time

    import numpy as np
    import torch

    sys.path.insert(0, str(dpvo_dir))  # fallback if not pip-installed
    from dpvo.config import cfg
    from dpvo.dpvo import DPVO

    cfg.merge_from_file(str(Path(dpvo_dir) / "config" / "default.yaml"))
    if cfg_opts:
        cfg.merge_from_list(list(cfg_opts))
    network = network or str(Path(dpvo_dir) / "dpvo.pth")
    torch.manual_seed(1234)
    slam = None
    lc = {"lc_edge_batches": 0, "lc_edges": 0, "global_ba_calls": 0, "global_ba_s": 0.0, "ba_failures": 0}
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad(), _GpuSampler() as gpu:
        for ts, image, intr in frame_stream(frames, calib, dataset, stride):
            image = torch.as_tensor(image, device="cuda")
            intr = torch.as_tensor(intr, dtype=torch.float, device="cuda")
            if slam is None:
                slam = DPVO(cfg, network, ht=image.shape[-2], wd=image.shape[-1], viz=False)
                if cfg.LOOP_CLOSURE:
                    _count_loop_closure(slam, lc)
            slam(ts, image, intr)
        n_keyframes = int(slam.n)
        poses, tstamps = slam.terminate()
    stats = {
        "wall_s": time.time() - t0,
        "gpu": torch.cuda.get_device_name(0),
        "vram_total_peak_mib": gpu.peak / 2**20,        # incl. memory used by other processes
        "vram_total_base_mib": gpu.base / 2**20,        # before this run's allocations
        "torch_max_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
        "torch_max_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
        "ram_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,  # Linux: KiB
        "n_frames": len(frames[::stride]),
    }
    if cfg.LOOP_CLOSURE:
        stats.update(lc, n_keyframes=n_keyframes,
                     lc_cfg=f"BACKEND_THRESH={cfg.BACKEND_THRESH} MAX_EDGE_AGE={cfg.MAX_EDGE_AGE} "
                            f"GLOBAL_OPT_FREQ={cfg.GLOBAL_OPT_FREQ}")
    return np.asarray(poses), np.asarray(tstamps), stats


def stats_note(stats):
    return (f"{stats['gpu']}; wall {stats['wall_s']:.0f}s ({stats['n_frames'] / stats['wall_s']:.1f} fps); "
            f"VRAM peak {stats['vram_total_peak_mib']:.0f} MiB total used "
            f"(base {stats['vram_total_base_mib']:.0f}), torch alloc {stats['torch_max_allocated_mib']:.0f} / "
            f"reserved {stats['torch_max_reserved_mib']:.0f} MiB; RAM peak {stats['ram_peak_mib']:.0f} MiB.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--method", default=METHOD, choices=sorted(METHOD_OPTS),
                    help="dpvo (default) or dpvslam = DPVO + proximity loop closure (METHOD_OPTS); sets the results folder")
    ap.add_argument("--opts", nargs="*", default=[], metavar="KEY VALUE",
                    help="extra DPVO cfg options after the method's own (yacs list); recorded in meta notes")
    ap.add_argument("--dataset", required=True, choices=["tum", "ucu"])
    ap.add_argument("--seq", required=True)
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--calib", required=True, type=Path, help="calib file (repo-relative path is recorded in meta)")
    ap.add_argument("--dpvo-dir", type=Path,
                    default=Path(os.environ.get("DPVO_DIR", str(Path.home() / "src" / "DPVO"))))
    ap.add_argument("--network", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-frames", type=int, default=0, help="debug/smoke test only: use first N frames")
    ap.add_argument("--results-root", type=Path, default=REPO_ROOT / "results")
    ap.add_argument("--run", type=int, default=None,
                    help="explicit run number N for traj_run<N>.txt (default: next free; used by the queue worker)")
    args = ap.parse_args()

    frames = read_rgb_txt(args.data_dir / "rgb.txt")
    if args.max_frames > 0:
        frames = frames[:args.max_frames]
    calib = read_calib(args.calib if args.calib.is_absolute() else REPO_ROOT / args.calib)
    rgb_ts = [t for t, _ in frames]
    out_dir = args.results_root / args.method / MODE / args.dataset / args.seq
    opts = effective_opts(args.method, args.opts)
    sha, dirty = git_commit()
    config = str(args.calib)
    notes = base_notes(args.method, args.opts, args.stride, len(frames))
    if args.max_frames > 0:
        notes += " TRUNCATED smoke run (--max-frames)."
    if dirty:
        notes += " Repo had uncommitted changes."
    notes += f" host={os.environ.get('QUEUE_HOST') or socket.gethostname()}."
    if args.run is not None and (out_dir / f"traj_run{args.run}.txt").exists():
        raise SystemExit(f"{out_dir / f'traj_run{args.run}.txt'} already exists; refusing to overwrite a run")

    try:
        poses, tstamps, stats = run_slam(frames, calib, args.dataset, args.dpvo_dir,
                                         args.network, args.stride, opts)
        notes += " " + stats_note(stats)
        print(stats_note(stats))
        if lc_note(stats):
            notes += " " + lc_note(stats)
            print(lc_note(stats))
    except Exception as e:  # tracking failure / CUDA error: record it, never leave stale meta
        write_outputs(out_dir, dataset=args.dataset, seq=args.seq, config=config, commit=sha,
                      rgb_tstamps=rgb_ts, tstamps=[], poses=[], tracking_lost=True,
                      notes=f"{notes} Failed: {type(e).__name__}: {e}", run=args.run, method=args.method)
        raise
    check_tstamps(tstamps, [t for t, _ in frames[::args.stride]])
    run, traj = write_outputs(out_dir, dataset=args.dataset, seq=args.seq, config=config,
                              commit=sha, rgb_tstamps=rgb_ts, tstamps=tstamps, poses=poses,
                              notes=notes, run=args.run, method=args.method)
    print(f"Wrote {traj} ({len(poses)} poses) and {out_dir / 'meta.yaml'}")


if __name__ == "__main__":
    main()
