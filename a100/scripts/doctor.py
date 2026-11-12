"""Check the selected A100, local dependencies, and rootless runtime prerequisites."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from a100sim.host import configured_gpu
from a100sim.newton_compat import conditional_graphs_enabled, read_cuda_driver_version


def main() -> None:
    """Exit unsuccessfully when a deployment prerequisite is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true", help="Also require installed runtime files")
    args = parser.parse_args()
    report: dict = {"success": False, "checks": {}, "errors": []}
    checks, errors = report["checks"], report["errors"]
    checks["linux_x86_64"] = platform.system() == "Linux" and platform.machine() == "x86_64"
    checks["python_312"] = sys.version_info[:2] == (3, 12)
    checks["independent_python"] = sys.base_prefix not in {"/", "/usr", "/usr/local"}
    commands = ["bash", "curl", "tar", "git", "dpkg-deb", "unshare", "mount", "chroot", "cmake", "c++", "uv"]
    missing = [command for command in commands if not shutil.which(command)]
    checks["host_tools"] = not missing
    if missing:
        errors.append("Missing host tools: " + ", ".join(missing))
    toolkit = Path(os.environ.get("A100SIM_CUDA_ROOT") or "/nonexistent-cuda-toolkit")
    checks["cuda_toolkit"] = (toolkit / "bin/nvcc").is_file()
    try:
        gpu = configured_gpu()
        report["gpu"] = {"name": gpu.name, "driver": gpu.driver, "selection": "allowed A100"}
        checks["allocated_a100"] = True
        version = read_cuda_driver_version()
        report["cuda_driver_api"] = version
        report["newton_conditional_graphs"] = conditional_graphs_enabled(
            version, os.environ.get("A100SIM_NEWTON_CONDITIONAL_GRAPHS", "auto")
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        checks["allocated_a100"] = False
        errors.append(str(error))
    if shutil.which("unshare"):
        result = subprocess.run(["unshare", "-Ur", "-m", "true"], capture_output=True, text=True)
        checks["user_mount_namespace"] = result.returncode == 0
        if result.returncode:
            errors.append("User/mount namespaces are unavailable; enable them or use an equivalent Linux container")
    code = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    if args.runtime:
        checks["rootfs"] = (runtime / "rootfs/bin/bash").is_file()
        checks["isaac_packages"] = (runtime / "packages/isaacsim").is_dir()
        checks["render_packages"] = (runtime / "render-packages/mitsuba").is_dir()
        driver_directory = Path(os.environ["A100SIM_NVIDIA_ROOT"]) / "usr/lib/x86_64-linux-gnu"
        checks["optix_libraries"] = (driver_directory / "libnvoptix.so.1").is_file()
        checks["drjit_patch"] = (runtime / "render-packages/drjit/libdrjit-core.so.original").is_file()
    report["success"] = all(checks.values())
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
