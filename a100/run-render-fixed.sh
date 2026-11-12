#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
source "$A100SIM_CODE_ROOT/resolve-gpu.sh"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export PYTHONPATH="$A100SIM_CODE_ROOT:$A100SIM_ROOT/render-packages:$A100SIM_ROOT/http-packages:$A100SIM_ROOT/packages"
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES="$ISAAC_GPU"
export XDG_CACHE_HOME="$A100SIM_STATE_ROOT/cache" XDG_CONFIG_HOME="$A100SIM_STATE_ROOT/config"
export DRJIT_LIBOPTIX_PATH="${DRJIT_LIBOPTIX_PATH:-$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu/libnvoptix.so.1}"
mkdir -p "$A100SIM_STATE_ROOT/home" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"
cd "$A100SIM_CODE_ROOT"
exec /usr/bin/unshare -Ur -m /usr/bin/bash "$A100SIM_CODE_ROOT/namespace.sh" "$A100SIM_PYTHON" "$@"
