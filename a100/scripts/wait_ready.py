"""Wait for all selected loopback GPU services to report ready."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_for_grpc(target: str, timeout: float) -> None:
    """Wait for the authenticated gateway health service in its separate environment."""
    import grpc
    from grpc_health.v1 import health_pb2, health_pb2_grpc

    target = os.environ.get("A100SIM_GRPC_PROBE_TARGET") or target
    host, port = target.rsplit(":", 1)
    if host in ("0.0.0.0", "[::]"):
        target = "127.0.0.1:" + port if host == "0.0.0.0" else "[::1]:" + port
    token = os.environ.get("A100SIM_GRPC_TOKEN")
    metadata = (("authorization", "Bearer " + token),) if token else ()
    ca = os.environ.get("A100SIM_GRPC_TLS_CA")
    credentials = grpc.ssl_channel_credentials(Path(ca).read_bytes() if ca else None)
    channel = (
        grpc.secure_channel(target, credentials)
        if os.environ.get("A100SIM_GRPC_TLS_CERT")
        else grpc.insecure_channel(target)
    )
    with channel:
        client = health_pb2_grpc.HealthStub(channel)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = client.Check(
                    health_pb2.HealthCheckRequest(service="a100sim.v1.Simulator"),
                    metadata=metadata,
                    timeout=min(3, max(0.01, deadline - time.monotonic())),
                )
                if value.status == health_pb2.HealthCheckResponse.SERVING:
                    print("Ready: simulator gRPC gateway", flush=True)
                    return
            except grpc.RpcError as error:
                if error.code() == grpc.StatusCode.UNAUTHENTICATED:
                    raise SystemExit("Gateway health authentication failed; check the configured credential") from error
            time.sleep(0.2)
    raise SystemExit("Gateway health did not become ready; inspect the deployment gRPC log")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--grpc", action="store_true", help="Check the authenticated gRPC health service")
    args = parser.parse_args()
    if args.grpc:
        if len(args.urls) != 1:
            parser.error("--grpc requires one host:port target")
        wait_for_grpc(args.urls[0], args.timeout)
        return
    pending = set(args.urls)
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        for url in list(pending):
            try:
                with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3) as response:
                    value = json.load(response)
                if value.get("status") == "failed":
                    raise RuntimeError(f"Service initialization failed at {url}: {value.get('error')}")
                if value.get("status") == "ready":
                    if value.get("cpu_fallback") is not False:
                        raise RuntimeError(f"Service at {url} did not confirm GPU execution")
                    pending.remove(url)
                    print(f"Ready: {url}", flush=True)
            except (OSError, urllib.error.URLError, ValueError):
                continue
        if pending:
            time.sleep(1)
    if pending:
        raise SystemExit(
            f"Services did not become ready: {sorted(pending)}. Inspect the deployment logs under A100SIM_ROOT/logs."
        )


if __name__ == "__main__":
    main()
