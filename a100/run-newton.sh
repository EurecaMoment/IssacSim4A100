#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
# Register the tensor backend; create_app applies driver capability and local kernel compatibility.
export A100SIM_ENABLE_EXTENSIONS="${A100SIM_ENABLE_EXTENSIONS:-isaacsim.physics.newton,isaacsim.physics.newton.tensors,isaacsim.core.experimental.prims}"
export A100SIM_EXCLUDE_EXTENSIONS="${A100SIM_EXCLUDE_EXTENSIONS-isaacsim.test.utils}"
exec /bin/bash "$A100SIM_CODE_ROOT/run-features.sh" "$@"
