#!/usr/bin/env bash
# Phase 4a: DPVO sources @ 0ac95b6 + Eigen 3.4.0 (no build yet). No sudo.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
mkdir -p "$(dirname "$DPVO_DIR")"
[ -d "$DPVO_DIR/.git" ] || git clone https://github.com/princeton-vl/DPVO.git "$DPVO_DIR"
cd "$DPVO_DIR"
git checkout -q 0ac95b6
git log --oneline -1
mkdir -p thirdparty
if [ ! -d thirdparty/eigen-3.4.0 ]; then
  wget -q -O /tmp/eigen-3.4.0.zip https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.zip
  unzip -q /tmp/eigen-3.4.0.zip -d thirdparty && rm /tmp/eigen-3.4.0.zip
fi
ls thirdparty
