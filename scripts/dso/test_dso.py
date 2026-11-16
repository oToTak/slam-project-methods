#!/usr/bin/env python3
"""Stub tests of the pure-python parts of the DSO integration (no DSO, no data, no GPU, stdlib only).

Run: python3 scripts/dso/test_dso.py        (seconds). Covers: camera-file generation (RadTan, honest numbers, committed files in sync),
image list, driver-output -> TUM conversion with honest gaps, meta.yaml, no-overwrite rule.
The C++ driver, the recipe and run.sh are checked by `bash -n` only; their real test is the sanity run in PREP_NOTES.md.
"""
import math
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dso_tools as dt  # noqa: E402

lib = dt.lib
REPO = dt.REPO_ROOT


def write(p, text):
    Path(p).write_text(text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ camera file

def test_camera():
    vals = dt.read_calib_numbers(REPO / "configs" / "dpvo" / "calib_ucu.txt")
    text = dt.camera_text("ucu")
    l1, l2, l3, l4 = text.splitlines()
    assert l1.startswith("RadTan ") and l2 == "640 480" and l3 == "crop" and l4 == "640 480"
    nums = [float(v) for v in l1.split()[1:]]
    assert len(nums) == 8 and nums == vals[:8]                       # exact: nothing rounded, nothing reordered
    # same numbers as the ORB-SLAM3 calibration file (one source of truth for the camera)
    c = lib.load_calibration()["camera"]
    assert nums == [c[k] for k in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2")]
    assert nums[2] > 1 and nums[3] > 1                               # absolute pixels: DSO would rescale values < 1
    assert len(l1.split()) == 9                                      # tag + exactly 8 numbers (DSO's sscanf expects that)
    # DSO RadTan parses the first line with 'RadTan %f x8': emulate the sscanf of Undistort.cpp
    assert re.fullmatch(r"RadTan( -?\d+(\.\d+)?(e-?\d+)?){8}", l1)
    # TUM fr1: the 9th number (k3) cannot be expressed and is dropped, the rest is kept
    tum = dt.read_calib_numbers(REPO / "configs" / "dpvo" / "calib_tum_fr1.txt")
    assert len(tum) == 9
    assert [float(v) for v in dt.camera_text("tum").splitlines()[0].split()[1:]] == tum[:8]
    # guards
    for bad in ([1, 2, 0.5, 0.5, 0, 0, 0, 0], [1, 2, 3], [1, 2, 3, 4, float("nan"), 0, 0, 0]):
        try:
            dt.render_camera(bad, (640, 480))
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    try:
        dt.render_camera(vals, (641, 480))
        raise AssertionError("size not divisible by 32 accepted")
    except ValueError:
        pass
    # committed camera files equal the generated text (no hand edits)
    for ds in ("ucu", "tum"):
        target = dt.CALIBS[ds][2]
        assert target.exists(), target
        assert target.read_text(encoding="utf-8").replace("\r\n", "\n") == dt.camera_text(ds), f"{target} is out of date"


def test_radtan_formula_matches_opencv():
    """DSO's distortCoordinates (Undistort.cpp) == OpenCV radtan with (k1, k2, p1, p2) = (k1, k2, r1, r2): same expression in python."""
    k1, k2, r1, r2 = 0.139268662386042, -0.30741279731381366, 0.0007272799549728076, 0.0006655761403425819
    for ix, iy in ((0.1, -0.2), (0.5, 0.4), (-0.3, 0.05)):
        rho2 = ix * ix + iy * iy
        rad = k1 * rho2 + k2 * rho2 * rho2
        dso_x = ix + ix * rad + 2.0 * r1 * ix * iy + r2 * (rho2 + 2.0 * ix * ix)       # as in Undistort.cpp:1057
        dso_y = iy + iy * rad + 2.0 * r2 * ix * iy + r1 * (rho2 + 2.0 * iy * iy)       # as in Undistort.cpp:1058
        cv_x = ix * (1 + k1 * rho2 + k2 * rho2 ** 2) + 2 * r1 * ix * iy + r2 * (rho2 + 2 * ix * ix)   # OpenCV: p1=r1, p2=r2
        cv_y = iy * (1 + k1 * rho2 + k2 * rho2 ** 2) + r1 * (rho2 + 2 * iy * iy) + 2 * r2 * ix * iy
        assert abs(dso_x - cv_x) < 1e-15 and abs(dso_y - cv_y) < 1e-15


# ------------------------------------------------------------------ image list

def test_image_list():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "rgb").mkdir()
        for n in ("a", "b", "c"):
            (d / "rgb" / f"{n}.png").write_bytes(b"x")
        write(d / "rgb.txt", "# c\n100.000000000 rgb/a.png\n100.033333333 rgb/b.png\n100.066666666 rgb/c.png\n")
        lst = dt.make_image_list(d / "rgb.txt")
        assert [Path(p).name for p in lst] == ["a.png", "b.png", "c.png"] and all(Path(p).is_absolute() for p in lst)
        assert len(dt.make_image_list(d / "rgb.txt", 2)) == 2
        assert dt.main(["prep", "--rgb", str(d / "rgb.txt"), "--out", str(d / "w" / "images.txt")]) == 0
        assert (d / "w" / "images.txt").read_text().count("\n") == 3
        (d / "rgb" / "c.png").unlink()
        assert dt.main(["prep", "--rgb", str(d / "rgb.txt"), "--out", str(d / "w2" / "images.txt")]) == 1   # missing image is an error


# ------------------------------------------------------------------ driver output -> TUM

def test_postprocess():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n = 10
        write(d / "rgb.txt", "# h\n" + "".join(f"{1000 + i / 30:.9f} rgb/{i}.png\n" for i in range(n)))
        # driver: frames 0-2 absent (initialisation), 3..6 valid, 7..9 absent (lost); plus junk lines
        lines = ["# comment", "3 0 0 0 0 0 0 1", "4 0.1 0 0 0 0 0 2", "5 0.2 0 0 0 0 0 1", "5 9 9 9 0 0 0 1",   # duplicate index 5
                 "6 0.3 0 0 0 0 0 1", "99 1 1 1 0 0 0 1", "x y z", "7 nan 0 0 0 0 0 1", "8 1 1 1 0 0 0 0"]       # outside, junk, nan, zero quat
        write(d / "poses.txt", "\n".join(lines) + "\n")
        out = d / "res"
        rc = dt.process(poses=d / "poses.txt", rgb=d / "rgb.txt", out_dir=out, run=1, dataset="ucu", seq="seq01", config="cfg", commit="abc")
        assert rc == 0
        rows = [l.split() for l in (out / "traj_run1.txt").read_text().splitlines()]
        assert [r[0] for r in rows] == [f"{1000 + i / 30:.9f}" for i in (3, 4, 5, 6)]          # exact rgb.txt timestamps, gaps kept
        assert all(len(r) == 8 for r in rows)
        assert [float(r[1]) for r in rows] == [0.0, 0.1, 0.2, 0.3]                              # first pose of a duplicate index wins
        q = [float(v) for v in rows[1][4:8]]
        assert abs(math.sqrt(sum(v * v for v in q)) - 1.0) < 1e-8 and abs(q[3] - 1.0) < 1e-8     # '0 0 0 2' normalised
        meta = (out / "meta.yaml").read_text()
        for k in ("method: dso", "mode: mono", "dataset: ucu", "seq: seq01", "run: 1", "commit: abc", "config: cfg", "tracking_lost: false"):
            assert k in meta, k
        cov = float(re.search(r"coverage: ([0-9.]+)", meta).group(1))
        assert abs(cov - 3 / 9) < 0.006                                                         # (t6-t3)/(t9-t0): time span, like CONVENTIONS
        assert "4 of 10 frames" in meta and "NOT interpolated" in meta
        # a run is never overwritten
        assert dt.process(poses=d / "poses.txt", rgb=d / "rgb.txt", out_dir=out, run=1, dataset="ucu", seq="seq01", config="c", commit="x") == 1
        # --max-frames cuts the sequence (frames beyond it are 'outside')
        assert dt.process(poses=d / "poses.txt", rgb=d / "rgb.txt", out_dir=out, run=2, dataset="ucu", seq="seq01", config="c", commit="x",
                          max_frames=5) == 0
        assert len((out / "traj_run2.txt").read_text().splitlines()) == 2                       # frames 3 and 4 only
        # nothing usable -> exit 4, no trajectory file, meta says lost
        write(d / "empty.txt", "")
        assert dt.process(poses=d / "empty.txt", rgb=d / "rgb.txt", out_dir=out, run=3, dataset="ucu", seq="seq01", config="c", commit="x") == 4
        assert not (out / "traj_run3.txt").exists() and "tracking_lost: true" in (out / "meta.yaml").read_text()
        assert dt.process(poses=d / "missing.txt", rgb=d / "rgb.txt", out_dir=out, run=4, dataset="ucu", seq="seq01", config="c", commit="x") == 4
        assert dt.process(poses=d / "poses.txt", rgb=d / "rgb.txt", out_dir=out, run=5, dataset="ucu", seq="seq01", config="c", commit="x",
                          driver_failed="boom") == 4 and "Driver failed: boom" in (out / "meta.yaml").read_text()
        # only one usable pose is not a trajectory
        write(d / "one.txt", "3 0 0 0 0 0 0 1\n")
        assert dt.process(poses=d / "one.txt", rgb=d / "rgb.txt", out_dir=out, run=6, dataset="ucu", seq="seq01", config="c", commit="x") == 4


def test_cli_post_path():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        write(d / "rgb.txt", "".join(f"{500 + i / 30:.9f} rgb/{i}.png\n" for i in range(4)))
        write(d / "p.txt", "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n3 3 0 0 0 0 0 1\n")
        rc = dt.main(["post", "--poses", str(d / "p.txt"), "--rgb", str(d / "rgb.txt"), "--results-root", str(d / "R"), "--dataset", "ucu",
                      "--seq", "seq01", "--run", "1", "--config", "c", "--commit", "x"])
        assert rc == 0 and (d / "R" / "dso" / "mono" / "ucu" / "seq01" / "traj_run1.txt").exists()


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"OK: {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
