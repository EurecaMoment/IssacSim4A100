"""Transfer assets and job-scoped artifacts through opaque public handles."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from typing import Any

import grpc

from a100sim.rpc.errors import ServiceError
from a100sim.rpc.v1 import simulator_pb2 as pb

ASSET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class AssetStore:
    """Keep uploaded files inside an explicitly configured asset directory."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def resolve(self, asset_id: str, relative_path: str = "", *, must_exist: bool = True) -> Path:
        """Resolve an asset handle, rejecting traversal and symbolic-link escapes."""
        if not ASSET_ID.fullmatch(asset_id):
            raise ValueError("asset_id must be a single portable directory name")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in relative_path or ":" in relative_path:
            raise ValueError("relative_path must stay inside its asset")
        directory = (self.root / asset_id).resolve()
        path = (directory / relative_path).resolve()
        if not directory.is_relative_to(self.root) or not path.is_relative_to(directory):
            raise ValueError("Asset symbolic links must stay inside the configured root")
        if must_exist and not path.exists():
            raise ServiceError(grpc.StatusCode.NOT_FOUND, "Asset file or directory does not exist")
        return path

    def list_assets(self) -> pb.AssetList:
        """List directory handles and portable relative file names."""
        assets = []
        for directory in sorted(self.root.iterdir()):
            if directory.is_dir() and not directory.is_symlink() and ASSET_ID.fullmatch(directory.name):
                files = [
                    p.relative_to(directory).as_posix()
                    for p in directory.rglob("*")
                    if p.is_file() and not p.is_symlink() and not p.name.startswith(".upload-")
                ]
                assets.append(pb.Asset(asset_id=directory.name, files=sorted(files)))
        return pb.AssetList(assets=assets)

    async def upload(self, chunks: AsyncIterator[pb.UploadChunk]) -> pb.AssetFile:
        """Atomically publish one bounded file after validating its optional digest."""
        first = await anext(chunks, None)
        if first is None or not first.relative_path:
            raise ValueError("The first upload chunk must specify relative_path")
        asset_id = first.asset_id or uuid.uuid4().hex
        destination = self.resolve(asset_id, first.relative_path, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / (".upload-" + uuid.uuid4().hex)
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as stream:
                chunk = first
                while chunk is not None:
                    if chunk.asset_id not in ("", asset_id) or chunk.relative_path not in ("", first.relative_path):
                        raise ValueError("An upload stream must contain exactly one asset file")
                    if len(chunk.data) > 1024 * 1024:
                        raise ValueError("Upload chunks must not exceed 1 MiB")
                    size += len(chunk.data)
                    if size > self.max_bytes:
                        raise ServiceError(grpc.StatusCode.RESOURCE_EXHAUSTED, "Asset exceeds the upload limit")
                    digest.update(chunk.data)
                    await asyncio.to_thread(stream.write, chunk.data)
                    chunk = await anext(chunks, None)
                if first.sha256 and digest.hexdigest() != first.sha256.lower():
                    raise ValueError("Uploaded contents do not match sha256")
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link creation is atomic and refuses to replace an existing file.
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise ServiceError(
                    grpc.StatusCode.ALREADY_EXISTS, "Asset files are immutable; use a new path"
                ) from error
            return pb.AssetFile(
                asset_id=asset_id, relative_path=first.relative_path, size_bytes=size, sha256=digest.hexdigest()
            )
        finally:
            temporary.unlink(missing_ok=True)


class ArtifactStore:
    """Replace result file paths with handles authorized for their owning job."""

    def __init__(self, roots: tuple[Path, ...]) -> None:
        self.roots = tuple(root.resolve() for root in roots)

    def validate(self, value: str) -> Path:
        """Resolve a regular file inside the configured result roots."""
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in self.roots) or not path.is_file():
            raise ServiceError(grpc.StatusCode.PERMISSION_DENIED, "Artifact is outside the configured result roots")
        return path

    def collect(self, result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build a manifest without copying or encoding large binary files in protobuf Structs."""
        artifacts: dict[str, dict[str, Any]] = {}

        def replace(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if isinstance(value, str) and len(value) < 4096 and Path(value).is_absolute():
                path = Path(value).resolve()
                if any(path.is_relative_to(root) for root in self.roots) and path.is_file():
                    identity = hashlib.sha256(str(path).encode()).hexdigest()[:32]
                    artifacts[identity] = {
                        "artifact_id": identity,
                        "name": path.name,
                        "size_bytes": path.stat().st_size,
                        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        "path": str(path),
                    }
                    return "artifact://" + identity
            return value

        cleaned = replace(result)
        return cleaned, list(artifacts.values())
