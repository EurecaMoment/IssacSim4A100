#!/usr/bin/env bash
# Source this before narrowing CUDA_VISIBLE_DEVICES to one physical device.
[[ -x "$A100SIM_PYTHON" ]] || { echo "Activate the Conda environment or set A100SIM_PYTHON" >&2; return 2; }
ISAAC_GPU="$(PYTHONPATH="$A100SIM_CODE_ROOT" "$A100SIM_PYTHON" -m a100sim.host)" || return $?
export ISAAC_GPU
