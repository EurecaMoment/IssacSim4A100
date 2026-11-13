#!/usr/bin/env bash
# Keep gRPC/protobuf outside Kit's Python environment.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
gateway_env="${A100SIM_GRPC_ENV:-$A100SIM_ROOT/.venv-grpc}"
if [[ "${1:-}" = install ]]; then
    [[ -x "$A100SIM_PYTHON" ]] || { echo "Activate the Conda Python environment first" >&2; exit 2; }
    "$A100SIM_PYTHON" -m venv "$gateway_env"
    "$gateway_env/bin/python" -m pip install -r "$A100SIM_CODE_ROOT/requirements-grpc.txt"
    exit 0
fi
[[ -x "$gateway_env/bin/python" ]] || { echo "Run bash serve-grpc.sh install first" >&2; exit 2; }
export PYTHONPATH="$A100SIM_CODE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
umask 077
exec "$gateway_env/bin/python" -m a100sim.rpc.server "$@"
