"""Measure real gRPC transport, durable admission and end-to-end job completion."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp.web
import grpc
from google.protobuf.empty_pb2 import Empty

from a100sim.rpc.backend import SimulatorBackend
from a100sim.rpc.config import Settings
from a100sim.rpc.jobs import JobManager
from a100sim.rpc.server import build_server
from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc


def summarize(values: list[float]) -> dict[str, float]:
    """Calculate observed latency distributions in milliseconds."""
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "p50_ms": ordered[math.ceil(len(ordered) * 0.50) - 1] * 1000,
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1] * 1000,
        "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1] * 1000,
        "mean_ms": statistics.mean(values) * 1000,
    }


class LoopbackFixture:
    """Serve deterministic HTTP fixture results; perform no physics or CUDA work."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.runner: aiohttp.web.AppRunner | None = None
        self.records: dict[str, dict[str, Any]] = {}

    async def start(self) -> str:
        """Bind a real HTTP backend so fixture tests include both network transports."""
        app = aiohttp.web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/simulate", self.submit)
        app.router.add_get("/jobs/{identity}", self.get_job)
        self.runner = aiohttp.web.AppRunner(app)
        await self.runner.setup()
        site = aiohttp.web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        """Release the fixture's HTTP listener."""
        if self.runner:
            await self.runner.cleanup()

    async def health(self, request: Any) -> aiohttp.web.Response:
        """Advertise the fixture explicitly as a non-simulator worker."""
        return aiohttp.web.json_response({"status": "ready", "engine": "benchmark fixture; no simulation"})

    async def submit(self, request: Any) -> aiohttp.web.Response:
        """Return a deterministic structural result with a tiny output artifact."""
        payload = await request.json()
        identity = uuid.uuid4().hex
        path = self.output / (identity + ".bin")
        await asyncio.to_thread(path.write_bytes, b"benchmark-fixture-output")
        self.records[identity] = {
            "id": identity,
            "state": "succeeded",
            "bodies": payload.get("bodies", []),
            "result_file": str(path),
            "queue_seconds": 0,
            "execution_seconds": 0,
            "scope": "HTTP transport fixture; no physics, GPU or rendering",
        }
        return aiohttp.web.json_response({"id": identity, "state": "queued"}, status=202)

    async def get_job(self, request: Any) -> aiohttp.web.Response:
        """Return the stored fixture result."""
        return aiohttp.web.json_response(self.records[request.match_info["identity"]])


def make_request(workload: str) -> pb.SubmitTaskRequest:
    """Create a unique request for a declared benchmark workload."""
    request = pb.SubmitTaskRequest(request_id=uuid.uuid4().hex)
    if workload == "snapshot":
        request.snapshot.SetInParent()
    elif workload == "capture":
        request.capture.CopyFrom(pb.CaptureRequest(size=128, spp=4))
    elif workload == "step":
        request.step.steps = 1
    else:
        request.simulate.CopyFrom(
            pb.SimulateRequest(
                bodies=[
                    pb.Body(id=f"cube-{i}", position=[(i % 4) * 0.3, (i // 4) * 0.3, 1.0], size=[0.2] * 3)
                    for i in range(16)
                ],
                steps=120,
                dt=1 / 120,
                **({"render": pb.RenderSettings(size=256, spp=4)} if workload == "render" else {}),
            )
        )
    return request


async def measure(
    client: rpc.SimulatorStub, *, workload: str, count: int, concurrency: int, metadata: tuple[Any, ...], timeout: float
) -> dict[str, Any]:
    """Measure admission, completion, queueing and simulator execution independently."""
    semaphore = asyncio.Semaphore(concurrency)
    admission: list[float] = []
    completion: list[float] = []
    queueing: list[float] = []
    upstream: list[float] = []
    cuda_jobs = 0
    failed: dict[str, int] = {}

    async def run_one() -> None:
        nonlocal cuda_jobs
        async with semaphore:
            started = time.perf_counter()
            try:
                if workload == "capabilities":
                    await client.GetCapabilities(Empty(), metadata=metadata, timeout=timeout)
                    admission.append(time.perf_counter() - started)
                else:
                    request = make_request(workload)
                    job = await client.SubmitTask(request, metadata=metadata, timeout=timeout)
                    admission.append(time.perf_counter() - started)
                    async for job in client.WatchJob(
                        pb.WatchJobRequest(job_id=job.job_id), metadata=metadata, timeout=timeout
                    ):
                        pass
                    if job.state != pb.SUCCEEDED:
                        code = job.failure.code or pb.JobState.Name(job.state)
                        failed[code] = failed.get(code, 0) + 1
                        return
                    queueing.append(job.timings.gateway_queue_seconds + job.timings.upstream_queue_seconds)
                    upstream.append(job.timings.upstream_execution_seconds)
                    if (
                        "physics_device" in job.result
                        and job.result["physics_device"] == "cuda:0"
                        and ("cpu_fallback" not in job.result or job.result["cpu_fallback"] is not True)
                    ):
                        cuda_jobs += 1
                completion.append(time.perf_counter() - started)
            except grpc.aio.AioRpcError as error:
                failed[error.code().name] = failed.get(error.code().name, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(run_one() for _ in range(count)))
    elapsed = time.perf_counter() - started
    return {
        "workload": workload,
        "concurrency": concurrency,
        "requests": count,
        "completed": len(completion),
        "cuda_jobs": cuda_jobs,
        "errors": failed,
        "elapsed_seconds": elapsed,
        "completed_per_second": len(completion) / elapsed,
        "admission": summarize(admission),
        "end_to_end": summarize(completion),
        "queue": summarize(queueing),
        "upstream_execution": summarize(upstream),
    }


async def main_async(arguments: argparse.Namespace) -> dict[str, Any]:
    """Benchmark an existing deployment or a clearly labeled local transport fixture."""
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    fixture = manager = server = channel = None
    target = arguments.target
    token = os.environ.get("A100SIM_GRPC_TOKEN", "")
    metadata = (("authorization", "Bearer " + token),) if token else ()
    try:
        if arguments.loopback_fixture:
            if any(item not in ("capabilities", "simulate", "render") for item in arguments.workloads):
                raise ValueError("The fixture supports only capabilities, simulate and render transport workloads")
            output = root / "results"
            output.mkdir()
            fixture = LoopbackFixture(output)
            url = await fixture.start()
            settings = Settings(
                root / "state", root / "assets", (output,), {"generic": url}, root, capacity=128, workers=32
            )
            manager = JobManager(settings, SimulatorBackend(settings))
            await manager.start()
            server, port = build_server(manager, "127.0.0.1:0", token=token)
            await server.start()
            target = f"127.0.0.1:{port}"
        channel = (
            grpc.aio.secure_channel(target, grpc.ssl_channel_credentials(arguments.ca.read_bytes()))
            if arguments.ca
            else (grpc.aio.insecure_channel(target))
        )
        await asyncio.wait_for(channel.channel_ready(), timeout=10)
        client = rpc.SimulatorStub(channel)
        capabilities = await client.GetCapabilities(Empty(), metadata=metadata, timeout=10)
        measurements = []
        for workload in arguments.workloads:
            await measure(
                client,
                workload=workload,
                count=arguments.warmup,
                concurrency=1,
                metadata=metadata,
                timeout=arguments.timeout,
            )
            for concurrency in arguments.concurrency:
                for trial in range(1, arguments.rounds + 1):
                    measurement = await measure(
                        client,
                        workload=workload,
                        count=arguments.requests,
                        concurrency=concurrency,
                        metadata=metadata,
                        timeout=arguments.timeout,
                    )
                    measurement["trial"] = trial
                    measurements.append(measurement)
        return {
            "scope": "loopback gRPC + HTTP fixture; no simulator/GPU work" if fixture else "live simulator gateway",
            "simulator_execution_verified": not bool(fixture) and any(item["cuda_jobs"] for item in measurements),
            "platform": platform.system(),
            "python": platform.python_version(),
            "grpcio": grpc.__version__,
            "timestamp_unix": time.time(),
            "warmup_requests_per_workload": arguments.warmup,
            "api_version": capabilities.api_version,
            "measurements": measurements,
            "success": all(not item["errors"] for item in measurements),
        }
    finally:
        if channel:
            await channel.close()
        if server:
            await server.stop(0)
        if manager:
            await manager.close()
        if fixture:
            await fixture.close()
        temporary.cleanup()


def main() -> None:
    """Write an anonymized JSON report and fail if any measured call failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--loopback-fixture", action="store_true", help="Measure transport only; no simulation")
    parser.add_argument("--ca", type=Path, help="CA certificate for a TLS deployment")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    parser.add_argument(
        "--workloads",
        nargs="+",
        default=["capabilities", "simulate"],
        choices=("capabilities", "simulate", "render", "snapshot", "step", "capture"),
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (
        not 1 <= args.requests <= 100000
        or not 1 <= args.rounds <= 20
        or not 0 <= args.warmup <= 1000
        or any(not 1 <= c <= 128 for c in args.concurrency)
    ):
        parser.error("Requests, warmup or concurrency exceed benchmark bounds")
    report = asyncio.run(main_async(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for item in report["measurements"]:
        print(
            f"{item['workload']:12} concurrency={item['concurrency']:3} "
            f"completed/s={item['completed_per_second']:.1f} "
            f"p95={item['end_to_end'].get('p95_ms', 0):.2f} ms errors={item['errors']}"
        )
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
