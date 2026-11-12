#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
launcher=run-features.sh
export A100SIM_FEATURES_ROOT="${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"
options=()
if [[ "${A100SIM_FEATURE_ROS2:-1}" = 1 ]]; then
    launcher=run-ros2.sh
    options+=(--ros2)
fi
exec /bin/bash "$A100SIM_CODE_ROOT/$launcher" -m a100sim.feature_service \
    --output "$A100SIM_FEATURES_ROOT/jobs" --port "${A100SIM_FEATURE_PORT:-8322}" \
    --arms "${A100SIM_FEATURE_ARMS:-4}" "${options[@]}" "$@"
