"""Submit one feature operation and poll its existing job ID until completion."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def execute(value: dict[str, Any], url: str = "http://127.0.0.1:8322", timeout: float = 180) -> dict[str, Any]:
    """Submit exactly once; a polling timeout never retries the physical operation."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url.rstrip("/") + "/robotics", data=json.dumps(value).encode(), headers={"Content-Type": "application/json"}
    )
    with opener.open(request, timeout=10) as response:
        record = json.load(response)
    job_id = record["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with opener.open(url.rstrip("/") + "/jobs/" + job_id, timeout=10) as response:
            record = json.load(response)
        if record["state"] == "succeeded":
            return record
        if record["state"] in ("failed", "interrupted"):
            raise RuntimeError(json.dumps(record, ensure_ascii=False))
        time.sleep(0.15)
    raise TimeoutError(f"Job {job_id} is still pending; query /jobs/{job_id} without resubmitting")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON object or path to a JSON file")
    parser.add_argument("--url", default="http://127.0.0.1:8322")
    parser.add_argument("--timeout", type=float, default=180)
    arguments = parser.parse_args()
    source = arguments.request if arguments.request.lstrip().startswith("{") else Path(arguments.request).read_text()
    print(json.dumps(execute(json.loads(source), arguments.url, arguments.timeout), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
