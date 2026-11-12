"""Compile upstream Newton tensor CUDA kernels for the selected GPU and local toolkit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from a100sim.host import configured_gpu
from a100sim.newton_compat import find_tensor_plugin


def hash_file(path: Path) -> str:
    """Hash input binaries before recording their compatibility contract."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_launch_symbols(path: Path) -> set[str]:
    """Read exported C++ launch wrappers while excluding CUDA device stubs."""
    result = subprocess.run(["nm", "-D", "--defined-only", str(path)], check=True, capture_output=True, text=True)
    return {
        line.split()[-1]
        for line in result.stdout.splitlines()
        if line.split()[-1].startswith("_ZN8isaacsim7physics6newton7tensors") and "launch" in line.split()[-1]
    }


def main() -> None:
    """Build an isolated, ABI-checked override without replacing the official plugin."""
    parser = argparse.ArgumentParser(description=__doc__)
    code = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--source",
        type=Path,
        default=code.parent / "source/extensions/isaacsim.physics.newton.tensors/src",
        help="Upstream Newton tensor src/ directory from this repository revision",
    )
    args = parser.parse_args()
    gpu = configured_gpu()
    toolkit = Path(os.environ.get("A100SIM_CUDA_ROOT", ""))
    nvcc = toolkit / "bin/nvcc"
    if not nvcc.is_file():
        raise RuntimeError("CUDA compiler not detected; configure A100SIM_CUDA_ROOT with a compatible toolkit")
    capability = (
        subprocess.check_output(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader", "--id=" + gpu.uuid], text=True
        )
        .strip()
        .replace(".", "")
    )
    if not capability.isdigit():
        raise RuntimeError("The installed nvidia-smi could not report the selected GPU's compute capability")
    runtime = Path(os.environ.get("A100SIM_ROOT", code))
    features = Path(os.environ.get("A100SIM_FEATURES_ROOT", runtime / "features"))
    destination = features / "tools/newton-kernels"
    destination.mkdir(parents=True, exist_ok=True)
    plugin = find_tensor_plugin()
    names = [
        "gpu/CudaKernels.cu",
        "gpu/CudaKernels.hpp",
        "gpu/CudaCommon.hpp",
        "utils/WarpCompat.hpp",
        "utils/WrenchOps.hpp",
    ]
    with tempfile.TemporaryDirectory(prefix="build-", dir=destination) as temporary:
        build = Path(temporary)
        for name in names:
            target = build / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.source / name, target)
        # This compatibility library has no Kit lifecycle; retain CUDA error checking with stderr diagnostics.
        common = build / "gpu/CudaCommon.hpp"
        original = common.read_text(encoding="utf-8")
        include = "#include <carb/logging/Log.h>"
        if include not in original:
            raise RuntimeError("Unexpected upstream CUDA header; review the compatibility build against this revision")
        common.write_text(
            original.replace(
                include,
                "#include <cstdio>\n#define CARB_LOG_ERROR(...) do { fprintf(stderr, __VA_ARGS__); fputc(10, stderr); } while (0)\n",
            ),
            encoding="utf-8",
        )
        exports = build / "exports.map"
        exports.write_text("{ global: _ZN8isaacsim7physics6newton7tensors*launch*; local: *; };\n", encoding="utf-8")
        library = build / "libnewton-a100-kernels.so"
        command = [
            str(nvcc),
            "-shared",
            "--cudart=static",
            "-std=c++17",
            "-O3",
            "-Xcompiler=-fPIC",
            f"-gencode=arch=compute_{capability},code=sm_{capability}",
            "-I" + str(build),
            "-Xlinker=--version-script=" + str(exports),
            str(build / "gpu/CudaKernels.cu"),
            "-o",
            str(library),
        ]
        subprocess.run(command, check=True)
        required, available = read_launch_symbols(plugin), read_launch_symbols(library)
        missing = required - available
        if not required or missing:
            raise RuntimeError("CUDA wrapper ABI differs from the installed plugin: " + ", ".join(sorted(missing)))
        manifest = {
            "plugin_sha256": hash_file(plugin),
            "library_sha256": hash_file(library),
            "compute_capability": capability,
            "code_type": "native cubin",
            "launch_wrappers": len(required),
            "nvcc": subprocess.check_output([str(nvcc), "--version"], text=True).strip(),
            "source_sha256": {name: hash_file(args.source / name) for name in names},
        }
        library.replace(destination / library.name)
        (destination / "build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": True, "compute_capability": capability, "launch_wrappers": len(required)}))


if __name__ == "__main__":
    main()
