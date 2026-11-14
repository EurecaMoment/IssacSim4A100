"""Validate simulator operations solely through the public gRPC interface."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import struct
import uuid
from pathlib import Path
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import MessageToDict

from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc


def cube_glb() -> bytes:
    """Create a self-contained mesh fixture without installing an asset SDK on the caller."""
    vertices = [(x, y, z) for z in (-0.1, 0.1) for y in (-0.1, 0.1) for x in (-0.1, 0.1)]
    indices = [
        0,
        2,
        3,
        0,
        3,
        1,
        4,
        5,
        7,
        4,
        7,
        6,
        0,
        1,
        5,
        0,
        5,
        4,
        2,
        6,
        7,
        2,
        7,
        3,
        0,
        4,
        6,
        0,
        6,
        2,
        1,
        3,
        7,
        1,
        7,
        5,
    ]
    geometry = struct.pack("<24f", *(coordinate for vertex in vertices for coordinate in vertex))
    geometry += struct.pack("<36H", *indices)
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(geometry)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 96},
            {"buffer": 0, "byteOffset": 96, "byteLength": 72},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-0.1] * 3, "max": [0.1] * 3},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    geometry += b"\0" * (-len(geometry) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(encoded) + 8 + len(geometry))
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(geometry), 0x004E4942)
        + geometry
    )


async def validate(arguments: argparse.Namespace) -> dict[str, Any]:
    """Run live operations, checking physical results and transferring artifacts remotely."""
    token = os.environ.get("A100SIM_GRPC_TOKEN", "")
    metadata = (("authorization", "Bearer " + token),) if token else ()
    credentials = grpc.ssl_channel_credentials(arguments.ca.read_bytes()) if arguments.ca else None
    channel = (
        grpc.aio.secure_channel(arguments.target, credentials)
        if credentials
        else grpc.aio.insecure_channel(arguments.target)
    )
    report: dict[str, Any] = {"scope": "public gRPC live simulator acceptance", "checks": {}, "skipped": {}}
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=10)
        client = rpc.SimulatorStub(channel)
        capabilities = await client.GetCapabilities(Empty(), metadata=metadata, timeout=10)
        available = {item.name for item in capabilities.capabilities if item.availability == pb.AVAILABLE}
        report["capabilities"] = MessageToDict(capabilities, preserving_proto_field_name=True)
        catalog = await client.GetApiCatalog(Empty(), metadata=metadata, timeout=10)
        if catalog.simulator_version != "6.2.0.0" or len(catalog.domains) != 30:
            raise RuntimeError("Expected the Isaac Sim 6.2.0 catalog with all 30 domains")
        report["catalog"] = MessageToDict(catalog, preserving_proto_field_name=True)
        if not available:
            raise RuntimeError("No simulator operation is available")

        async def run(name: str, message: Any, *, label: str = "") -> dict[str, Any] | None:
            key = label or name
            if name not in available:
                report["skipped"][key] = "Required simulator capability is unavailable"
                return None
            if arguments.skip_workflows and name in ("newton", "import_robot", "convert_asset"):
                report["skipped"][key] = "Workflow acceptance was explicitly skipped"
                return None
            job = await client.SubmitTask(
                pb.SubmitTaskRequest(request_id=uuid.uuid4().hex, **{name: message}), metadata=metadata, timeout=10
            )
            async for job in client.WatchJob(pb.WatchJobRequest(job_id=job.job_id), metadata=metadata, timeout=600):
                pass
            if job.state != pb.SUCCEEDED:
                raise RuntimeError(f"{key} failed: {job.failure.code} {job.failure.message}")
            report["checks"][key] = {
                "success": True,
                "job_id": job.job_id,
                "timings": MessageToDict(job.timings, preserving_proto_field_name=True),
            }
            for artifact in job.artifacts:
                digest = hashlib.sha256()
                size = 0
                async for chunk in client.DownloadArtifact(
                    pb.DownloadRequest(job_id=job.job_id, artifact_id=artifact.artifact_id),
                    metadata=metadata,
                    timeout=120,
                ):
                    if chunk.offset != size:
                        raise RuntimeError("Artifact stream offsets are not contiguous")
                    size += len(chunk.data)
                    digest.update(chunk.data)
                if size != artifact.size_bytes:
                    raise RuntimeError("Downloaded artifact has the wrong length")
            return MessageToDict(job.result, preserving_proto_field_name=True)

        async def upload(name: str, contents: bytes) -> pb.AssetFile:
            async def chunks() -> Any:
                yield pb.UploadChunk(relative_path=name, data=contents, sha256=hashlib.sha256(contents).hexdigest())

            return await client.UploadAsset(chunks(), metadata=metadata, timeout=30)

        simulation = await run(
            "simulate",
            pb.SimulateRequest(
                bodies=[
                    pb.Body(
                        id="cube",
                        position=[0, 0, 1],
                        size=[0.2] * 3,
                        material=pb.PbrMaterial(metallic=0.6, roughness=0.25),
                        semantic_label="test_cube",
                    )
                ],
                steps=120,
                route=pb.GENERIC,
                render=pb.RenderSettings(size=128, spp=4, annotations=True),
            ),
        )
        if simulation is not None:
            if simulation.get("physics_device") != "cuda:0" or simulation.get("cpu_fallback") is not False:
                raise RuntimeError("Simulation did not report the required CUDA backend")
            if abs(simulation["bodies"][0]["pose_xyzw"][2] - 0.1) > 0.02:
                raise RuntimeError("Falling cube did not reach the ground")
            annotations = simulation.get("render", {}).get("annotations", {})
            if annotations.get("valid_pixels", 0) <= 0 or "test_cube" not in annotations.get("classes", {}).values():
                raise RuntimeError("Custom scene semantic annotations did not preserve the supplied label")
        randomized = await run(
            "randomize",
            pb.RandomizeRequest(
                scene=pb.SimulateRequest(bodies=[pb.Body(id="random", position=[0, 0, 1])], steps=120),
                seed=0,
                position_min=[-0.5, -0.5, 1],
                position_max=[0.5, 0.5, 1.5],
                randomize_color=True,
                metallic_range=[0, 1],
                roughness_range=[0.2, 0.8],
            ),
        )
        if randomized and randomized.get("randomization", {}).get("seed") != 0:
            raise RuntimeError("Randomization did not preserve the seed and initial scene metadata")
        await run("render_asset", pb.RenderSettings(spp=4))
        state = await run("snapshot", Empty())
        await run("step", pb.StepRequest(steps=2))
        if state is not None:
            count = len(state["snapshot"]["joint_positions"])
            controlled = await run(
                "control",
                pb.ControlRequest(targets=[pb.JointTargets(values=[0.3, -0.2])], steps=300),
                label="control_position",
            )
            if (
                controlled
                and max(abs(a - b) for a, b in zip(controlled["snapshot"]["joint_positions"][0], (0.3, -0.2))) > 0.02
            ):
                raise RuntimeError("Robot position target tracking failed")
            for mode, values in [(pb.VELOCITY, [0.2, -0.1]), (pb.EFFORT, [0.3, 0])]:
                await run(
                    "control",
                    pb.ControlRequest(mode=mode, targets=[pb.JointTargets(values=values)], steps=24),
                    label="control_" + pb.ControlMode.Name(mode).lower(),
                )
            await run(
                "control",
                pb.ControlRequest(targets=[pb.JointTargets(values=[0.1, 0.2])] * count, steps=300),
                label="control_all_arms",
            )
        for name in ("dynamics", "scan", "export_scene", "inspect_gains", "ros2_status"):
            await run(name, Empty())
        await run("capture", pb.CaptureRequest(size=128, spp=4))
        await run("gripper", pb.GripperRequest(operation="cycle"))
        await run(
            "trajectory",
            pb.TrajectoryRequest(points=[pb.JointTargets(values=[0.1, 0.2]), pb.JointTargets(values=[0.2, 0.1])]),
        )
        drive = await run(
            "differential_drive",
            pb.DifferentialDriveRequest(linear_velocity=0.5, angular_velocity=0.5, wheel_radius=0.1, wheel_base=0.4),
        )
        if drive and drive["controller"]["wheel_velocities_rad_s"] != [[4.0, 6.0]]:
            # Controllers may return a flat row depending on the installed wheel.
            if drive["controller"]["wheel_velocities_rad_s"] != [4.0, 6.0]:
                raise RuntimeError("Differential drive wheel commands are incorrect")
        await run("rl_step", pb.ControlRequest(targets=[pb.JointTargets(values=[0, 0])], steps=2))
        cloth = await run("cloth", pb.ClothRequest(frames=90))
        if cloth and (not cloth["cloth"]["success"] or cloth["cloth"]["anchor_error_m"] > 1e-5):
            raise RuntimeError("Cloth constraint acceptance failed")
        if "import_robot" in available and not arguments.skip_workflows:
            fixtures = Path(__file__).resolve().parents[1] / "examples/robots"
            for filename in ("two_link.urdf", "two_link.xml"):
                path = fixtures / filename
                if not path.exists():
                    candidates = (
                        list(fixtures.glob("*.xml")) if filename.endswith("xml") else list(fixtures.glob("*.urdf"))
                    )
                    if not candidates:
                        raise RuntimeError("The packaged robot import fixture is missing")
                    path = candidates[0]
                asset = await upload(path.name, path.read_bytes())
                await run(
                    "import_robot",
                    pb.ImportRobotRequest(
                        asset_id=asset.asset_id, relative_path=asset.relative_path, physics_check=True
                    ),
                    label="import_" + path.suffix[1:],
                )
        for solver in (pb.XPBD, pb.MUJOCO_NATIVE):
            await run("newton", pb.NewtonRequest(solver=solver), label="newton_" + pb.NewtonSolver.Name(solver).lower())
        if "convert_asset" in available and not arguments.skip_workflows:
            asset = await upload("cube.glb", cube_glb())
            converted = await run(
                "convert_asset",
                pb.ConvertAssetRequest(
                    asset_id=asset.asset_id,
                    relative_path=asset.relative_path,
                    source_up="z",
                    max_size=0.2,
                    strict_materials=True,
                ),
            )
            if converted:
                await run(
                    "simulate",
                    pb.SimulateRequest(
                        bodies=[pb.Body(id="mesh", shape=pb.MESH, asset_id=converted["asset_id"], position=[0, 0, 1])],
                        steps=120,
                        route=pb.GENERIC,
                        render=pb.RenderSettings(size=128, spp=4),
                    ),
                    label="mesh_simulation",
                )
        report["success"] = True
    except Exception as error:
        report.update(success=False, error=f"{type(error).__name__}: {error}")
    finally:
        await channel.close()
    return report


def main() -> None:
    """Write machine-independent acceptance evidence and fail on numerical or RPC errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--skip-workflows", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    report = asyncio.run(validate(arguments))
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "checks": len(report["checks"]), "skipped": report["skipped"]}))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
