#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
export A100SIM_FEATURES_ROOT="${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"
ros="$A100SIM_FEATURES_ROOT/packages/isaacsim/exts/isaacsim.ros2.core/jazzy"
[[ -d "$ros/rclpy/rclpy" ]] || { echo "Install the Isaac Sim ROS 2 wheel first" >&2; exit 2; }
export A100SIM_EXTRA_PYTHONPATH="$ros/rclpy${A100SIM_EXTRA_PYTHONPATH:+:$A100SIM_EXTRA_PYTHONPATH}"
export A100SIM_EXTRA_LD_LIBRARY_PATH="$ros/lib${A100SIM_EXTRA_LD_LIBRARY_PATH:+:$A100SIM_EXTRA_LD_LIBRARY_PATH}"
export AMENT_PREFIX_PATH="$ros${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export ROS_DISTRO=jazzy ROS_VERSION=2
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-80}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
exec /bin/bash "$A100SIM_CODE_ROOT/run-features.sh" "$@"
