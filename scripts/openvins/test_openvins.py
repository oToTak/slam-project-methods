#!/usr/bin/env python3
"""Stub tests of the pure-python parts of the OpenVINS integration (no OpenVINS, no ROS, no data, no GPU, stdlib only).

Run: python3 scripts/openvins/test_openvins.py        (seconds). Covers: config generation (keys the upstream parsers need, the camera-IMU
transform = inverse of the dataset's T_cam_imu, noise, committed files in sync), pose composition T_G_C = T_G_I * T_I_C, driver-output ->
TUM conversion with honest gaps, no-overwrite rule.
The C++ driver, the recipe and run.sh are checked by `bash -n` only; their real test is the sanity run in PREP_NOTES.md.
"""
import math
import random
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_config as mc  # noqa: E402
import openvins_tools as ot  # noqa: E402

lib = ot.lib
REPO = ot.REPO_ROOT

# keys that the pinned parsers (VioManagerOptions.h, InertialInitializerOptions.h, StateOptions.h @d0075dd) read with parse_config (required)
ESTIMATOR_KEYS = ["verbosity", "use_fej", "integration", "use_stereo", "max_cameras", "calib_cam_extrinsics", "calib_cam_intrinsics",
                  "calib_cam_timeoffset", "calib_imu_intrinsics", "calib_imu_g_sensitivity", "max_clones", "max_slam", "max_slam_in_update",
                  "max_msckf_in_update", "dt_slam_delay", "gravity_mag", "feat_rep_msckf", "feat_rep_slam", "feat_rep_aruco", "try_zupt",
                  "zupt_chi2_multipler", "zupt_max_velocity", "zupt_noise_multiplier", "zupt_max_disparity", "zupt_only_at_beginning",
                  "init_window_time", "init_imu_thresh", "init_max_disparity", "init_max_features", "init_dyn_use", "init_dyn_mle_opt_calib",
                  "init_dyn_mle_max_iter", "init_dyn_mle_max_time", "init_dyn_mle_max_threads", "init_dyn_num_pose", "init_dyn_min_deg",
                  "init_dyn_inflation_ori", "init_dyn_inflation_vel", "init_dyn_inflation_bg", "init_dyn_inflation_ba", "init_dyn_min_rec_cond",
                  "init_dyn_bias_g", "init_dyn_bias_a", "record_timing_information", "record_timing_filepath", "use_klt", "num_pts",
                  "fast_threshold", "grid_x", "grid_y", "min_px_dist", "knn_ratio", "track_frequency", "downsample_cameras", "num_opencv_threads",
                  "histogram_method", "use_aruco", "num_aruco", "downsize_aruco", "up_msckf_sigma_px", "up_msckf_chi2_multipler",
                  "up_slam_sigma_px", "up_slam_chi2_multipler", "up_aruco_sigma_px", "up_aruco_chi2_multipler", "use_mask",
                  "relative_config_imu", "relative_config_imucam"]
IMU_KEYS = ["T_i_b", "accelerometer_noise_density", "accelerometer_random_walk", "gyroscope_noise_density", "gyroscope_random_walk", "model",
            "Tw", "R_IMUtoGYRO", "Ta", "R_IMUtoACC", "Tg"]
CAM_KEYS = ["T_imu_cam", "camera_model", "distortion_coeffs", "distortion_model", "intrinsics", "resolution", "timeshift_cam_imu"]


def close(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def write(p, text):
    Path(p).write_text(text, encoding="utf-8", newline="\n")


def top_keys(text):
    return {m.group(1) for m in re.finditer(r"^([A-Za-z0-9_]+):", text, re.M)}


def scalar(text, key):
    m = re.search(rf"^\s*{key}:\s*([^#\n]+)", text, re.M)
    return m.group(1).strip()


# ------------------------------------------------------------------ config

def test_config_keys_and_values():
    cal = lib.load_calibration()
    files = mc.render_all(cal)
    est, imu, cam = files["estimator_config.yaml"], files["kalibr_imu_chain.yaml"], files["kalibr_imucam_chain.yaml"]
    for t in (est, imu, cam):
        assert t.startswith("%YAML:1.0")
    missing = [k for k in ESTIMATOR_KEYS if k not in top_keys(est)]
    assert not missing, missing
    for k in IMU_KEYS:
        assert re.search(rf"^  {k}:", imu, re.M), k
    for k in CAM_KEYS:
        assert re.search(rf"^  {k}:", cam, re.M), k
    assert scalar(est, "max_cameras") == "1" and scalar(est, "use_stereo") == "false"
    assert scalar(est, "num_opencv_threads") == "0" and scalar(est, "use_aruco") == "false" and scalar(est, "use_mask") == "false"
    assert scalar(est, "relative_config_imu") == '"kalibr_imu_chain.yaml"' and scalar(est, "relative_config_imucam") == '"kalibr_imucam_chain.yaml"'
    assert scalar(est, "init_dyn_use") == "false" and scalar(est, "calib_cam_extrinsics") == "true"

    # IMU noise = dataset calibration, as is
    im = cal["imu"]
    for key, src in (("accelerometer_noise_density", "accelerometer_noise_density"), ("accelerometer_random_walk", "accelerometer_random_walk"),
                     ("gyroscope_noise_density", "gyroscope_noise_density"), ("gyroscope_random_walk", "gyroscope_random_walk")):
        assert float(scalar(imu, key)) == im[src], key
    assert float(scalar(imu, "update_rate")) == im["rate_hz"] and scalar(imu, "model") == '"kalibr"'

    # camera: intrinsics, distortion, resolution from the same calibration file (and the DPVO calibration of the dataset)
    c = cal["camera"]
    intr = [float(v) for v in re.search(r"intrinsics: \[([^\]]+)\]", cam).group(1).split(",")]
    dist = [float(v) for v in re.search(r"distortion_coeffs: \[([^\]]+)\]", cam).group(1).split(",")]
    assert intr == [c["fx"], c["fy"], c["cx"], c["cy"]] and dist == [c["k1"], c["k2"], c["p1"], c["p2"]]
    assert intr + dist == [float(v) for v in (REPO / "configs" / "dpvo" / "calib_ucu.txt").read_text().split()]
    assert re.search(r"resolution: \[640, 480\]", cam) and scalar(cam, "distortion_model") == "radtan" and scalar(cam, "camera_model") == "pinhole"
    assert float(scalar(cam, "timeshift_cam_imu")) == 0.0       # the exporter already applied the Kalibr shift

    # T_imu_cam = camera -> IMU = inverse(Kalibr T_cam_imu) = printed Kalibr T_ic, and NOT the dataset's T_base_color taken at face value
    rows = [[float(v) for v in re.findall(r"-?\d+\.\d+(?:e[+-]\d+)?|-?\d+", line)] for line in
            re.search(r"T_imu_cam:[^\n]*\n((?:    - \[[^\n]*\n){4})", cam).group(1).splitlines()]
    assert len(rows) == 4 and all(len(r) == 4 for r in rows)
    for i in range(4):
        assert close(rows[i], cal["T_ic_kalibr_report_rounded"][i], 6e-9), (i, rows[i])
    assert lib.is_rotation(lib.rot_of(rows)) and rows[3] == [0.0, 0.0, 0.0, 1.0]
    prod = lib.mat_mul(rows, cal["T_cam_imu"])                   # T_ic * T_ci = I
    assert all(abs(prod[i][j] - (1.0 if i == j else 0.0)) < 1e-12 for i in range(4) for j in range(4))
    assert abs(rows[1][3] - cal["T_cam_imu"][1][3]) > 0.04       # the inversion matters (translation differs by > 4 cm)


def test_variants_and_sync():
    cal = lib.load_calibration()
    dyn = mc.render_estimator(init="dynamic", online_calib=False)
    assert scalar(dyn, "init_dyn_use") == "true" and scalar(dyn, "calib_cam_extrinsics") == "false" and scalar(dyn, "calib_cam_timeoffset") == "false"
    assert mc.render_estimator() != dyn
    try:
        mc.render_estimator(init="bogus")
        raise AssertionError("bad init accepted")
    except ValueError:
        pass
    for name, text in mc.render_all(cal).items():                # committed files equal the generator output (no hand edits)
        p = mc.CONFIG_DIR / name
        assert p.exists(), p
        assert p.read_text(encoding="utf-8").replace("\r\n", "\n") == text, f"{p} is out of date"
    with tempfile.TemporaryDirectory() as d:
        assert mc.main(["--out", d, "--init", "dynamic", "--no-online-calib"]) == 0
        assert (Path(d) / "estimator_config.yaml").read_text() == dyn
        try:
            mc.main(["--write", "--init", "dynamic"])
            raise AssertionError("--write with a variant accepted")
        except SystemExit:
            pass


# ------------------------------------------------------------------ poses

def rand_pose(rng):
    q = lib.quat_normalize([rng.gauss(0, 1) for _ in range(4)])
    return [rng.uniform(-5, 5) for _ in range(3)] + q


def test_compose():
    rng = random.Random(7)
    cal = lib.load_calibration()
    T_ic = lib.se3_inverse(cal["T_cam_imu"])
    t_i_c = lib.rot_to_quat(lib.rot_of(T_ic)) + lib.trans_of(T_ic)          # [qx qy qz qw px py pz] as the driver writes it
    # identity IMU pose -> camera pose is exactly T_ic
    cam = ot.compose_camera_pose([0, 0, 0, 0, 0, 0, 1], t_i_c)
    assert close(cam[:3], lib.trans_of(T_ic), 1e-12)
    assert close(lib.quat_to_rot(cam[3:7])[0], lib.rot_of(T_ic)[0], 1e-9)
    for _ in range(100):
        imu = rand_pose(rng)
        cam = ot.compose_camera_pose(imu, t_i_c)
        want = lib.mat_mul(lib.pose_to_mat(imu), T_ic)
        got = lib.pose_to_mat(cam)
        assert all(abs(got[i][j] - want[i][j]) < 1e-9 for i in range(4) for j in range(4))
        # going back: T_G_I = T_G_C * inverse(T_I_C)
        back = lib.mat_mul(got, lib.se3_inverse(T_ic))
        ref = lib.pose_to_mat(imu)
        assert all(abs(back[i][j] - ref[i][j]) < 1e-9 for i in range(4) for j in range(4))
    # lever arm ~7 cm between IMU and camera centre (printed Kalibr T_ic)
    assert abs(math.sqrt(sum(v * v for v in lib.trans_of(T_ic))) - 0.0710) < 0.001


def line15(idx, imu_pose, t_i_c):
    return f"{idx} " + " ".join(repr(float(v)) for v in imu_pose + t_i_c) + "\n"


def test_postprocess():
    cal = lib.load_calibration()
    T_ic = lib.se3_inverse(cal["T_cam_imu"])
    t_i_c = lib.rot_to_quat(lib.rot_of(T_ic)) + lib.trans_of(T_ic)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n = 12
        write(d / "rgb.txt", "# h\n" + "".join(f"{2000 + i / 30:.9f} rgb/{i}.png\n" for i in range(n)))
        # filter initialises after 4 frames; frames 4..9 get poses (index 6 appears twice, 11 is after the last IMU sample); junk lines
        rows = [line15(i, [0.1 * i, 0.0, 0.0, 0, 0, 0, 1], t_i_c) for i in range(4, 10)]
        rows += [line15(6, [9, 9, 9, 0, 0, 0, 1], t_i_c), "# comment\n", "7 1 2 3\n", "bad line here\n",
                 line15(99, [0, 0, 0, 0, 0, 0, 1], t_i_c), line15(10, [float("nan"), 0, 0, 0, 0, 0, 1], t_i_c)]
        write(d / "raw.txt", "".join(rows))
        out = d / "res"
        assert ot.process(poses=d / "raw.txt", rgb=d / "rgb.txt", out_dir=out, run=1, dataset="ucu", seq="seq01", config="cfg", commit="abc") == 0
        got = [l.split() for l in (out / "traj_run1.txt").read_text().splitlines()]
        assert [r[0] for r in got] == [f"{2000 + i / 30:.9f}" for i in range(4, 10)]            # exact rgb.txt stamps; frames 0-3 and 10, 11 absent
        assert all(len(r) == 8 for r in got)
        # camera pose = IMU pose * T_ic: for an identity IMU rotation the camera x is p_IinG.x + t_ic.x
        assert abs(float(got[2][1]) - (0.1 * 6 + lib.trans_of(T_ic)[0])) < 1e-8                  # first pose of the duplicated index 6 wins
        meta = (out / "meta.yaml").read_text()
        for k in ("method: openvins", "mode: mono_inertial", "dataset: ucu", "seq: seq01", "run: 1", "commit: abc", "tracking_lost: false"):
            assert k in meta, k
        assert "6 of 12 frames" in meta and "NOT interpolated" in meta
        assert abs(float(re.search(r"coverage: ([0-9.]+)", meta).group(1)) - 5 / 11) < 0.006
        assert ot.process(poses=d / "raw.txt", rgb=d / "rgb.txt", out_dir=out, run=1, dataset="ucu", seq="seq01", config="c", commit="x") == 1   # no overwrite
        # body frame (EuRoC sanity): the IMU pose itself
        assert ot.process(poses=d / "raw.txt", rgb=d / "rgb.txt", out_dir=out, run=2, dataset="euroc", seq="MH_01", config="c", commit="x",
                          body_frame=True) == 0
        b = [l.split() for l in (out / "traj_run2.txt").read_text().splitlines()]
        assert abs(float(b[2][1]) - 0.6) < 1e-9 and abs(float(b[2][2])) < 1e-12
        # the filter never initialised / driver failed -> exit 4, no trajectory, meta says lost
        write(d / "empty.txt", "")
        assert ot.process(poses=d / "empty.txt", rgb=d / "rgb.txt", out_dir=out, run=3, dataset="ucu", seq="seq01", config="c", commit="x") == 4
        assert not (out / "traj_run3.txt").exists() and "tracking_lost: true" in (out / "meta.yaml").read_text()
        assert ot.process(poses=d / "raw.txt", rgb=d / "rgb.txt", out_dir=out, run=4, dataset="ucu", seq="seq01", config="c", commit="x",
                          driver_failed="boom") == 4
        assert ot.process(poses=d / "missing.txt", rgb=d / "rgb.txt", out_dir=out, run=5, dataset="ucu", seq="seq01", config="c", commit="x") == 4
        # --max-frames: only frames inside the prefix keep their pose
        assert ot.process(poses=d / "raw.txt", rgb=d / "rgb.txt", out_dir=out, run=6, dataset="ucu", seq="seq01", config="c", commit="x",
                          max_frames=7) == 0
        assert len((out / "traj_run6.txt").read_text().splitlines()) == 3                          # frames 4, 5, 6


def test_cli():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        write(d / "rgb.txt", "".join(f"{700 + i / 30:.9f} rgb/{i}.png\n" for i in range(5)))
        write(d / "raw.txt", line15(2, [1, 2, 3, 0, 0, 0, 1], [0, 0, 0, 1, 0, 0, 0]) + line15(3, [1, 2, 4, 0, 0, 0, 1], [0, 0, 0, 1, 0, 0, 0]))
        rc = ot.main(["post", "--poses", str(d / "raw.txt"), "--rgb", str(d / "rgb.txt"), "--results-root", str(d / "R"), "--dataset", "ucu",
                      "--seq", "seq01", "--run", "1", "--config", "c", "--commit", "x"])
        assert rc == 0 and (d / "R" / "openvins" / "mono_inertial" / "ucu" / "seq01" / "traj_run1.txt").exists()


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"OK: {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
