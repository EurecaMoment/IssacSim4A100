#!/usr/bin/env bash
set -euo pipefail
code="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec /bin/bash "$code/run-physics-fixed.sh" "$code/isaac_sam3d_demo.py" "$@"
