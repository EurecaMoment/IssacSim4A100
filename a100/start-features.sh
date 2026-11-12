#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
systemctl --user enable --now isaacsim-a100-features.service
exec "$A100SIM_PYTHON" "$A100SIM_CODE_ROOT/scripts/wait_ready.py" \
    "http://127.0.0.1:${A100SIM_FEATURE_PORT:-8322}" --timeout 240
