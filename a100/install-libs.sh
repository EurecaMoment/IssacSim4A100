#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
unshare -Ur -m bash "$A100SIM_CODE_ROOT/namespace.sh" /usr/bin/apt-get -o APT::Sandbox::User=root update
unshare -Ur -m bash "$A100SIM_CODE_ROOT/namespace.sh" /usr/bin/apt-get -o APT::Sandbox::User=root \
    --download-only -y --no-install-recommends install \
    ca-certificates libstdc++6 libgcc-s1 libxkbcommon0 libx11-6 libxxf86vm1 libxfixes3 libxi6 \
    libxrender1 libgl1 libsm6 libice6 libfontconfig1 libxrandr2 libxcursor1 libxinerama1 \
    libegl1 libvulkan1 libgomp1 libasound2t64 libnss3 libdbus-1-3 libglib2.0-0t64 \
    libxext6 libxdamage1 libxcomposite1 libxau6 libxcb1 libgtk-3-0t64 libxkbcommon-x11-0
for deb in "$A100SIM_ROOT"/rootfs/var/cache/apt/archives/*.deb; do
    [[ -f "$deb" ]] || continue
    dpkg-deb -x "$deb" "$A100SIM_ROOT/rootfs"
done
echo "Noble runtime libraries ready; no host packages were installed"
