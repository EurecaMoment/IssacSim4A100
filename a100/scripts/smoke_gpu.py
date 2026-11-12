"""Run one small CUDA PhysX scene and render its live USD stage on the selected A100."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from a100sim.app import create_app
from a100sim.physics_service import PhysicsQueue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    application = create_app()
    try:
        state = PhysicsQueue(output)
        request = state.prepare({
            "bodies": [{"id": "smoke-cube", "position": [-0.2, 0, 1]},
                       {"id": "smoke-sphere", "shape": "sphere", "position": [0.2, 0, 0.6]}],
            "steps": 120, "render": {"size": 128, "spp": 4, "distance": 3, "target": [0, 0, 0.2]},
        })
        result = state.process(request, output)
        if not result["physics_device"].startswith("cuda"):
            raise RuntimeError(f"Expected CUDA PhysX, received {result['physics_device']}")
        if not Path(result["image"]).is_file() or not Path(result["scene"]).is_file():
            raise RuntimeError("The smoke test did not produce both an image and a USD scene")
        manager = application.app.get_extension_manager()
        active = [item["name"] for item in manager.get_extensions() if manager.is_extension_enabled(item["id"])]
        if any(name.startswith("omni.hydra.rtx") for name in active):
            raise RuntimeError("The native RTX renderer was unexpectedly enabled")
        print("A100_GPU_SMOKE_SUCCEEDED " + json.dumps(result), flush=True)
    finally:
        application.close()


if __name__ == "__main__":
    main()
