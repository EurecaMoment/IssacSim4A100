"""Optional OIDN 2 CPU postprocessing for already rendered RGB PNG images."""
from __future__ import annotations

import argparse
import ctypes as ct
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--library", type=Path, required=True, help="Path to libOpenImageDenoise.so.2")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    library = ct.CDLL(str(args.library.resolve()))
    pointer = ct.c_void_p

    def bind(name: str, result: object, arguments: list) -> object:
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
        return function

    new_device = bind("oidnNewDevice", pointer, [ct.c_int])
    set_integer = bind("oidnSetDeviceInt", None, [pointer, ct.c_char_p, ct.c_int])
    commit_device = bind("oidnCommitDevice", None, [pointer])
    new_filter = bind("oidnNewFilter", pointer, [pointer, ct.c_char_p])
    set_image = bind("oidnSetSharedFilterImage", None, [pointer, ct.c_char_p, pointer, ct.c_int,
                     ct.c_size_t, ct.c_size_t, ct.c_size_t, ct.c_size_t, ct.c_size_t])
    set_boolean = bind("oidnSetFilterBool", None, [pointer, ct.c_char_p, ct.c_bool])
    commit_filter = bind("oidnCommitFilter", None, [pointer])
    execute = bind("oidnExecuteFilter", None, [pointer])
    get_error = bind("oidnGetDeviceError", ct.c_int, [pointer, ct.POINTER(ct.c_char_p)])
    release_filter = bind("oidnReleaseFilter", None, [pointer])
    release_device = bind("oidnReleaseDevice", None, [pointer])
    device = new_device(1)  # OIDN_DEVICE_TYPE_CPU; simulation and ray tracing remain on CUDA.
    if not device:
        raise RuntimeError("Cannot create the OIDN CPU device")
    filter_handle = None
    try:
        set_integer(device, b"numThreads", args.threads)
        commit_device(device)
        with Image.open(args.image) as source:
            pixels = np.ascontiguousarray(np.asarray(source.convert("RGB"), np.float32) / 255)
        output = np.empty_like(pixels)
        height, width = pixels.shape[:2]
        filter_handle = new_filter(device, b"RT")
        set_image(filter_handle, b"color", pixels.ctypes.data, 3, width, height, 0, 12, width * 12)
        set_image(filter_handle, b"output", output.ctypes.data, 3, width, height, 0, 12, width * 12)
        set_boolean(filter_handle, b"hdr", False)
        set_boolean(filter_handle, b"srgb", True)
        commit_filter(filter_handle)
        execute(filter_handle)
        message = ct.c_char_p()
        code = get_error(device, ct.byref(message))
        if code:
            raise RuntimeError(f"OIDN error {code}: {message.value!r}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(np.clip(output * 255 + 0.5, 0, 255))).save(args.output)
        print(f"Denoised image: {args.output.resolve()}")
    finally:
        if filter_handle:
            release_filter(filter_handle)
        release_device(device)


if __name__ == "__main__":
    main()
