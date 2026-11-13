"""Translate versioned protobuf requests into simulator-owned operations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from a100sim.rpc.storage import AssetStore
from a100sim.rpc.v1 import simulator_pb2 as pb


@dataclass(frozen=True)
class Task:
    """A validated operation independent of the caller and transport."""

    operation: str
    arguments: dict[str, Any]


def require_finite(value: Any) -> None:
    """Reject nonfinite numeric values anywhere in a structured request."""
    if isinstance(value, dict):
        for item in value.values():
            require_finite(item)
    elif isinstance(value, list):
        for item in value:
            require_finite(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("All numeric arguments must be finite")


def validate_numbers(message: Message) -> None:
    """Validate protobuf floats before JSON conversion can turn NaN into a string."""
    for descriptor, value in message.ListFields():
        if descriptor.message_type is not None and descriptor.message_type.GetOptions().map_entry:
            values = value.values()
        else:
            values = value if descriptor.is_repeated else [value]
        for item in values:
            if isinstance(item, Message):
                validate_numbers(item)
            else:
                require_finite(item)


def decode(request: pb.SubmitTaskRequest, assets: AssetStore) -> Task:
    """Resolve typed requests while preserving explicitly authored zero values."""
    if not request.request_id or len(request.request_id) > 128:
        raise ValueError("request_id must contain 1–128 characters")
    operation = request.WhichOneof("task")
    if operation is None:
        raise ValueError("Exactly one task is required")
    message = getattr(request, operation)
    validate_numbers(message)
    payload = MessageToDict(message, preserving_proto_field_name=True)
    require_finite(payload)
    if operation == "simulate":
        if message.route not in (pb.AUTO, pb.GENERIC):
            raise ValueError("Unknown physics route")
        payload["route"] = "generic" if message.route == pb.GENERIC else "auto"
        for index, body in enumerate(payload.get("bodies", [])):
            body["shape"] = pb.Shape.Name(message.bodies[index].shape).lower()
            if body["shape"] == "mesh":
                body["asset"] = str(assets.resolve(body.pop("asset_id", "")))
            elif body.pop("asset_id", ""):
                raise ValueError("Only mesh bodies accept asset_id")
            if "id" not in body and message.bodies[index].id:
                body["id"] = message.bodies[index].id
        if message.HasField("render"):
            if message.render.kind not in (pb.MESH_RENDER, pb.GAUSSIAN_RENDER):
                raise ValueError("Unknown render kind")
            payload["render"]["kind"] = "gaussian" if message.render.kind == pb.GAUSSIAN_RENDER else "mesh"
    elif operation == "render_asset":
        if message.kind not in (pb.MESH_RENDER, pb.GAUSSIAN_RENDER):
            raise ValueError("Unknown render kind")
        payload["kind"] = "gaussian" if message.kind == pb.GAUSSIAN_RENDER else "mesh"
        if set(payload) - {"kind", "spp", "seed", "azimuth", "elevation", "distance"}:
            raise ValueError("The preloaded renderer accepts kind, spp, seed, azimuth, elevation and distance")
    elif operation in ("control", "rl_step"):
        payload["mode"] = pb.ControlMode.Name(message.mode).lower()
        payload["arm_index"] = message.arm_index
        rows = [list(row.values) for row in message.targets]
        if not rows:
            raise ValueError("Control targets require at least one row")
        payload["targets"] = rows[0] if len(rows) == 1 else rows
    elif operation == "trajectory":
        payload["points"] = [list(point.values) for point in message.points]
    elif operation in ("import_robot", "convert_asset"):
        payload["source"] = str(assets.resolve(message.asset_id, message.relative_path))
        payload.pop("asset_id", None)
        payload.pop("relative_path", None)
    elif operation == "newton":
        if message.solver not in (pb.XPBD, pb.MUJOCO_NATIVE):
            raise ValueError("Unknown Newton solver")
        payload["solver"] = "native" if message.solver == pb.MUJOCO_NATIVE else "xpbd"
    elif operation == "extension":
        if not message.operation:
            raise ValueError("The registered extension operation is required")
        operation = "extension." + message.operation
        payload = MessageToDict(message.arguments, preserving_proto_field_name=True)
        require_finite(payload)
    return Task(operation, payload)
