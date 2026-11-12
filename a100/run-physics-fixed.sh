#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
cd "$A100SIM_CODE_ROOT"
[[ "${OMNI_KIT_ACCEPT_EULA:-}" = YES ]] || { echo "Set OMNI_KIT_ACCEPT_EULA=YES after accepting the NVIDIA terms" >&2; exit 2; }
[[ -x "$A100SIM_PYTHON" ]] || { echo "Set A100SIM_PYTHON to a portable Python 3.12 executable" >&2; exit 2; }
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export PYTHONPATH="$A100SIM_CODE_ROOT:$A100SIM_ROOT/packages:$A100SIM_ROOT/render-packages:$A100SIM_ROOT/http-packages${A100SIM_EXTRA_PYTHONPATH:+:$A100SIM_EXTRA_PYTHONPATH}"
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1
export CUDA_VISIBLE_DEVICES="$ISAAC_GPU"
export OMNI_USER_DIR="$A100SIM_STATE_ROOT/user" OMNI_DATA_PATH="$A100SIM_STATE_ROOT/data"
export XDG_CACHE_HOME="$A100SIM_STATE_ROOT/cache" XDG_CONFIG_HOME="$A100SIM_STATE_ROOT/config"
export DRJIT_LIBOPTIX_PATH="${DRJIT_LIBOPTIX_PATH:-$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu/libnvoptix.so.1}"
mkdir -p "$A100SIM_STATE_ROOT/home" "$OMNI_USER_DIR" "$OMNI_DATA_PATH" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"
exec /usr/bin/unshare -Ur -m /usr/bin/bash "$A100SIM_CODE_ROOT/namespace.sh" "$A100SIM_PYTHON" "$@"
