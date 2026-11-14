"""Reuse independent simulator workers behind a transport-neutral backend."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiohttp
import grpc
from google.protobuf.struct_pb2 import Struct

from a100sim.rpc.codec import Task
from a100sim.rpc.config import Settings
from a100sim.rpc.errors import ServiceError
from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.workflows import Workflows

FEATURE_ACTIONS = {
    "snapshot": "snapshot",
    "step": "step",
    "control": "command",
    "dynamics": "dynamics",
    "scan": "scan",
    "capture": "capture",
    "gripper": "gripper",
    "trajectory": "trajectory",
    "differential_drive": "differential_drive",
    "rl_step": "rl_step",
    "cloth": "cloth",
    "export_scene": "export_scene",
    "inspect_gains": "inspect_gains",
}
SCOPES = {
    "simulate": "CUDA PhysX rigid bodies, mesh collision, USD output and optional CUDA rendering",
    "render_asset": "Preloaded mesh/Gaussian asset and configurable camera; fixed image resolution",
    "snapshot": "Persistent CUDA robot state and IMU, contact, joint-state and effort readings",
    "step": "Advance the persistent robot world by a bounded number of physics steps",
    "control": "Position, velocity or effort targets on the configured two-joint arms",
    "dynamics": "Jacobian, generalized mass matrix, actuation and projected joint forces",
    "scan": "180-beam planar collision-geometry scan; no optical signal model",
    "capture": "CUDA RGB, metric depth/range, world normals and explicit instance/semantic IDs",
    "gripper": "Constraint-based grasp, lift and release; no verified native Surface Gripper",
    "trajectory": "Generate and execute joint trajectories on the configured arms",
    "differential_drive": "Calculate wheel-speed commands; no mobile robot dynamics",
    "rl_step": "Batched observation/reward step; no training algorithm or Isaac Lab integration",
    "cloth": "Independent Newton XPBD cloth; no bidirectional PhysX coupling",
    "export_scene": "Export the persistent robot scene using current CUDA body poses",
    "inspect_gains": "Discover joints and effective inertia; no automatic gain optimization",
    "ros2_status": "Inspect the bundled ROS 2 bridge and its topic/command counters",
    "import_robot": "Import uploaded URDF/MJCF into USD in an isolated simulator process",
    "newton": "Isolated XPBD or MuJoCo native-tensor numerical acceptance workflow",
    "convert_asset": "Convert GLB to linear vertex-baked base color, metallic and roughness; inspect warnings",
    "randomize": "Seeded uniform body placement, linear color, metallic, roughness and mass randomization",
}
UNAVAILABLE_SCOPES = {
    "rtx.camera": (pb.UNSUPPORTED, "A100 has no RT Cores; the native RTX camera renderer is unsupported"),
    "rtx.lidar_radar_acoustic": (
        pb.UNSUPPORTED,
        "Native RTX nonvisual sensor signal models require supported RTX hardware",
    ),
    "replicator.writer": (pb.UNAVAILABLE, "Native render-product writers are not integrated into this A100 adapter"),
    "isaac_lab.training": (
        pb.UNAVAILABLE,
        "Isaac Lab, Torch and a training task have not been integrated or validated",
    ),
    "motion.ik_cumotion_pink": (
        pb.UNVERIFIED,
        "Robot configuration, planner dependencies and numerical acceptance are required",
    ),
    "physics.deformables_particles": (pb.UNVERIFIED, "Only independent XPBD cloth has A100 acceptance evidence"),
    "sensors.tactile": (
        pb.UNVERIFIED,
        "Sensor assets and their physics/rendering dependencies need separate validation",
    ),
    "teleoperation_sdg": (pb.UNAVAILABLE, "Teleoperation inputs and recording workflows have not been integrated"),
    "viewport.webrtc": (pb.UNAVAILABLE, "Native RTX viewport streaming is not provided by this deployment"),
    "asset.transformer": (
        pb.UNVERIFIED,
        "The validated import path disables asset transformer and multi-physics conversion",
    ),
    "asset.cad_and_urdf_export": (pb.UNAVAILABLE, "CAD conversion and URDF export have no registered RPC workflow"),
    "scene.arbitrary_editing": (pb.UNAVAILABLE, "Arbitrary USD stage editing has not been integrated"),
    "motion.policy_playback": (pb.UNAVAILABLE, "Policy dependencies and robot-specific playback are not integrated"),
    "navigation.closed_loop": (
        pb.UNVERIFIED,
        "Nav2, MoveIt 2 and ros2_control integration require separate acceptance",
    ),
    "physics.vehicles_and_differentiation": (
        pb.UNVERIFIED,
        "Vehicle models and differentiable simulation workflows require separate acceptance",
    ),
}


class TaskBackend(Protocol):
    """Define the simulator lifecycle and operations required by the task manager."""

    async def start(self) -> None:
        """Open simulator connections."""
        ...

    async def close(self) -> None:
        """Release simulator connections."""
        ...

    async def describe(self) -> list[pb.Capability]:
        """Describe operations and their current availability."""
        ...

    async def execute(self, task: Task, directory: Path) -> tuple[dict[str, Any], dict[str, float]]:
        """Execute one task and return its result and backend timings."""
        ...


@dataclass(frozen=True)
class RegisteredOperation:
    """An operator-installed simulator operation with a discoverable parameter schema."""

    handler: Callable[[dict[str, Any], Path], Awaitable[dict[str, Any]]]
    scope: str
    parameter_schema: dict[str, Any]


class SimulatorBackend:
    """Own internal worker connections; callers never receive these addresses."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workflows = Workflows(settings)
        self.session: aiohttp.ClientSession | None = None
        self.operations: dict[str, RegisteredOperation] = {}
        self._health_cache: dict[str, dict[str, Any]] = {}
        self._health_checked = 0.0
        self._health_lock = asyncio.Lock()
        self._feature_lock = asyncio.Lock()

    async def start(self) -> None:
        """Create persistent internal connections without importing the simulator runtime."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=40),
            connector=aiohttp.TCPConnector(limit=self.settings.workers * 2 + 8),
            trust_env=False,
        )

    async def close(self) -> None:
        """Close the internal connection pool."""
        if self.session is not None:
            await self.session.close()

    def register(self, name: str, operation: RegisteredOperation) -> None:
        """Register trusted simulator-side functionality before accepting requests."""
        if not name or name in self.operations:
            raise ValueError("Registered operation names must be nonempty and unique")
        self.operations[name] = operation

    async def _request(
        self, backend: str, route: str, payload: dict[str, Any] | None = None, *, timeout: float = 40
    ) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("The backend connection pool has not started")
        try:
            async with self.session.request(
                "POST" if payload is not None else "GET",
                self.settings.backend_urls[backend] + route,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                value = await response.json()
                if response.status >= 400:
                    code = {
                        400: grpc.StatusCode.INVALID_ARGUMENT,
                        404: grpc.StatusCode.NOT_FOUND,
                        429: grpc.StatusCode.RESOURCE_EXHAUSTED,
                        503: grpc.StatusCode.UNAVAILABLE,
                    }.get(response.status, grpc.StatusCode.INTERNAL)
                    # Internal addresses and filesystem paths stay in server logs.
                    raise ServiceError(code, f"Simulator worker rejected the operation (HTTP {response.status})")
                if not isinstance(value, dict):
                    raise ServiceError(grpc.StatusCode.INTERNAL, "Simulator worker returned an invalid result")
                return value
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ServiceError(
                grpc.StatusCode.UNAVAILABLE, "Simulator worker is unavailable; accepted work is not retried"
            ) from error

    async def health(self) -> dict[str, dict[str, Any]]:
        """Coalesce capability discovery and cache worker state for one second."""
        async with self._health_lock:
            if time.monotonic() - self._health_checked < 1:
                return self._health_cache

            async def probe(name: str) -> tuple[str, dict[str, Any]]:
                try:
                    return name, await self._request(name, "/health", timeout=1)
                except ServiceError:
                    return name, {"status": "unavailable"}

            self._health_cache = dict(await asyncio.gather(*(probe(name) for name in self.settings.backend_urls)))
            self._health_checked = time.monotonic()
            return self._health_cache

    async def describe(self) -> list[pb.Capability]:
        """Report live availability separately from historical feature acceptance."""
        health = await self.health()
        feature = health.get("features", {})
        capabilities = []
        for name, scope in SCOPES.items():
            if name in ("import_robot", "newton", "convert_asset"):
                available = self.workflows.is_available(name)
            elif name in ("simulate", "randomize"):
                available = any(health.get(key, {}).get("status") == "ready" for key in ("resident", "generic"))
            elif name == "render_asset":
                available = health.get("render", {}).get("status") == "ready"
            else:
                available = feature.get("status") == "ready" and (
                    bool(feature.get("ros2"))
                    if name == "ros2_status"
                    else FEATURE_ACTIONS[name] in feature.get("actions", [])
                )
            details = Struct()
            if name in FEATURE_ACTIONS:
                details.update(
                    {key: feature[key] for key in ("arm_count", "controls", "sensors", "scan_beams") if key in feature}
                )
            if name == "render_asset":
                details.update({"loaded_kinds": health.get("render", {}).get("loaded_kinds", [])})
            capabilities.append(
                pb.Capability(
                    name=name,
                    availability=pb.AVAILABLE if available else pb.UNAVAILABLE,
                    reason="" if available else "Required simulator worker or workflow is not ready",
                    scope=scope,
                    details=details,
                )
            )
        for name, (state, reason) in UNAVAILABLE_SCOPES.items():
            capabilities.append(pb.Capability(name=name, availability=state, reason=reason))
        for name, operation in self.operations.items():
            details = Struct()
            details.update({"parameter_schema": operation.parameter_schema})
            capabilities.append(
                pb.Capability(
                    name="extension." + name, availability=pb.AVAILABLE, scope=operation.scope, details=details
                )
            )
        return capabilities

    async def execute(self, task: Task, directory: Path) -> tuple[dict[str, Any], dict[str, float]]:
        """Serialize persistent-world requests and preserve independent simulation concurrency."""
        if task.operation in FEATURE_ACTIONS:
            async with self._feature_lock:
                return await self._execute(task, directory)
        return await self._execute(task, directory)

    async def _execute(self, task: Task, directory: Path) -> tuple[dict[str, Any], dict[str, float]]:
        if task.operation.startswith("extension."):
            operation = self.operations.get(task.operation.removeprefix("extension."))
            if operation is None:
                raise ServiceError(grpc.StatusCode.UNIMPLEMENTED, "The requested extension operation is not registered")
            directory.mkdir(parents=True, exist_ok=True)
            return await operation.handler(task.arguments, directory), {}
        if task.operation in ("import_robot", "newton", "convert_asset"):
            return await self.workflows.execute(task, directory), {}
        if task.operation == "ros2_status":
            health = (await self.health()).get("features", {})
            if health.get("status") != "ready" or not health.get("ros2"):
                raise ServiceError(grpc.StatusCode.UNAVAILABLE, "The ROS 2 data bridge is not ready")
            return {key: val for key, val in health.items() if key.startswith("ros2")}, {}
        payload = dict(task.arguments)
        randomization = payload.pop("_randomization", None)
        if task.operation in ("simulate", "randomize"):
            route = payload.pop("route", "auto")
            health = await self.health()
            backend = (
                "resident" if route == "auto" and health.get("resident", {}).get("status") == "ready" else "generic"
            )
            endpoint = "/simulate"
        elif task.operation == "render_asset":
            backend, endpoint = "render", "/render"
        elif task.operation in FEATURE_ACTIONS:
            backend, endpoint = "features", "/robotics"
            payload["action"] = FEATURE_ACTIONS[task.operation]
        else:
            raise ServiceError(grpc.StatusCode.UNIMPLEMENTED, "Operation is not implemented on this simulator")
        started = time.perf_counter()
        accepted = await self._request(backend, endpoint, payload)
        submit_seconds = time.perf_counter() - started
        upstream_id = accepted.get("id")
        if (
            not isinstance(upstream_id, str)
            or len(upstream_id) != 32
            or not all(c in "0123456789abcdef" for c in upstream_id)
        ):
            raise ServiceError(grpc.StatusCode.INTERNAL, "Worker returned an invalid job identity")
        # Resident workers support bounded waits in their original protocol. Other
        # workers advertise the capability; older workers use read-only polling.
        long_poll = backend == "resident" or (await self.health()).get(backend, {}).get("job_long_poll") is True
        status_route = "/jobs/" + upstream_id + ("?wait_ms=30000" if long_poll else "")
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            value = await self._request(backend, status_route)
            state = value.get("state")
            if state == "succeeded":
                if randomization is not None:
                    value["randomization"] = randomization
                return value, {
                    "submit_seconds": submit_seconds,
                    "backend_wait_seconds": time.perf_counter() - started - submit_seconds,
                    "upstream_queue_seconds": value.get("queue_seconds", 0),
                    "upstream_execution_seconds": value.get("execution_seconds", 0),
                }
            if state in ("failed", "interrupted"):
                raise ServiceError(grpc.StatusCode.INTERNAL, "Simulator job failed; inspect the simulator worker log")
            if not long_poll:
                await asyncio.sleep(0.02)
        raise ServiceError(
            grpc.StatusCode.DEADLINE_EXCEEDED, "Worker exceeded the execution limit; its work was not retried"
        )
