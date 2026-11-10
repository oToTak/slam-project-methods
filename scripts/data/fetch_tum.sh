#!/usr/bin/env bash
# Download TUM RGB-D freiburg1_xyz into $DATA_ROOT/tum/fr1_xyz (sanity sequence).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../setup/env.sh"
dst="$DATA_ROOT/tum/fr1_xyz"
if [ -f "$dst/rgb.txt" ] && [ -f "$dst/groundtruth.txt" ]; then echo "fr1_xyz present"; exit 0; fi
mkdir -p "$dst"
tmp=$(mktemp -d)
wget -q --show-progress -O "$tmp/fr1_xyz.tgz" https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_xyz.tgz
tar -xzf "$tmp/fr1_xyz.tgz" -C "$dst" --strip-components=1
rm -rf "$tmp"
wc -l "$dst/rgb.txt" "$dst/groundtruth.txt"
