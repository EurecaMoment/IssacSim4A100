#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
actual="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader --id="$ISAAC_GPU" | tr -d '[:space:]')"
[[ "$actual" = "$A100SIM_DRIVER_VERSION" ]] || {
    echo "Host driver is $actual; configure matching NVIDIA userspace libraries before proceeding" >&2; exit 2;
}
branch="${A100SIM_DRIVER_VERSION%%.*}"
url="${A100SIM_NVIDIA_DEB_URL:-https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/libnvidia-gl-${branch}_${A100SIM_DRIVER_VERSION}-0ubuntu1_amd64.deb}"
mkdir -p "$A100SIM_ROOT/downloads" "$A100SIM_NVIDIA_ROOT"
archive="$A100SIM_ROOT/downloads/libnvidia-gl-$A100SIM_DRIVER_VERSION.deb"
curl --fail --location --retry 3 --connect-timeout 20 "$url" -o "$archive"
dpkg-deb -x "$archive" "$A100SIM_NVIDIA_ROOT"
[[ -e "$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu/libnvoptix.so.1" ]] || {
    echo "The selected package did not contain libnvoptix.so.1" >&2; exit 2;
}
echo "Matched OptiX userspace libraries extracted; the host driver was not changed"
