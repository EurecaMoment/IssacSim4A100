"""Capture deterministic primary-surface annotations through the CUDA renderer.

These are custom Mitsuba outputs. They do not invoke RTX sensors or Replicator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def capture_primary_surfaces(
    scene: Any, stage: Any, sources: list[dict[str, Any]], camera_world: Any, prefix: Path
) -> dict[str, Any]:
    """Save metric depth, world normals, and explicit USD instance/semantic IDs.

    Rays use pixel centers without motion blur. Depth is camera-forward Z in
    meters, range is Euclidean distance from the camera center. Misses are zero.
    Semantic classes must be authored on a prim or ancestor with the custom
    ``a100sim:semanticLabel`` string attribute. Unlabeled surfaces use ID zero.
    """
    import drjit as dr
    import mitsuba as mi
    import numpy as np
    from PIL import Image
    from pxr import UsdGeom

    if mi.variant() != "cuda_ad_rgb" or not dr.has_backend(dr.JitBackend.CUDA):
        raise RuntimeError("CUDA is required for annotation ray intersections")
    width, height = [int(value) for value in scene.sensors()[0].film().crop_size()]
    pixels = dr.arange(mi.UInt32, width * height)
    coordinates = mi.Point2f((mi.Float(pixels % width) + 0.5) / width, (mi.Float(pixels // width) + 0.5) / height)
    rays, _ = scene.sensors()[0].sample_ray(0.0, 0.5, coordinates, mi.Point2f(0.5))
    intersections = scene.ray_intersect(rays)
    valid = intersections.is_valid()
    delta = intersections.p - mi.Point3f(camera_world[:3, 3])
    meters = float(UsdGeom.GetStageMetersPerUnit(stage))
    depth = dr.select(valid, dr.dot(delta, mi.Vector3f(-camera_world[:3, 2])) * meters, 0.0)
    distance = dr.select(valid, dr.norm(delta) * meters, 0.0)
    normal = dr.select(valid, intersections.n, mi.Normal3f(0))
    instances = dr.zeros(mi.UInt32, width * height)
    semantics = dr.zeros(mi.UInt32, width * height)
    by_identifier = {item["prim"].replace("/", "_"): item["prim"] for item in sources}
    by_identifier.update({item["prim"]: item["prim"] for item in sources})
    classes = {"unlabeled": 0}
    labels = {}
    for number, shape in enumerate(scene.shapes(), start=1):
        path = by_identifier.get(shape.id())
        if path is None:
            raise RuntimeError(f"Unable to map rendered shape to USD: {shape.id()}")
        prim = stage.GetPrimAtPath(path)
        label = "unlabeled"
        while prim and not prim.IsPseudoRoot():
            attribute = prim.GetAttribute("a100sim:semanticLabel")
            if attribute and attribute.Get():
                label = str(attribute.Get())
                break
            prim = prim.GetParent()
        semantic_id = classes.setdefault(label, len(classes))
        matching = valid & (intersections.shape == mi.ShapePtr(shape))
        instances = dr.select(matching, number, instances)
        semantics = dr.select(matching, semantic_id, semantics)
        labels[str(number)] = {"prim_path": path, "semantic_id": semantic_id, "semantic_label": label}
    dr.eval(depth, distance, normal, instances, semantics, valid)
    dr.sync_thread()
    arrays = {
        "depth_m": np.asarray(depth).reshape(height, width),
        "range_m": np.asarray(distance).reshape(height, width),
        "normal_world": np.asarray(normal).T.reshape(height, width, 3),
        "instance_id": np.asarray(instances).reshape(height, width),
        "semantic_id": np.asarray(semantics).reshape(height, width),
        "valid": np.asarray(valid).reshape(height, width),
    }
    np.savez_compressed(str(prefix) + "-annotations.npz", **arrays)
    metadata = {
        "backend": "Mitsuba CUDA primary-ray intersections",
        "native_rtx": False,
        "replicator": False,
        "resolution": [width, height],
        "depth_unit": "meter",
        "depth_convention": "camera-forward Z",
        "range_convention": "distance from camera center",
        "normal_frame": "world",
        "sampling": "one ray per pixel center, no motion blur",
        "miss_value": 0,
        "instances": labels,
        "classes": {str(value): key for key, value in classes.items()},
    }
    Path(str(prefix) + "-labels.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    valid_np, depth_np = arrays["valid"], arrays["depth_m"]
    scaled = np.zeros_like(depth_np)
    if valid_np.any():
        near, far = np.percentile(depth_np[valid_np], [2, 98])
        scaled[valid_np] = 1 - np.clip((depth_np[valid_np] - near) / max(far - near, 1e-6), 0, 1)
    Image.fromarray((scaled * 255).astype("uint8")).save(str(prefix) + "-depth.png")
    normal_rgb = np.where(valid_np[:, :, None], arrays["normal_world"] * 0.5 + 0.5, 0)
    Image.fromarray((np.clip(normal_rgb, 0, 1) * 255).astype("uint8")).save(str(prefix) + "-normals.png")
    ids = arrays["instance_id"]
    colors = np.stack([(ids * 67) % 255, (ids * 131) % 255, (ids * 197) % 255], axis=-1).astype("uint8")
    colors[~valid_np] = 0
    Image.fromarray(colors).save(str(prefix) + "-instances.png")
    return {
        "arrays": str(prefix) + "-annotations.npz",
        "labels": str(prefix) + "-labels.json",
        "depth_preview": str(prefix) + "-depth.png",
        "normal_preview": str(prefix) + "-normals.png",
        "instance_preview": str(prefix) + "-instances.png",
        "valid_pixels": int(valid_np.sum()),
        "instance_count": len(labels),
        "class_count": len(classes),
        **metadata,
    }
