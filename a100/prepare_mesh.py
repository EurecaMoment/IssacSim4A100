"""Preserve SAM3D vertex colors and undo GLB's documented z-up -> y-up transform."""
import argparse
import json
from pathlib import Path

import numpy as np
import trimesh

p = argparse.ArgumentParser()
p.add_argument("glb", type=Path)
p.add_argument("output", type=Path)
args = p.parse_args()
scene = trimesh.load(str(args.glb), force="scene", process=False)
parts = []
for node in scene.graph.nodes_geometry:
    transform, name = scene.graph[node]
    geometry = scene.geometry[name].copy()
    geometry.apply_transform(transform)
    parts.append(geometry)
mesh = trimesh.util.concatenate(parts)
# GLB coordinates (x,z,-y) -> SAM3D Gaussian coordinates (x,y,z).
mesh.apply_transform(np.array([[1, 0, 0, 0], [0, 0, -1, 0],
                              [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float))
colors = np.asarray(mesh.visual.vertex_colors[:, :3], dtype=np.float32) / 255
args.output.parent.mkdir(parents=True, exist_ok=True)
np.savez(args.output, vertices=np.asarray(mesh.vertices, np.float32),
         normals=np.asarray(mesh.vertex_normals, np.float32),
         faces=np.asarray(mesh.faces, np.uint32), colors=colors)
print(json.dumps({"vertices": len(mesh.vertices), "triangles": len(mesh.faces),
                  "bounds": mesh.bounds.tolist(), "output": str(args.output)}), flush=True)
