"""Record enabled Kit extensions and probe the headless Gain Tuner core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from a100sim.app import create_app


def main() -> None:
    """Inspect a separate CUDA world; importing a package alone is not a feature test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report: dict[str, Any] = {}
    app = create_app()
    world = None
    try:
        import omni.kit.app
        from a100sim.robotics import RoboticsWorld
        from isaacsim.robot_setup.gain_tuner import GainTuner
        from usd.schema.isaac import robot_schema

        manager = omni.kit.app.get_app().get_extension_manager()
        enabled = [item["id"] for item in manager.get_extensions() if item.get("enabled")]
        report["enabled_extensions"] = sorted(enabled)
        report["native_rtx_enabled"] = bool(manager.get_enabled_extension_id("omni.hydra.rtx"))
        world = RoboticsWorld()
        # Gain Tuner 6.2 discovers joints through the modern robot-schema relationships.
        robot_schema.ApplyRobotAPI(world.stage.GetPrimAtPath("/World/Arm"))
        tuner = GainTuner()
        tuner.setup("/World/Arm")
        tuner.compute_joints_accumulated_inertia()
        entries = tuner.get_joint_entries()
        report["gain_tuner"] = {
            "joint_count": len(entries),
            "joints": [
                {
                    "name": entry.display_name,
                    "index": entry.dof_index,
                    "inertia": tuner.get_joint_accumulated_inertia(entry.joint),
                }
                for entry in entries
            ],
            "scope": "headless joint discovery and effective inertia; automatic tuning tests not run",
            "success": len(entries) == 2,
        }
        report["physics_device"] = world.device
        report["success"] = report["gain_tuner"]["success"]
    except Exception as error:
        report.update(success=False, error=f"{type(error).__name__}: {error}")
    finally:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        if world:
            world.close()
        app.close()


if __name__ == "__main__":
    main()
