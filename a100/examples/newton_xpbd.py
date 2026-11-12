"""Check the Kit Newton XPBD solver using direct CUDA Warp state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from a100sim.app import create_app


def main() -> None:
    """Run a one-second drop and verify the live CUDA state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"success": False}
    app = create_app()
    try:
        import isaacsim.physics.newton as physics
        import newton
        import numpy as np
        import omni.timeline
        import omni.usd
        from a100sim.robotics import make_arm, make_box
        from isaacsim.core.simulation_manager import SimulationManager
        from pxr import Gf, UsdGeom, UsdPhysics

        physics.configure_newton(
            physics.NewtonConfig(
                use_cuda_graph=False,
                solver_cfg=physics.XPBDSolverConfig(iterations=10),
            )
        )
        context = omni.usd.get_context()
        context.new_stage()
        stage = context.get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        # The authored scene otherwise overrides the global solver back to MuJoCo.
        physics.switch_newton_solver("xpbd", scene.GetPrim())
        make_box(stage, "/World/Ground", [0, 0, -0.05], [20, 20, 0.1])
        make_box(stage, "/World/Cube", [0, -1, 1], [0.2, 0.2, 0.2], 1.0)
        make_arm(stage)
        SimulationManager.set_backend("warp")
        SimulationManager.switch_physics_engine("newton")
        SimulationManager.setup_simulation(dt=1 / 120, device="cuda:0")
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        SimulationManager.initialize_physics()
        SimulationManager.step(steps=120)
        current = physics.acquire_stage()
        poses = current.state_0.body_q.numpy()
        cube_index = list(current.model.body_label).index("/World/Cube")
        cube_height = float(poses[cube_index, 2])
        if not current.state_0.body_q.device.is_cuda or not np.isfinite(poses).all():
            raise RuntimeError("Newton state is not finite CUDA data")
        if abs(cube_height - 0.1) > 0.01:
            raise RuntimeError(f"The cube did not settle on the floor: {cube_height}")
        report.update(
            success=True,
            newton_version=newton.__version__,
            solver=current.cfg.solver_cfg.solver_type,
            device=str(current.state_0.body_q.device),
            cube_height_m=cube_height,
            body_paths=list(current.model.body_label),
            body_poses=poses.tolist(),
            scope="Kit Newton XPBD direct Warp state; native tensor control is checked separately by newton_native.py",
            cuda_graph=False,
        )
        timeline.stop()
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (arguments.output / "newton-xpbd.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        app.close()


if __name__ == "__main__":
    main()
