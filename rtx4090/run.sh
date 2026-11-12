#!/usr/bin/env bash
set -euo pipefail
adapter_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname "$adapter_dir")"
if [[ -f "$adapter_dir/.env" ]]; then
    set -a
    source "$adapter_dir/.env"
    set +a
fi
export ISAAC_SIM_DIR="${ISAAC_SIM_DIR:-/mnt/data/zhouwei/isaacsim}"
export ISAAC_GPU="${ISAAC_GPU:-1}"
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
gpu_name="$(nvidia-smi -i "$ISAAC_GPU" --query-gpu=name --format=csv,noheader)"
if [[ "$gpu_name" != *"RTX 4090"* ]]; then
    echo "Expected RTX 4090 at GPU $ISAAC_GPU; received $gpu_name." >&2
    exit 1
fi
export RTX4090_GPU_UUID="$(nvidia-smi -i "$ISAAC_GPU" --query-gpu=uuid --format=csv,noheader)"
export PYTHONPATH="$repo_dir:$repo_dir/a100${PYTHONPATH:+:$PYTHONPATH}"
export OMNI_KIT_ALLOW_ROOT=1
export OMNI_KIT_ACCEPT_EULA=YES
export PYTHONUNBUFFERED=1
mkdir -p "$RTX4090_STATE_ROOT" "$A100SIM_ASSET_ROOT"
cd "$repo_dir"
exec "$ISAAC_SIM_DIR/python.sh" -m rtx4090.service "$@"
