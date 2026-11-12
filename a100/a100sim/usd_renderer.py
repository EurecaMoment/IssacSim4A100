"""Render a live Isaac USD stage on CUDA without the RTX renderer.

This adapter covers meshes with displayColor, cubes, perspective cameras,
constant dome lights and rectangular lights. A mesh may provide a standard
3DGS PLY via a100sim:gaussianAsset. Unsupported render schemas raise an error
instead of silently dropping them. This is an external renderer adapter, not
a replacement implementation of Isaac Replicator or RTX sensor extensions.
"""
import math
import time
from pathlib import Path

import numpy as np
import drjit as dr
import mitsuba as mi
from pxr import Usd, UsdGeom, UsdLux, UsdShade

mi.set_variant("cuda_ad_rgb")
from . import hybrid_integrator
from .gaussian import load_gaussians, shape_dict


def world_matrix(prim, cache):
    return np.asarray(cache.GetLocalToWorldTransform(prim), dtype=np.float64).T.copy()


def surface_material(prim, default=(.32, .34, .37), vertex_color=False):
    display = UsdGeom.Gprim(prim).GetDisplayColorAttr().Get() if prim.IsA(UsdGeom.Gprim) else None
    if display and not vertex_color:
        default = tuple(display[0])
    color = {"type": "mesh_attribute", "name": "vertex_color"} if vertex_color else {
        "type": "rgb", "value": list(default)}
    roughness, metallic = .85, 0.
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
        shader = material.ComputeSurfaceSource()[0]
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            raise ValueError(f"Unsupported USD material on {prim.GetPath()}")
        for name in ("diffuseColor", "roughness", "metallic"):
            value = shader.GetInput(name)
            if value and value.HasConnectedSource():
                raise ValueError(f"USD texture network is not yet supported: {prim.GetPath()}/{name}")
            if value and value.Get() is not None:
                if name == "diffuseColor":
                    color = {"type": "rgb", "value": list(value.Get())}
                elif name == "roughness":
                    roughness = float(value.Get())
                elif name == "metallic":
                    metallic = float(value.Get())
    return mi.load_dict({"type": "twosided", "material": {"type": "principled",
        "roughness": roughness, "metallic": metallic, "base_color": color}})


def mesh_shape(prim, matrix):
    mesh = UsdGeom.Mesh(prim)
    if mesh.GetSubdivisionSchemeAttr().Get() not in (None, "none"):
        raise ValueError(f"Subdivision surfaces require tessellation: {prim.GetPath()}")
    points = np.asarray(mesh.GetPointsAttr().Get(), np.float32)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), np.int32)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), np.uint32)
    if not len(points) or np.any(counts != 3):
        raise ValueError(f"USD mesh must be triangulated: {prim.GetPath()}")
    normals = mesh.GetNormalsAttr().Get()
    normals = np.asarray(normals, np.float32) if normals else None
    if normals is not None and (mesh.GetNormalsInterpolation() != "vertex" or len(normals) != len(points)):
        raise ValueError(f"USD normals must have vertex interpolation: {prim.GetPath()}")
    colors = mesh.GetDisplayColorPrimvar()
    rgb = np.asarray(colors.ComputeFlattened(), np.float32) if colors and colors.Get() else None
    if rgb is not None and len(rgb) != len(points):
        if len(rgb) == 1:
            rgb = np.repeat(rgb, len(points), axis=0)
        else:
            raise ValueError(f"USD displayColor must have vertex interpolation: {prim.GetPath()}")
    props = mi.Properties()
    props["bsdf"] = surface_material(prim, vertex_color=rgb is not None)
    shape = mi.Mesh(str(prim.GetPath()), vertex_count=len(points), face_count=len(counts),
                    props=props, has_vertex_normals=normals is not None)
    if rgb is not None:
        shape.add_attribute("vertex_color", 3, rgb.reshape(-1))
    params = mi.traverse(shape)
    params["vertex_positions"] = (points @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32).reshape(-1)
    params["faces"] = indices.reshape(-1)
    if normals is not None:
        normals = normals @ np.linalg.inv(matrix[:3, :3])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-10)
        params["vertex_normals"] = normals.astype(np.float32).reshape(-1)
    params.update()
    return shape


def render_stage(stage, camera_path, mode, output, size=512, spp=64, seed=42, gaussian_gain=1.0):
    if mi.variant() != "cuda_ad_rgb" or not dr.has_backend(dr.JitBackend.CUDA):
        raise RuntimeError("A100 CUDA backend is required")
    if mode not in ("mesh", "gaussian"):
        raise ValueError("mode must be mesh or gaussian")
    started = time.perf_counter()
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    camera = UsdGeom.Camera.Get(stage, camera_path)
    if not camera or camera.GetProjectionAttr().Get() != "perspective":
        raise ValueError("a perspective USD camera is required")
    camera_world = world_matrix(camera.GetPrim(), cache)
    origin = camera_world[:3, 3]
    fov = math.degrees(2 * math.atan(float(camera.GetHorizontalApertureAttr().Get()) /
                                   (2 * float(camera.GetFocalLengthAttr().Get()))))
    scene = {"type": "scene", "integrator": {"type": "a100_hybrid_path" if mode == "gaussian"
             else "path", "max_depth": 6}, "sensor": {
        "type": "perspective", "fov": fov, "fov_axis": "x", "to_world":
        mi.ScalarTransform4f.look_at(origin=origin, target=origin - camera_world[:3, 2],
                                    up=camera_world[:3, 1]), "film": {
            "type": "hdrfilm", "width": size, "height": size, "pixel_format": "rgb",
            "rfilter": {"type": "box"}}}}
    if mode == "gaussian":
        scene["integrator"]["max_events"] = 512
        scene["integrator"]["gaussian_gain"] = float(gaussian_gain)
    sources, unsupported = [], []
    for prim in stage.Traverse():
        if not prim.IsActive():
            continue
        imageable = UsdGeom.Imageable(prim)
        if imageable and imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        key = str(prim.GetPath()).replace("/", "_")
        matrix = world_matrix(prim, cache) if prim.IsA(UsdGeom.Xformable) else np.eye(4)
        if prim.IsA(UsdGeom.Mesh):
            gaussian = prim.GetAttribute("a100sim:gaussianAsset")
            if mode == "gaussian" and gaussian and gaussian.Get():
                value = gaussian.Get()
                path = value.resolvedPath or value.path
                data = load_gaussians(path)
                scene[key] = shape_dict(data, matrix=matrix)
                source = {"prim": str(prim.GetPath()), "kind": "gaussian", "asset": path,
                          "gaussians": len(data["means"]), "world_matrix": matrix.tolist()}
            else:
                scene[key] = mesh_shape(prim, matrix)
                source = {"prim": str(prim.GetPath()), "kind": "mesh",
                          "triangles": len(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get()),
                          "world_matrix": matrix.tolist()}
            sources.append(source)
        elif prim.IsA(UsdGeom.Sphere):
            radius = float(UsdGeom.Sphere(prim).GetRadiusAttr().Get())
            scene[key] = {"type": "sphere", "to_world": mi.ScalarTransform4f(matrix)
                          @ mi.ScalarTransform4f.scale(radius), "bsdf": surface_material(prim)}
            sources.append({"prim": str(prim.GetPath()), "kind": "sphere", "world_matrix": matrix.tolist()})
        elif prim.IsA(UsdGeom.Cube):
            size_value = float(UsdGeom.Cube(prim).GetSizeAttr().Get())
            scene[key] = {"type": "cube", "to_world": mi.ScalarTransform4f(matrix)
                          @ mi.ScalarTransform4f.scale(size_value / 2), "bsdf": surface_material(prim)}
            sources.append({"prim": str(prim.GetPath()), "kind": "cube", "world_matrix": matrix.tolist()})
        elif prim.IsA(UsdLux.DomeLight) or prim.IsA(UsdLux.RectLight):
            light = UsdLux.LightAPI(prim)
            rgb = np.asarray(light.GetColorAttr().Get(), np.float64)
            rgb *= float(light.GetIntensityAttr().Get()) * 2 ** float(light.GetExposureAttr().Get())
            if prim.IsA(UsdLux.DomeLight):
                dome = UsdLux.DomeLight(prim)
                if dome.GetTextureFileAttr().Get():
                    raise ValueError("Textured dome lights require an envmap adapter")
                scene[key] = {"type": "constant", "radiance": {"type": "rgb", "value": rgb.tolist()}}
            else:
                rect = UsdLux.RectLight(prim)
                transform = matrix @ np.diag([float(rect.GetWidthAttr().Get()) / 2,
                                              -float(rect.GetHeightAttr().Get()) / 2, -1, 1])
                scene[key] = {"type": "rectangle", "to_world": mi.ScalarTransform4f(transform),
                    "emitter": {"type": "area", "radiance": {"type": "rgb", "value": rgb.tolist()}}}
            sources.append({"prim": str(prim.GetPath()), "kind": prim.GetTypeName(), "radiance": rgb.tolist()})
        elif prim.IsA(UsdGeom.Gprim) or prim.HasAPI(UsdLux.LightAPI):
            unsupported.append(f"{prim.GetPath()} ({prim.GetTypeName()})")
    if unsupported:
        raise ValueError("Unsupported USD rendering schemas: " + ", ".join(unsupported))
    scene = mi.load_dict(scene)
    setup_seconds = time.perf_counter() - started
    tick = time.perf_counter()
    image = mi.render(scene, spp=spp, seed=seed)
    dr.eval(image)
    dr.sync_thread()
    render_seconds = time.perf_counter() - tick
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    mi.util.write_bitmap(str(output), image, write_async=False)
    return {"mode": mode, "renderer": "A100 CUDA USD adapter", "native_rtx": False,
            "cpu_fallback": False, "resolution": [size, size], "spp": spp,
            "gaussian_gain": float(gaussian_gain) if mode == "gaussian" else None,
            "setup_seconds": setup_seconds, "render_seconds": render_seconds,
            "image": output.name, "camera": camera_path, "camera_world": camera_world.tolist(),
            "fov_degrees": fov, "usd_sources": sources}
