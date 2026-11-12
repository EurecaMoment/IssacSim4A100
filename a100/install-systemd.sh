#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
python3 "$A100SIM_CODE_ROOT/scripts/install_systemd.py" "$@"
if [[ $# = 0 ]]; then systemctl --user daemon-reload; fi
