#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
configuration="${ISAACSIM_CONFIG:-$repo_dir/.env}"
if [[ -f "$configuration" ]]; then
    set -a
    source "$configuration"
    set +a
fi
action="${1:-detect}"
[[ $# = 0 ]] || shift
case "$action" in
    detect) exec python3 "$repo_dir/deployment/detect_gpu.py" "$@" ;;
    prepare|smoke|serve) ;;
    *) echo 'Usage: bash deploy.sh detect|prepare|smoke|serve [service arguments]' >&2; exit 2 ;;
esac
selection="$(python3 "$repo_dir/deployment/detect_gpu.py" --format shell)"
eval "$selection"
export ISAACSIM_PORT="${ISAACSIM_PORT:-8321}"
export ISAACSIM_HOST="${ISAACSIM_HOST:-127.0.0.1}"
export ISAACSIM_STATE_ROOT="${ISAACSIM_STATE_ROOT:-$repo_dir/state}"
printf 'GPU %s: %s -> %s backend (driver %s)\n' \
    "$ISAACSIM_GPU_INDEX" "$ISAACSIM_GPU_NAME" "$ISAACSIM_GPU_BACKEND" "$ISAACSIM_GPU_DRIVER"
if [[ "$ISAACSIM_GPU_BACKEND" = rtx ]]; then
    export ISAAC_GPU="$ISAACSIM_GPU_INDEX"
    export RTX4090_CONFIG_LOADED=1
    export RTX4090_STATE_ROOT="$ISAACSIM_STATE_ROOT/rtx"
    export RTX4090_PORT="$ISAACSIM_PORT" RTX4090_HOST="$ISAACSIM_HOST"
    if [[ -z "${ISAAC_SIM_DIR:-}" ]]; then
        for candidate in "${ISAAC_PATH:-}" "$repo_dir/_build/linux-x86_64/release" /opt/isaacsim /isaac-sim; do
            if [[ -n "$candidate" && -x "$candidate/python.sh" ]]; then export ISAAC_SIM_DIR="$candidate"; break; fi
        done
    fi
    if [[ -z "${ISAAC_SIM_DIR:-}" || ! -x "$ISAAC_SIM_DIR/python.sh" ]]; then
        echo 'Set ISAAC_SIM_DIR in .env to an official standalone Isaac Sim 6.x installation.' >&2
        exit 2
    fi
    case "$action" in
        prepare)
            [[ -f "$ISAAC_SIM_DIR/apps/isaacsim.exp.base.python.kit" && -f "$ISAAC_SIM_DIR/VERSION" ]]
            printf 'Using installed official RTX runtime: %s\n' "$ISAAC_SIM_DIR"
            cat "$ISAAC_SIM_DIR/VERSION"
            ;;
        smoke) exec bash "$repo_dir/rtx4090/run.sh" --smoke --output "$ISAACSIM_STATE_ROOT/smoke" "$@" ;;
        serve) exec bash "$repo_dir/rtx4090/run.sh" --output "$ISAACSIM_STATE_ROOT/jobs" "$@" ;;
    esac
    exit 0
fi

# Load an existing A100 runtime configuration, then enforce the physical GPU
# selected above. Child launchers inherit this resolved configuration.
export ISAAC_GPU="$ISAACSIM_GPU_UUID"
source "$repo_dir/a100/config.sh"
export ISAAC_GPU="$ISAACSIM_GPU_UUID"
[[ "$A100SIM_DRIVER_VERSION" = "$ISAACSIM_GPU_DRIVER" ]] || {
    echo 'A100SIM_DRIVER_VERSION differs from the selected host driver; update it or leave it empty.' >&2; exit 2;
}
export A100SIM_STATE_ROOT="$ISAACSIM_STATE_ROOT/cuda"
export A100SIM_RESIDENT_PORT="$ISAACSIM_PORT"
export A100SIM_GENERIC_PORT="${ISAACSIM_INTERNAL_PORT:-18323}"
case "$action" in
    prepare)
        [[ "${OMNI_KIT_ACCEPT_EULA:-}" = YES ]] || {
            echo 'Set OMNI_KIT_ACCEPT_EULA=YES after accepting the NVIDIA terms.' >&2; exit 2;
        }
        [[ -d "$A100SIM_CUDA_ROOT" ]] || {
            echo 'Set A100SIM_CUDA_ROOT to the installed CUDA toolkit directory.' >&2; exit 2;
        }
        if [[ ! -x "$A100SIM_PYTHON" ]]; then
            command -v uv >/dev/null || { echo 'Install uv or set A100SIM_PYTHON to portable Python 3.12.' >&2; exit 2; }
            uv python install 3.12
            export A100SIM_PYTHON="$(uv python find 3.12)"
        fi
        if [[ ! -x "$A100SIM_ROOT/rootfs/bin/bash" || ! -d "$A100SIM_ROOT/packages/isaacsim" ||
              ! -d "$A100SIM_ROOT/render-packages/mitsuba" ]]; then
            bash "$repo_dir/a100/prepare.sh" minimal
        fi
        if [[ ! -e "$A100SIM_ROOT/rootfs/usr/lib/x86_64-linux-gnu/libstdc++.so.6" ]]; then
            bash "$repo_dir/a100/install-libs.sh"
        fi
        if [[ ! -e "$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu/libnvoptix.so.1" ]]; then
            bash "$repo_dir/a100/get-driver-libs.sh"
        fi
        if [[ ! -f "$A100SIM_ROOT/render-packages/drjit/libdrjit-core.so.original" ]]; then
            bash "$repo_dir/a100/build-drjit.sh"
        fi
        printf 'CUDA/Mitsuba runtime ready: %s\n' "$A100SIM_ROOT"
        ;;
    smoke)
        exec bash "$repo_dir/a100/run-physics-fixed.sh" "$repo_dir/a100/scripts/smoke_gpu.py" \
            --output "$A100SIM_STATE_ROOT/smoke" "$@"
        ;;
    serve)
        [[ "$A100SIM_RESIDENT_PORT" != "$A100SIM_GENERIC_PORT" ]] || {
            echo 'ISAACSIM_PORT and ISAACSIM_INTERNAL_PORT must differ.' >&2; exit 2;
        }
        mkdir -p "$ISAACSIM_STATE_ROOT"
        children=()
        cleanup() {
            trap - EXIT INT TERM
            if [[ ${#children[@]} -gt 0 ]]; then
                kill -TERM "${children[@]}" 2>/dev/null || true
                wait "${children[@]}" 2>/dev/null || true
            fi
        }
        trap cleanup EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        bash "$repo_dir/a100/serve.sh" generic --host 127.0.0.1 \
            > "$ISAACSIM_STATE_ROOT/generic.log" 2>&1 &
        children+=("$!")
        bash "$repo_dir/a100/serve.sh" resident --host "$ISAACSIM_HOST" "$@" &
        children+=("$!")
        wait -n "${children[@]}"
        ;;
esac
