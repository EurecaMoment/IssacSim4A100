"""Bake embedded GLB metallic/roughness PBR into the service's mesh.npz format."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import trimesh


def decode_srgb(value: np.ndarray) -> np.ndarray:
    """Decode texture color samples; metallic/roughness maps are already linear."""
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def sample_texture(image: object, uv: np.ndarray, *, srgb: bool = False) -> np.ndarray:
    """Bilinearly sample repeating glTF UVs, converting color before interpolation."""
    texture = np.asarray(image.convert("RGB"), np.float32) / 255
    if srgb:
        texture = decode_srgb(texture)
    height, width = texture.shape[:2]
    coordinates = np.asarray(uv, np.float64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2 or not np.isfinite(coordinates).all():
        raise ValueError("Texture UVs require finite Nx2 coordinates")
    x, y = coordinates[:, 0] * width - 0.5, (1 - coordinates[:, 1]) * height - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    c00, c10 = texture[y0 % height, x0 % width], texture[y0 % height, (x0 + 1) % width]
    c01, c11 = texture[(y0 + 1) % height, x0 % width], texture[(y0 + 1) % height, (x0 + 1) % width]
    return ((c00 * (1 - fx) + c10 * fx) * (1 - fy) + (c01 * (1 - fx) + c11 * fx) * fy).astype(np.float32)


def bake_pbr(mesh: trimesh.Trimesh) -> dict[str, np.ndarray]:
    """Preserve base-color factor and linear G-roughness/B-metallic texture channels."""
    visual = mesh.visual
    material = getattr(visual, "material", None)
    image = getattr(material, "baseColorTexture", None)
    if image is None:
        image = getattr(material, "image", None)
    factor = getattr(material, "baseColorFactor", None)
    factor = np.ones(3, np.float32) if factor is None else np.asarray(factor, np.float32)[:3]
    if np.max(factor) > 1:
        factor /= 255
    uv = getattr(visual, "uv", None)
    if image is not None and uv is not None:
        colors = sample_texture(image, uv, srgb=True)
    elif visual.kind == "vertex":
        # glTF COLOR_0 is linear, unlike baseColorTexture's sRGB encoding.
        colors = np.asarray(visual.vertex_colors[:, :3], np.float32) / 255
    else:
        colors = np.ones((len(mesh.vertices), 3), np.float32)
    result = {"colors": np.clip(colors * factor, 0, 1).astype(np.float32)}
    texture = getattr(material, "metallicRoughnessTexture", None)
    sampled = sample_texture(texture, uv) if texture is not None and uv is not None else None
    for name, index in (("roughness", 1), ("metallic", 2)):
        factor = getattr(material, name + "Factor", None)
        factor = float(factor) if factor is not None else 1.0
        if not np.isfinite(factor) or not 0 <= factor <= 1:
            raise ValueError(f"Invalid glTF {name}Factor")
        values = sampled[:, index] * factor if sampled is not None else np.full(len(mesh.vertices), factor)
        result[name] = np.clip(values, 0, 1).astype(np.float32)
    return result


def inspect_material_limits(source: Path) -> list[str]:
    """Reject external resources and report unsupported source material features explicitly."""
    with source.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("Invalid GLB header")
        magic, version, length, chunk_size, chunk_type = struct.unpack("<5I", header)
        if magic != 0x46546C67 or version != 2 or chunk_type != 0x4E4F534A or length != source.stat().st_size:
            raise ValueError("Expected a complete glTF 2.0 binary file")
        if chunk_size > 32 * 1024 * 1024 or chunk_size > length - 20:
            raise ValueError("Invalid or oversized GLB JSON chunk")
        document = json.loads(stream.read(chunk_size))
    for resource in [*document.get("buffers", []), *document.get("images", [])]:
        uri = resource.get("uri", "")
        if uri and not uri.startswith("data:"):
            raise ValueError("GLB conversion requires embedded resources; external URIs are not allowed")
    warnings = set()
    for material in document.get("materials", []):
        for name in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            if name in material:
                warnings.add(name + " is not represented by the vertex-baked PBR adapter")
        if any(material.get("emissiveFactor", [])):
            warnings.add("emissiveFactor is not represented by the vertex-baked PBR adapter")
        if material.get("alphaMode", "OPAQUE") != "OPAQUE":
            warnings.add("Alpha masking/blending is not represented; converted surfaces are opaque")
        if material.get("extensions"):
            warnings.add("glTF material extensions are not represented: " + ", ".join(sorted(material["extensions"])))
        for channel in material.get("pbrMetallicRoughness", {}).values():
            if isinstance(channel, dict) and (channel.get("texCoord", 0) != 0 or channel.get("extensions")):
                warnings.add("Only untransformed TEXCOORD_0 material textures are represented")
    if any(sampler.get(key, 10497) != 10497 for sampler in document.get("samplers", []) for key in ("wrapS", "wrapT")):
        warnings.add("Texture clamp/mirror wrap modes are not represented; baking uses repeat")
    return sorted(warnings)


def convert_asset(
    source: Path, destination: Path, source_up: str, max_size: float, asset_id: str, strict_materials: bool = False
) -> dict:
    """Convert a GLB, retaining geometry and baking PBR at source vertex density."""
    if source_up not in ("y", "z") or not np.isfinite(max_size) or not 0 < max_size <= 100:
        raise ValueError("Use y/z source-up and max-size between 0 and 100 meters")
    warnings = inspect_material_limits(source)
    if strict_materials and warnings:
        raise ValueError("Unsupported source material features: " + "; ".join(warnings))
    scene = trimesh.load(source, force="scene", process=False)
    rotation = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float) if source_up == "y" else np.eye(3)
    vertices, normals, faces = [], [], []
    channels = {name: [] for name in ("colors", "metallic", "roughness")}
    offset = 0
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        mesh = scene.geometry[name]
        points = (np.asarray(mesh.vertices) @ transform[:3, :3].T + transform[:3, 3]) @ rotation.T
        normal = np.asarray(mesh.vertex_normals) @ np.linalg.inv(transform[:3, :3]) @ rotation.T
        triangles = np.asarray(mesh.faces, np.uint32)
        if np.linalg.det(transform[:3, :3]) < 0:
            triangles = triangles[:, [0, 2, 1]]
        vertices.append(points)
        normals.append(normal)
        faces.append(triangles + offset)
        for name, values in bake_pbr(mesh).items():
            channels[name].append(values)
        offset += len(points)
    if not vertices:
        raise ValueError(f"No triangle meshes in {source}")
    vertices = np.concatenate(vertices)
    normals = np.concatenate(normals)
    faces = np.concatenate(faces)
    channels = {name: np.concatenate(values) for name, values in channels.items()}
    bounds = np.array([vertices.min(axis=0), vertices.max(axis=0)])
    largest_extent = float(np.max(bounds[1] - bounds[0]))
    if largest_extent <= 0 or not np.isfinite(vertices).all():
        raise ValueError(f"Asset has invalid geometry: {source}")
    scale = max_size / largest_extent
    center = (bounds[0] + bounds[1]) / 2
    center[2] = bounds[0, 2]
    vertices = ((vertices - center) * scale).astype(np.float32)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    destination.mkdir(parents=True, exist_ok=True)
    np.savez(
        destination / "mesh.npz",
        vertices=vertices,
        normals=normals.astype(np.float32),
        faces=faces,
        **channels,
        colors_linear=np.array(True),
    )
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    record = {
        "id": asset_id,
        "source": str(source),
        "source_sha256": digest.hexdigest(),
        "asset_directory": str(destination),
        "source_frame": source_up + "_up",
        "target_frame": "z_up",
        "scale_for_display": scale,
        "original_bounds_z_up": bounds.tolist(),
        "display_bounds": [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
        "vertices": len(vertices),
        "triangles": len(faces),
        "material_conversion": "Linear base color, metallic and roughness baked at source UV vertices.",
        "material_channels": ["base_color", "metallic", "roughness"],
        "material_limits": "Vertex baking interpolates between source vertices; texture detail below mesh density is lost.",
        "material_warnings": warnings,
    }
    (destination / "conversion.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="GLB files or directories to search recursively")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-up", choices=["y", "z"], default="y")
    parser.add_argument("--max-size", type=float, default=1.2)
    parser.add_argument("--strict-materials", action="store_true")
    args = parser.parse_args()
    if not 0 < args.max_size <= 100:
        parser.error("--max-size must be between 0 and 100 meters")
    sources = set()
    for source in args.sources:
        sources.update(source.rglob("*.glb") if source.is_dir() else [source])
    if not sources:
        parser.error("No GLB inputs found")
    output = args.output.resolve()
    records = []
    for index, source in enumerate(sorted(sources), 1):
        asset_id = f"{index:02d}-{source.stem}"
        record = convert_asset(
            source.resolve(),
            output / "assets" / asset_id,
            args.source_up,
            args.max_size,
            asset_id,
            args.strict_materials,
        )
        records.append(record)
        print(json.dumps({"prepared": asset_id, "triangles": record["triangles"]}), flush=True)
    (output / "assets.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"Prepared {len(records)} assets: {output / 'assets.json'}")


if __name__ == "__main__":
    main()
