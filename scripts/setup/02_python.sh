#!/usr/bin/env bash
# Phase 1b: uv + Python 3.12 + venv ~/venvs/slam (evo 1.37.1, exporter deps) + hf CLI. No sudo.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/env.sh"

if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh \
    || { sudo apt install -y python3-pip && python3 -m pip install --user uv; }
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

[ -x "$SLAM_VENV/bin/python" ] || uv venv --python 3.12 "$SLAM_VENV"
uv pip install --python "$SLAM_VENV/bin/python" -r "$here/requirements-slam.txt" huggingface_hub

# `hf` and evo on PATH regardless of venv activation
mkdir -p "$HOME/.local/bin"
ln -sf "$SLAM_VENV/bin/hf" "$HOME/.local/bin/hf"

"$SLAM_VENV/bin/evo_ape" --version >/dev/null 2>&1 || true
"$SLAM_VENV/bin/evo" pkg --version 2>/dev/null || "$SLAM_VENV/bin/python" -c "import evo; print('evo', evo.__version__)"
"$SLAM_VENV/bin/hf" version 2>/dev/null || "$SLAM_VENV/bin/hf" --version
