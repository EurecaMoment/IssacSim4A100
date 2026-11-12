"""Verify Newton's native CUDA tensor API and MuJoCo control on a small articulated scene."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from a100sim.app import create_app


def main() -> None:
    """Check numerical results, including tensor writes and actual joint motion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=240)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--line-search-iterations", type=int, default=8)
    args = parser.parse_args()
    if not 120 <= args.steps <= 1200 or not 1 <= args.iterations <= 100 or not 1 <= args.line_search_iterations <= 50:
        parser.error("steps must be 120–1200, iterations 1–100, and line-search-iterations 1–50")
    args.output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"success": False}
    app = create_app()
    try:
        import isaacsim.physics.newton as physics
        import newton
        import numpy as np
        import omni.physics.tensors as tensors
        import omni.timeline
        import omni.usd
        import warp as wp
        from a100sim.newton_compat import install_mujoco_compatibility
        from a100sim.robotics import make_arm, make_box
        from isaacsim.core.simulation_manager import SimulationManager
        from pxr import Gf, UsdGeom, UsdPhysics

        config = physics.MuJoCoSolverConfig()
        # Newton forwards these supported SolverMuJoCo options from the config dictionary.
        config.iterations = args.iterations
        config.ls_iterations = args.line_search_iterations
        physics.configure_newton(physics.NewtonConfig(use_cuda_graph=False, solver_cfg=config))
        context = omni.usd.get_context()
        context.new_stage()
        stage = context.get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        physics.switch_newton_solver("mujoco", scene.GetPrim())
        make_box(stage, "/World/Ground", [0, 0, -0.05], [20, 20, 0.1])
        cube = make_box(stage, "/World/Cube", [0, -1, 1], [0.2, 0.2, 0.2], 1.0)
        # This solver needs a free joint for a dynamic unconnected body.
        UsdPhysics.ArticulationRootAPI.Apply(cube)
        make_arm(stage)
        SimulationManager.set_backend("warp")
        SimulationManager.switch_physics_engine("newton")
        SimulationManager.setup_simulation(dt=1 / 120, device="cuda:0")
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        SimulationManager.initialize_physics()
        current = physics.acquire_stage()
        if not current.state_0.body_q.device.is_cuda or current.solver.use_mujoco_cpu:
            raise RuntimeError("The Newton solver must remain on CUDA")
        view = tensors.create_simulation_view("warp", backend="newton")
        bodies = view.create_rigid_body_view("/World/Cube")
        arm = view.create_articulation_view("/World/Arm")
        indices = wp.array([0], dtype=wp.int32, device="cuda:0")
        written_pose = wp.array([[0, -1, 1.5, 0, 0, 0, 1]], dtype=wp.float32, device="cuda:0")
        bodies.set_transforms(written_pose, indices)
        bodies.set_velocities(wp.zeros((1, 6), dtype=wp.float32, device="cuda:0"), indices)
        np.testing.assert_allclose(bodies.get_transforms().numpy(), written_pose.numpy(), atol=1e-6)
        targets = np.array([[0.3, -0.2]], dtype=np.float32)
        arm.set_dof_position_targets(wp.array(targets, dtype=wp.float32, device="cuda:0"), indices)
        started = time.perf_counter()
        SimulationManager.step(steps=args.steps)
        wp.synchronize()
        elapsed = time.perf_counter() - started
        poses = current.state_0.body_q.numpy()
        cube_index = list(current.model.body_label).index("/World/Cube")
        final_pose = bodies.get_transforms().numpy().copy()
        joints = arm.get_dof_positions().numpy().copy()
        velocities = bodies.get_velocities().numpy().copy()
        masses = bodies.get_masses().numpy().copy()
        inertias = bodies.get_inertias().numpy().copy()
        np.testing.assert_allclose(final_pose[0], poses[cube_index], atol=1e-6)
        np.testing.assert_allclose(joints, targets, atol=0.002)
        np.testing.assert_allclose(masses.reshape(-1), current.model.body_mass.numpy()[[cube_index]], atol=1e-6)
        np.testing.assert_allclose(
            inertias.reshape(1, 3, 3), current.model.body_inertia.numpy()[[cube_index]], atol=1e-6
        )
        if not np.isfinite(poses).all() or not np.isfinite(velocities).all():
            raise RuntimeError("Newton returned nonfinite state")
        if args.steps >= 240 and abs(float(final_pose[0, 2]) - 0.1) > 0.02:
            raise RuntimeError("The falling cube did not settle on the floor")
        report.update(
            success=True,
            newton_version=newton.__version__,
            device=str(current.state_0.body_q.device),
            cpu_fallback=False,
            solver="mujoco",
            compatibility=install_mujoco_compatibility(),
            conditional_graphs=bool(current.solver.mjw_model.opt.graph_conditional),
            outer_cuda_graph=False,
            steps=args.steps,
            iterations=args.iterations,
            line_search_iterations=args.line_search_iterations,
            step_seconds=elapsed,
            cube_pose=final_pose.tolist(),
            joint_positions=joints.tolist(),
            max_joint_error_rad=float(np.abs(joints - targets).max()),
            checks={
                "pose_write_read": True,
                "pose_matches_cuda_state": True,
                "velocity_read": True,
                "mass_matches_cuda_model": True,
                "inertia_matches_cuda_model": True,
                "joint_target_tracking": True,
            },
            scope="Native Newton tensor rigid-body reads/writes and MuJoCo articulation control; other APIs require validation",
        )
        timeline.stop()
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (args.output / "newton-native.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        app.close()


if __name__ == "__main__":
    main()
