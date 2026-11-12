"""Run numerical end-to-end checks against the optional A100 feature service."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from feature_client import execute


def require(condition: bool, message: str) -> None:
    """Fail on an observed physical or service contract violation."""
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8322")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--skip-cloth", action="store_true")
    arguments = parser.parse_args()
    report = {"checks": {}, "jobs": {}}
    failure = None
    try:
        import numpy as np
        from PIL import Image

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(arguments.url + "/health", timeout=5) as response:
            health = json.load(response)
        require(health["status"] == "ready" and health["cpu_fallback"] is False, "CUDA service is not ready")
        count = health["arm_count"]
        report["health_before"] = health
        targets = [[0.6, -0.4], [-0.35, 0.25], [0.2, 0.5], [-0.5, -0.2]][:count]
        if count > 4:
            targets += [[0.1, -0.1]] * (count - 4)

        def run(name: str, payload: dict) -> dict:
            result = execute(payload, arguments.url, timeout=240)
            report["jobs"][name] = result
            report["checks"][name] = True
            return result

        position = run("position", {"action": "command", "mode": "position", "targets": targets, "steps": 300})
        require(np.allclose(position["snapshot"]["joint_positions"], targets, atol=0.015), "Position tracking failed")
        velocity = run("velocity", {"action": "command", "mode": "velocity", "targets": [0.25, -0.15], "steps": 30})
        require(
            np.allclose(velocity["snapshot"]["joint_velocities"][0], [0.25, -0.15], atol=0.06),
            "Velocity tracking failed",
        )
        run("restore", {"action": "command", "targets": targets, "steps": 300})
        effort = run("effort", {"action": "command", "mode": "effort", "targets": [0.4, 0], "steps": 24})
        require(abs(effort["snapshot"]["joint_state"]["efforts"][0] - 0.4) < 0.08, "Measured effort is incorrect")
        run("hold", {"action": "command", "targets": targets, "steps": 300})
        dynamics = run("dynamics", {"action": "dynamics"})["dynamics"]
        require(np.asarray(dynamics["jacobians"]).shape == (count, 2, 6, 2), "Jacobian shape is incorrect")
        masses = np.asarray(dynamics["mass_matrices"])
        require(
            np.isfinite(masses).all() and (np.linalg.eigvalsh(masses) > 0).all(), "Mass matrix is not positive definite"
        )
        sensors = position["snapshot"]
        require(
            all(sensors[name]["valid"] for name in ("imu", "contact", "joint_state", "effort", "raycast")),
            "One or more native physics sensors returned invalid data",
        )
        require(abs(sensors["imu"]["linear_acceleration"][2] - 9.81) < 0.05, "IMU gravity check failed")
        require(abs(sensors["contact"]["force"] - 9.81) < 0.1, "Static support force check failed")
        require(np.allclose(sensors["raycast"]["depths"], [1.5] * 3, atol=1e-4), "Known-range sensor check failed")
        scan = run("scan", {"action": "scan"})["scan"]
        require(
            len(scan["depths"]) == 180 and abs(scan["depths"][135] - 1.9) < 0.01,
            "180-beam scan did not detect the known obstacle distance",
        )
        grasp = run("grasp", {"action": "gripper", "operation": "cycle"})["gripper"]
        require(grasp["closed"]["status"] == "Closed" and grasp["closed"]["gripped_objects"], "Cube was not gripped")
        require(
            grasp["lifted"]["target_pose_xyzw"][2] > grasp["before"]["target_pose_xyzw"][2] + 0.1,
            "The actual cube did not rise",
        )
        require(
            grasp["released"]["status"] == "Open" and not grasp["released"]["gripped_objects"], "Cube was not released"
        )
        require(grasp["released"]["target_pose_xyzw"][2] < 0.04, "Released cube did not fall to the ground")
        trajectory = run("trajectory", {"action": "trajectory", "points": [[0.6, -0.4], [0.3, -0.2], [0.1, 0.2]]})
        require(
            np.allclose(trajectory["snapshot"]["joint_positions"][0], [0.1, 0.2], atol=0.015),
            "The actual robot did not reach the trajectory endpoint",
        )
        controller = run("differential_drive", {"action": "differential_drive"})["controller"]
        require(
            np.allclose(np.asarray(controller["wheel_velocities_rad_s"]).reshape(-1), [4, 6], atol=1e-4),
            "Differential controller output disagrees with its analytical solution",
        )
        batch = run("batched_step", {"action": "rl_step", "targets": targets, "steps": 2})
        require(np.asarray(batch["observations"]).shape == (count, 4), "Batched observation shape is incorrect")
        capture = run("capture", {"action": "capture", "size": 256, "spp": 16})
        sources = {
            item["prim"]: np.asarray(item["world_matrix"])
            for item in capture["capture"]["usd_sources"]
            if "world_matrix" in item
        }
        for prim, pose in capture["capture"]["body_poses_xyzw"].items():
            matrix = sources[prim + "/Visual"] if prim + "/Visual" in sources else sources[prim]
            require(np.allclose(matrix[:3, 3], pose[:3], atol=1e-5), "Rendered body position is stale: " + prim)
            x, y, z, w = pose[3:7]
            rotation = np.array(
                [
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ]
            )
            rendered_rotation = matrix[:3, :3] / np.linalg.norm(matrix[:3, :3], axis=0)
            require(np.allclose(rendered_rotation, rotation, atol=1e-5), "Rendered body orientation is stale: " + prim)
        report["checks"]["capture_pose_sync"] = True
        path = Path(capture["result_file"]).parent
        image = np.asarray(Image.open(path / "render.png").convert("RGB"))
        require(float(image.mean()) > 25 and float(image.std()) > 10, "RGB render is dark or empty")
        arrays = np.load(path / "render-annotations.npz")
        require(arrays["depth_m"].shape == (256, 256), "Depth dimensions are incorrect")
        require(np.isfinite(arrays["depth_m"]).all() and arrays["valid"].sum() > 1000, "Depth is invalid or empty")
        labels = json.loads((path / "render-labels.json").read_text())
        known = {0, *[int(number) for number in labels["instances"]]}
        require(set(np.unique(arrays["instance_id"])).issubset(known), "Instance ID mapping is incomplete")
        require("robot" in labels["classes"].values(), "Robot semantic label is missing")
        report["preview"] = str(path / "render.png")
        report["annotations"] = str(path / "render-annotations.npz")
        if not arguments.skip_cloth:
            cloth = run("cloth", {"action": "cloth", "frames": 90})["cloth"]
            require(
                cloth["success"] and cloth["device"].startswith("cuda") and cloth["anchor_error_m"] < 1e-5,
                "CUDA cloth validation failed",
            )
        invalid = urllib.request.Request(
            arguments.url + "/robotics",
            data=json.dumps({"action": "command", "targets": [float("nan"), 0]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            opener.open(invalid, timeout=5)
        except urllib.error.HTTPError as error:
            require(error.code == 400, "Malformed command returned the wrong error status")
        else:
            raise RuntimeError("Nonfinite command was accepted")
        report["checks"]["reject_nonfinite"] = True
        with opener.open(arguments.url + "/health", timeout=5) as response:
            report["health_after"] = json.load(response)
        report["success"] = True
    except Exception as error:
        failure = error
        report.update(success=False, error=str(error), traceback=traceback.format_exc())
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "success": report["success"],
                "checks": report["checks"],
                "report": str(arguments.report),
                **({"error": str(failure)} if failure else {}),
            },
            indent=2,
        ),
        flush=True,
    )
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
