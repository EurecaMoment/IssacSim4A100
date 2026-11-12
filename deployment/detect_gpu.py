"""Select a physical NVIDIA GPU and an explicit RTX or CUDA rendering backend."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shlex
import subprocess
from typing import Any


def classify_gpu(name: str) -> str | None:
    """Return the known rendering backend for a GPU model, or None when unknown."""
    normalized = name.upper()
    if re.search(r"\bA(?:100|800)\b", normalized):
        return "cuda"
    if "RTX" in normalized or re.search(r"\b(?:L4|L40S?|A10G?|A16|A40)\b", normalized):
        return "rtx"
    return None


def read_gpus() -> list[dict[str, Any]]:
    """Query model and physical identity once, without importing CUDA libraries."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid,name,driver_version,memory.total,memory.used",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    devices = []
    for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        if len(row) != 6:
            raise ValueError("Unexpected nvidia-smi GPU inventory format")
        index, uuid, name, driver, total, used = (value.strip() for value in row)
        devices.append({"index": int(index), "uuid": uuid, "name": name, "driver": driver,
                        "memory_total_mib": int(total), "memory_used_mib": int(used)})
    if not devices:
        raise ValueError("No NVIDIA GPUs were reported")
    return devices


def select_gpu(devices: list[dict[str, Any]], selection: str, backend: str = "auto") -> dict[str, Any]:
    """Select one physical GPU and refuse unsupported or contradictory automatic routing."""
    if backend not in {"auto", "cuda", "rtx"}:
        raise ValueError("ISAACSIM_BACKEND must be auto, cuda or rtx")
    if selection == "auto":
        candidates = [device for device in devices if classify_gpu(device["name"]) is not None]
        if backend == "rtx":
            candidates = [device for device in candidates if classify_gpu(device["name"]) == "rtx"]
        if not candidates:
            raise ValueError("No recognized GPU is available; select a physical index and backend explicitly")
        device = min(candidates, key=lambda item: (item["memory_used_mib"], item["index"]))
    else:
        matches = [device for device in devices if selection in {str(device["index"]), device["uuid"]}]
        if len(matches) != 1:
            raise ValueError(f"Physical GPU {selection!r} was not found")
        device = matches[0]
    known_backend = classify_gpu(device["name"])
    if known_backend == "cuda" and backend == "rtx":
        raise ValueError(f"{device['name']} has no RT cores; select the cuda backend")
    if backend == "auto" and known_backend is None:
        raise ValueError(f"Unknown RT capability for {device['name']}; set ISAACSIM_BACKEND=rtx or cuda explicitly")
    return {**device, "backend": known_backend if backend == "auto" else backend,
            "detection": "model_table" if backend == "auto" else "explicit_override"}


def main() -> None:
    """Print the selected GPU as JSON or safely quoted shell assignments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", default=os.environ.get("ISAAC_GPU", "auto"))
    parser.add_argument("--backend", choices=["auto", "cuda", "rtx"],
                        default=os.environ.get("ISAACSIM_BACKEND", "auto"))
    parser.add_argument("--format", choices=["json", "shell"], default="json")
    args = parser.parse_args()
    try:
        if os.environ.get("CUDA_VISIBLE_DEVICES"):
            raise ValueError("Unset CUDA_VISIBLE_DEVICES and select a physical GPU with ISAAC_GPU")
        device = select_gpu(read_gpus(), args.gpu, args.backend)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"GPU detection failed: {exc}\n")
    if args.format == "json":
        print(json.dumps(device, ensure_ascii=False, indent=2))
    else:
        for field in ("index", "uuid", "name", "driver", "backend"):
            print(f"export ISAACSIM_GPU_{field.upper()}={shlex.quote(str(device[field]))}")


if __name__ == "__main__":
    main()
