#!/usr/bin/env bash
# Phase 1a: system packages + CUDA toolkit 12.1 (build only; the GPU driver comes from Windows).
# Run as root:  wsl -d Ubuntu-22.04 -u root -- bash scripts/setup/01_system_root.sh
# or inside WSL: sudo bash scripts/setup/01_system_root.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build unzip git curl wget ca-certificates pkg-config \
  libgl1 libglib2.0-0
# (libgl1, libglib2.0-0: runtime libraries of opencv-python, needed by `import cv2`)

# NVIDIA's WSL-Ubuntu repo: ships the toolkit WITHOUT a Linux driver (WSL uses the Windows driver).
if ! dpkg -s cuda-keyring >/dev/null 2>&1; then
  wget -q -O /tmp/cuda-keyring.deb \
    https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i /tmp/cuda-keyring.deb && rm /tmp/cuda-keyring.deb
  apt-get update
fi
# Only the 12.1 toolkit; never install the `cuda` or `cuda-drivers` metapackages in WSL.
apt-get install -y --no-install-recommends cuda-toolkit-12-1

/usr/local/cuda-12.1/bin/nvcc --version | tail -n 2
