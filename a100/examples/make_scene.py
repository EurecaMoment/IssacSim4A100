"""Arrange converted assets on plinths with reproducible placement and shared lights."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random


def quaternion(yaw: float) -> list[float]:
    angle = math.radians(yaw) / 2
    return [0, 0, math.sin(angle), math.cos(angle)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--spp", type=int, default=128)
    args = parser.parse_args()
    assets = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not 1 <= len(assets) <= 60:
        parser.error("The gallery supports 1 to 60 assets (the service permits up to 256 bodies)")
    if not 64 <= args.size <= 1024 or not 1 <= args.spp <= 256:
        parser.error("Use --size 64..1024 and --spp 1..256")
    generator = random.Random(args.seed)
    generator.shuffle(assets)
    columns = min(5, math.ceil(math.sqrt(len(assets) * 1.5)))
    rows = math.ceil(len(assets) / columns)
    width, depth = columns * 2.1 + 1.1, rows * 2.1 + 1
    bodies = []
    layout = []

    def add_box(name: str, position: list, size: list, color: list, yaw: float = 0) -> None:
        bodies.append({"id": name, "shape": "cube", "position": position, "size": size,
                       "color": color, "mass": 10, "orientation_xyzw": quaternion(yaw)})

    add_box("gallery-floor", [0, 0, 0.08], [width, depth, 0.16], [0.105, 0.12, 0.14])
    add_box("backdrop", [0, depth / 2, 1.76], [width, 0.12, 3.2], [0.065, 0.085, 0.115])
    for index, asset in enumerate(assets):
        column, row = index % columns, index // columns
        x = (column - (columns - 1) / 2) * 2.1 + generator.uniform(-0.12, 0.12)
        y = (row - (rows - 1) / 2) * 2.1 + generator.uniform(-0.10, 0.10)
        yaw = generator.uniform(-20, 20)
        height = 0.16 + row * 0.22 + generator.uniform(0, 0.08)
        bounds = asset["display_bounds"]
        plinth_width = max(0.95, bounds[1][0] - bounds[0][0] + 0.22)
        plinth_depth = max(0.85, bounds[1][1] - bounds[0][1] + 0.20)
        add_box("plinth-" + asset["id"], [x, y, 0.16 + height / 2],
                [plinth_width, plinth_depth, height], [0.20, 0.185, 0.16], yaw)
        add_box("trim-" + asset["id"], [x, y, 0.178],
                [plinth_width + 0.018, plinth_depth + 0.018, 0.026], [0.34, 0.24, 0.105], yaw)
        position = [x, y, 0.16 + height + 0.012]
        bodies.append({"id": asset["id"], "shape": "mesh", "asset": asset["asset_directory"],
                       "position": position, "mass": 1, "orientation_xyzw": quaternion(yaw)})
        layout.append({"asset_id": asset["id"], "position": position, "yaw_degrees": yaw})
    lighting = {
        "dome": {"color": [0.70, 0.82, 1.0], "intensity": 0.12},
        "key": {"color": [1.0, 0.80, 0.58], "intensity": 30, "width": 4.5, "height": 3.0,
                "origin": [-4, -5, 7.5], "target": [0, 0, 0.9]},
        "fills": [
            {"color": [0.40, 0.62, 1.0], "intensity": 16, "width": 3.5, "height": 3.5,
             "origin": [5.5, -1, 7.5], "target": [0, 0, 1]},
            {"color": [1.0, 0.65, 0.36], "intensity": 24, "width": 3.0, "height": 2.0,
             "origin": [0, depth / 2 - 1, 7], "target": [0, -0.3, 0.8]},
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    distance = max(5.0, math.hypot(width, depth) * 1.25)
    for name, azimuth, elevation, zoom in [("overview", 17, 26, 1), ("left-angle", -23, 28, 1.04),
                                            ("close-up", 8, 24, 0.88)]:
        request = {
            "bodies": bodies, "steps": 1, "dt": 1 / 120, "gravity": 0, "ground": False,
            "render": {"kind": "mesh", "size": args.size, "spp": args.spp, "seed": args.seed,
                       "azimuth": azimuth, "elevation": elevation, "distance": distance * zoom,
                       "target": [0, 0, 1.1], "lighting": lighting},
        }
        (args.output / f"{name}.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    (args.output / "scene-layout.json").write_text(json.dumps({
        "seed": args.seed, "layout": layout, "lighting": lighting,
        "pose_policy": "Zero gravity and one CUDA physics step preserve display poses.",
        "material_policy": "Linear baked vertex base colors; metallic/roughness textures omitted.",
    }, indent=2), encoding="utf-8")
    print(f"Prepared {len(assets)} assets / {len(bodies)} scene bodies in {args.output.resolve()}")


if __name__ == "__main__":
    main()
