#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
[[ "${OMNI_KIT_ACCEPT_EULA:-}" = YES ]] || { echo "Accept the NVIDIA terms and set OMNI_KIT_ACCEPT_EULA=YES" >&2; exit 2; }
[[ -x "$A100SIM_PYTHON" ]] || { echo "Install portable Python 3.12 with uv, or set A100SIM_PYTHON" >&2; exit 2; }
mode="${1:-minimal}"
[[ "$mode" = minimal || "$mode" = full ]] || { echo "Usage: prepare.sh [minimal|full]" >&2; exit 2; }
mkdir -p "$A100SIM_ROOT/rootfs" "$A100SIM_ROOT/downloads" "$A100SIM_ROOT/packages"
if [[ ! -x "$A100SIM_ROOT/rootfs/bin/bash" ]]; then
    curl --fail --location --retry 3 --connect-timeout 20 \
        "${A100SIM_ROOTFS_URL:-https://partner-images.canonical.com/oci/noble/current/ubuntu-noble-oci-amd64-root.tar.gz}" \
        -o "$A100SIM_ROOT/downloads/ubuntu-noble.tar.gz"
    if [[ -n "${A100SIM_ROOTFS_SHA256:-}" ]]; then
        printf '%s  %s\n' "$A100SIM_ROOTFS_SHA256" "$A100SIM_ROOT/downloads/ubuntu-noble.tar.gz" | sha256sum --check -
    fi
    tar -xzf "$A100SIM_ROOT/downloads/ubuntu-noble.tar.gz" -C "$A100SIM_ROOT/rootfs" --no-same-owner --exclude='dev/*'
fi
isaac_packages=(isaacsim isaacsim-kernel isaacsim-app isaacsim-core isaacsim-extscache-physics)
if [[ "$mode" = full ]]; then
    isaac_packages+=(isaacsim-extscache-kit isaacsim-extscache-kit-sdk)
fi
pinned=()
for package in "${isaac_packages[@]}"; do pinned+=("$package==6.2.0.0"); done
UV_HTTP_TIMEOUT=180 UV_CONCURRENT_DOWNLOADS=2 uv pip install --no-deps \
    --target "$A100SIM_ROOT/packages" --python "$A100SIM_PYTHON" \
    --python-platform x86_64-manylinux_2_35 --extra-index-url https://pypi.nvidia.com \
    --index-strategy unsafe-best-match "${pinned[@]}"
uv pip install --target "$A100SIM_ROOT/packages" --python "$A100SIM_PYTHON" \
    --python-platform x86_64-manylinux_2_35 -r "$A100SIM_CODE_ROOT/requirements-runtime.txt"
uv pip install --target "$A100SIM_ROOT/render-packages" --python "$A100SIM_PYTHON" \
    --python-platform x86_64-manylinux_2_35 -r "$A100SIM_CODE_ROOT/requirements-render.txt"
uv pip install --target "$A100SIM_ROOT/http-packages" --python "$A100SIM_PYTHON" \
    --python-platform x86_64-manylinux_2_35 -r "$A100SIM_CODE_ROOT/requirements-http.txt"
if [[ "$mode" = minimal ]]; then
    "$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/research/fetch_physics_extensions.py"
fi
echo "Runtime packages ready. Next: install-libs.sh, get-driver-libs.sh, build-drjit.sh"
