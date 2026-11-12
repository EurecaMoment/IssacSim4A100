#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
source "$A100SIM_CODE_ROOT/resolve-gpu.sh"
export PYTHONPATH="$A100SIM_CODE_ROOT"
exec "$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/scripts/build_newton_kernels.py" "$@"
