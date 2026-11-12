"""Import a real SAM3D asset into Isaac Sim; render its GPU-simulated USD pose.

The A100 CUDA renderer replaces RTX here. Images are captured from the live
Isaac USD stage, including vertex colors, camera pose and the physics pose.
"""
import argparse
import json
import os
import time
from pathlib import Path

from a100sim.app import create_app, Physics

parser = argparse.ArgumentParser()
parser.add_argument("asset", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--size", type=int, default=512)
parser.add_argument("--spp", type=int, default=64)
args = parser.parse_args()
args.asset, args.output = args.asset.resolve(), args.output.resolve()
args.output.mkdir(parents=True, exist_ok=True)
t0 = time.perf_counter()
app = create_app()
startup = time.perf_counter() - t0

import numpy as np
import omni.usd
from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdLux, Vt, Sdf

context = omni.usd.get_context()
context.new_stage()
stage = context.get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1)
world = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(world.GetPrim())
body = UsdGeom.Xform.Define(stage, "/World/Plush")
body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1.4))
UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(1)

with np.load(args.asset / "mesh.npz", allow_pickle=False) as source:
    vertices = source["vertices"].copy()
    faces = source["faces"].copy()
    normals = source["normals"].copy()
    colors = source["colors"].copy()
colors = np.where(colors <= .04045, colors / 12.92, ((colors + .055) / 1.055) ** 2.4)
visual = UsdGeom.Mesh.Define(stage, "/World/Plush/Visual")
visual.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices))
visual.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(faces), 3, np.int32)))
visual.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces.reshape(-1).astype(np.int32)))
visual.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(normals))
visual.SetNormalsInterpolation("vertex")
visual.CreateSubdivisionSchemeAttr("none")
visual.CreateDisplayColorPrimvar("vertex").Set(Vt.Vec3fArray.FromNumpy(colors.astype(np.float32)))
# Use a cooked convex hull for a dynamic rigid body; the full mesh stays visible.
UsdPhysics.CollisionAPI.Apply(visual.GetPrim())
UsdPhysics.MeshCollisionAPI.Apply(visual.GetPrim()).CreateApproximationAttr("convexHull")
visual.GetPrim().CreateAttribute("a100sim:gaussianAsset", Sdf.ValueTypeNames.Asset).Set(
    Sdf.AssetPath(str(args.asset / "object.ply")))

ground = UsdGeom.Cube.Define(stage, "/World/Ground")
ground.CreateSizeAttr(1)
ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.1))
ground.AddScaleOp().Set(Gf.Vec3f(100, 100, .2))
UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
camera = UsdGeom.Camera.Define(stage, "/World/Camera")
camera.CreateFocalLengthAttr(35)
camera.CreateHorizontalApertureAttr(2 * 35 * np.tan(np.deg2rad(34) / 2))
target = np.array([.2, .2, .95])
azimuth, elevation = np.deg2rad(35), np.deg2rad(12)
origin = target + 4.8 * np.array([np.sin(azimuth) * np.cos(elevation),
                                -np.cos(azimuth) * np.cos(elevation), np.sin(elevation)])
def orient(prim, eye, at):
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*at), Gf.Vec3d(0, 0, 1))
    UsdGeom.Xformable(prim).AddTransformOp().Set(view.GetInverse())
orient(camera.GetPrim(), origin, target)
dome = UsdLux.DomeLight.Define(stage, "/World/Dome")
dome.CreateColorAttr(Gf.Vec3f(.45, .48, .52))
dome.CreateIntensityAttr(1)
lamp = UsdLux.RectLight.Define(stage, "/World/KeyLight")
lamp.CreateWidthAttr(3.6)
lamp.CreateHeightAttr(3.6)
lamp.CreateIntensityAttr(5)
orient(lamp.GetPrim(), [-2, -3, 4], [0, 0, .5])
physics = Physics(stage)
rigids = physics.view.create_rigid_body_view("/World/Plush")
assert rigids.count == 1, f"missing Isaac rigid body: {rigids.count}"
print("ISAAC_GPU_READY", str(physics.view.device), "startup_seconds", startup, flush=True)

def pose_matrix(pose):
    position = Gf.Vec3d(*[float(x) for x in pose[:3]])
    qx, qy, qz, qw = [float(x) for x in pose[3:7]]
    matrix = Gf.Matrix4d().SetRotate(Gf.Quatd(qw, Gf.Vec3d(qx, qy, qz)))
    matrix.SetTranslateOnly(position)
    return matrix

# Build the renderer's asset from the live USD stage, rather than the original GLB.
captured = args.output / "usd_asset"
captured.mkdir(exist_ok=True)
np.savez(captured / "mesh.npz", vertices=np.asarray(visual.GetPointsAttr().Get(), np.float32),
         faces=np.asarray(visual.GetFaceVertexIndicesAttr().Get(), np.uint32).reshape(-1, 3),
         normals=np.asarray(visual.GetNormalsAttr().Get(), np.float32),
         colors=np.asarray(visual.GetDisplayColorPrimvar().Get(), np.float32), colors_linear=True)
(captured / "object.ply").symlink_to(args.asset / "object.ply") if not (captured / "object.ply").exists() else None

from a100sim.usd_renderer import render_stage
report = {"isaac_sim": "6.2.0", "physical_gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
          "physics_device": str(physics.view.device), "rigid_bodies": rigids.count,
          "mesh_triangles": len(faces), "renderer": "A100 CUDA replacement (Mitsuba/Dr.Jit)",
          "native_rtx": False, "cpu_fallback": False,
          "collision": "convex hull of SAM3D mesh", "startup_seconds": startup,
          "captures": []}
try:
    last_step = 0
    for step in (1, 60, 180):
        tick = time.perf_counter()
        physics.step(step - last_step)
        step_seconds = time.perf_counter() - tick
        last_step = step
        pose = rigids.get_transforms().numpy()[0].tolist()
        # PhysX tensors are authoritative. Copy the GPU pose into the USD stage
        # for this capture so the external A100 renderer reads the live result.
        body.ClearXformOpOrder()
        body.AddTransformOp().Set(pose_matrix(pose))
        matrix = np.asarray(body.ComputeLocalToWorldTransform(Usd.TimeCode.Default()),
                            np.float64).T.copy()
        assert np.allclose(matrix[:3, 3], pose[:3], atol=.005), (matrix.tolist(), pose)
        assert np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-5)
        capture = {"step": step, "simulation_seconds": step / 120, "physics_seconds": step_seconds,
                   "gpu_pose_xyzw": pose, "usd_matrix_column_major": matrix.tolist(), "renders": []}
        stage.GetRootLayer().Export(str(args.output / f"isaac-stage-{step:03d}.usdc"))
        for kind in ("mesh", "gaussian"):
            path = args.output / f"isaac-{kind}-{step:03d}.png"
            result = render_stage(stage, "/World/Camera", kind, path,
                                  size=args.size, spp=args.spp)
            capture["renders"].append(result)
        report["captures"].append(capture)
        (args.output / "isaac_report.json").write_text(json.dumps(report, indent=2))
        print("ISAAC_CAPTURE", json.dumps(capture), flush=True)
    assert report["captures"][0]["gpu_pose_xyzw"][2] > report["captures"][-1]["gpu_pose_xyzw"][2]
    report["status"] = "passed"
    (args.output / "isaac_report.json").write_text(json.dumps(report, indent=2))
    print("ISAAC_SAM3D_PASSED", json.dumps(report), flush=True)
finally:
    physics.close()
    app.close()
