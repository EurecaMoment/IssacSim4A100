"""Run a bounded CUDA XPBD cloth simulation independently of PhysX and RTX."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def simulate_cloth(output: Path, frames: int = 90) -> dict[str, Any]:
    """Simulate a hanging cloth, check fixed/free particles, and export animated USD."""
    import newton
    import numpy as np
    import warp as wp
    from pxr import Gf, Usd, UsdGeom

    if not 1 <= frames <= 300:
        raise ValueError("frames must be between 1 and 300")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with wp.ScopedDevice("cuda:0"):
        builder = newton.ModelBuilder()
        builder.add_ground_plane()
        builder.add_cloth_grid(
            pos=wp.vec3(0, 0, 1.8),
            rot=wp.quat_identity(),
            vel=wp.vec3(0),
            dim_x=16,
            dim_y=12,
            cell_x=0.06,
            cell_y=0.06,
            mass=0.05,
            fix_left=True,
            edge_ke=10.0,
            edge_kd=0.0,
            particle_radius=0.015,
            add_springs=True,
            spring_ke=1000.0,
            spring_kd=1.0,
        )
        model = builder.finalize(device="cuda:0")
        if not model.device.is_cuda:
            raise RuntimeError("Newton cloth requires CUDA")
        solver = newton.solvers.SolverXPBD(model, iterations=10)
        state, next_state = model.state(), model.state()
        control = model.control()
        collision = newton.CollisionPipeline(model)
        contacts = collision.contacts()
        initial = state.particle_q.numpy().copy()
        fixed = model.particle_mass.numpy() == 0
        history = [initial]
        for _ in range(frames):
            for _ in range(8):
                state.clear_forces()
                collision.collide(state, contacts)
                solver.step(state, next_state, control, contacts, 1 / 480)
                state, next_state = next_state, state
            history.append(state.particle_q.numpy().copy())
        final = history[-1]
        if not np.isfinite(final).all() or final[:, 2].min() < -0.03:
            raise RuntimeError("Cloth state is nonfinite or passed through the floor")
        anchor_error = float(np.abs(final[fixed] - initial[fixed]).max())
        displacement = float(np.linalg.norm(final[~fixed] - initial[~fixed], axis=1).mean())
        if anchor_error > 1e-5 or (frames >= 60 and displacement < 0.05):
            raise RuntimeError("Cloth anchors or free-particle motion failed validation")
        triangles = model.tri_indices.numpy().reshape(-1, 3)
        np.savez_compressed(
            output / "cloth.npz", positions=np.asarray(history), triangles=triangles, fixed=fixed, frame_dt=1 / 60
        )
        stage = Usd.Stage.CreateNew(str(output / "cloth.usdc"))
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        root = UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(root.GetPrim())
        mesh = UsdGeom.Mesh.Define(stage, "/World/Cloth")
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateFaceVertexCountsAttr([3] * len(triangles))
        mesh.CreateFaceVertexIndicesAttr(triangles.reshape(-1).tolist())
        mesh.CreateDisplayColorAttr([Gf.Vec3f(0.12, 0.4, 0.8)])
        points = mesh.CreatePointsAttr()
        points.Set(final.tolist())
        for frame, positions in enumerate(history):
            points.Set(positions.tolist(), Usd.TimeCode(frame))
        stage.SetStartTimeCode(0)
        stage.SetEndTimeCode(frames)
        stage.SetTimeCodesPerSecond(60)
        stage.GetRootLayer().Save()
        report = {
            "success": True,
            "newton_version": newton.__version__,
            "solver": "XPBD",
            "scope": "standalone Newton Warp model; not the Kit Newton tensor backend",
            "device": str(model.device),
            "native_rtx": False,
            "cpu_fallback": False,
            "particles": len(initial),
            "triangles": len(triangles),
            "fixed_particles": int(fixed.sum()),
            "frames": frames,
            "simulation_seconds": frames / 60,
            "anchor_error_m": anchor_error,
            "mean_free_displacement_m": displacement,
            "minimum_height_m": float(final[:, 2].min()),
            "execution_seconds": time.perf_counter() - started,
            "scene": str(output / "cloth.usdc"),
            "trajectory": str(output / "cloth.npz"),
        }
        (output / "cloth.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def main() -> None:
    """Initialize the portable Kit/Warp runtime and run the exported example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=90)
    arguments = parser.parse_args()
    from .app import create_app

    app = create_app()
    try:
        print(json.dumps(simulate_cloth(arguments.output, arguments.frames), indent=2), flush=True)
    finally:
        app.close()


if __name__ == "__main__":
    main()
