"""Persistent CUDA renderer. No renderer or ray-intersection CPU fallback."""
import math
import time
from pathlib import Path

import numpy as np
import drjit as dr
import mitsuba as mi

mi.set_variant("cuda_ad_rgb")
from . import hybrid_integrator  # Registers mixed mesh / Gaussian path tracing.
from .gaussian import load_gaussians, shape_dict


def linear_rgb(value):
    value = np.asarray(value, np.float32)
    return np.where(value <= .04045, value / 12.92,
                    ((value + .055) / 1.055) ** 2.4).astype(np.float32)


def load_mesh(path, matrix=None):
    with np.load(path, allow_pickle=False) as data:
        vertices, faces = data["vertices"], data["faces"]
        normals, colors = data["normals"], data["colors"]
        colors = colors if "colors_linear" in data else linear_rgb(colors)
    props = mi.Properties()
    props["bsdf"] = mi.load_dict({"type": "twosided", "material": {
        "type": "principled", "roughness": .85,
        "base_color": {"type": "mesh_attribute", "name": "vertex_color"}}})
    mesh = mi.Mesh("sam3d_mesh", vertex_count=len(vertices), face_count=len(faces),
                   props=props, has_vertex_normals=True)
    mesh.add_attribute("vertex_color", 3, colors.reshape(-1))
    if matrix is not None:
        vertices = vertices @ np.asarray(matrix[:3, :3]).T + matrix[:3, 3]
        normals = normals @ np.linalg.inv(matrix[:3, :3])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-10)
    params = mi.traverse(mesh)
    params["vertex_positions"] = vertices.reshape(-1)
    params["vertex_normals"] = normals.reshape(-1)
    params["faces"] = faces.reshape(-1)
    params.update()
    return mesh


def camera_pose(azimuth=0, elevation=12, distance=2.15, target=None):
    azimuth, elevation = math.radians(azimuth), math.radians(elevation)
    target = np.array(target if target is not None else [0, 0, 0], float)
    origin = target + distance * np.array([
        math.sin(azimuth) * math.cos(elevation),
        -math.cos(azimuth) * math.cos(elevation), math.sin(elevation)])
    return mi.ScalarTransform4f.look_at(origin=origin, target=target, up=[0, 0, 1])


class Renderer:
    def __init__(self, asset, kind="mesh", width=512, height=512, max_depth=6,
                 max_events=512, floor=True, matrix=None, ground_z=None, target=None):
        if mi.variant() != "cuda_ad_rgb" or not dr.has_backend(dr.JitBackend.CUDA):
            raise RuntimeError("CUDA rendering is mandatory")
        self.asset, self.kind = Path(asset), kind
        self.width, self.height = width, height
        self.target = target
        scene = {
            "type": "scene",
            "integrator": {"type": "a100_hybrid_path" if kind == "gaussian" else "path",
                           "max_depth": max_depth},
            "sensor": {"type": "perspective", "fov": 34, "to_world": camera_pose(),
                       "film": {"type": "hdrfilm", "width": width, "height": height,
                                "pixel_format": "rgb", "rfilter": {"type": "box"}}},
            "environment": {"type": "constant", "radiance": {
                "type": "rgb", "value": [.45, .48, .52]}},
        }
        if kind == "gaussian":
            data = load_gaussians(self.asset / "object.ply")
            self.elements = len(data["means"])
            scene["object"] = shape_dict(data, matrix=matrix)
            scene["integrator"]["max_events"] = max_events
            floor_z = float(np.quantile(data["means"][:, 2], .001)) - .01
        elif kind == "mesh":
            scene["object"] = load_mesh(self.asset / "mesh.npz", matrix=matrix)
            with np.load(self.asset / "mesh.npz", allow_pickle=False) as data:
                self.elements = len(data["faces"])
                floor_z = float(data["vertices"][:, 2].min()) - .005
        else:
            raise ValueError("kind must be mesh or gaussian")
        if ground_z is not None:
            floor_z = ground_z
        if floor:
            scene["floor"] = {
                "type": "rectangle", "to_world": mi.ScalarTransform4f.translate([0, 0, floor_z])
                @ mi.ScalarTransform4f.scale(100), "bsdf": {
                    "type": "diffuse", "reflectance": {"type": "rgb", "value": [.32, .34, .37]}}}
            scene["key_light"] = {
                "type": "rectangle", "to_world": mi.ScalarTransform4f.look_at(
                    origin=[-2, -3, 4], target=[0, 0, 0], up=[0, 0, 1])
                @ mi.ScalarTransform4f.scale(1.8),
                "emitter": {"type": "area", "radiance": {"type": "rgb", "value": [5, 5, 5]}}}
        self.scene = mi.load_dict(scene)
        self.params = mi.traverse(self.scene)
        self.last = None

    def render(self, spp=16, seed=42, azimuth=0, elevation=12, distance=2.15, output=None):
        t = time.perf_counter()
        self.params["sensor.to_world"] = camera_pose(azimuth, elevation, distance, self.target)
        self.params.update()
        image = mi.render(self.scene, spp=spp, seed=seed)
        dr.eval(image)
        dr.sync_thread()
        elapsed = time.perf_counter() - t
        self.last = image
        if output is not None:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            mi.util.write_bitmap(str(output), image, write_async=False)
        return {"backend": "cuda_ad_rgb", "kind": self.kind, "elements": self.elements,
                "resolution": [self.width, self.height], "spp": spp, "seed": seed,
                "azimuth": azimuth, "render_seconds": elapsed, "cpu_fallback": False}
