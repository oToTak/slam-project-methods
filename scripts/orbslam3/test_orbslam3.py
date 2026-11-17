#!/usr/bin/env python3
"""Stub tests of the pure-python parts of the ORB-SLAM3 integration (no ORB-SLAM3, no data, no GPU, stdlib only).

Run: python3 scripts/orbslam3/test_orbslam3.py        (seconds). Covers: settings generation, the camera-IMU transform,
IMU conversion, raw-trajectory post-processing (matching, honest gaps, frame conversion, outputs), EuRoC adapter.
The C++ driver, the recipe and run.sh are checked by `bash -n` only; their real test is the sanity run in PREP_NOTES.md.
"""
import math
import os
import random
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import convert_imu as ci  # noqa: E402
import euroc_to_tum as e2t  # noqa: E402
import make_settings as ms  # noqa: E402
import orb3_lib as lib  # noqa: E402
import postprocess as pp  # noqa: E402

REPO = lib.REPO_ROOT


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


def rand_pose(rng):
    q = lib.quat_normalize([rng.gauss(0, 1) for _ in range(4)])
    return [rng.uniform(-5, 5) for _ in range(3)] + q


# ------------------------------------------------------------------ maths

def test_maths():
    rng = random.Random(1)
    for _ in range(200):
        q = lib.quat_normalize([rng.gauss(0, 1) for _ in range(4)])
        R = lib.quat_to_rot(q)
        assert lib.is_rotation(R)
        q2 = lib.rot_to_quat(R)
        assert close(q2, q, 1e-9) or close(q2, [-v for v in q], 1e-9)
        T = lib.pose_to_mat(rand_pose(rng))
        I = lib.mat_mul(T, lib.se3_inverse(T))
        assert all(abs(I[i][j] - (1.0 if i == j else 0.0)) < 1e-9 for i in range(4) for j in range(4))
    # 180 degree rotations (Shepperd branches)
    for q in ([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]):
        assert close([abs(v) for v in lib.rot_to_quat(lib.quat_to_rot(q))], [abs(v) for v in q], 1e-12)
    # fmt_real: always a real for OpenCV's YAML reader
    for x, want in ((125, "125.0"), (1e-5, "1.0e-05"), (3.2793722442857696e-05, "3.2793722442857696e-05"), (-0.5, "-0.5"), (1e16, "1.0e+16")):
        assert lib.fmt_real(x) == want, (x, lib.fmt_real(x))
        assert re.fullmatch(r"-?\d+\.\d+(e[+-]\d+)?", lib.fmt_real(x))
    try:
        lib.fmt_real(float("nan"))
        raise AssertionError("nan accepted")
    except ValueError:
        pass
    assert lib.ts_9dec("1785948198.16237402") == "1785948198.162374020"
    assert lib.ts_9dec("1305031102.175304") == "1305031102.175304000"


# ------------------------------------------------------------------ calibration / settings

def parse_yaml_numbers(text):
    """Tiny reader for the generated file: 'key: value' scalars and the T_b_c1 data block."""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.]+):\s+([^#\s]+)\s*$", line)
        if m:
            vals[m.group(1)] = m.group(2)
    m = re.search(r"data:\s*\[([^\]]+)\]", text)
    mat = [float(v) for v in m.group(1).replace("\n", " ").split(",")] if m else None
    return vals, mat


# keys that src/Settings.cc @4452a3c requires (Camera.type, Camera1.fx.., Camera.width/height/fps/RGB, ORBextractor.*, Viewer.*)
MONO_KEYS = ["File.version", "Camera.type", "Camera1.fx", "Camera1.fy", "Camera1.cx", "Camera1.cy", "Camera1.k1", "Camera1.k2",
             "Camera1.p1", "Camera1.p2", "Camera.width", "Camera.height", "Camera.fps", "Camera.RGB",
             "ORBextractor.nFeatures", "ORBextractor.scaleFactor", "ORBextractor.nLevels", "ORBextractor.iniThFAST",
             "ORBextractor.minThFAST", "Viewer.KeyFrameSize", "Viewer.KeyFrameLineWidth", "Viewer.GraphLineWidth",
             "Viewer.PointSize", "Viewer.CameraSize", "Viewer.CameraLineWidth", "Viewer.ViewpointX", "Viewer.ViewpointY",
             "Viewer.ViewpointZ", "Viewer.ViewpointF"]
IMU_KEYS = ["IMU.NoiseGyro", "IMU.NoiseAcc", "IMU.GyroWalk", "IMU.AccWalk", "IMU.Frequency"]
INT_KEYS = {"Camera.width", "Camera.height", "Camera.fps", "Camera.RGB", "ORBextractor.nFeatures", "ORBextractor.nLevels",
            "ORBextractor.iniThFAST", "ORBextractor.minThFAST"}
STR_KEYS = {"File.version", "Camera.type"}


def test_settings():
    cal = lib.load_calibration()
    # intrinsics agree with the DPVO calibration of the same dataset (one source of truth)
    d = [float(v) for v in (REPO / "configs" / "dpvo" / "calib_ucu.txt").read_text().split()]
    c = cal["camera"]
    assert [c[k] for k in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2")] == d
    # T_b_c = inverse(T_cam_imu) reproduces Kalibr's printed T_ic (8 decimals) and is a proper rigid transform
    T = ms.t_b_c(cal)
    assert lib.is_rotation(lib.rot_of(T)) and T[3] == [0.0, 0.0, 0.0, 1.0]
    for i in range(4):
        assert close(T[i], cal["T_ic_kalibr_report_rounded"][i], 6e-9), (i, T[i])
    # ... and it is NOT the dataset's T_base_color (the naming trap): translation differs by far more than rounding
    assert abs(T[0][3] - cal["T_cam_imu"][0][3]) < 0.1 and abs(T[1][3] - cal["T_cam_imu"][1][3]) > 0.04
    assert abs(T[2][3] - cal["T_cam_imu"][2][3]) > 0.04

    mono, inert = ms.render(cal, "mono"), ms.render(cal, "mono_inertial")
    assert mono.startswith("%YAML:1.0\n") and inert.startswith("%YAML:1.0\n")
    for text, extra in ((mono, []), (inert, IMU_KEYS)):
        vals, mat = parse_yaml_numbers(text)
        for k in MONO_KEYS + extra:
            assert k in vals, f"missing key {k}"
            v = vals[k]
            if k in STR_KEYS:
                assert v.startswith('"') and v.endswith('"')
            elif k in INT_KEYS:
                assert re.fullmatch(r"-?\d+", v), (k, v)            # int keys: ints
            else:
                assert re.fullmatch(r"-?\d+\.\d+(e[+-]\d+)?", v), (k, v)   # float keys must be reals
        assert vals["Camera.type"] == '"PinHole"' and vals["File.version"] == '"1.0"'
    assert "IMU." not in mono and "T_b_c1" not in mono
    vals, mat = parse_yaml_numbers(inert)
    assert "IMU.T_b_c1: !!opencv-matrix" in inert and "   dt: f\n" in inert and "rows: 4" in inert and "cols: 4" in inert
    assert len(mat) == 16 and close(mat, [v for r in T for v in r], 1e-15)
    assert float(vals["IMU.Frequency"]) == 125.0
    im = cal["imu"]
    assert float(vals["IMU.NoiseGyro"]) == im["gyroscope_noise_density"] and float(vals["IMU.AccWalk"]) == im["accelerometer_random_walk"]
    assert float(vals["Camera1.fx"]) == c["fx"] and int(vals["Camera.fps"]) == 30 and int(vals["Camera.RGB"]) == 0

    # committed configs equal the generator output (no hand edits, no drift)
    for mode, path in ms.FILES.items():
        assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == ms.render(cal, mode), f"{path} is stale: run make_settings.py --write"
    assert ms.main(["--check"]) == 0


def test_against_dataset_repo_if_present():
    """ucu_calib.json vs the official calibration files (only where ~/src/ucu-slam-dataset exists; skipped otherwise)."""
    root = Path(os.environ.get("DATASET_REPO_DIR", str(Path.home() / "src" / "ucu-slam-dataset"))) / "calibration"
    chain = root / "raw" / "camchain-imucam.yaml"
    if not chain.exists():
        print("  (skipped: dataset repo not found at", root, ")")
        return
    cal = lib.load_calibration()
    txt = chain.read_text()
    rows = re.findall(r"^\s*- \[([^\]]+)\]\s*$", txt.split("T_cam_imu:")[1].split("cam_overlaps")[0], re.M)
    mat = [[float(v) for v in r.split(",")] for r in rows]
    assert mat == cal["T_cam_imu"], "T_cam_imu differs from camchain-imucam.yaml"
    ci_txt = (root / "raw" / "imu.yaml").read_text()
    for key, mine in (("accelerometer_noise_density", "accelerometer_noise_density"), ("accelerometer_random_walk", "accelerometer_random_walk"),
                      ("gyroscope_noise_density", "gyroscope_noise_density"), ("gyroscope_random_walk", "gyroscope_random_walk")):
        got = float(re.search(rf"^{key}:\s*(\S+)", ci_txt, re.M).group(1))
        assert got == cal["imu"][mine], key
    # the naming trap: T_base_color in calibration.yaml is numerically T_cam_imu
    full = (root / "calibration.yaml").read_text()
    data = re.search(r"T_base_color:.*?data:\s*\[([^\]]+)\]", full, re.S).group(1)
    flat = [float(v) for v in data.replace("\n", " ").split(",")]
    assert flat == [v for r in cal["T_cam_imu"] for v in r]
    print("  dataset repo cross-check passed:", root)


# ------------------------------------------------------------------ IMU conversion

def write(path, text):
    Path(path).write_text(text)


def test_convert_imu():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        t0 = 1000.0
        # frames 10.0 .. 10.9 s after t0 at 10 Hz; IMU 125 Hz from 0.1 s before the first frame to 0.1 s after the last
        write(d / "rgb.txt", "# h\n" + "".join(f"{t0 + 10 + i / 10:.9f} rgb/{i}.png\n" for i in range(10)))
        lines = ["#timestamp ax ay az gx gy gz"]
        n = int((0.9 + 0.2) * 125) + 1
        for i in range(n):
            t = t0 + 9.9 + i / 125
            lines.append(f"{t:.9f} 0.1 0.2 -9.8 0.01 0.02 0.03")
        good = len(lines) - 1
        lines.insert(5, lines[4])                                        # duplicate timestamp
        lines.insert(8, "1000.0 0.0 0.0 0.0 0.0 0.0 0.0")                # time goes back
        lines.insert(12, f"{t0 + 9.95:.9f} nan 0 0 0 0 0")               # non-finite
        write(d / "imu.txt", "\n".join(lines) + "\n")
        ok, st = ci.convert(d / "imu.txt", d / "out.txt", d / "rgb.txt")
        assert ok, st
        assert st["n"] == good and st["dropped"] == {"non_finite": 1, "non_increasing_time": 2}, st
        assert abs(st["rate_hz"] - 125.0) < 1e-3 and st["covers_frames"] and st["n_gaps_over_limit"] == 0
        out = (d / "out.txt").read_text().splitlines()
        assert out[0].startswith("#") and len(out) == good + 1
        first = out[1].split()
        assert len(first) == 7 and len(first[0].split(".")[1]) == 9
        assert [float(v) for v in first[1:]] == [0.1, 0.2, -9.8, 0.01, 0.02, 0.03]      # column order untouched (accel, gyro)
        ts = [float(l.split()[0]) for l in out[1:]]
        assert all(b > a for a, b in zip(ts, ts[1:]))

        # IMU that ends before the last frame: refused unless --allow-partial; nothing written
        write(d / "imu_short.txt", "\n".join(lines[:60]) + "\n")
        ok, st = ci.convert(d / "imu_short.txt", d / "short_out.txt", d / "rgb.txt")
        assert not ok and "cover" in st["error"] and not (d / "short_out.txt").exists()
        ok, st = ci.convert(d / "imu_short.txt", d / "short_out.txt", d / "rgb.txt", allow_partial=True)
        assert ok and (d / "short_out.txt").exists()
        # real-data shape (seq13): one stray first frame stamped 18 s before the IMU starts -> trimmed copy of rgb.txt, no failure
        stray = [f"{t0 - 8:.9f} rgb/stray.png"] + [f"{t0 + 10 + i / 10:.9f} rgb/{i}.png" for i in range(60)]
        write(d / "rgb_stray.txt", "# h\n" + "\n".join(stray) + "\n")
        n2 = int(7.0 * 125)
        write(d / "imu_long.txt", "\n".join(f"{t0 + 9.9 + i / 125:.9f} 0.1 0.2 -9.8 0.01 0.02 0.03" for i in range(n2)) + "\n")
        ok, st = ci.convert(d / "imu_long.txt", d / "o2.txt", d / "rgb_stray.txt", rgb_out=d / "w" / "rgb.txt")
        assert ok and st["trim"]["leading"] == 1 and st["trim"]["trailing"] == 0 and st["trim"]["frames_kept"] == 60, st
        kept = (d / "w" / "rgb.txt").read_text().splitlines()
        assert kept[0].startswith("#") and len(kept) == 61 and "stray" not in "".join(kept)
        tok, path = kept[1].split()
        assert tok == f"{t0 + 10:.9f}" and path == (d / "rgb" / "0.png").resolve().as_posix()      # timestamp text kept, path absolute
        # IMU starting 0.13 s after the first real frame (seq12): stray + 2 frames = 3 of 61 (4.9 %) -> refused unless the limit is raised
        write(d / "imu_late.txt", "\n".join(f"{t0 + 10.13 + i / 125:.9f} 0 0 -9.8 0 0 0" for i in range(int(6.5 * 125))) + "\n")
        ok, st = ci.convert(d / "imu_late.txt", d / "o3.txt", d / "rgb_stray.txt", rgb_out=d / "w3" / "rgb.txt")
        assert not ok and st["trim"]["leading"] == 3 and not (d / "w3").exists()
        ok, st = ci.convert(d / "imu_late.txt", d / "o3.txt", d / "rgb_stray.txt", rgb_out=d / "w3" / "rgb.txt", max_trim_frac=0.1)
        assert ok and st["trim"]["leading"] == 3 and len((d / "w3" / "rgb.txt").read_text().splitlines()) == 59
        # nothing inside the IMU span at all
        write(d / "imu_early.txt", "\n".join(f"{t0 + 1 + i / 125:.9f} 0 0 -9.8 0 0 0" for i in range(250)) + "\n")
        ok, st = ci.convert(d / "imu_early.txt", d / "o4.txt", d / "rgb_stray.txt", allow_partial=True, rgb_out=d / "w4" / "rgb.txt")
        assert not ok and "no frame" in st["error"] and not (d / "w4").exists()
        # gap statistics
        rows = [(i * 0.008 + (0.2 if i > 50 else 0), 0, 0, -9.8, 0, 0, 0) for i in range(100)]
        assert ci.stats_of(rows, {}, max_gap_s=0.05)["n_gaps_over_limit"] == 1
        # wrong column count is an error, not a silent skip
        write(d / "bad.txt", "1.0 2.0 3.0\n")
        try:
            ci.read_imu_txt(d / "bad.txt")
            raise AssertionError("bad row accepted")
        except ValueError:
            pass
        assert ci.main(["--imu", str(d / "imu.txt"), "--rgb", str(d / "rgb.txt"), "--out", str(d / "cli.txt")]) == 0
        assert ci.main(["--imu", str(d / "imu_short.txt"), "--rgb", str(d / "rgb.txt"), "--out", str(d / "cli2.txt")]) == 1


# ------------------------------------------------------------------ post-processing

def raw_line(t_rel, pose):
    """Exactly what System::SaveTrajectoryEuRoC prints: fixed, 1e9*t with 6 decimals, pose with 9."""
    return f"{1e9 * t_rel:.6f} " + " ".join(f"{v:.9f}" for v in pose)


def make_rgb(path, n=100, t0=1785948198.162374020, hz=30.0):
    toks = []
    for i in range(n):
        ns = int(round(t0 * 1e9)) + int(round(i * 1e9 / hz))
        toks.append(f"{ns // 10**9}.{ns % 10**9:09d}")
    write(path, "#timestamp path\n" + "".join(f"{t} rgb/{t.replace('.', '')}.png\n" for t in toks))
    return toks


def test_postprocess():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        toks = make_rgb(d / "rgb.txt", 100)
        ft = [float(t) for t in toks]
        t0 = ft[0]
        # poses exist for frames 0..39 and 60..99 (frames 40..59 lost); a duplicate time (lost-tracking corner case),
        # a time that matches no frame and a malformed line are also thrown in
        have = list(range(0, 40)) + list(range(60, 100))
        lines = []
        for k, i in enumerate(have):
            pose = [0.01 * i, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
            lines.append(raw_line(ft[i] - t0 + 1e-7 * (k % 3), pose))     # ~1e-7 s of float noise like the real file
        lines.insert(10, lines[9])                                           # duplicate frame time -> dropped
        lines.insert(11, raw_line(ft[3] - t0 + 0.016, [9, 9, 9, 0, 0, 0, 1]))  # half a frame off: unmatched
        lines.insert(12, "garbage line")
        write(d / "raw.txt", "\n".join(lines) + "\n")

        res = d / "results"
        rc = pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono", out_dir=res / "orbslam3/mono/ucu/seq01", run=1,
                        dataset="ucu", seq="seq01", config="configs/orbslam3/UCU_mono.yaml", commit="abc1234")
        assert rc == 0
        out = res / "orbslam3/mono/ucu/seq01"
        tl = (out / "traj_run1.txt").read_text().splitlines()
        assert len(tl) == len(have)                                          # one line per frame WITH a pose; none invented
        got_t = [l.split()[0] for l in tl]
        assert got_t == [toks[i] for i in have]                              # original rgb.txt timestamps, 9 decimals
        assert all(len(l.split()) == 8 for l in tl)
        assert abs(float(tl[5].split()[1]) - 0.05) < 1e-9
        meta = (out / "meta.yaml").read_text()
        assert "method: orbslam3\nmode: mono\n" in meta and "tracking_lost: false" in meta and "commit: abc1234" in meta
        cov = float(re.search(r"coverage: (\S+)", meta).group(1))
        assert abs(cov - 1.0) < 0.011                                        # (t_last - t_first) of the trajectory spans the whole run
        assert "unmatched 1" in meta and "duplicate-frame 1" in meta and "malformed 1" in meta and "NOT interpolated" in meta
        # never overwrite
        assert pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono", out_dir=out, run=1, dataset="ucu", seq="seq01",
                          config="c", commit="x") == 1
        # run 2 goes next to run 1
        assert pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono", out_dir=out, run=2, dataset="ucu", seq="seq01",
                          config="c", commit="x") == 0 and (out / "traj_run2.txt").exists()
        # --max-frames truncates the frame list: poses beyond it do not match
        rc = pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono", out_dir=out, run=3, dataset="ucu", seq="seq01",
                        config="c", commit="x", max_frames=30)
        assert rc == 0 and len((out / "traj_run3.txt").read_text().splitlines()) == 30

        # tracking lost / never initialised: empty or missing raw file -> exit 4, meta says so, NO trajectory file
        for name, text in (("empty.txt", ""), ("missing.txt", None), ("one.txt", raw_line(0.0, [0, 0, 0, 0, 0, 0, 1]) + "\n")):
            if text is not None:
                write(d / name, text)
            o = res / "lost" / name
            rc = pp.process(raw=d / name, rgb=d / "rgb.txt", mode="mono", out_dir=o, run=1, dataset="ucu", seq="seq01", config="c", commit="x")
            assert rc == pp.EXIT_LOST and not (o / "traj_run1.txt").exists()
            m = (o / "meta.yaml").read_text()
            assert "tracking_lost: true" in m and "coverage: 0.00" in m
        o = res / "failed"
        assert pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono", out_dir=o, run=1, dataset="ucu", seq="seq01", config="c",
                          commit="x", driver_failed="exit 4") == pp.EXIT_LOST and not (o / "traj_run1.txt").exists()

        # CLI wiring (results-root/method/mode/dataset/seq)
        assert pp.main(["--raw", str(d / "raw.txt"), "--rgb", str(d / "rgb.txt"), "--mode", "mono", "--results-root", str(res / "cli"),
                        "--dataset", "ucu", "--seq", "seq02", "--run", "1", "--commit", "x"]) == 0
        assert (res / "cli/orbslam3/mono/ucu/seq02/traj_run1.txt").exists()


def test_inertial_frame_conversion():
    """mono_inertial raw = body pose; output must be the CAMERA pose T_w_c = T_w_b * T_b_c with T_b_c = inverse(T_cam_imu)."""
    cal = lib.load_calibration()
    T_bc = lib.se3_inverse(cal["T_cam_imu"])
    rng = random.Random(7)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        toks = make_rgb(d / "rgb.txt", 20)
        ft = [float(t) for t in toks]
        body = [rand_pose(rng) for _ in range(20)]
        write(d / "raw.txt", "\n".join(raw_line(ft[i] - ft[0], body[i]) for i in range(20)) + "\n")
        out = d / "r/orbslam3/mono_inertial/ucu/seq01"
        assert pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono_inertial", out_dir=out, run=1, dataset="ucu", seq="seq01",
                          config="c", commit="x") == 0
        for i, line in enumerate((out / "traj_run1.txt").read_text().splitlines()):
            p = [float(v) for v in line.split()[1:]]
            want = lib.mat_mul(lib.pose_to_mat(body[i]), T_bc)
            Tg = lib.pose_to_mat(p)
            assert all(abs(Tg[r][c] - want[r][c]) < 5e-8 for r in range(4) for c in range(4)), i
        assert "camera pose from body pose" in (out / "meta.yaml").read_text()
        # the body-frame option (EuRoC: ground truth is the body pose) keeps the pose untouched
        out2 = d / "r2"
        assert pp.process(raw=d / "raw.txt", rgb=d / "rgb.txt", mode="mono_inertial", out_dir=out2, run=1, dataset="euroc", seq="MH_01",
                          config="c", commit="x", body_frame=True) == 0
        line = (out2 / "traj_run1.txt").read_text().splitlines()[3].split()
        assert close([float(v) for v in line[1:4]], body[3][:3], 5e-9)
        # camera centre sits where T_ic says: with an identity body pose the camera is at t_ic
        cam = pp.body_to_camera([0, 0, 0, 0, 0, 0, 1], T_bc)
        assert close(cam[:3], lib.trans_of(T_bc), 1e-12)
        assert abs(math.sqrt(sum(v * v for v in lib.trans_of(T_bc))) - 0.0710) < 0.001      # ~7 cm lever arm (printed Kalibr T_ic)


# ------------------------------------------------------------------ EuRoC adapter

def test_euroc_adapter():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        mav = d / "MH_01" / "mav0"
        (mav / "cam0" / "data").mkdir(parents=True)
        (mav / "imu0").mkdir()
        (mav / "state_groundtruth_estimate0").mkdir()
        write(mav / "cam0" / "data.csv", "#timestamp [ns],filename\n1403636579763555584,1403636579763555584.png\n1403636579813555456,1403636579813555456.png\n")
        write(mav / "imu0" / "data.csv", "#timestamp [ns],w_x,w_y,w_z,a_x,a_y,a_z\n1403636579758555392,-0.1,0.2,0.3,8.0,-1.0,-3.0\n")
        write(mav / "state_groundtruth_estimate0" / "data.csv", "#ts,px,py,pz,qw,qx,qy,qz,vx\n1403636580013555456,4.0,-1.8,0.8,0.5,0.1,0.2,0.3,0\n")
        n = e2t.convert(d / "MH_01", d / "out")
        assert n == (2, 1, 1)
        assert (d / "out" / "imu.txt").read_text().splitlines()[1] == "1403636579.758555392 8.0 -1.0 -3.0 -0.1 0.2 0.3"   # accel first, then gyro
        assert (d / "out" / "groundtruth.txt").read_text().splitlines()[1] == "1403636580.013555456 4.0 -1.8 0.8 0.1 0.2 0.3 0.5"   # qw last
        first = (d / "out" / "rgb.txt").read_text().splitlines()[1].split()
        assert first[0] == "1403636579.763555584" and first[1].endswith("cam0/data/1403636579763555584.png") and Path(first[1]).is_absolute()


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"OK: {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
