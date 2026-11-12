#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
mode="${1:?Usage: serve.sh resident|generic|render [additional service arguments]}"
shift
actual_uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader --id="$ISAAC_GPU" | tr -d '[:space:]')"
[[ "$actual_uuid" == GPU-* ]] || { echo "Cannot resolve the selected physical GPU" >&2; exit 2; }
if [[ -n "${A100SIM_EXPECTED_GPU_UUID:-}" && "$actual_uuid" != "$A100SIM_EXPECTED_GPU_UUID" ]]; then
    echo "Selected GPU does not match A100SIM_EXPECTED_GPU_UUID" >&2
    exit 2
fi
export ISAAC_GPU="$actual_uuid"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}" OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export A100SIM_KIT_ROOT="${A100SIM_KIT_ROOT:-$A100SIM_STATE_ROOT/kit-$mode}"
umask 077
mkdir -p "$A100SIM_STATE_ROOT" "$A100SIM_ROOT/logs" "$A100SIM_LOCAL_STATE" "$A100SIM_ASSET_ROOT"
case "$mode" in
    resident)
        exec /bin/bash "$A100SIM_CODE_ROOT/run-physics-fixed.sh" -m a100sim.resident_service \
            --output "$A100SIM_LOCAL_STATE/jobs" --port "$A100SIM_RESIDENT_PORT" --queue-size "$A100SIM_QUEUE_SIZE" \
            --batch-wait-ms 300 --writers 1 --artifact-processes 8 --retention-hours 24 --max-retained-jobs 4096 \
            --generic-url "http://127.0.0.1:$A100SIM_GENERIC_PORT" \
            --legacy-output "$A100SIM_ROOT/results/physics-service" "$@"
        ;;
    generic)
        exec /bin/bash "$A100SIM_CODE_ROOT/run-physics-fixed.sh" -m a100sim.physics_service \
            --output "$A100SIM_ROOT/results/physics-service" --port "$A100SIM_GENERIC_PORT" \
            --queue-size "$A100SIM_QUEUE_SIZE" "$@"
        ;;
    render)
        [[ -n "${A100SIM_RENDER_ASSET:-}" && -d "$A100SIM_RENDER_ASSET" ]] || {
            echo "Set A100SIM_RENDER_ASSET to a prepared asset directory" >&2; exit 2;
        }
        exec /bin/bash "$A100SIM_CODE_ROOT/run-render-fixed.sh" -m a100sim.render_service \
            "$A100SIM_RENDER_ASSET" --output "$A100SIM_ROOT/results/render-service" \
            --size 256 --queue-size "$A100SIM_QUEUE_SIZE" --port "$A100SIM_RENDER_PORT" "$@"
        ;;
    *) echo "Unknown service mode: $mode" >&2; exit 2 ;;
esac
