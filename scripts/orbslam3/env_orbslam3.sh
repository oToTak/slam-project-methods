# Environment of the ORB-SLAM3 method. Source it AFTER scripts/setup/env.sh (the shared file is not touched by this method):
#   source scripts/setup/env.sh; source scripts/orbslam3/env_orbslam3.sh
# Everything is overridable from the caller's environment.
#
# Pins (verified on GitHub 2026-11-16):
#   ORB-SLAM3 master HEAD = 4452a3c4ab75b1cde34e5505a36ec3f9edcdc4c4 ("Update Dependencies.md", 2022-02-10; the last commit
#   after the v1.0 release 0df83dd of 2021-12-22). Repo: https://github.com/UZ-SLAMLab/ORB_SLAM3
#   Pangolin tag v0.9.1 = commit 7364b59 (2024-01-18). Repo: https://github.com/stevenlovegrove/Pangolin
export ORB3_COMMIT="${ORB3_COMMIT:-4452a3c4ab75b1cde34e5505a36ec3f9edcdc4c4}"
export ORB3_DIR="${ORB3_DIR:-$HOME/src/ORB_SLAM3}"                 # ORB-SLAM3 sources + build tree
export ORB3_PREFIX="${ORB3_PREFIX:-$HOME/opt/orbslam3}"            # Pangolin install + our driver binary (no sudo needed)
export ORB3_PANGOLIN_TAG="${ORB3_PANGOLIN_TAG:-v0.9.1}"
export ORB3_PANGOLIN_SHORT="${ORB3_PANGOLIN_SHORT:-7364b59}"       # expected `git rev-parse --short=7` of that tag ("" = do not check)
export ORB3_PANGOLIN_SRC="${ORB3_PANGOLIN_SRC:-$HOME/src/Pangolin}"
export ORB3_PANGOLIN_PREFIX="${ORB3_PANGOLIN_PREFIX:-$ORB3_PREFIX/pangolin}"
# ORB-SLAM3 asks for -std=c++11, Pangolin's headers (sigslot) need >= C++14: the recipe rewrites the flag. Fallback: c++17.
export ORB3_CXX_STD="${ORB3_CXX_STD:-c++14}"
export ORB3_BIN="${ORB3_BIN:-$ORB3_PREFIX/bin/orbslam3_tum}"
export ORB3_VOCAB="${ORB3_VOCAB:-$ORB3_DIR/Vocabulary/ORBvoc.txt}"
# Frame pacing of the driver: 0 = no sleeping (as the task asks); 1 = real time like the upstream examples.
export ORB3_PACE="${ORB3_PACE:-0}"

export LD_LIBRARY_PATH="$ORB3_DIR/lib:$ORB3_DIR/Thirdparty/DBoW2/lib:$ORB3_DIR/Thirdparty/g2o/lib:$ORB3_PANGOLIN_PREFIX/lib:${LD_LIBRARY_PATH:-}"
