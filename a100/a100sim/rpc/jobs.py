"""Bound and persist task execution with idempotency and revisioned observations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import grpc
from google.protobuf.struct_pb2 import Struct

from a100sim.rpc.backend import TaskBackend
from a100sim.rpc.codec import Task, decode
from a100sim.rpc.config import Settings
from a100sim.rpc.errors import ServiceError
from a100sim.rpc.storage import ArtifactStore, AssetStore
from a100sim.rpc.v1 import simulator_pb2 as pb

LOGGER = logging.getLogger(__name__)
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}


class JobManager:
    """Own bounded execution and durable deduplication in the gateway event loop."""

    def __init__(self, settings: Settings, backend: TaskBackend) -> None:
        settings.validate()
        self.settings = settings
        self.backend = backend
        self.assets = AssetStore(settings.asset_root, settings.max_upload_bytes)
        self.artifacts = ArtifactStore(settings.artifact_roots)
        settings.state_root.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(settings.state_root / "jobs.sqlite3")
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute("PRAGMA synchronous=FULL")
        self.database.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
        self.records: dict[str, dict[str, Any]] = {}
        self.request_ids: dict[str, str] = {}
        self.tasks: dict[str, Task] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue(settings.capacity)
        self.changed = asyncio.Condition()
        self.workers: list[asyncio.Task[None]] = []
        self.accepting = True
        for identity, text in self.database.execute("SELECT id, record FROM jobs").fetchall():
            record = json.loads(text)
            if record["state"] not in TERMINAL:
                record.update(
                    state="INTERRUPTED",
                    revision=record["revision"] + 1,
                    failure={"code": "ABORTED", "message": "Gateway restarted; task was not replayed"},
                )
                self._persist(record)
            self.records[identity] = record
            self.request_ids[record["request_id"]] = identity

    async def start(self) -> None:
        """Open the backend and start bounded dispatch workers."""
        await self.backend.start()
        self.workers = [asyncio.create_task(self._work(), name=f"rpc-worker-{i}") for i in range(self.settings.workers)]

    async def close(self) -> None:
        """Stop local dispatch and persist interrupted work without retrying upstream effects."""
        self.accepting = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        for record in self.records.values():
            if record["state"] not in TERMINAL:
                self._update(
                    record,
                    state="INTERRUPTED",
                    failure={"code": "ABORTED", "message": "Gateway stopped; task was not replayed"},
                )
        async with self.changed:
            self.changed.notify_all()
        await self.backend.close()
        self.database.close()

    def _persist(self, record: dict[str, Any]) -> None:
        with self.database:
            self.database.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?)", (record["job_id"], json.dumps(record, allow_nan=False))
            )

    def _update(self, record: dict[str, Any], **values: Any) -> None:
        record.update(values)
        record["revision"] += 1
        self._persist(record)

    def get_record(self, identity: str) -> dict[str, Any]:
        """Read a retained job or raise a stable NOT_FOUND error."""
        try:
            return self.records[identity]
        except KeyError as error:
            raise ServiceError(grpc.StatusCode.NOT_FOUND, "Job does not exist or has expired") from error

    def encode(self, record: dict[str, Any]) -> pb.Job:
        """Create a public snapshot without backend addresses or artifact file paths."""
        result = Struct()
        result.update(record.get("result", {}))
        return pb.Job(
            job_id=record["job_id"],
            request_id=record["request_id"],
            operation=record["operation"],
            state=pb.JobState.Value(record["state"]),
            revision=record["revision"],
            result=result,
            failure=pb.Failure(**record.get("failure", {})),
            artifacts=[
                pb.Artifact(**{key: value for key, value in item.items() if key != "path"})
                for item in record.get("artifacts", [])
            ],
            timings=pb.Timings(**record.get("timings", {})),
            created_at_unix=record["created_at_unix"],
        )

    async def submit(self, request: pb.SubmitTaskRequest) -> pb.Job:
        """Atomically deduplicate, persist and enqueue one request."""
        if not self.accepting:
            raise ServiceError(grpc.StatusCode.UNAVAILABLE, "Gateway is stopping")
        identity_message = pb.SubmitTaskRequest()
        identity_message.CopyFrom(request)
        identity_message.ClearField("request_id")
        digest = hashlib.sha256(identity_message.SerializeToString(deterministic=True)).hexdigest()
        if previous := self.request_ids.get(request.request_id):
            record = self.records[previous]
            if record["digest"] != digest:
                raise ServiceError(grpc.StatusCode.ALREADY_EXISTS, "request_id was already used for a different task")
            return self.encode(record)
        task = decode(request, self.assets)
        if self.queue.full():
            raise ServiceError(grpc.StatusCode.RESOURCE_EXHAUSTED, "Gateway queue is full; no task was accepted")
        self._prune()
        identity = uuid.uuid4().hex
        record = {
            "job_id": identity,
            "request_id": request.request_id,
            "digest": digest,
            "operation": task.operation,
            "state": "QUEUED",
            "revision": 1,
            "created_at_unix": time.time(),
        }
        self._persist(record)
        self.records[identity] = record
        self.request_ids[request.request_id] = identity
        self.tasks[identity] = task
        self.queue.put_nowait(identity)
        return self.encode(record)

    async def cancel(self, identity: str) -> pb.Job:
        """Cancel only work that has not been dispatched to any simulator worker."""
        async with self.changed:
            record = self.get_record(identity)
            if record["state"] == "RUNNING":
                raise ServiceError(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Running simulator work cannot be safely cancelled; observe its job to completion",
                )
            if record["state"] == "QUEUED":
                self._update(record, state="CANCELLED")
                self.tasks.pop(identity, None)
                self.changed.notify_all()
            return self.encode(record)

    async def observe(self, identity: str, revision: int = 0) -> AsyncIterator[pb.Job]:
        """Yield state transitions without polling the database or replaying execution."""
        while True:
            async with self.changed:
                record = self.get_record(identity)
                await self.changed.wait_for(lambda: record["revision"] > revision or record["state"] in TERMINAL)
                snapshot = self.encode(record)
            if snapshot.revision > revision:
                yield snapshot
                revision = snapshot.revision
            if snapshot.state in (pb.SUCCEEDED, pb.FAILED, pb.CANCELLED, pb.INTERRUPTED):
                return

    def _prune(self) -> None:
        if len(self.records) < self.settings.retained_jobs:
            return
        completed = sorted(
            (item for item in self.records.values() if item["state"] in TERMINAL),
            key=lambda item: item["created_at_unix"],
        )
        if not completed:
            raise ServiceError(grpc.StatusCode.RESOURCE_EXHAUSTED, "Retained job capacity is occupied by active work")
        for record in completed[: max(1, len(completed) // 4)]:
            self.records.pop(record["job_id"])
            self.request_ids.pop(record["request_id"])
            with self.database:
                self.database.execute("DELETE FROM jobs WHERE id = ?", (record["job_id"],))

    async def _work(self) -> None:
        while True:
            identity = await self.queue.get()
            record = self.records.get(identity)
            if record is None:
                self.queue.task_done()
                continue
            try:
                if record["state"] != "QUEUED":
                    continue
                started = time.perf_counter()
                timings = {"gateway_queue_seconds": time.time() - record["created_at_unix"]}
                async with self.changed:
                    self._update(record, state="RUNNING", timings=timings)
                    self.changed.notify_all()
                result, backend_timings = await self.backend.execute(
                    self.tasks[identity], self.settings.state_root / "workflows" / identity
                )
                cleaned, artifacts = await asyncio.to_thread(self.artifacts.collect, result)
                timings.update(backend_timings)
                timings["total_seconds"] = timings["gateway_queue_seconds"] + time.perf_counter() - started
                async with self.changed:
                    self._update(record, state="SUCCEEDED", result=cleaned, artifacts=artifacts, timings=timings)
                    self.changed.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = (
                    error.code
                    if isinstance(error, ServiceError)
                    else (
                        grpc.StatusCode.INVALID_ARGUMENT
                        if isinstance(error, (ValueError, TypeError))
                        else grpc.StatusCode.INTERNAL
                    )
                )
                message = (
                    str(error)
                    if isinstance(error, ServiceError)
                    else "Simulator operation failed; inspect gateway logs"
                )
                LOGGER.exception("Simulator task %s failed", identity)
                async with self.changed:
                    self._update(record, state="FAILED", failure={"code": code.name, "message": message})
                    self.changed.notify_all()
            finally:
                self.tasks.pop(identity, None)
                self.queue.task_done()
