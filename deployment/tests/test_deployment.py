"""Verify model routing and launcher configuration without consuming GPU time."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment.detect_gpu import classify_gpu, read_gpus, select_gpu


def make_device(index: int, name: str, used: int = 0) -> dict:
    """Create one inventory row for routing boundary cases."""
    return {"index": index, "uuid": f"GPU-example-{index}", "name": name, "driver": "550.54.15",
            "memory_total_mib": 81920, "memory_used_mib": used}


class GpuSelectionTests(unittest.TestCase):
    """Route RTX and RT-free Ampere models while preserving physical GPU identity."""

    def test_required_models_and_rtx_datacenter_models(self) -> None:
        """Distinguish RT-capable server GPUs from A100 and A800 models."""
        for name, expected in (("NVIDIA GeForce RTX 4090", "rtx"), ("NVIDIA A100-SXM4-80GB", "cuda"),
                               ("NVIDIA A100-PCIE-40GB", "cuda"), ("NVIDIA A800-SXM4-80GB", "cuda"),
                               ("NVIDIA A800 80GB PCIe", "cuda"), ("NVIDIA L40S", "rtx")):
            with self.subTest(name=name):
                self.assertEqual(classify_gpu(name), expected)

    def test_auto_selects_recognized_gpu_with_least_used_memory(self) -> None:
        """Avoid busy GPUs and unknown devices; resolve explicit UUIDs exactly."""
        devices = [make_device(0, "unknown", 0), make_device(1, "NVIDIA RTX 4090", 3000),
                   make_device(2, "NVIDIA A800-SXM4-80GB", 1)]
        self.assertEqual(select_gpu(devices, "auto")["index"], 2)
        self.assertEqual(select_gpu(devices, "auto", "rtx")["index"], 1)
        self.assertEqual(select_gpu(devices, "GPU-example-1")["index"], 1)

    def test_unknown_or_incompatible_devices_do_not_silently_fallback(self) -> None:
        """Refuse unknown capabilities, missing ordinals and native RTX on A800."""
        with self.assertRaises(ValueError):
            select_gpu([make_device(0, "unknown")], "0")
        with self.assertRaises(ValueError):
            select_gpu([make_device(0, "NVIDIA A800-SXM4-80GB")], "0", "rtx")
        with self.assertRaises(ValueError):
            select_gpu([make_device(0, "NVIDIA RTX 4090")], "9")

    def test_inventory_is_parsed_from_one_bounded_nvidia_query(self) -> None:
        """Read physical ordinals, UUIDs and driver versions from CSV fields."""
        response = subprocess.CompletedProcess([], 0,
            stdout="3, GPU-selected, NVIDIA A100-SXM4-80GB, 535.161.08, 81920, 7\n")
        with patch("deployment.detect_gpu.subprocess.run", return_value=response) as command:
            device = read_gpus()[0]
        self.assertEqual((device["index"], device["uuid"], device["memory_used_mib"]), (3, "GPU-selected", 7))
        command.assert_called_once()


@unittest.skipUnless(os.name == "posix", "Linux shell launchers are verified on the target machine")
class LauncherTests(unittest.TestCase):
    """Exercise real shell dispatch with small stand-in runtime executables."""

    def test_rtx_dispatch_preserves_common_gpu_port_and_runtime(self) -> None:
        """Prevent a backend-local configuration from overriding the selected GPU."""
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            smi = tools / "nvidia-smi"
            smi.write_text("#!/bin/sh\ncase \"$*\" in\n*--query-gpu=uuid*) echo GPU-example-2;;\n"
                           "*) echo '2, GPU-example-2, NVIDIA GeForce RTX 4090, 580.95.05, 24564, 0';;\nesac\n")
            smi.chmod(0o755)
            runtime = root / "official"
            runtime.mkdir()
            python = runtime / "python.sh"
            python.write_text('#!/bin/sh\nprintf "GPU=%s PORT=%s ROOT=%s ARGS=%s\\n" '
                              '"$ISAAC_GPU" "$RTX4090_PORT" "$RTX4090_STATE_ROOT" "$*"\n')
            python.chmod(0o755)
            config = root / "common.env"
            config.write_text(f"ISAAC_GPU=2\nISAACSIM_PORT=18999\nISAAC_SIM_DIR={runtime}\n")
            environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                           "ISAACSIM_CONFIG": str(config), "ISAACSIM_STATE_ROOT": str(root / "state"),
                           "RTX4090_ASSET_ROOT": str(root / "assets")}
            environment.pop("CUDA_VISIBLE_DEVICES", None)
            result = subprocess.run(["bash", str(repo / "deploy.sh"), "serve"], env=environment,
                                    capture_output=True, text=True, timeout=15, check=True)
            self.assertIn("GPU=2 PORT=18999", result.stdout)
            self.assertIn("-m rtx4090.service --output", result.stdout)

    def test_a800_prepare_reuses_runtime_and_detects_host_driver(self) -> None:
        """Route an RT-free GPU without assuming the original A100 R535 driver."""
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            smi = tools / "nvidia-smi"
            smi.write_text("#!/bin/sh\ncase \"$*\" in\n*--query-gpu=driver_version*) echo 550.54.15;;\n"
                           "*) echo '3, GPU-example-3, NVIDIA A800-SXM4-80GB, 550.54.15, 81920, 5';;\nesac\n")
            smi.chmod(0o755)
            runtime = root / "runtime"
            for path in ("packages/isaacsim", "render-packages/mitsuba"):
                (runtime / path).mkdir(parents=True)
            for path in ("rootfs/bin/bash", "rootfs/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
                         "tools/nvidia-550.54.15/usr/lib/x86_64-linux-gnu/libnvoptix.so.1",
                         "render-packages/drjit/libdrjit-core.so.original"):
                target = runtime / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
                target.chmod(0o755)
            config = root / "common.env"
            config.write_text(f"ISAAC_GPU=3\nA100SIM_ROOT={runtime}\nA100SIM_PYTHON=/bin/true\n"
                              f"OMNI_KIT_ACCEPT_EULA=YES\nA100SIM_CUDA_ROOT={root}\n")
            environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                           "ISAACSIM_CONFIG": str(config), "A100SIM_CONFIG": str(root / "no-backend.env")}
            for key in ("CUDA_VISIBLE_DEVICES", "A100SIM_CONFIG_LOADED", "A100SIM_DRIVER_VERSION", "A100SIM_NVIDIA_ROOT"):
                environment.pop(key, None)
            result = subprocess.run(["bash", str(repo / "deploy.sh"), "prepare"], env=environment,
                                    capture_output=True, text=True, timeout=15, check=True)
            self.assertIn("A800-SXM4-80GB -> cuda backend (driver 550.54.15)", result.stdout)


if __name__ == "__main__":
    unittest.main()
