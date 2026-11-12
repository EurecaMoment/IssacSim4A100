"""Adapt MuJoCo Warp graph options to the loaded CUDA driver."""

from __future__ import annotations

import ctypes
import functools
import hashlib
import json
import os
from pathlib import Path
from typing import Any

_kernel_library: Any = None


def find_tensor_plugin() -> Path:
    """Find the plugin selected from the optional overlay or base runtime."""
    code = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    features = Path(os.environ.get("A100SIM_FEATURES_ROOT", runtime / "features"))
    folders = [Path(item) for item in os.environ.get("A100SIM_EXT_FOLDERS", "").split(os.pathsep) if item]
    folders += [features / "packages/isaacsim/exts", runtime / "packages/isaacsim/exts"]
    for folder in folders:
        plugin = folder / "isaacsim.physics.newton.tensors/bin/libisaacsim.physics.newton.tensors.plugin.so"
        if plugin.is_file():
            return plugin
    raise FileNotFoundError("Newton tensor plugin not found; install the base runtime and optional Newton packages")


def load_kernel_override() -> bool:
    """Load only a locally built override that matches the installed native plugin."""
    global _kernel_library
    policy = os.environ.get("A100SIM_NEWTON_NATIVE_KERNELS", "auto")
    if policy not in {"auto", "on", "off"}:
        raise ValueError("A100SIM_NEWTON_NATIVE_KERNELS must be auto, on, or off")
    if policy == "off":
        return False
    code = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    features = Path(os.environ.get("A100SIM_FEATURES_ROOT", runtime / "features"))
    directory = features / "tools/newton-kernels"
    library, manifest = directory / "libnewton-a100-kernels.so", directory / "build.json"
    if not library.is_file() or not manifest.is_file():
        if policy == "on":
            raise RuntimeError("Run build-newton-kernels.sh before requiring the Newton native kernel override")
        return False
    data = json.loads(manifest.read_text(encoding="utf-8"))
    with find_tensor_plugin().open("rb") as source:
        plugin_hash = hashlib.file_digest(source, "sha256").hexdigest()
    with library.open("rb") as source:
        library_hash = hashlib.file_digest(source, "sha256").hexdigest()
    if plugin_hash != data["plugin_sha256"] or library_hash != data["library_sha256"]:
        raise RuntimeError("Newton plugin or kernel override changed; rebuild with build-newton-kernels.sh")
    _kernel_library = ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
    return True


def read_cuda_driver_version() -> int:
    """Read the CUDA driver API version without guessing from a toolkit or hostname."""
    driver = ctypes.CDLL("libcuda.so.1")
    version = ctypes.c_int()
    result = driver.cuDriverGetVersion(ctypes.byref(version))
    if result:
        raise RuntimeError(f"cuDriverGetVersion failed: {result}")
    return version.value


def conditional_graphs_enabled(driver_version: int, policy: str = "auto") -> bool:
    """Reject explicitly requested conditional nodes when the driver lacks their API."""
    if policy not in {"auto", "on", "off"}:
        raise ValueError("A100SIM_NEWTON_CONDITIONAL_GRAPHS must be auto, on, or off")
    supported = driver_version >= 12040
    if policy == "on" and not supported:
        raise RuntimeError("Conditional CUDA graph nodes require CUDA driver API 12.4 or newer")
    return policy != "off" and supported


def install_mujoco_compatibility() -> dict[str, Any]:
    """Set options at model creation, before MuJoCo's constructor can launch graph nodes."""
    import mujoco_warp

    version = read_cuda_driver_version()
    enabled = conditional_graphs_enabled(version, os.environ.get("A100SIM_NEWTON_CONDITIONAL_GRAPHS", "auto"))
    if not getattr(mujoco_warp.put_model, "_a100sim_compatible", False):
        original = mujoco_warp.put_model

        @functools.wraps(original)
        def create_compatible_model(*args: Any, **kwargs: Any) -> Any:
            model = original(*args, **kwargs)
            model.opt.graph_conditional = enabled
            return model

        create_compatible_model._a100sim_compatible = True
        mujoco_warp.put_model = create_compatible_model
    return {"driver_api_version": version, "conditional_graphs": enabled, "cpu_fallback": False}
