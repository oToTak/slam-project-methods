# DSO (mono, no photometric calibration): notes for integration (T5)

Status: PREPARED, NOT BUILT, NOT RUN. Nothing here was compiled or executed (only `py_compile`, `bash -n` and stub tests of the python helpers). The C++ driver is written against the sources of the pinned commit and has never seen a compiler.

## 1. Registry line (scripts/queue/methods.conf)

```
dso | mono | scripts/dso/run.sh | cpu | 3
```

## 2. Hook line (setup_all_really_all.sh, right after the setup_all.sh call)

```
bash "$here/dso.sh" || todo "dso: рецепт не вдався (метод буде пропущено, доки 'bash scripts/setup/dso.sh --check' не пройде)"
```

(`$here` = `scripts/setup`; the recipe is `scripts/setup/dso.sh`.)

## 3. apt (Ubuntu 22.04): the ONLY step that needs sudo, run once

```
sudo apt-get install -y build-essential cmake git pkg-config libsuitesparse-dev libeigen3-dev libboost-system-dev libboost-thread-dev libopencv-dev
```

Pangolin and libzip are NOT needed: our own driver (`scripts/dso/driver/dso_headless.cc`) replaces `dso_dataset`, and the library is configured with `-DCMAKE_DISABLE_FIND_PACKAGE_Pangolin=ON -DCMAKE_DISABLE_FIND_PACKAGE_LibZip=ON`. Everything else goes into `$HOME` (`~/src/dso`, `~/opt/dso`); nothing into `/usr/local`.

## 4. Build steps (exact commands)

```
bash scripts/dso/run_tests.sh                 # bash -n + python stub tests, seconds, CPU
bash scripts/setup/dso.sh --apt-only          # (or the sudo command above); skips itself if all packages are present
bash scripts/setup/dso.sh --no-apt            # sources @7b0c99f, OpenCV4 + -std/-march patches, libdso.a, dso_headless
bash scripts/setup/dso.sh --check             # exit 0 = binary + lib + pinned sources + no missing .so
# sanity before the queue (TUM fr1_xyz, camera from configs/dso/camera_tum_fr1.txt):
bash scripts/dso/run.sh tum rgbd_dataset_freiburg1_xyz --run 1       # NO_EVAL=1 to skip eval
# real run (what the queue will call):
bash scripts/dso/run.sh ucu seq01 --run 1
```

Output: `results/dso/mono/ucu/seqNN/traj_run<R>.txt` (TUM, one line per frame that has a pose) + `meta.yaml`. Work files: `$DATA_ROOT/dso_work/mono/<ds>/<seq>/run<R>/`, log `$DATA_ROOT/logs/dso_<seq>_run<R>_<ts>.log`.

## 5. What the output is (honest)

- Mode `mono`: NO photometric calibration (DSO "mode 1": no gamma/vignette, exposure unknown). The camera is rolling-shutter with auto-exposure, which DSO assumes away; expect worse results than the paper. This is a real limitation, written in meta notes.
- Stock `result.txt` of upstream DSO holds ONLY the marginalised keyframes (`setting_onlyLogKFPoses` defaults to true). Our driver instead keeps every frame: non-keyframes are re-expressed through their tracking reference (`trackingRef->camToWorld * camToTrackingRef`) after the final bundle adjustment, written into `poses_refined.txt`. The raw tracked poses (`poses_tracked.txt`), the keyframe poses and the stock-format file are kept next to it.
- Frames without a pose (before initialisation, frames dropped by a restart, the frame where tracking was lost, non-finite poses) are NOT written and NOT interpolated. Coverage = time-span and frame share, in meta. If fewer than 2 poses exist: exit 4 and `tracking_lost: true` in meta.
- Camera: `configs/dso/camera_ucu.txt` (RadTan, from `configs/dpvo/calib_ucu.txt`), `crop` rectification, 640x480 (the crop reduces the FOV). Generate/check with `python scripts/dso/dso_tools.py camera --dataset ucu --check`.
- Scale is arbitrary (monocular): `eval.py` must align with Sim(3), as for the other mono methods.

## 6. TODO(verify), in the order they will probably bite

1. Uncompiled C++ driver: `dso_headless.cc` + `driver/CMakeLists.txt`. First `--no-apt` run is the compile test; expect small fixes (API names of the pinned commit).
2. GCC 11 + Eigen 3.4 with `-std=c++0x`: DSO is old code. Fallback knobs (env, `scripts/dso/env_dso.sh`): `DSO_CXX_STD=c++14`.
3. `-march=native`: DSO needs matching alignment between lib and driver (both are built with the same flag). If segfaults/alignment asserts: `DSO_MARCH=x86-64-v2` and re-run `dso.sh --no-apt` with `OV_FORCE`-like stamps cleared (`rm -r ~/opt/dso/.stamps`).
4. `-fno-access-control` in the driver is a deliberate hack to read the private `allFrameHistory` / `allKeyFramesHistory`; verify it compiles and the poses are sane (compare `poses_tracked.txt` vs `poses_refined.txt`).
5. Mono result quality on UCU: unknown. May lose tracking early (rolling shutter, auto-exposure, fast turns). `crop` FOV loss. Report as is, do not tune per sequence.
6. Run-to-run non-determinism: DSO is not deterministic in the multithreaded mode; the driver uses `linearizeOperation` (no sleeps, single pipeline order), which may make the 3 runs nearly identical. If identical, lower `runs` to 1 in the registry line (decision for the lead).
7. TUM fr1 sanity: `k3` is dropped in `camera_tum_fr1.txt` (RadTan has 4 coefficients); tiny distortion error expected. Only a sanity check, not a result.
8. `POSE_TOL=0.98` in `run_queue.py`: a run that loses tracking or never initialises has coverage below 0.98 and will be marked `.bad_run`. For DSO (and OpenVINS) this is likely; the lead must decide whether a lower coverage is acceptable for these methods (or add a per-method tolerance). Coverage is reported honestly in meta either way.
9. `START_BUSY_PATTERN` in the queue (process-name check that decides "machine is busy"): add `|dso_headless`.
10. `.gitattributes` files (`scripts/dso/.gitattributes`, `configs/dso/.gitattributes`) force LF for the shell/python/camera files; check they survive the merge (CRLF breaks `bash` and the camera parser on Linux).
11. Reset rule in the driver (`ii < 250` and init failed: restart): verify on a sequence whose start is static; the dropped frames are counted in `DSO_STATS dropped=`.
