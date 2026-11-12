#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
export A100SIM_ENABLE_EXTENSIONS="${A100SIM_ENABLE_EXTENSIONS:-isaacsim.asset.importer.urdf,isaacsim.asset.importer.mjcf,isaacsim.asset.exporter.urdf,isaacsim.core.simulation_manager}"
exec /bin/bash "$A100SIM_CODE_ROOT/run-features.sh" "$@"
