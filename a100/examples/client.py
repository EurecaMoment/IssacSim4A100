"""Submit exactly once, then long-poll the returned job until it finishes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.request


def fetch_json(url: str, payload: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8321")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--route", choices=["simulate", "render"], default="simulate")
    parser.add_argument("--wait-ms", type=int, default=30000, help="Use 0 for the generic or asset-render backends")
    args = parser.parse_args()
    payload = json.dumps(json.loads(args.request.read_text(encoding="utf-8"))).encode()
    query = f"?wait_ms={args.wait_ms}" if args.wait_ms else ""
    result = fetch_json(args.url.rstrip("/") + "/" + args.route + query, payload)
    job_id = result["id"]
    deadline = time.monotonic() + args.timeout
    while result["state"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise SystemExit(f"Client timeout; job {job_id} may still be running. Continue with GET /jobs/{job_id}.")
        if not args.wait_ms:
            time.sleep(0.1)
        result = fetch_json(args.url.rstrip("/") + f"/jobs/{job_id}" + query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["state"] != "succeeded":
        raise SystemExit(f"Job {job_id} ended in state {result['state']}")


if __name__ == "__main__":
    main()
