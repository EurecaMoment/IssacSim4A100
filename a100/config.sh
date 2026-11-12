#!/usr/bin/env bash
# Shared configuration for launchers; this file is sourced, not executed.
if [[ "${A100SIM_CONFIG_LOADED:-0}" = 1 ]]; then
    return 0
fi
export A100SIM_CONFIG_LOADED=1
export A100SIM_CODE_ROOT
A100SIM_CODE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
configuration="${A100SIM_CONFIG:-$A100SIM_CODE_ROOT/.env}"
if [[ -f "$configuration" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$configuration"
    set +a
fi
export A100SIM_ROOT="${A100SIM_ROOT:-$A100SIM_CODE_ROOT}"
export A100SIM_STATE_ROOT="${A100SIM_STATE_ROOT:-$A100SIM_ROOT/state}"
export A100SIM_LOCAL_STATE="${A100SIM_LOCAL_STATE:-/var/tmp/a100sim-$UID}"
export A100SIM_ASSET_ROOT="${A100SIM_ASSET_ROOT:-$A100SIM_ROOT/assets}"
if [[ -z "${A100SIM_CUDA_ROOT:-}" ]]; then
    if [[ -d /usr/local/cuda-12.2 ]]; then
        export A100SIM_CUDA_ROOT=/usr/local/cuda-12.2
    elif [[ -d /usr/local/cuda ]]; then
        export A100SIM_CUDA_ROOT=/usr/local/cuda
    else
        export A100SIM_CUDA_ROOT=/usr/local/cuda-12.2
    fi
fi
export ISAAC_GPU="${ISAAC_GPU:-0}"
if [[ -z "${A100SIM_DRIVER_VERSION:-}" ]]; then
    A100SIM_DRIVER_VERSION="$(nvidia-smi --id="$ISAAC_GPU" \
        --query-gpu=driver_version --format=csv,noheader | tr -d '[:space:]')"
fi
export A100SIM_DRIVER_VERSION
export A100SIM_NVIDIA_ROOT="${A100SIM_NVIDIA_ROOT:-$A100SIM_ROOT/tools/nvidia-$A100SIM_DRIVER_VERSION}"
export A100SIM_HOST_HOME="${A100SIM_HOST_HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
export A100SIM_RESIDENT_PORT="${A100SIM_RESIDENT_PORT:-8321}"
export A100SIM_GENERIC_PORT="${A100SIM_GENERIC_PORT:-18323}"
export A100SIM_RENDER_PORT="${A100SIM_RENDER_PORT:-8320}"
export A100SIM_QUEUE_SIZE="${A100SIM_QUEUE_SIZE:-64}"
if [[ -z "${A100SIM_PYTHON:-}" ]]; then
    if command -v uv >/dev/null 2>&1; then
        A100SIM_PYTHON="$(uv python find 3.12 2>/dev/null || true)"
    fi
fi
export A100SIM_PYTHON="${A100SIM_PYTHON:-$A100SIM_HOST_HOME/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12}"
