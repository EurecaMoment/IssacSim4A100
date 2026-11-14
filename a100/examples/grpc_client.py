"""Call the simulator exclusively through gRPC, with no simulator SDK or shared mount."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import MessageToDict

from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc


async def run(arguments: argparse.Namespace) -> None:
    """Inspect the catalog, upload an asset, or execute and download a feature result."""
    credential = os.environ.get("A100SIM_GRPC_TOKEN")
    metadata = (("authorization", "Bearer " + credential),) if credential else ()
    channel = (
        grpc.aio.secure_channel(arguments.target, grpc.ssl_channel_credentials(arguments.ca.read_bytes()))
        if arguments.ca
        else grpc.aio.insecure_channel(arguments.target)
    )
    async with channel:
        client = rpc.SimulatorStub(channel)
        if arguments.command in ("capabilities", "catalog", "assets"):
            method = {
                "capabilities": client.GetCapabilities,
                "catalog": client.GetApiCatalog,
                "assets": client.ListAssets,
            }[arguments.command]
            value = await method(Empty(), metadata=metadata, timeout=10)
        elif arguments.command == "upload":
            digest = hashlib.sha256()
            with arguments.file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)

            async def chunks() -> Any:
                with arguments.file.open("rb") as stream:
                    first = True
                    while True:
                        chunk = stream.read(1024 * 1024)
                        yield pb.UploadChunk(
                            asset_id=arguments.asset_id if first else "",
                            relative_path=(arguments.relative_path or arguments.file.name) if first else "",
                            sha256=digest.hexdigest() if first else "",
                            data=chunk,
                        )
                        first = False
                        if not chunk:
                            break

            value = await client.UploadAsset(chunks(), metadata=metadata, timeout=300)
        else:
            request_id = arguments.request_id or uuid.uuid4().hex
            # Print before admission so an ambiguous timeout can be retried with this exact ID.
            print(json.dumps({"request_id": request_id}), flush=True)
            payload = json.loads(arguments.arguments.read_text(encoding="utf-8")) if arguments.arguments else {}
            value = await client.InvokeFeature(
                pb.FeatureRequest(request_id=request_id, feature_id=arguments.feature, arguments=payload),
                metadata=metadata,
                timeout=10,
            )
            job_id = value.job_id
            async for snapshot in client.WatchJob(pb.WatchJobRequest(job_id=job_id), metadata=metadata, timeout=900):
                value = snapshot
            if value.state != pb.SUCCEEDED:
                raise RuntimeError(f"Job {job_id}: {value.failure.code}: {value.failure.message}")
            if arguments.output:
                arguments.output.mkdir(parents=True, exist_ok=True)
                for item in value.artifacts:
                    # Prefix an opaque identity; never trust an artifact name as a local path.
                    name = item.artifact_id + "-" + Path(item.name).name
                    destination = arguments.output / name
                    count = 0
                    with destination.open("wb") as stream:
                        async for chunk in client.DownloadArtifact(
                            pb.DownloadRequest(job_id=job_id, artifact_id=item.artifact_id),
                            metadata=metadata,
                            timeout=300,
                        ):
                            if chunk.offset != count:
                                raise RuntimeError("Artifact stream offset is not contiguous")
                            stream.write(chunk.data)
                            count += len(chunk.data)
                    if count != item.size_bytes:
                        raise RuntimeError("Artifact stream size did not match its manifest")
        print(json.dumps(MessageToDict(value, preserving_proto_field_name=True), indent=2, ensure_ascii=False))


def main() -> None:
    """Run a portable example using only the generated protocol and client dependencies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--ca", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("capabilities", "catalog", "assets"):
        commands.add_parser(name)
    upload = commands.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--asset-id", default="")
    upload.add_argument("--relative-path", default="")
    invoke = commands.add_parser("invoke")
    invoke.add_argument("feature")
    invoke.add_argument("--arguments", type=Path, help="JSON object matching the catalog request_schema")
    invoke.add_argument("--request-id", help="Reuse the printed ID when retrying the same logical operation")
    invoke.add_argument("--output", type=Path, help="Download this job's artifacts through gRPC")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
