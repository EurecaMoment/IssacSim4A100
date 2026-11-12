"""Bake GLB base-color textures into the service's mesh.npz asset format."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh


def decode_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def bake_color(mesh: trimesh.Trimesh) -> np.ndarray:
    """Sample source base color bilinearly in linear space at every UV vertex."""
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
        texture = np.asarray(image.convert("RGB"), np.uint8)
        height, width = texture.shape[:2]
        coordinates = np.asarray(uv, np.float64)
        x, y = coordinates[:, 0] * width - 0.5, (1 - coordinates[:, 1]) * height - 0.5
        x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
        fx, fy = (x - x0)[:, None], (y - y0)[:, None]
        c00 = decode_srgb(texture[y0 % height, x0 % width].astype(np.float32) / 255)
        c10 = decode_srgb(texture[y0 % height, (x0 + 1) % width].astype(np.float32) / 255)
        c01 = decode_srgb(texture[(y0 + 1) % height, x0 % width].astype(np.float32) / 255)
        c11 = decode_srgb(texture[(y0 + 1) % height, (x0 + 1) % width].astype(np.float32) / 255)
        colors = (c00 * (1 - fx) + c10 * fx) * (1 - fy) + (c01 * (1 - fx) + c11 * fx) * fy
    elif visual.kind == "vertex":
        colors = decode_srgb(np.asarray(visual.vertex_colors[:, :3], np.float32) / 255)
    else:
        colors = np.ones((len(mesh.vertices), 3), np.float32)
    return np.clip(colors * factor, 0, 1).astype(np.float32)


def convert_asset(source: Path, destination: Path, source_up: str, max_size: float, asset_id: str) -> dict:
    scene = trimesh.load(source, force="scene", process=False)
    rotation = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float) if source_up == "y" else np.eye(3)
    vertices, normals, faces, colors = [], [], [], []
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
        colors.append(bake_color(mesh))
        offset += len(points)
    if not vertices:
        raise ValueError(f"No triangle meshes in {source}")
    vertices = np.concatenate(vertices)
    normals = np.concatenate(normals)
    faces = np.concatenate(faces)
    colors = np.concatenate(colors)
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
    np.savez(destination / "mesh.npz", vertices=vertices, normals=normals.astype(np.float32),
             faces=faces, colors=colors, colors_linear=np.array(True))
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    record = {
        "id": asset_id, "source": str(source), "source_sha256": digest.hexdigest(),
        "asset_directory": str(destination), "source_frame": source_up + "_up", "target_frame": "z_up",
        "scale_for_display": scale, "original_bounds_z_up": bounds.tolist(),
        "display_bounds": [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
        "vertices": len(vertices), "triangles": len(faces),
        "material_conversion": "Base color baked into linear vertex colors; source metallic/roughness maps omitted.",
    }
    (destination / "conversion.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="GLB files or directories to search recursively")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-up", choices=["y", "z"], default="y")
    parser.add_argument("--max-size", type=float, default=1.2)
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
        record = convert_asset(source.resolve(), output / "assets" / asset_id,
                               args.source_up, args.max_size, asset_id)
        records.append(record)
        print(json.dumps({"prepared": asset_id, "triangles": record["triangles"]}), flush=True)
    (output / "assets.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"Prepared {len(records)} assets: {output / 'assets.json'}")


if __name__ == "__main__":
    main()
