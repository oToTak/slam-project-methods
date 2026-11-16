# Environment of the OpenVINS method. Source it AFTER scripts/setup/env.sh (the shared file is not touched by this method):
#   source scripts/setup/env.sh; source scripts/openvins/env_openvins.sh
# Everything is overridable from the caller's environment.
#
# Pin (verified on GitHub 2026-11-16): rpng/open_vins commit d0075dd86f2d96618f14829e6c33425f9278610a ("Merge pull request #448", 2024-12-15).
#   It is AFTER v2.7 (2023-05) and contains the ROS-free build fixes ("enable building without ROS even if you have ROS" 2024-05-31,
#   "extend to all other packages" 2024-12-15) and BEFORE the 2025 changes (State_JPLQuatLocal update, Android logging) that were not vetted.
#   Repo: https://github.com/rpng/open_vins
export OV_COMMIT="${OV_COMMIT:-d0075dd86f2d96618f14829e6c33425f9278610a}"
export OV_DIR="${OV_DIR:-$HOME/src/open_vins}"              # sources + the build tree ov_msckf/build (libov_msckf_lib.so is built there)
export OV_PREFIX="${OV_PREFIX:-$HOME/opt/openvins}"          # our driver binary (no sudo needed, nothing in /usr/local)
export OV_BIN="${OV_BIN:-$OV_PREFIX/bin/ov_ucu_driver}"
export OV_CONFIG_DIR="${OV_CONFIG_DIR:-}"                    # override: folder with estimator_config.yaml + the two kalibr_*_chain.yaml (ablations)
export LD_LIBRARY_PATH="$OV_DIR/ov_msckf/build:${LD_LIBRARY_PATH:-}"
