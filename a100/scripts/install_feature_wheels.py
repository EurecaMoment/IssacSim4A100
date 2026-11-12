"""Download pinned Isaac Sim feature wheels and preserve the shared package namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    """Verify complete wheel hashes before writing anything into the feature overlay."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+")
    arguments = parser.parse_args()
    code = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    features = Path(os.environ.get("A100SIM_FEATURES_ROOT", runtime / "features"))
    destination, downloads = features / "packages", features / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    locks = {
        item["package"]: item
        for item in json.loads((code / "research/feature-package-lock.json").read_text())
        if item["group"] == "isaacsim"
    }
    for package in arguments.packages:
        item = locks[package]
        target = downloads / item["filename"]
        valid = False
        if target.is_file():
            with target.open("rb") as source:
                valid = hashlib.file_digest(source, "sha256").hexdigest() == item["sha256"]
        if not valid:
            temporary = target.with_suffix(".partial")
            url = "https://pypi.nvidia.com/" + package + "/" + item["filename"]
            with urllib.request.urlopen(url, timeout=180) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=4 * 1024 * 1024)
            with temporary.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise RuntimeError("Downloaded wheel hash mismatch: " + package)
            temporary.replace(target)
        with zipfile.ZipFile(target) as archive:
            for member in archive.infolist():
                path = (destination / member.filename).resolve()
                if not path.is_relative_to(destination.resolve()):
                    raise ValueError("Unsafe wheel member")
                if member.is_dir():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(member))
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    path.chmod(mode)
        print("Installed " + package + "==" + item["version"], flush=True)


if __name__ == "__main__":
    main()
