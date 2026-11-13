"""Exercise real gRPC channels, persistence, backpressure and isolated worker routing."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

try:
    import grpc
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.json_format import MessageToDict
    from grpc_health.v1 import health_pb2, health_pb2_grpc
    from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc
except ModuleNotFoundError as error:
    raise unittest.SkipTest("Install requirements-grpc.txt to run the optional gRPC tests") from error

from a100sim.rpc.backend import RegisteredOperation, SimulatorBackend
from a100sim.rpc.codec import Task, decode
from a100sim.rpc.config import Settings
from a100sim.rpc.jobs import JobManager
from a100sim.rpc.server import build_server
from a100sim.rpc.storage import ArtifactStore, AssetStore
from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc
from a100sim.service_queue import JobQueue, start_http


class ControlledBackend:
    """Model worker admission in tests without claiming physics or rendering behavior."""

    def __init__(self) -> None:
        self.executions = 0
        self.release = asyncio.Event()
        self.release.set()
        self.artifact: Path | None = None

    async def start(self) -> None:
        """Keep the test backend independent of network worker startup."""

    async def close(self) -> None:
        """Leave no external worker connections to close."""

    async def describe(self) -> list[pb.Capability]:
        """Identify the backend explicitly as a test double."""
        return [pb.Capability(name="test.controlled", availability=pb.AVAILABLE)]

    async def execute(self, task: Task, directory: Path) -> tuple[dict[str, Any], dict[str, float]]:
        """Wait on a controllable barrier before returning a structural result."""
        self.executions += 1
        await self.release.wait()
        return {
            "operation": task.operation,
            "arguments": task.arguments,
            **({"file": str(self.artifact)} if self.artifact else {}),
        }, {}


class GrpcContractTests(unittest.IsolatedAsyncioTestCase):
    """Verify caller-visible behavior over a real localhost HTTP/2 transport."""

    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(
            self.root / "state",
            self.root / "assets",
            (self.root / "results",),
            {},
            Path(__file__).resolve().parents[1],
            capacity=2,
            workers=1,
        )
        self.backend = ControlledBackend()
        self.manager = JobManager(self.settings, self.backend)
        await self.manager.start()
        self.server, port = build_server(self.manager, "127.0.0.1:0", token="test-token")
        await self.server.start()
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        self.client = rpc.SimulatorStub(self.channel)
        self.metadata = (("authorization", "Bearer test-token"),)

    async def asyncTearDown(self) -> None:
        await self.channel.close()
        await self.server.stop(0)
        await self.manager.close()
        self.temporary.cleanup()

    async def submit(self, request_id: str, **task: Any) -> pb.Job:
        """Submit using the same public metadata as an authenticated remote caller."""
        return await self.client.SubmitTask(
            pb.SubmitTaskRequest(request_id=request_id, **task), metadata=self.metadata, timeout=2
        )

    async def finish(self, job: pb.Job) -> pb.Job:
        """Read the task's terminal revision without client-side polling."""
        async for job in self.client.WatchJob(pb.WatchJobRequest(job_id=job.job_id), metadata=self.metadata, timeout=3):
            pass
        return job

    async def test_authentication_covers_unary_and_streaming_methods(self) -> None:
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.GetCapabilities(Empty(), timeout=1)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            async for _ in self.client.WatchJob(pb.WatchJobRequest(job_id="x"), timeout=1):
                pass
        self.assertEqual(failure.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
        info = await self.client.GetCapabilities(Empty(), metadata=self.metadata, timeout=1)
        self.assertEqual(info.api_version, "a100sim.v1")

    async def test_standard_health_and_reflection_share_the_rpc_authentication(self) -> None:
        health = health_pb2_grpc.HealthStub(self.channel)
        status = await health.Check(
            health_pb2.HealthCheckRequest(service="a100sim.v1.Simulator"), metadata=self.metadata, timeout=2
        )
        self.assertEqual(status.status, health_pb2.HealthCheckResponse.SERVING)
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await health.Check(health_pb2.HealthCheckRequest(), timeout=1)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

        async def list_services() -> Any:
            yield reflection_pb2.ServerReflectionRequest(list_services="")

        inspector = reflection_pb2_grpc.ServerReflectionStub(self.channel)
        replies = [
            reply async for reply in inspector.ServerReflectionInfo(list_services(), metadata=self.metadata, timeout=2)
        ]
        self.assertIn("a100sim.v1.Simulator", [item.name for item in replies[0].list_services_response.service])

    async def test_idempotency_executes_once_and_rejects_changed_payload(self) -> None:
        requests = [self.submit("same-id", step=pb.StepRequest(steps=3)) for _ in range(12)]
        jobs = await asyncio.gather(*requests)
        self.assertEqual(len({job.job_id for job in jobs}), 1)
        job = await self.finish(jobs[0])
        self.assertEqual(job.state, pb.SUCCEEDED)
        self.assertEqual(self.backend.executions, 1)
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.submit("same-id", step=pb.StepRequest(steps=4))
        self.assertEqual(failure.exception.code(), grpc.StatusCode.ALREADY_EXISTS)

    async def test_queue_backpressure_and_queued_only_cancellation(self) -> None:
        self.backend.release.clear()
        active = await self.submit("active", snapshot=Empty())
        for _ in range(50):
            if self.backend.executions:
                break
            await asyncio.sleep(0.002)
        queued = await self.submit("queued", snapshot=Empty())
        await self.submit("queued-2", snapshot=Empty())
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.submit("overflow", snapshot=Empty())
        self.assertEqual(failure.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
        cancelled = await self.client.CancelJob(pb.JobRequest(job_id=queued.job_id), metadata=self.metadata)
        self.assertEqual(cancelled.state, pb.CANCELLED)
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.CancelJob(pb.JobRequest(job_id=active.job_id), metadata=self.metadata)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        self.backend.release.set()
        await self.finish(active)
        await asyncio.wait_for(self.manager.queue.join(), timeout=2)
        self.assertEqual(self.backend.executions, 2)

    async def test_watch_deadline_does_not_cancel_or_duplicate_mutation(self) -> None:
        self.backend.release.clear()
        job = await self.submit("deadline", step=pb.StepRequest(steps=5))
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            async for _ in self.client.WatchJob(
                pb.WatchJobRequest(job_id=job.job_id), timeout=0.03, metadata=self.metadata
            ):
                pass
        self.assertEqual(failure.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        retry = await self.submit("deadline", step=pb.StepRequest(steps=5))
        self.assertEqual(retry.job_id, job.job_id)
        self.backend.release.set()
        self.assertEqual((await self.finish(retry)).state, pb.SUCCEEDED)
        self.assertEqual(self.backend.executions, 1)

    async def test_asset_upload_download_resume_and_job_ownership(self) -> None:
        contents = b"uploaded robot description"

        async def upload(path: str = "robot.urdf", digest: str = "") -> Any:
            yield pb.UploadChunk(asset_id="robot", relative_path=path, data=contents[:7], sha256=digest)
            yield pb.UploadChunk(data=contents[7:])

        uploaded = await self.client.UploadAsset(
            upload(digest=hashlib.sha256(contents).hexdigest()), metadata=self.metadata
        )
        self.assertEqual(uploaded.size_bytes, len(contents))
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.UploadAsset(upload(), metadata=self.metadata)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.ALREADY_EXISTS)
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.UploadAsset(upload("../escape"), metadata=self.metadata)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(grpc.aio.AioRpcError):
            await self.client.UploadAsset(upload("bad.urdf", "0" * 64), metadata=self.metadata)
        self.assertFalse((self.settings.asset_root / "robot/bad.urdf").exists())
        result = self.root / "results" / "image.png"
        result.parent.mkdir()
        result.write_bytes(contents)
        self.backend.artifact = result
        job = await self.finish(await self.submit("artifact", snapshot=Empty()))
        self.assertEqual(len(job.artifacts), 1)
        self.assertTrue(MessageToDict(job.result)["file"].startswith("artifact://"))
        blocks = [
            chunk.data
            async for chunk in self.client.DownloadArtifact(
                pb.DownloadRequest(
                    job_id=job.job_id, artifact_id=job.artifacts[0].artifact_id, offset=7, chunk_bytes=4
                ),
                metadata=self.metadata,
            )
        ]
        self.assertEqual(b"".join(blocks), contents[7:])
        self.backend.artifact = None
        another = await self.finish(await self.submit("other", snapshot=Empty()))
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            async for _ in self.client.DownloadArtifact(
                pb.DownloadRequest(job_id=another.job_id, artifact_id=job.artifacts[0].artifact_id),
                metadata=self.metadata,
            ):
                pass
        self.assertEqual(failure.exception.code(), grpc.StatusCode.NOT_FOUND)

    async def test_ordered_duplex_stream_and_unknown_jobs(self) -> None:
        async def tasks() -> Any:
            for i in range(4):
                yield pb.SubmitTaskRequest(request_id=f"ordered-{i}", step=pb.StepRequest(steps=i + 1))

        jobs = [job async for job in self.client.RunTasks(tasks(), metadata=self.metadata, timeout=3)]
        self.assertEqual([job.request_id for job in jobs], [f"ordered-{i}" for i in range(4)])
        self.assertTrue(all(job.state == pb.SUCCEEDED for job in jobs))
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.GetJob(pb.JobRequest(job_id="missing"), metadata=self.metadata)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.NOT_FOUND)

    async def test_restart_preserves_deduplication_and_does_not_replay(self) -> None:
        self.backend.release.clear()
        job = await self.submit("restart", snapshot=Empty())
        await self.server.stop(0)
        await self.manager.close()
        self.manager = JobManager(self.settings, self.backend)
        await self.manager.start()
        recovered = await self.manager.submit(pb.SubmitTaskRequest(request_id="restart", snapshot=Empty()))
        self.assertEqual(recovered.job_id, job.job_id)
        self.assertEqual(recovered.state, pb.INTERRUPTED)
        self.assertTrue(self.manager.queue.empty())

    async def test_watch_delivers_terminal_revision_if_job_finishes_between_reads(self) -> None:
        self.backend.release.clear()
        job = await self.manager.submit(pb.SubmitTaskRequest(request_id="watch-race", snapshot=Empty()))
        updates = self.manager.observe(job.job_id)
        first = await anext(updates)
        self.assertIn(first.state, (pb.QUEUED, pb.RUNNING))
        self.backend.release.set()
        await asyncio.wait_for(self.manager.queue.join(), timeout=2)
        terminal = await anext(updates)
        self.assertEqual(terminal.state, pb.SUCCEEDED)
        self.assertGreater(terminal.revision, first.revision)
        await updates.aclose()


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    """Check typed operation coverage and actual internal HTTP job observation."""

    async def test_registered_operation_exposes_schema_and_job_scoped_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / "state", root / "assets", (root / "state/workflows",), {}, root)
            backend = SimulatorBackend(settings)

            async def write_sample(arguments: dict[str, Any], directory: Path) -> dict[str, Any]:
                path = directory / "sample.txt"
                path.write_text(str(arguments["value"]), encoding="utf-8")
                return {"file": str(path), "value": arguments["value"]}

            backend.register(
                "write_sample",
                RegisteredOperation(write_sample, "test file output", {"type": "object", "required": ["value"]}),
            )
            manager = JobManager(settings, backend)
            await manager.start()
            try:
                capabilities = await backend.describe()
                operation = next(item for item in capabilities if item.name == "extension.write_sample")
                self.assertEqual(operation.availability, pb.AVAILABLE)
                self.assertEqual(operation.details["parameter_schema"]["required"], ["value"])
                job = await manager.submit(
                    pb.SubmitTaskRequest(
                        request_id="registered",
                        extension=pb.ExtensionRequest(operation="write_sample", arguments={"value": 3}),
                    )
                )
                async for job in manager.observe(job.job_id):
                    pass
                self.assertEqual(job.state, pb.SUCCEEDED, str(job.failure))
                self.assertEqual(job.result["value"], 3)
                self.assertEqual(len(job.artifacts), 1)
                self.assertTrue(job.result["file"].startswith("artifact://"))
            finally:
                await manager.close()

    async def test_protocol_maps_every_builtin_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary), 1024)
            robot = assets.root / "robot"
            robot.mkdir()
            (robot / "robot.urdf").write_text("<robot/>")
            (robot / "scene.glb").write_bytes(b"glb")
            requests = {
                "simulate": pb.SimulateRequest(bodies=[pb.Body(shape=pb.MESH, asset_id="robot")]),
                "render_asset": pb.RenderSettings(),
                "snapshot": Empty(),
                "step": pb.StepRequest(steps=1),
                "control": pb.ControlRequest(targets=[pb.JointTargets(values=[0, 0])]),
                "dynamics": Empty(),
                "scan": Empty(),
                "capture": pb.CaptureRequest(),
                "gripper": pb.GripperRequest(operation="cycle"),
                "trajectory": pb.TrajectoryRequest(),
                "differential_drive": pb.DifferentialDriveRequest(),
                "rl_step": pb.ControlRequest(targets=[pb.JointTargets(values=[0, 0])]),
                "cloth": pb.ClothRequest(),
                "import_robot": pb.ImportRobotRequest(asset_id="robot", relative_path="robot.urdf"),
                "newton": pb.NewtonRequest(),
                "convert_asset": pb.ConvertAssetRequest(asset_id="robot", relative_path="scene.glb"),
                "export_scene": Empty(),
                "inspect_gains": Empty(),
                "ros2_status": Empty(),
            }
            for name, message in requests.items():
                with self.subTest(name=name):
                    task = decode(pb.SubmitTaskRequest(request_id="test", **{name: message}), assets)
                    self.assertEqual(task.operation, name)
            zero = decode(
                pb.SubmitTaskRequest(request_id="zero", simulate=pb.SimulateRequest(gravity=0, ground=False)), assets
            )
            self.assertEqual(zero.arguments["gravity"], 0)
            self.assertFalse(zero.arguments["ground"])
            with self.assertRaises(ValueError):
                decode(
                    pb.SubmitTaskRequest(request_id="nan", simulate=pb.SimulateRequest(gravity=float("nan"))), assets
                )

    async def test_real_internal_job_queue_supports_long_poll_and_artifacts(self) -> None:
        class Worker(JobQueue):
            def process(self, payload: dict[str, Any], directory: Path) -> dict[str, Any]:
                time.sleep(0.03)
                path = directory / "result.bin"
                path.write_bytes(b"worker-output")
                return {"echo": payload["bodies"], "result_file": str(path)}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = Worker(root / "results")
            state.ready = True
            http = start_http(state, "127.0.0.1", 0, "/simulate")
            threading.Thread(target=lambda: state.run_one(state.jobs.get()), daemon=True).start()
            settings = Settings(
                root / "state",
                root / "assets",
                (root / "results",),
                {"generic": f"http://127.0.0.1:{http.server_port}"},
                root,
            )
            backend = SimulatorBackend(settings)
            manager = JobManager(settings, backend)
            await manager.start()
            try:
                job = await manager.submit(
                    pb.SubmitTaskRequest(
                        request_id="http", simulate=pb.SimulateRequest(bodies=[pb.Body(id="cube")], route=pb.GENERIC)
                    )
                )
                async for job in manager.observe(job.job_id):
                    pass
                self.assertEqual(job.state, pb.SUCCEEDED, str(job.failure))
                self.assertGreater(job.timings.upstream_execution_seconds, 0.02)
                self.assertEqual(len(job.artifacts), 1)
                self.assertTrue(
                    ArtifactStore(settings.artifact_roots)
                    .validate(manager.get_record(job.job_id)["artifacts"][0]["path"])
                    .is_file()
                )
            finally:
                await manager.close()
                await asyncio.to_thread(http.shutdown)
                http.server_close()


if __name__ == "__main__":
    unittest.main()
