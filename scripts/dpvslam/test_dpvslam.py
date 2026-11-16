"""CPU-only stub test of the dpvslam wrapper (run_dpvo.py --method dpvslam): option/notes/path building, no torch, no GPU.
Run: python -I scripts/dpvslam/test_dpvslam.py   (scripts/setup/run_dpvslam_tests.sh)"""
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "dpvo"))
import run_dpvo as r


def fake_dataset(d, n=40):
    ts = [1.7e9 + i / 30 for i in range(n)]
    (d / "rgb.txt").write_text("# h\n" + "".join(f"{t:.6f} rgb/{t:.6f}.png\n" for t in ts))
    return ts


def fake_run_slam(calls, ts):
    def run_slam(frames, calib, dataset, dpvo_dir, network=None, stride=1, cfg_opts=None):
        calls.append(list(cfg_opts or []))
        poses = [(0.01 * i, 0, 0, 0, 0, 0, 1) for i in range(len(frames))]
        stats = {"wall_s": 10.0, "gpu": "stub", "vram_total_peak_mib": 1.0, "vram_total_base_mib": 0.0,
                 "torch_max_allocated_mib": 1.0, "torch_max_reserved_mib": 1.0, "ram_peak_mib": 1.0,
                 "n_frames": len(frames)}
        if "LOOP_CLOSURE" in (cfg_opts or []):
            stats.update(lc_edge_batches=2, lc_edges=7, global_ba_calls=5, global_ba_s=1.5, n_keyframes=11, ba_failures=0,
                         lc_cfg="BACKEND_THRESH=64.0 MAX_EDGE_AGE=1000 GLOBAL_OPT_FREQ=15")
        return poses, ts[:len(frames)], stats
    return run_slam


def test_all():
    # options: DPVO has none (default path unchanged), DPV-SLAM = proximity loop closure only
    assert r.effective_opts("dpvo") == []
    assert r.effective_opts("dpvslam") == ["LOOP_CLOSURE", "True"]
    assert r.effective_opts("dpvslam", ["BACKEND_THRESH", "32"]) == ["LOOP_CLOSURE", "True", "BACKEND_THRESH", "32"]
    assert "CLASSIC_LOOP_CLOSURE" not in r.effective_opts("dpvslam")
    # notes: the dpvo text is exactly the pre-existing one
    assert r.base_notes("dpvo", [], 1, 300) == "DPVO 0ac95b6, config/default.yaml, stride=1, 300 frames."
    n = r.base_notes("dpvslam", [], 1, 300)
    assert "proximity loop closure" in n and "LOOP_CLOSURE=True" in n and "NOT used" in n and n.endswith("300 frames.")
    assert "extra opts BACKEND_THRESH 32" in r.base_notes("dpvslam", ["BACKEND_THRESH", "32"], 1, 3)
    assert r.lc_note({"wall_s": 1}) == ""

    # end to end of main() with the GPU part stubbed: result folder, run number, meta
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data = tmp / "ucu_tum" / "seq01"
        data.mkdir(parents=True)
        ts = fake_dataset(data)
        res = tmp / "results"
        for method in ("dpvslam", "dpvo"):
            calls = []
            r.run_slam = fake_run_slam(calls, ts)
            argv = ["run_dpvo.py", "--dataset", "ucu", "--seq", "seq01", "--data-dir", str(data),
                    "--calib", "configs/dpvo/calib_ucu.txt", "--results-root", str(res), "--run", "2"]
            if method == "dpvslam":
                argv[1:1] = ["--method", "dpvslam"]
            sys.argv = argv
            r.main()
            out = res / method / "mono" / "ucu" / "seq01"
            assert (out / "traj_run2.txt").read_text().count("\n") == len(ts)
            meta = (out / "meta.yaml").read_text()
            assert f"method: {method}\n" in meta and "mode: mono\n" in meta and "run: 2\n" in meta
            if method == "dpvslam":
                assert calls == [["LOOP_CLOSURE", "True"]]
                assert "proximity loop closure" in meta and "11 keyframes kept" in meta and "7 edges" in meta and "BA failures swallowed by DPVO: 0." in meta
            else:
                assert calls == [[]] and "Loop closure" not in meta and "DPV-SLAM" not in meta
        try:                                    # the worker's run number is never overwritten
            sys.argv = ["run_dpvo.py", "--method", "dpvslam", "--dataset", "ucu", "--seq", "seq01", "--data-dir",
                        str(data), "--calib", "configs/dpvo/calib_ucu.txt", "--results-root", str(res), "--run", "2"]
            r.main()
            raise AssertionError("existing traj_run2.txt was overwritten")
        except SystemExit:
            pass

    # registry line + run script + recipe exist
    spec = importlib.util.spec_from_file_location("run_queue", REPO / "scripts" / "queue" / "run_queue.py")
    q = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q)
    m = q.load_registry(REPO / "scripts" / "queue" / "methods.conf")["dpvslam:mono"]
    assert m.gpu and m.runs is None and (REPO / m.script).is_file()
    assert (REPO / "scripts" / "setup" / "dpvslam.sh").is_file()
    print("OK")


if __name__ == "__main__":
    test_all()
