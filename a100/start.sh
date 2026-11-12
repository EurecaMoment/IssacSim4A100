#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
units=(isaacsim-a100-generic.service isaacsim-a100-resident.service)
urls=("http://127.0.0.1:$A100SIM_GENERIC_PORT" "http://127.0.0.1:$A100SIM_RESIDENT_PORT")
if [[ -n "${A100SIM_RENDER_ASSET:-}" ]]; then
    units+=(isaacsim-a100-render.service)
    urls+=("http://127.0.0.1:$A100SIM_RENDER_PORT")
fi
for unit in "${units[@]}"; do
    [[ -f "$A100SIM_HOST_HOME/.config/systemd/user/$unit" ]] || { echo "Run install-systemd.sh first" >&2; exit 2; }
done
systemctl --user daemon-reload
systemctl --user enable --now "${units[@]}"
python3 "$A100SIM_CODE_ROOT/scripts/wait_ready.py" --timeout 180 "${urls[@]}"
