"""Run fixed, simulator-owned workflows in isolated child processes."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import grpc

from a100sim.rpc.codec import Task
from a100sim.rpc.config import Settings
from a100sim.rpc.errors import ServiceError


def write_import_bundle(directory: Path) -> Path:
    """Package an imported USD and generated dependencies with their relative layout."""
    destination = directory / "robot-import.zip"
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path != destination and path.resolve().is_relative_to(directory.resolve())
    )
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.relative_to(directory).as_posix())
    return destination


class Workflows:
    """Serialize optional import and Newton processes without accepting commands from callers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = asyncio.Semaphore(1)

    def is_available(self, operation: str) -> bool:
        """Check the operator switch and workflow-specific runtime files."""
        if not self.settings.enable_workflows or os.name != "posix":
            return False
        if operation == "convert_asset":
            return self.asset_python().is_file()
        runtime = Path(os.environ.get("A100SIM_ROOT") or self.settings.code_root)
        features = Path(os.environ.get("A100SIM_FEATURES_ROOT") or runtime / "features") / "packages"
        extension = "isaacsim.physics.newton" if operation == "newton" else "isaacsim.asset.importer.urdf"
        return (
            Path(os.environ.get("A100SIM_PYTHON") or "/nonexistent/python").is_file()
            and (runtime / "rootfs/bin/bash").is_file()
            and (features / "isaacsim/exts" / extension).is_dir()
        )

    def asset_python(self) -> Path:
        """Locate the separately installed GLB conversion environment."""
        return Path(os.environ.get("A100SIM_ASSET_PYTHON") or self.settings.code_root / ".venv-assets/bin/python")

    def build_command(self, task: Task, output: Path) -> tuple[list[str], Path]:
        """Build an argument vector from an allowlisted workflow and bounded parameters."""
        root = self.settings.code_root
        values = task.arguments
        if task.operation == "convert_asset":
            source = Path(values["source"])
            source_up, max_size = values.get("source_up", "y"), values.get("max_size", 1.2)
            if source.suffix.lower() != ".glb" or source_up not in ("y", "z") or not 0 < max_size <= 100:
                raise ValueError("Conversion requires GLB, y/z source-up and max-size between 0 and 100")
            if not self.asset_python().is_file():
                raise ServiceError(
                    grpc.StatusCode.UNAVAILABLE, "Install the separate asset conversion environment first"
                )
            destination = self.settings.asset_root / ("converted-" + uuid.uuid4().hex)
            report = output / "conversion-report.json"
            command = [
                str(self.asset_python()),
                str(root / "scripts/convert_rpc_asset.py"),
                str(source),
                str(destination),
                "--report",
                str(report),
                "--source-up",
                source_up,
                "--max-size",
                str(max_size),
            ]
            if values.get("strict_materials"):
                command.append("--strict-materials")
            return command, report
        if task.operation == "import_robot":
            source = Path(values["source"])
            if source.suffix.lower() not in (".urdf", ".xml", ".mjcf"):
                raise ValueError("Robot import requires URDF or MJCF")
            command = [
                "bash",
                str(root / "run-assets.sh"),
                str(root / "examples/import_robot.py"),
                str(source),
                "--output",
                str(output),
            ]
            if values.get("physics_check"):
                command.append("--physics-check")
            return command, output / "import-report.json"
        if task.operation == "newton":
            native = values["solver"] == "native"
            name = "newton_native.py" if native else "newton_xpbd.py"
            command = ["bash", str(root / "run-newton.sh"), str(root / "examples" / name), "--output", str(output)]
            for field, minimum, maximum in [
                ("steps", 120, 1200),
                ("iterations", 1, 100),
                ("line_search_iterations", 1, 50),
            ]:
                if field in values:
                    if not native or not minimum <= values[field] <= maximum:
                        raise ValueError(f"{field} is supported only by native Newton within {minimum}–{maximum}")
                    command.extend(["--" + field.replace("_", "-"), str(values[field])])
            return command, output / ("newton-native.json" if native else "newton-xpbd.json")
        raise ServiceError(grpc.StatusCode.UNIMPLEMENTED, "No built-in workflow implements this operation")

    async def execute(self, task: Task, directory: Path) -> dict[str, Any]:
        """Execute one subprocess and verify its report independently of its exit code."""
        if not self.is_available(task.operation):
            raise ServiceError(grpc.StatusCode.UNAVAILABLE, "Enable prepared Linux workflows in gateway settings")
        async with self.lock:
            directory.mkdir(parents=True, exist_ok=True)
            command, report = self.build_command(task, directory)
            log = directory / "workflow.log"
            with log.open("wb") as stream:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self.settings.code_root,
                    stdout=stream,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    await asyncio.wait_for(process.wait(), timeout=600)
                except (TimeoutError, asyncio.CancelledError):
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                    raise
            if not report.is_file():
                raise ServiceError(
                    grpc.StatusCode.INTERNAL, "Workflow produced no acceptance report; inspect server log"
                )
            result = json.loads(report.read_text(encoding="utf-8"))
            if process.returncode or result.get("success") is not True:
                raise ServiceError(grpc.StatusCode.INTERNAL, "Workflow acceptance failed; inspect server workflow log")
            if task.operation == "import_robot":
                result["bundle_file"] = str(await asyncio.to_thread(write_import_bundle, directory))
            return {**result, "report_file": str(report), "log_file": str(log)}
