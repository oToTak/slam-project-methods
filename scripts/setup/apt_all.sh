#!/usr/bin/env bash
# One sudo step for ALL methods (run once per PC, before START.bat): installs every apt package that the recipes of
# orbslam3, dso and openvins list in APT_PKGS (their union). Nothing goes to /usr/local; everything else the recipes
# build in $HOME without sudo. Safe to run again: apt skips what is installed.
#   bash scripts/setup/apt_all.sh          (asks the sudo password once)
set -euo pipefail
PKGS=(build-essential cmake git pkg-config
      libopencv-dev libeigen3-dev libjpeg-dev libpng-dev libssl-dev
      libboost-serialization-dev libboost-system-dev libboost-filesystem-dev libboost-thread-dev libboost-date-time-dev
      libglew-dev libgl1-mesa-dev libegl1-mesa-dev libwayland-dev libxkbcommon-dev wayland-protocols
      libsuitesparse-dev libceres-dev)
miss=()
for p in "${PKGS[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || miss+=("$p"); done
[ ${#miss[@]} -eq 0 ] && { echo "all ${#PKGS[@]} packages already installed: no sudo needed"; exit 0; }
echo "installing: ${miss[*]}"
sudo apt-get update && sudo apt-get install -y "${miss[@]}"
