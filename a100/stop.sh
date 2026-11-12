#!/usr/bin/env bash
set -euo pipefail
systemctl --user stop isaacsim-a100-resident.service isaacsim-a100-generic.service isaacsim-a100-render.service
