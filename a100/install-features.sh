#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
[[ "${OMNI_KIT_ACCEPT_EULA:-}" = YES ]] || { echo "Accept the NVIDIA runtime terms first" >&2; exit 2; }
export A100SIM_FEATURES_ROOT="${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"
profile="${1:-all}"
case "$profile" in all|robotics|ros2|newton|assets) ;; *) echo "Use all, robotics, ros2, newton, or assets" >&2; exit 2;; esac
packages="$A100SIM_FEATURES_ROOT/packages"
mkdir -p "$packages"
isaac_packages=(isaacsim-sensor isaacsim-robot isaacsim-robot-motion isaacsim-robot-setup isaacsim-gui isaacsim-test isaacsim-storage)
options=()
if [[ "$profile" = all || "$profile" = ros2 ]]; then isaac_packages+=(isaacsim-ros2); fi
if [[ "$profile" = all || "$profile" = assets ]]; then isaac_packages+=(isaacsim-asset); options+=(--assets); fi
# Hash-verify the official wheels and unpack into one namespace without replacing the base runtime.
"$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/scripts/install_feature_wheels.py" "${isaac_packages[@]}"
UV_HTTP_TIMEOUT=180 uv pip install --no-deps --require-hashes --target "$packages" \
    --python "$A100SIM_PYTHON" --python-platform x86_64-manylinux_2_35 \
    -r "$A100SIM_CODE_ROOT/requirements-features.txt"
if [[ "$profile" = all || "$profile" = newton || "$profile" = assets ]]; then
    UV_HTTP_TIMEOUT=180 uv pip install --no-deps --require-hashes --target "$packages" \
        --python "$A100SIM_PYTHON" --python-platform x86_64-manylinux_2_35 \
        -r "$A100SIM_CODE_ROOT/requirements-newton.txt"
fi
"$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/research/fetch_feature_extensions.py" "${options[@]}"
echo "Optional packages ready in $A100SIM_FEATURES_ROOT. Existing services were not restarted."
