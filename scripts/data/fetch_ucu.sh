#!/usr/bin/env bash
# Download one UCU sequence from HF and export it to TUM with the official exporter.
# Usage: scripts/data/fetch_ucu.sh <N> [--keep-mcap]      e.g.  fetch_ucu.sh 1  -> seq01
# Timings (seconds) are appended to $DATA_ROOT/logs/fetch_timings.csv.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/../setup/env.sh"
[ $# -ge 1 ] || { echo "usage: $0 <N> [--keep-mcap]" >&2; exit 2; }
n=$((10#$1)); keep=${2:-}
seq=$(printf 'seq%02d' "$n")
raw="$DATA_ROOT/ucu/sequence-$n"
out="$DATA_ROOT/ucu_tum/$seq"
mkdir -p "$DATA_ROOT/ucu" "$DATA_ROOT/ucu_tum" "$DATA_ROOT/logs"

if [ ! -d "$DATASET_REPO_DIR/.git" ]; then
  mkdir -p "$(dirname "$DATASET_REPO_DIR")"
  git clone https://github.com/ucu-autonomous-ugv/ucu-slam-dataset.git "$DATASET_REPO_DIR"
fi

if [ -f "$out/rgb.txt" ]; then echo "$seq already exported, skipping"; exit 0; fi

t0=$(date +%s)
if ! ls "$raw"/*.mcap >/dev/null 2>&1; then
  hf download ucu-autonomous-ugv/ucu-slam-dataset-v1 --repo-type dataset \
    --include "sequence-$n/*" --local-dir "$DATA_ROOT/ucu"
fi
t1=$(date +%s)
# export_tum.py needs Python >= 3.12 and rosbags: the slam venv has both.
tmp="$out.partial"; rm -rf "$tmp"
( cd "$DATASET_REPO_DIR" && "$SLAM_VENV/bin/python" scripts/export_tum.py "$raw" "$tmp" )
mv "$tmp" "$out"
t2=$(date +%s)

echo "$seq,download_s=$((t1-t0)),export_s=$((t2-t1)),mcap_bytes=$(du -sb "$raw" | cut -f1),export_bytes=$(du -sb "$out" | cut -f1)" \
  | tee -a "$DATA_ROOT/logs/fetch_timings.csv"
[ "$keep" = "--keep-mcap" ] || echo "(raw .mcap kept at $raw; delete it with: rm -rf '$raw')"
