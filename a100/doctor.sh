#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
[[ -x "$A100SIM_PYTHON" ]] || { echo "Install Python 3.12 with uv or set A100SIM_PYTHON" >&2; exit 2; }
export PYTHONPATH="$A100SIM_CODE_ROOT"
exec "$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/scripts/doctor.py" "$@"
