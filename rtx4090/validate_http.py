"""Submit real primitive and mesh jobs, then inspect completed RTX artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def fetch(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read one HTTP response, submitting a request only when a payload is given."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def complete_job(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit once and poll the returned durable ID until completion."""
    result = fetch(url + "/simulate", payload)
    job_id = result["id"]
    deadline = time.monotonic() + 180
    while result["state"] in {"queued", "running"}:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Job {job_id} is still running; do not resubmit")
        time.sleep(0.2)
        result = fetch(url + "/jobs/" + job_id)
    if result["state"] != "succeeded":
        raise RuntimeError(json.dumps(result))
    return result


def main() -> None:
    """Validate RGB-D, labels, USD export, rejection and sequential GPU scene reuse."""
    # Official python.sh exposes Python packages but loads USD through Kit
    # extensions. Reuse that installed USD library for offline artifact reads.
    runtime = os.environ.get("ISAAC_PATH", os.environ.get("ISAAC_SIM_DIR"))
    if runtime and not os.environ.get("RTX4090_USD_VALIDATOR_READY"):
        libraries = list((Path(runtime) / "extscache").glob("omni.usd.libs-*"))
        if len(libraries) != 1:
            raise RuntimeError("Expected one omni.usd.libs extension in the selected official runtime")
        library = libraries[0]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(library) + os.pathsep + environment.get("PYTHONPATH", "")
        environment["LD_LIBRARY_PATH"] = str(library / "bin") + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
        environment["RTX4090_USD_VALIDATOR_READY"] = "1"
        os.execve(sys.executable, [sys.executable, *sys.argv], environment)
    import numpy as np
    from PIL import Image
    from pxr import Usd, UsdGeom

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18409")
    parser.add_argument("--report", type=Path, default=Path(__file__).parent / "results/http-acceptance.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    health = fetch(args.url + "/health")
    if health["status"] != "ready" or not health["native_rtx"] or health["cpu_fallback"]:
        raise RuntimeError(f"Unexpected service status: {health}")
    payload = json.loads((root.parent / "a100/examples/cube.json").read_text())
    primitive_result = complete_job(args.url, payload)

    asset = root / "assets/http-test-mesh"
    asset.mkdir(parents=True, exist_ok=True)
    vertices = np.asarray([[-.2, -.2, 0], [.2, -.2, 0], [0, .2, 0], [0, 0, .4]], np.float32)
    faces = np.asarray([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], np.int32)
    normals = vertices - vertices.mean(axis=0)
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    np.savez(asset / "mesh.npz", vertices=vertices, faces=faces, normals=normals,
             colors=np.tile(np.asarray([.2, .8, .3], np.float32), (4, 1)))
    mesh_result = complete_job(args.url, {
        "bodies": [{"id": "green-mesh", "shape": "mesh", "asset": str(asset),
                    "position": [0, 0, 1.2]}],
        "steps": 240, "render": {"size": 256, "spp": 8, "distance": 2.5, "target": [0, 0, .15]},
    })
    inspected = []
    for result in (primitive_result, mesh_result):
        if result["physics_device"] != f"cuda:{health['physical_gpu']}":
            raise RuntimeError("Physics tensors used the wrong GPU")
        render = result["render"]
        rgb = np.asarray(Image.open(result["image"]))
        depth = np.load(render["depth_m"])
        semantic = np.load(render["semantic_ids"])
        labels = json.loads(Path(render["semantic_labels"]).read_text())
        if rgb.shape[:2] != depth.shape or depth.shape != semantic.shape:
            raise RuntimeError("Unaligned RGB-D and semantic output sizes")
        if rgb.mean() < 10 or rgb.mean() > 220:
            raise RuntimeError(f"Invalid image exposure: {rgb.mean()}")
        label_map = labels["idToLabels"]
        for body in result["bodies"]:
            matching = [int(key) for key, value in label_map.items() if body["id"] in str(value)]
            if not matching or not np.isin(semantic, matching).any():
                raise RuntimeError(f"No semantic pixels for body {body['id']}")
        stage = Usd.Stage.Open(result["scene"])
        for body in result["bodies"]:
            prim = stage.GetPrimAtPath(body["prim_path"])
            matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            if not np.allclose(matrix.ExtractTranslation(), body["pose_xyzw"][:3], atol=1e-4):
                raise RuntimeError("Exported USD does not match the simulated body pose")
        inspected.append({"id": result["id"], "state": result["state"], "image": result["image"],
                          "rgb_mean": float(rgb.mean()), "shape": list(depth.shape),
                          "execution_seconds": result["execution_seconds"]})
    try:
        fetch(args.url + "/simulate", {"render": {"kind": "gaussian"}})
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
    else:
        raise RuntimeError("Unsupported Gaussian capture was not rejected")
    report = {"status": "succeeded", "health": health, "jobs": inspected,
              "checks": ["primitive simulation", "mesh collision and RTX", "metric depth", "semantic pixels",
                         "USD pose consistency", "unsupported input rejection", "sequential scene reuse"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("RTX4090_HTTP_SUCCEEDED", json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
