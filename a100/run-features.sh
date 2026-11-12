#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
actual_uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader --id="$ISAAC_GPU" | tr -d '[:space:]')"
[[ "$actual_uuid" == GPU-* ]] || { echo "Cannot resolve the selected physical GPU" >&2; exit 2; }
if [[ -n "${A100SIM_EXPECTED_GPU_UUID:-}" && "$actual_uuid" != "$A100SIM_EXPECTED_GPU_UUID" ]]; then
    echo "Selected GPU does not match A100SIM_EXPECTED_GPU_UUID" >&2
    exit 2
fi
export ISAAC_GPU="$actual_uuid"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}" OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
umask 077
export A100SIM_FEATURES_ROOT="${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"
packages="$A100SIM_FEATURES_ROOT/packages"
[[ -d "$packages/isaacsim/exts/isaacsim.sensors.experimental.physics" ]] || {
    echo "Install optional features with install-features.sh first" >&2
    exit 2
}
export A100SIM_EXT_FOLDERS="$packages/isaacsim/exts:$packages/isaacsim/extscache:$packages/isaacsim/extsDeprecated${A100SIM_EXT_FOLDERS:+:$A100SIM_EXT_FOLDERS}"
export A100SIM_EXTRA_PYTHONPATH="$packages${A100SIM_EXTRA_PYTHONPATH:+:$A100SIM_EXTRA_PYTHONPATH}"
defaults="isaacsim.sensors.experimental.physics,isaacsim.core.cloner,isaacsim.robot.surface_gripper,isaacsim.robot_motion.experimental.motion_generation,isaacsim.robot_motion.controllers,isaacsim.robot_setup.gain_tuner"
export A100SIM_ENABLE_EXTENSIONS="${A100SIM_ENABLE_EXTENSIONS:-$defaults}"
# Installed optional test helpers otherwise pull RTX/Replicator into a physics-only launch.
export A100SIM_EXCLUDE_EXTENSIONS="${A100SIM_EXCLUDE_EXTENSIONS-isaacsim.physics.newton,isaacsim.physics.newton.tensors,isaacsim.test.utils}"
# Keep feature experiments' Kit state separate from the original rigid-body service.
export A100SIM_KIT_ROOT="${A100SIM_KIT_ROOT:-$A100SIM_STATE_ROOT/features-kit}"
export A100SIM_FAST_SHUTDOWN="${A100SIM_FAST_SHUTDOWN:-1}"
exec /bin/bash "$A100SIM_CODE_ROOT/run-physics-fixed.sh" "$@"
