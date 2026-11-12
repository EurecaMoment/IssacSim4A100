"""Fetch locked non-RTX Kit dependencies into an isolated optional overlay."""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from range_wheel import RangeFile


def extract_selected(destination: Path, archive: zipfile.ZipFile, selected: list[str]) -> int:
    """Extract selected extension roots while preserving native executable modes."""
    entries = [item for item in archive.infolist() if any(item.filename.startswith(prefix) for prefix in selected)]
    for item in sorted(entries, key=lambda item: item.header_offset):
        target = (destination / item.filename).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError("Unsafe wheel member")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(item))
        mode = (item.external_attr >> 16) & 0o777
        if mode:
            target.chmod(mode)
    return len(entries)


def main() -> None:
    """Resolve the recorded cache slices without downloading the complete RTX cache."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    arguments = parser.parse_args()
    code = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    features = Path(os.environ.get("A100SIM_FEATURES_ROOT", runtime / "features"))
    destination = features / "packages"
    plan: dict[str, Any] = json.loads((code / "research/feature-extensions.json").read_text())
    groups = ["robotics"] + (["assets"] if arguments.assets else [])
    selected: dict[str, dict[str, str]] = {}
    for group in groups:
        selected.update(plan[group]["cache_extensions"])
    receipt: dict[str, Any] = {"profiles": groups, "downloaded": [], "already_present": [], "https_bytes_received": 0}
    pending: dict[str, list[str]] = {}
    for name, item in sorted(selected.items()):
        prefix = item["prefix"]
        if any((root / prefix / "config/extension.toml").is_file() for root in (destination, runtime / "packages")):
            receipt["already_present"].append(name)
        else:
            pending.setdefault(item["package"], []).append(prefix)
            receipt["downloaded"].append(name)
    if not arguments.plan_only:
        for package, prefixes in pending.items():
            filename = package.replace("-", "_") + "-6.2.0.0-cp312-none-manylinux_2_35_x86_64.whl"
            stream = RangeFile("https://pypi.nvidia.com/" + package + "/" + filename)
            with zipfile.ZipFile(stream) as archive:
                for prefix in prefixes:
                    if prefix + "config/extension.toml" not in archive.namelist():
                        raise RuntimeError("Locked extension is absent from wheel: " + prefix)
                extract_selected(destination, archive, prefixes)
            receipt["https_bytes_received"] += stream.downloaded
        features.mkdir(parents=True, exist_ok=True)
        (features / "extension-installation.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2), flush=True)


if __name__ == "__main__":
    main()
