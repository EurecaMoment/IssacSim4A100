"""Persistent Isaac Sim 6.2 GPU PhysX service, with optional CUDA USD capture.

HTTP handlers only validate and enqueue data. Kit and PhysX always run on the
main thread. Requests contain scene data, never shell commands or Python code.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .service_queue import JobQueue, asset_directory, finite, integer, persist, start_http


def vector(value, name, length=3, low=-10000, high=10000):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} needs {length} numbers")
    return [finite(v, name, low, high) for v in value]


class PhysicsQueue(JobQueue):
    def prepare(self, value):
        unknown = set(value) - {"bodies", "steps", "dt", "gravity", "ground", "render"}
        if unknown:
            raise ValueError("Unsupported simulation fields: " + ", ".join(sorted(unknown)))
        bodies = value.get("bodies")
        if bodies is None:
            bodies = [{"id": f"box-{i}", "position": [(i % 8 - 3.5) * .4,
                       (i // 8 - 3.5) * .4, 2 + i * .005]} for i in range(64)]
        if not isinstance(bodies, list) or not 1 <= len(bodies) <= 256:
            raise ValueError("bodies must contain 1 to 256 rigid bodies")
        normalized = []
        ids = set()
        for index, body in enumerate(bodies):
            if not isinstance(body, dict):
                raise ValueError("each body must be an object")
            extra = set(body) - {"id", "shape", "position", "orientation_xyzw",
                                 "size", "radius", "mass", "asset", "color"}
            if extra:
                raise ValueError("Unsupported body fields: " + ", ".join(sorted(extra)))
            shape = body.get("shape", "cube")
            if shape not in {"cube", "sphere", "mesh"}:
                raise ValueError("shape must be cube, sphere or mesh")
            body_id = body.get("id", str(index))
            if not isinstance(body_id, str) or not body_id or body_id in ids:
                raise ValueError("body ids must be unique nonempty strings")
            ids.add(body_id)
            item = {"id": body_id, "shape": shape,
                    "position": vector(body.get("position", [0, 0, 2]), "position"),
                    "orientation_xyzw": vector(body.get("orientation_xyzw", [0, 0, 0, 1]),
                                               "orientation_xyzw", 4, -1, 1),
                    "mass": finite(body.get("mass", 1), "mass", .0001, 100000),
                    "color": vector(body.get("color", [.35, .5, .7]), "color", 3, 0, 1)}
            q = item["orientation_xyzw"]
            norm = sum(x * x for x in q) ** .5
            if norm < 1e-6:
                raise ValueError("orientation_xyzw must be a nonzero quaternion")
            item["orientation_xyzw"] = [x / norm for x in q]
            if shape == "mesh":
                asset = asset_directory(body["asset"])
                if not (asset / "mesh.npz").is_file():
                    raise ValueError("mesh asset needs mesh.npz")
                item["asset"] = str(asset)
            elif shape == "cube":
                size = body.get("size", [.2, .2, .2])
                if isinstance(size, (int, float)):
                    size = [size] * 3
                item["size"] = vector(size, "size", 3, .001, 100)
            else:
                item["radius"] = finite(body.get("radius", .1), "radius", .001, 100)
            normalized.append(item)
        render = value.get("render")
        if render is not None:
            if not isinstance(render, dict):
                raise ValueError("render must be an object")
            extra = set(render) - {"kind", "size", "spp", "seed", "azimuth",
                                  "elevation", "distance", "target", "lighting", "gaussian_gain"}
            if extra:
                raise ValueError("Unsupported render fields: " + ", ".join(sorted(extra)))
            if render.get("kind", "mesh") not in {"mesh", "gaussian"}:
                raise ValueError("render.kind must be mesh or gaussian")
            lighting = render.get("lighting")
            if lighting is not None:
                if not isinstance(lighting, dict):
                    raise ValueError("render.lighting must be an object")
                unknown = set(lighting) - {"dome", "key", "fills"}
                if unknown:
                    raise ValueError("Unsupported lighting fields: " + ", ".join(sorted(unknown)))
                for name in ("dome", "key"):
                    spec = lighting.get(name, {})
                    if not isinstance(spec, dict):
                        raise ValueError(f"render.lighting.{name} must be an object")
                    bad = set(spec) - {"color", "intensity", "exposure", "width", "height",
                                       "origin", "target"}
                    if bad:
                        raise ValueError(f"Unsupported render.lighting.{name} fields: "
                                         + ", ".join(sorted(bad)))
                    if "color" in spec:
                        vector(spec["color"], f"lighting.{name}.color", 3, 0, 10)
                    for field in ("intensity", "exposure", "width", "height"):
                        if field in spec:
                            finite(spec[field], f"lighting.{name}.{field}", 0, 10000)
                    for field in ("origin", "target"):
                        if field in spec:
                            vector(spec[field], f"lighting.{name}.{field}")
                fills = lighting.get("fills", [])
                if not isinstance(fills, list) or len(fills) > 8:
                    raise ValueError("render.lighting.fills needs at most 8 rectangles")
                for spec in fills:
                    if not isinstance(spec, dict):
                        raise ValueError("each fill light must be an object")
                    bad = set(spec) - {"color", "intensity", "exposure", "width", "height",
                                       "origin", "target"}
                    if bad:
                        raise ValueError("Unsupported fill fields: " + ", ".join(sorted(bad)))
                    if "color" in spec:
                        vector(spec["color"], "lighting.fill.color", 3, 0, 10)
                    for field in ("intensity", "width", "height"):
                        if field in spec:
                            finite(spec[field], f"lighting.fill.{field}", 0, 10000)
                    for field in ("origin", "target"):
                        if field in spec:
                            vector(spec[field], "lighting.fill.target")
            render = {"kind": render.get("kind", "mesh"),
                      "size": integer(render.get("size", 256), "render.size", 64, 1024),
                      "spp": integer(render.get("spp", 8), "render.spp", 1, 256),
                      "seed": integer(render.get("seed", 42), "render.seed", 0, 2147483647),
                      "azimuth": finite(render.get("azimuth", 35), "azimuth", -36000, 36000),
                      "elevation": finite(render.get("elevation", 20), "elevation", -80, 80),
                      "distance": finite(render.get("distance", 5), "distance", .1, 1000),
                      "target": vector(render.get("target", [0, 0, .5]), "target"),
                      "gaussian_gain": finite(render.get("gaussian_gain", 1.0),
                                              "gaussian_gain", 0, 10)}
            if lighting is not None:
                render["lighting"] = lighting
        if not isinstance(value.get("ground", True), bool):
            raise ValueError("ground must be boolean")
        return {"bodies": normalized, "steps": integer(value.get("steps", 240), "steps", 1, 1200),
                "dt": finite(value.get("dt", 1 / 120), "dt", .0001, .02),
                "gravity": finite(value.get("gravity", 9.81), "gravity", 0, 100),
                "ground": value.get("ground", True), "render": render}

    def process(self, request, directory):
        import numpy as np
        import omni.usd
        from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdLux, Vt, Sdf
        from .app import Physics

        started = time.perf_counter()
        context = omni.usd.get_context()
        context.new_stage()
        stage = context.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(request["gravity"])
        if request["ground"]:
            ground = UsdGeom.Cube.Define(stage, "/World/Ground")
            ground.CreateSizeAttr(1)
            ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.1))
            ground.AddScaleOp().Set(Gf.Vec3f(200, 200, .2))
            UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
        paths = []
        for index, body in enumerate(request["bodies"]):
            path = f"/World/Bodies/B{index:04d}"
            paths.append(path)
            root = UsdGeom.Xform.Define(stage, path)
            root.AddTranslateOp().Set(Gf.Vec3d(*body["position"]))
            qx, qy, qz, qw = body["orientation_xyzw"]
            root.AddOrientOp().Set(Gf.Quatf(qw, Gf.Vec3f(qx, qy, qz)))
            UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
            UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(body["mass"])
            visual_path = path + "/Visual"
            if body["shape"] == "mesh":
                asset = Path(body["asset"])
                with np.load(asset / "mesh.npz", allow_pickle=False) as data:
                    vertices, faces = data["vertices"], data["faces"]
                    normals, colors = data["normals"], data["colors"]
                    if "colors_linear" not in data:
                        colors = np.where(colors <= .04045, colors / 12.92,
                                          ((colors + .055) / 1.055) ** 2.4)
                visual = UsdGeom.Mesh.Define(stage, visual_path)
                visual.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))
                visual.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(faces), 3, np.int32)))
                visual.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces.reshape(-1).astype(np.int32)))
                visual.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(normals.astype(np.float32)))
                visual.SetNormalsInterpolation("vertex")
                visual.CreateSubdivisionSchemeAttr("none")
                visual.CreateDisplayColorPrimvar("vertex").Set(Vt.Vec3fArray.FromNumpy(colors.astype(np.float32)))
                UsdPhysics.MeshCollisionAPI.Apply(visual.GetPrim()).CreateApproximationAttr("convexHull")
                if (asset / "object.ply").is_file():
                    visual.GetPrim().CreateAttribute("a100sim:gaussianAsset", Sdf.ValueTypeNames.Asset).Set(
                        Sdf.AssetPath(str(asset / "object.ply")))
            elif body["shape"] == "sphere":
                visual = UsdGeom.Sphere.Define(stage, visual_path)
                visual.CreateRadiusAttr(body["radius"])
                visual.CreateDisplayColorAttr([Gf.Vec3f(*body["color"])])
            else:
                visual = UsdGeom.Cube.Define(stage, visual_path)
                visual.CreateSizeAttr(1)
                visual.AddScaleOp().Set(Gf.Vec3f(*body["size"]))
                visual.CreateDisplayColorAttr([Gf.Vec3f(*body["color"])])
            UsdPhysics.CollisionAPI.Apply(visual.GetPrim())
        physics = None
        try:
            physics = Physics(stage)
            rigids = physics.view.create_rigid_body_view("/World/Bodies/B*")
            if rigids.count != len(paths):
                raise RuntimeError(f"Expected {len(paths)} GPU rigid bodies; found {rigids.count}")
            setup_seconds = time.perf_counter() - started
            tick = time.perf_counter()
            physics.step(request["steps"], request["dt"])
            physics_seconds = time.perf_counter() - tick
            poses = rigids.get_transforms().numpy().copy()
            velocities = rigids.get_velocities().numpy().copy()
            actual_paths = paths
            bodies = []
            by_path = {path: body for path, body in zip(paths, request["bodies"])}
            for path, pose, velocity in zip(actual_paths, poses, velocities):
                position = Gf.Vec3d(*[float(v) for v in pose[:3]])
                qx, qy, qz, qw = [float(v) for v in pose[3:7]]
                matrix = Gf.Matrix4d().SetRotate(Gf.Quatd(qw, Gf.Vec3d(qx, qy, qz)))
                matrix.SetTranslateOnly(position)
                xform = UsdGeom.Xformable(stage.GetPrimAtPath(path))
                xform.ClearXformOpOrder()
                xform.AddTransformOp().Set(matrix)
                bodies.append({"id": by_path[path]["id"], "prim_path": path,
                               "pose_xyzw": pose.tolist(), "velocity": velocity.tolist()})
            np.savez(directory / "state.npz", poses_xyzw=poses, velocities=velocities)
            result = {"isaac_sim": "6.2.0", "physics_device": str(physics.view.device),
                      "physical_gpu": self.health()["physical_gpu"], "native_rtx": False,
                      "cpu_fallback": False, "bodies": bodies, "rigid_bodies": len(bodies),
                      "steps": request["steps"], "dt": request["dt"],
                      "simulation_seconds": request["steps"] * request["dt"],
                      "setup_seconds": setup_seconds, "physics_seconds": physics_seconds,
                      "state_npz": str(directory / "state.npz"),
                      "collision": "PhysX primitives; convexHull for dynamic mesh",
                      "scene": str(directory / "scene.usdc")}
            capture = request["render"]
            if capture:
                import math
                camera = UsdGeom.Camera.Define(stage, "/World/Camera")
                camera.CreateFocalLengthAttr(35)
                camera.CreateHorizontalApertureAttr(2 * 35 * math.tan(math.radians(34) / 2))
                az, el = math.radians(capture["azimuth"]), math.radians(capture["elevation"])
                target = np.asarray(capture["target"])
                eye = target + capture["distance"] * np.array([
                    math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
                def orient(prim, eye, target):
                    matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1))
                    UsdGeom.Xformable(prim).AddTransformOp().Set(matrix.GetInverse())
                orient(camera.GetPrim(), eye, target)
                lighting = capture.get("lighting") or {}
                dome_spec, key_spec = lighting.get("dome", {}), lighting.get("key", {})
                dome = UsdLux.DomeLight.Define(stage, "/World/Dome")
                dome.CreateColorAttr(Gf.Vec3f(*dome_spec.get("color", [.45, .48, .52])))
                dome.CreateIntensityAttr(float(dome_spec.get("intensity", 1)))
                if "exposure" in dome_spec:
                    dome.CreateExposureAttr(float(dome_spec["exposure"]))
                lamp = UsdLux.RectLight.Define(stage, "/World/KeyLight")
                lamp.CreateWidthAttr(float(key_spec.get("width", 3.6)))
                lamp.CreateHeightAttr(float(key_spec.get("height", 3.6)))
                lamp.CreateIntensityAttr(float(key_spec.get("intensity", 5)))
                if "color" in key_spec:
                    lamp.CreateColorAttr(Gf.Vec3f(*key_spec["color"]))
                if "exposure" in key_spec:
                    lamp.CreateExposureAttr(float(key_spec["exposure"]))
                orient(lamp.GetPrim(), key_spec.get("origin", [-2, -3, 4]),
                       key_spec.get("target", [0, 0, .5]))
                for fill_index, fill in enumerate(lighting.get("fills", [])):
                    wash = UsdLux.RectLight.Define(stage, f"/World/Fill{fill_index:02d}")
                    wash.CreateWidthAttr(float(fill.get("width", 2.0)))
                    wash.CreateHeightAttr(float(fill.get("height", 2.0)))
                    wash.CreateIntensityAttr(float(fill.get("intensity", 1)))
                    if "color" in fill:
                        wash.CreateColorAttr(Gf.Vec3f(*fill["color"]))
                    if "exposure" in fill:
                        wash.CreateExposureAttr(float(fill["exposure"]))
                    orient(wash.GetPrim(), fill.get("origin", [0, 0, 3]),
                           fill.get("target", [0, 0, 0]))
                from .usd_renderer import render_stage
                image = directory / "render.png"
                result["render"] = render_stage(stage, "/World/Camera", capture["kind"], image,
                                                size=capture["size"], spp=capture["spp"], seed=capture["seed"],
                                                gaussian_gain=float(capture.get("gaussian_gain", 1.0)))
                result["image"] = str(image)
            stage.GetRootLayer().Export(str(directory / "scene.usdc"))
            persist(directory / "simulation.json", result)
            result["simulation_result"] = str(directory / "simulation.json")
            return result
        finally:
            if physics is not None:
                physics.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue-size", type=int, default=64)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18323)
    args = parser.parse_args()
    state = PhysicsQueue(args.output, args.queue_size, "Isaac Sim 6.2 GPU PhysX")
    server = start_http(state, args.host, args.port, "/simulate")
    from .app import create_app
    app = create_app()
    try:
        warm = args.output / "warmup"
        warm.mkdir(parents=True, exist_ok=True)
        state.process(state.prepare({"bodies": [{"id": "warmup"}], "steps": 1}), warm)
        state.details = {"supported_shapes": ["cube", "sphere", "mesh"],
                         "render_kinds": ["mesh", "gaussian"], "max_bodies": 256, "max_steps": 1200}
        state.ready = True
        print("PHYSICS_SERVICE_READY", json.dumps(state.health()), flush=True)
        state.work_forever()
    finally:
        state.ready = False
        server.shutdown()
        app.close()


if __name__ == "__main__":
    main()
