#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
# Register the tensor backend for SimulationManager; the example reads direct Warp state
# because executing Newton tensor GPU kernels is incompatible with the R535 deployment.
export A100SIM_ENABLE_EXTENSIONS="${A100SIM_ENABLE_EXTENSIONS:-isaacsim.physics.newton,isaacsim.physics.newton.tensors,isaacsim.core.experimental.prims}"
export A100SIM_EXCLUDE_EXTENSIONS="${A100SIM_EXCLUDE_EXTENSIONS-isaacsim.test.utils}"
exec /bin/bash "$A100SIM_CODE_ROOT/run-features.sh" "$@"
