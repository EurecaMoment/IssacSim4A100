"""Wait for all selected loopback GPU services to report ready."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
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
        raise SystemExit(f"Services did not become ready: {sorted(pending)}. Inspect journalctl --user -u 'isaacsim-a100-*'.")


if __name__ == "__main__":
    main()
