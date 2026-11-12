"""GPU physics invariants plus independent USD/NPZ output validation."""

import argparse
import json
import math
import os
import time
import urllib.request
from pathlib import Path

from .app import create_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--benchmark-report", type=Path, required=True, help="Completed benchmark_http.py report")
arguments = parser.parse_args()
app = create_app()
try:
    import numpy as np
    from pxr import Usd, UsdGeom

    from .batch_physics import ResidentPhysics
    from .physics_service import PhysicsQueue

    resident = ResidentPhysics()
    state = PhysicsQueue(Path("results/resident-validation"))
    checks = []

    def check(name, condition, details=None):
        checks.append({"name": name, "passed": bool(condition), "details": details})
        print("CHECK", name, bool(condition), details, flush=True)
        if not condition:
            raise AssertionError(name)

    requests = [
        state.prepare(
            {"bodies": [{"id": f"body-{i}", "position": [i * 0.001, 0, 2], "mass": 0.1 + i * 0.2}], "steps": 30}
        )
        for i in range(64)
    ]
    values, timing = resident.simulate(requests)
    expected_z = 2 - 9.81 * (1 / 120) ** 2 * 30 * 31 / 2
    check(
        "64 independent free falls",
        all(abs(v[0][0, 2] - expected_z) < 2e-5 and abs(v[1][0, 2] + 9.81 * 0.25) < 2e-5 for v in values),
        timing,
    )
    expected_x = [
        float(
            np.float32(np.float32(i * 0.001) + np.float32(resident.origins[i][0])) - np.float32(resident.origins[i][0])
        )
        for i in range(64)
    ]
    errors = [abs(float(v[0][0, 0]) - i * 0.001) for i, v in enumerate(values)]
    check(
        "64 independent x coordinates match float32 world transforms",
        all(abs(float(v[0][0, 0]) - x) < 2e-5 for v, x in zip(values, expected_x)),
        {"max_local_quantization_error_m": max(errors), "world_spacing_m": 256},
    )
    values2, _ = resident.simulate(requests)
    check(
        "state reset repeatability",
        all(
            np.allclose(a[0], b[0], atol=1e-5, rtol=0) and np.allclose(a[1], b[1], atol=1e-5, rtol=0)
            for a, b in zip(values, values2)
        ),
    )
    requests = [state.prepare({}) for _ in range(64)]
    values, _ = resident.simulate(requests)
    check("all 4096 bodies collide with their floor", all(np.allclose(p[:, 2], 0.1, atol=0.003) for p, v in values))
    check("no cross request stacking", max(float(p[:, 2].max()) for p, v in values) < 0.11)
    zero = state.prepare({"bodies": [{"id": "zero", "position": [0.5, 0.5, 2]}], "steps": 240, "gravity": 0})
    values, _ = resident.simulate([zero] * 64)
    check(
        "gravity can change between batches",
        all(np.allclose(p[0, :3], [0.5, 0.5, 2], atol=1e-5) and np.allclose(v, 0, atol=1e-5) for p, v in values),
    )
    variants = [state.prepare({"bodies": [{"id": "drop"}], "steps": n}) for n in [1, 30, 60, 120]]
    values, _ = resident.simulate(variants)
    check(
        "mixed step counts are independent",
        all(
            abs(p[0, 2] - (2 - 9.81 * (1 / 120) ** 2 * r["steps"] * (r["steps"] + 1) / 2)) < 2e-5
            for (p, v), r in zip(values, variants)
            if r["steps"] <= 60
        ),
    )
    stack = state.prepare(
        {"bodies": [{"id": "bottom", "position": [0, 0, 0.1]}, {"id": "top", "position": [0, 0, 0.8]}], "steps": 240}
    )
    values, _ = resident.simulate([stack] * 64)
    check("body-body collisions preserved", all(0.08 < p[0, 2] < 0.12 and 0.28 < p[1, 2] < 0.32 for p, v in values))
    # Read a real service output with the USD parser (the export worker does not load Kit).
    report = json.loads(arguments.benchmark_report.read_text())
    job_id = report["rounds"][0]["rows"][0]["id"]
    job = json.loads(
        (
            Path(os.environ.get("A100SIM_LOCAL_STATE", f"/var/tmp/a100sim-{os.getuid()}"))
            / "jobs"
            / job_id
            / "job.json"
        ).read_text()
    )
    stage = Usd.Stage.Open(job["scene"])
    check("exported USD parses", bool(stage))
    prim = stage.GetPrimAtPath("/World/Bodies/B0000")
    check("exported USD includes all bodies", len([p for p in stage.Traverse() if p.GetName() == "Visual"]) == 64)
    matrix = UsdGeom.Xformable(prim).GetLocalTransformation()
    check(
        "exported USD matches tensor state",
        np.allclose(np.asarray(matrix.ExtractTranslation()), job["bodies"][0]["pose_xyzw"][:3], atol=1e-6),
    )
    check(
        "NPZ matches tensor state",
        np.allclose(np.load(job["state_npz"])["poses_xyzw"], [b["pose_xyzw"] for b in job["bodies"]], atol=1e-7),
    )
    Path("results/performance/correctness-spacing256.json").write_text(
        json.dumps({"checks": checks, "passed": True}, indent=2)
    )
    resident.close()
finally:
    app.close()
