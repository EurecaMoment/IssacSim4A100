"""Convert local URDF/MJCF to USD with the verified, non-RTX importer path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from a100sim.app import create_app


def check_physics(app: Any, path: Path) -> dict[str, Any]:
    """Initialize the imported articulation on CUDA and verify a driven joint target."""
    import numpy as np
    import omni.timeline
    import omni.usd
    import warp as wp
    from isaacsim.core.simulation_manager import SimulationManager
    from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

    context = omni.usd.get_context()
    imported_stage = Usd.Stage.Open(str(path))
    context.new_stage()
    stage = context.get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(imported_stage))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(imported_stage))
    root = UsdGeom.Xform.Define(stage, "/Imported").GetPrim()
    root.GetReferences().AddReference(str(path))
    stage.SetDefaultPrim(root)
    roots = [str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.ArticulationRootAPI)]
    if len(roots) != 1:
        raise ValueError("The physics smoke check requires exactly one articulation")
    scenes = [prim for prim in stage.Traverse() if prim.IsA(UsdPhysics.Scene)]
    if not scenes:
        scene = UsdPhysics.Scene.Define(stage, "/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        scenes = [scene.GetPrim()]
    for prim in scenes:
        settings = PhysxSchema.PhysxSceneAPI.Apply(prim)
        settings.CreateEnableGPUDynamicsAttr(True)
        settings.CreateBroadphaseTypeAttr("GPU")
    SimulationManager.set_backend("warp")
    SimulationManager.setup_simulation(dt=1 / 120, device="cuda:0")
    timeline = omni.timeline.get_timeline_interface()
    timeline.play()
    try:
        SimulationManager.initialize_physics()
        view = SimulationManager.get_physics_simulation_view()
        arm = view.create_articulation_view(roots[0])
        if not str(view.device).startswith("cuda") or arm.count != 1 or arm.max_dofs != 1:
            raise ValueError("The physics smoke check requires one CUDA articulation with one driven DOF")
        indices = wp.array([0], dtype=wp.uint32, device=view.device)
        arm.set_dof_position_targets(wp.array([[0.3]], dtype=wp.float32, device=view.device), indices)
        SimulationManager.step(steps=360)
        position = arm.get_dof_positions().numpy()
        if not np.isfinite(position).all() or not np.allclose(position, [[0.3]], atol=0.03):
            raise RuntimeError(f"Imported robot did not track the 0.3 rad target: {position.tolist()}")
        return {"device": str(view.device), "target_rad": 0.3, "positions_rad": position.tolist(), "steps": 360}
    finally:
        timeline.stop()
        SimulationManager.invalidate_physics()


def main() -> None:
    """Import local files; the optional motion smoke check is for the one-DOF fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--physics-check", action="store_true", help="For the supplied one-DOF fixtures only")
    arguments = parser.parse_args()
    source, output = arguments.source.resolve(), arguments.output.resolve()
    if not source.is_file() or source.suffix.lower() not in (".urdf", ".xml", ".mjcf"):
        parser.error("source must be an existing local URDF or MJCF XML file")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"source_format": source.suffix.lower(), "success": False}
    app = create_app()
    try:
        from pxr import Usd, UsdPhysics

        if source.suffix.lower() == ".urdf":
            from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig

            configuration = URDFImporterConfig(
                urdf_path=str(source),
                usd_path=str(output),
                fix_base=True,
                joint_drive_type="force",
                joint_target_type="position",
                override_joint_stiffness=150.0,
                override_joint_damping=15.0,
                run_asset_transformer=False,
                run_multi_physics_conversion=False,
            )
            path = Path(URDFImporter(configuration).import_urdf())
        else:
            from isaacsim.asset.importer.mjcf import MJCFImporter, MJCFImporterConfig

            configuration = MJCFImporterConfig(
                mjcf_path=str(source),
                usd_path=str(output),
                import_scene=True,
                run_asset_transformer=False,
            )
            path = Path(MJCFImporter(configuration).import_mjcf())
        stage = Usd.Stage.Open(str(path))
        report.update(
            usd=str(path),
            joints=[str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdPhysics.Joint)],
            articulations=[
                str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.ArticulationRootAPI)
            ],
            asset_transformer=False,
        )
        if source.suffix.lower() == ".urdf":
            report["urdf_multi_physics_conversion"] = False
        if arguments.physics_check:
            report["physics"] = check_physics(app, path)
        report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "import-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        # Optional Kit fast shutdown may force exit 0; also inspect report.success.
        app.close()


if __name__ == "__main__":
    main()
