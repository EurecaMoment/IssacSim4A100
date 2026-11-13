"""Serve the versioned simulator contract on a bounded asynchronous gRPC server."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from a100sim.rpc.backend import SimulatorBackend
from a100sim.rpc.config import Settings
from a100sim.rpc.errors import ServiceError
from a100sim.rpc.jobs import JobManager
from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc

LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "a100sim.v1.Simulator"


@asynccontextmanager
async def handle_errors(context: grpc.aio.ServicerContext) -> AsyncIterator[None]:
    """Translate expected boundary errors without leaking backend addresses or paths."""
    try:
        yield
    except ServiceError as error:
        await context.abort(error.code, str(error))
    except (ValueError, TypeError, KeyError) as error:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
    except OSError:
        LOGGER.exception("RPC filesystem operation failed")
        await context.abort(grpc.StatusCode.INTERNAL, "File operation failed; inspect gateway logs")
    except Exception:
        LOGGER.exception("Unexpected RPC failure")
        await context.abort(grpc.StatusCode.INTERNAL, "Gateway operation failed; inspect gateway logs")


class TokenInterceptor(grpc.aio.ServerInterceptor):
    """Apply one operator-supplied bearer credential to all RPCs, including reflection."""

    def __init__(self, token: str) -> None:
        self.expected = ("Bearer " + token).encode()

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]],
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Reject unauthorized calls while preserving each method's streaming shape."""
        handler = await continuation(details)
        supplied = dict(details.invocation_metadata).get("authorization", "").encode()
        if handler is None or hmac.compare_digest(supplied, self.expected):
            return handler

        async def deny(_request: Any, context: grpc.aio.ServicerContext) -> None:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "A valid gateway bearer credential is required")

        async def deny_stream(request: Any, context: grpc.aio.ServicerContext) -> AsyncIterator[None]:
            await deny(request, context)
            if False:
                yield None

        key = (
            ("stream" if handler.request_streaming else "unary")
            + "_"
            + ("stream" if handler.response_streaming else "unary")
        )
        return handler._replace(**{key: deny_stream if handler.response_streaming else deny})


class SimulatorService(rpc.SimulatorServicer):
    """Implement the simulator-side API independently of client orchestration."""

    def __init__(self, manager: JobManager) -> None:
        self.manager = manager

    async def GetCapabilities(self, request: Empty, context: grpc.aio.ServicerContext) -> pb.Capabilities:
        """Return live simulator availability and explicit unsupported functionality."""
        async with handle_errors(context):
            capabilities = await self.manager.backend.describe()
            return pb.Capabilities(
                api_version="a100sim.v1",
                simulator_version="6.2.0.0",
                capabilities=capabilities,
                queue_capacity=self.manager.settings.capacity,
                queued_jobs=self.manager.queue.qsize(),
                running_jobs=sum(r["state"] == "RUNNING" for r in self.manager.records.values()),
            )

    async def SubmitTask(self, request: pb.SubmitTaskRequest, context: grpc.aio.ServicerContext) -> pb.Job:
        """Accept a durable task once; caller deadlines do not revoke accepted work."""
        async with handle_errors(context):
            return await self.manager.submit(request)

    async def GetJob(self, request: pb.JobRequest, context: grpc.aio.ServicerContext) -> pb.Job:
        """Return a retained job snapshot."""
        async with handle_errors(context):
            return self.manager.encode(self.manager.get_record(request.job_id))

    async def WatchJob(self, request: pb.WatchJobRequest, context: grpc.aio.ServicerContext) -> AsyncIterator[pb.Job]:
        """Stream revision changes until the task reaches a terminal state."""
        async with handle_errors(context):
            async for job in self.manager.observe(request.job_id, request.after_revision):
                yield job

    async def CancelJob(self, request: pb.JobRequest, context: grpc.aio.ServicerContext) -> pb.Job:
        """Cancel a task only while it is still queued inside the gateway."""
        async with handle_errors(context):
            return await self.manager.cancel(request.job_id)

    async def RunTasks(
        self, requests: AsyncIterator[pb.SubmitTaskRequest], context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.Job]:
        """Execute an ordered control stream with one task in flight per stream."""
        async with handle_errors(context):
            async for request in requests:
                job = await self.manager.submit(request)
                async for job in self.manager.observe(job.job_id):
                    pass
                yield job

    async def ListAssets(self, request: Empty, context: grpc.aio.ServicerContext) -> pb.AssetList:
        """List handles that can be referenced by simulation or import requests."""
        async with handle_errors(context):
            return await asyncio.to_thread(self.manager.assets.list_assets)

    async def UploadAsset(
        self, requests: AsyncIterator[pb.UploadChunk], context: grpc.aio.ServicerContext
    ) -> pb.AssetFile:
        """Publish one immutable asset file with stream flow control and SHA-256 validation."""
        async with handle_errors(context):
            return await self.manager.assets.upload(requests)

    async def DownloadArtifact(
        self, request: pb.DownloadRequest, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.DownloadChunk]:
        """Stream only artifacts belonging to the requested job with optional resume."""
        async with handle_errors(context):
            record = self.manager.get_record(request.job_id)
            item = next(
                (item for item in record.get("artifacts", []) if item["artifact_id"] == request.artifact_id), None
            )
            if item is None:
                raise ServiceError(grpc.StatusCode.NOT_FOUND, "Artifact does not belong to this job")
            chunk_size = request.chunk_bytes if request.HasField("chunk_bytes") else 256 * 1024
            if not 1 <= chunk_size <= 1024 * 1024 or request.offset > item["size_bytes"]:
                raise ValueError("Chunk size must be 1 byte–1 MiB and offset must not exceed file size")
            path = self.manager.artifacts.validate(item["path"])
            with path.open("rb") as stream:
                stream.seek(request.offset)
                offset = request.offset
                while block := await asyncio.to_thread(stream.read, chunk_size):
                    yield pb.DownloadChunk(offset=offset, data=block)
                    offset += len(block)


def build_server(
    manager: JobManager, listen: str, *, token: str = "", certificate: bytes = b"", private_key: bytes = b""
) -> tuple[grpc.aio.Server, int]:
    """Bind a server with reflection, standard health and bounded message/concurrency limits."""
    if bool(certificate) != bool(private_key):
        raise ValueError("TLS requires both certificate and private key")
    if not listen.startswith(("127.0.0.1:", "localhost:", "[::1]:")) and not (token and certificate):
        raise ValueError("A non-loopback listener requires TLS and A100SIM_GRPC_TOKEN")
    server = grpc.aio.server(
        interceptors=[TokenInterceptor(token)] if token else [],
        maximum_concurrent_rpcs=256,
        options=[
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
        ],
    )
    rpc.add_SimulatorServicer_to_server(SimulatorService(manager), server)
    checker = health.HealthServicer()
    checker.set("", health_pb2.HealthCheckResponse.SERVING)
    checker.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(checker, server)
    reflection.enable_server_reflection((SERVICE_NAME, health.SERVICE_NAME, reflection.SERVICE_NAME), server)
    port = (
        server.add_secure_port(listen, grpc.ssl_server_credentials(((private_key, certificate),)))
        if certificate
        else (server.add_insecure_port(listen))
    )
    if not port:
        raise RuntimeError("Could not bind the configured gRPC address")
    return server, port


async def serve(settings: Settings) -> None:
    """Run until interrupted, keeping SDK dependencies outside the simulator process."""
    manager = JobManager(settings, SimulatorBackend(settings))
    certificate = (
        Path(os.environ["A100SIM_GRPC_TLS_CERT"]).read_bytes() if os.environ.get("A100SIM_GRPC_TLS_CERT") else b""
    )
    private_key = (
        Path(os.environ["A100SIM_GRPC_TLS_KEY"]).read_bytes() if os.environ.get("A100SIM_GRPC_TLS_KEY") else b""
    )
    server, _ = build_server(
        manager, settings.listen, token=settings.token, certificate=certificate, private_key=private_key
    )
    await manager.start()
    try:
        await server.start()
        LOGGER.info("Simulator gRPC gateway listening on %s", settings.listen)
        stopped = asyncio.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopped.set())
        await stopped.wait()
    finally:
        await server.stop(5)
        await manager.close()


def main() -> None:
    """Launch the gateway using environment configuration and an optional bind override."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen")
    args = parser.parse_args()
    if args.listen:
        os.environ["A100SIM_GRPC_LISTEN"] = args.listen
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve(Settings.from_environment()))


if __name__ == "__main__":
    main()
