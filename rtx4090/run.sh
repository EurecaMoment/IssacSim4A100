#!/usr/bin/env bash
set -euo pipefail
adapter_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname "$adapter_dir")"
if [[ "${RTX4090_CONFIG_LOADED:-0}" != 1 && -f "$adapter_dir/.env" ]]; then
    set -a
    source "$adapter_dir/.env"
    set +a
fi
export ISAAC_SIM_DIR="${ISAAC_SIM_DIR:-${ISAAC_PATH:-$repo_dir/_build/linux-x86_64/release}}"
export ISAAC_GPU="${ISAAC_GPU:-0}"
export RTX4090_STATE_ROOT="${RTX4090_STATE_ROOT:-$adapter_dir/state}"
export A100SIM_ASSET_ROOT="${RTX4090_ASSET_ROOT:-$adapter_dir/assets}"
if [[ ! -x "$ISAAC_SIM_DIR/python.sh" ]]; then
    echo "Set ISAAC_SIM_DIR to an official Isaac Sim standalone installation." >&2
    exit 1
fi
if [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]]; then
    echo "Use ISAAC_GPU as a physical GPU index and launch without CUDA_VISIBLE_DEVICES." >&2
    exit 1
fi
if [[ ! "$ISAAC_GPU" =~ ^[0-9]+$ ]]; then
    echo "ISAAC_GPU must be a physical nvidia-smi index." >&2
    exit 1
fi
selection="$(python3 "$repo_dir/deployment/detect_gpu.py" --gpu "$ISAAC_GPU" --backend rtx --format shell)"
eval "$selection"
export RTX4090_GPU_UUID="$ISAACSIM_GPU_UUID"
# Physical GPU zero is also CUDA ordinal zero when it is the only visible GPU.
# Restrict CUDA discovery here so Warp does not allocate contexts on other cards.
if [[ "$ISAAC_GPU" = 0 ]]; then
    export CUDA_VISIBLE_DEVICES="$RTX4090_GPU_UUID"
fi
export PYTHONPATH="$repo_dir:$repo_dir/a100${PYTHONPATH:+:$PYTHONPATH}"
export OMNI_KIT_ALLOW_ROOT=1
export OMNI_KIT_ACCEPT_EULA=YES
export PYTHONUNBUFFERED=1
mkdir -p "$RTX4090_STATE_ROOT" "$A100SIM_ASSET_ROOT"
cd "$repo_dir"
exec "$ISAAC_SIM_DIR/python.sh" -m rtx4090.service "$@"
