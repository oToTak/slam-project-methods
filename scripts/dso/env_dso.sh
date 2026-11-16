# Environment of the DSO method. Source it AFTER scripts/setup/env.sh (the shared file is not touched by this method):
#   source scripts/setup/env.sh; source scripts/dso/env_dso.sh
# Everything is overridable from the caller's environment.
#
# Pin (verified on GitHub 2026-11-16): DSO master HEAD = 7b0c99f01d238f801c625beaff90240bcb007198
#   (Nikolaus Demmel, 2021-03-08). Repo: https://github.com/JakobEngel/dso. No release tags are used; the commit is the pin.
export DSO_COMMIT="${DSO_COMMIT:-7b0c99f01d238f801c625beaff90240bcb007198}"
export DSO_DIR="${DSO_DIR:-$HOME/src/dso}"                   # DSO sources + build tree (libdso.a is built here)
export DSO_PREFIX="${DSO_PREFIX:-$HOME/opt/dso}"             # our driver binary (no sudo needed, nothing in /usr/local)
export DSO_BIN="${DSO_BIN:-$DSO_PREFIX/bin/dso_headless}"
# DSO's CMakeLists asks for '-std=c++0x -march=native'. Both are kept as upstream unless you override them:
#   DSO_CXX_STD=c++14   fallback if GCC 11 / Eigen 3.4 reject c++0x      (rewrites the flag, redo with DSO_FORCE=1)
#   DSO_MARCH=x86-64-v2 fallback if -march=native gives alignment crashes with Eigen (any value accepted by -march=)
export DSO_CXX_STD="${DSO_CXX_STD:-c++0x}"
export DSO_MARCH="${DSO_MARCH:-native}"
# Run knobs of scripts/dso/run.sh
export DSO_NOMT="${DSO_NOMT:-0}"                              # 1 = single-threaded DSO (slower, maybe more repeatable)
export DSO_QUIET="${DSO_QUIET:-1}"                            # 1 = DSO's quiet mode (less console output)
