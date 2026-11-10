#!/usr/bin/env bash
# Pack the committed scripts/configs into one small archive for Google Drive (the brother's PC).
#   bash scripts/setup/make_bundle.sh [outdir]      -> <outdir>/slam-local-<commit>.tar.gz   (default outdir: $DATA_ROOT/bundle)
# Only COMMITTED files go in (git archive); no data, weights, venvs or images.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
out=${1:-$DATA_ROOT/bundle}
mkdir -p "$out"
cd "$SLAM_REPO"
if [ -n "$(git status --porcelain scripts configs .gitattributes)" ]; then
  echo "WARNING: uncommitted changes in scripts/ or configs/ are NOT in the bundle (commit first)." >&2
fi
sha=$(git rev-parse --short HEAD)
tmpd=$(mktemp -d); git rev-parse HEAD > "$tmpd/BUNDLE_COMMIT"; tmp="$tmpd/BUNDLE_COMMIT"
git archive --format=tar.gz --prefix=slam-local/ --add-file="$tmp" -o "$out/slam-local-$sha.tar.gz" HEAD scripts configs .gitattributes
rm -rf "$tmpd"
ls -la "$out/slam-local-$sha.tar.gz"
echo "On the other PC (inside WSL):  mkdir -p ~ && tar -xzf slam-local-$sha.tar.gz -C ~ && cd ~/slam-local && bash scripts/setup/setup_all.sh"

# CRLF guard: Windows line endings in .sh/.py would break the scripts inside WSL
chk=$(mktemp -d); tar -xzf "$out/slam-local-$sha.tar.gz" -C "$chk"
if grep -rlI $'\r' --include='*.sh' --include='*.py' --include='*.txt' "$chk" >/dev/null; then
  echo "ERROR: CRLF found in the bundle:" >&2; grep -rlI $'\r' --include='*.sh' --include='*.py' --include='*.txt' "$chk" >&2; rm -rf "$chk"; exit 1
fi
rm -rf "$chk"; echo "bundle OK (no CRLF)"
