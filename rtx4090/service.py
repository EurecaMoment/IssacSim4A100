"""Serve the A100 scene protocol with official CUDA PhysX and RTX annotators."""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import queue
import signal
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from a100sim.app import Physics
from a100sim.physics_service import PhysicsQueue
from a100sim.service_queue import persist, start_http


def create_app() -> Any:
    """Start the installed official runtime on one physical RTX GPU."""
    from isaacsim import SimulationApp

    gpu = int(os.environ.get("ISAAC_GPU", "1"))
    runtime = Path(os.environ["ISAAC_SIM_DIR"])
    state = Path(os.environ["RTX4090_STATE_ROOT"])
    return SimulationApp(
        {
            "headless": True,
            "active_gpu": gpu,
            "physics_gpu": gpu,
            "multi_gpu": False,
            "renderer": "RayTracedLighting",
            "width": 512,
            "height": 512,
            "limit_cpu_threads": 8,
            "enable_crashreporter": False,
            "fast_shutdown": True,
            "extra_args": [
                "--allow-root",
                "--/app/extensions/registryEnabled=false",
                "--/app/audio/enabled=false",
                "--/telemetry/enableAnonymousData=false",
                "--/renderer/multiGpu/enabled=false",
                "--/physics/cudaDevice=" + str(gpu),
                "--enable", "omni.physx.tensors",
                "--enable", "omni.physx.fabric",
                "--enable", "isaacsim.core.experimental.utils",
                "--portable-root",
                str(state / "kit"),
            ],
        },
        experience=str(runtime / "apps/isaacsim.exp.base.python.kit"),
    )


class RtxQueue(PhysicsQueue):
    """Use native RTX capture while preserving the shared bounded job queue.

    Args:
        output: Directory containing durable jobs and output artifacts.
        app: Running official SimulationApp instance, owned by the main thread.
        capacity: Maximum number of waiting jobs.
    """

    native_rtx = True
    close_physics_before_capture = True

    def __init__(self, output: Path, app: Any, capacity: int = 64) -> None:
        super().__init__(output, capacity, "Isaac Sim RTX 4090 / CUDA PhysX")
        self.app = app
        self.gpu = int(os.environ.get("ISAAC_GPU", "1"))
        self.runtime_version = (Path(os.environ["ISAAC_SIM_DIR"]) / "VERSION").read_text().strip()
        self.body_ids: list[str] = []

    def prepare(self, value: dict[str, Any]) -> dict[str, Any]:
        """Validate the common protocol and reject unsupported Gaussian inputs."""
        request = super().prepare(value)
        if request["render"] and request["render"]["kind"] != "mesh":
            raise ValueError("The RTX adapter supports mesh capture; use the A100 backend for Gaussian inputs")
        return request

    def health(self) -> dict[str, Any]:
        """Report the actual runtime, GPU identity and implemented sensors."""
        result = super().health()
        result.update(
            native_rtx=True,
            physical_gpu=self.gpu,
            gpu_uuid=os.environ.get("RTX4090_GPU_UUID"),
            isaac_sim=self.runtime_version,
            renderer="RayTracedLighting",
            supported_shapes=["cube", "sphere", "mesh"],
            annotators=["rgb", "distance_to_image_plane", "semantic_segmentation"],
        )
        return result

    def create_physics(self, stage: Any) -> Physics:
        """Require CUDA tensors on the same physical GPU as the renderer."""
        physics = Physics(stage, device=self.gpu)
        if str(physics.view.device) != f"cuda:{self.gpu}":
            physics.close()
            raise RuntimeError(f"Expected cuda:{self.gpu}; received {physics.view.device}")
        return physics

    def process(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        """Build and simulate the shared A100 scene, then capture native RTX."""
        self.body_ids = [body["id"] for body in request["bodies"]]
        return super().process(request, directory)

    def capture_scene(self, stage: Any, capture: dict[str, Any], image: Path) -> dict[str, Any]:
        """Write aligned RGB, metric camera depth and semantic IDs from RTX."""
        print("RTX_CAPTURE_INITIALIZING", str(image), flush=True)
        import carb
        import numpy as np
        import omni.kit.app
        import omni.replicator.core as rep
        from isaacsim.core.experimental.utils.semantics import add_labels
        from PIL import Image
        from pxr import UsdLux

        started = time.perf_counter()
        settings = carb.settings.get_settings()
        if not omni.kit.app.get_app().get_extension_manager().is_extension_enabled("omni.hydra.rtx"):
            raise RuntimeError("Official omni.hydra.rtx renderer is not enabled")
        settings.set("/rtx/rendermode", "RayTracedLighting")
        settings.set("/rtx/post/tonemap/op", 4)
        settings.set("/rtx/post/tonemap/filmIso", 200.0)
        settings.set("/rtx/post/tonemap/whitepoint", 6500.0)
        settings.set("/rtx/post/tonemap/enabled", True)
        rep.orchestrator.set_capture_on_play(False)
        lighting = capture.get("lighting", {})
        # Keep the common lighting values in the request; convert their radiance
        # convention to native USD photometric intensity for the RTX renderer.
        dome = UsdLux.DomeLight(stage.GetPrimAtPath("/World/Dome"))
        dome.GetIntensityAttr().Set(float(lighting.get("dome", {}).get("intensity", 1)) * 300.0)
        key = UsdLux.RectLight(stage.GetPrimAtPath("/World/KeyLight"))
        key.GetIntensityAttr().Set(float(lighting.get("key", {}).get("intensity", 5)) * 300.0)
        for index, fill in enumerate(lighting.get("fills", [])):
            light = UsdLux.RectLight(stage.GetPrimAtPath(f"/World/Fill{index:02d}"))
            light.GetIntensityAttr().Set(float(fill.get("intensity", 1)) * 300.0)
        for index, body_id in enumerate(self.body_ids):
            add_labels(stage.GetPrimAtPath(f"/World/Bodies/B{index:04d}"), labels=[body_id])
        if stage.GetPrimAtPath("/World/Ground"):
            add_labels(stage.GetPrimAtPath("/World/Ground"), labels=["ground"])

        print("RTX_CAPTURE_CREATING_PRODUCT", flush=True)
        product = rep.create.render_product("/World/Camera", (capture["size"], capture["size"]))
        annotators = {
            "rgb": rep.AnnotatorRegistry.get_annotator("rgb"),
            "depth": rep.AnnotatorRegistry.get_annotator("distance_to_image_plane"),
            "semantic": rep.AnnotatorRegistry.get_annotator("semantic_segmentation", init_params={"colorize": False}),
        }
        try:
            for annotator in annotators.values():
                annotator.attach([product])
            print("RTX_CAPTURE_WARMING_RENDERER", flush=True)
            for _ in range(24):
                self.app.update()
            # The timeline remains paused: rendering must not advance the state
            # whose tensor poses were exported by the physics stage.
            rep.orchestrator.step(rt_subframes=max(8, capture["spp"]), delta_time=0.0, pause_timeline=True)
            print("RTX_CAPTURE_READING_ANNOTATORS", flush=True)
            rgb = np.asarray(annotators["rgb"].get_data())
            depth = np.asarray(annotators["depth"].get_data(), dtype=np.float32).squeeze()
            segmentation = annotators["semantic"].get_data()
            semantic = np.asarray(segmentation["data"], dtype=np.uint32).squeeze()
            size = capture["size"]
            if rgb.shape[:2] != (size, size) or depth.shape != (size, size) or semantic.shape != (size, size):
                raise RuntimeError(f"Invalid RTX frame shapes: {rgb.shape}, {depth.shape}, {semantic.shape}")
            rgb = rgb[:, :, :3].copy()
            if rgb.max() == 0 or not np.isfinite(depth[depth > 0]).any() or not (semantic > 1).any():
                raise RuntimeError("RTX capture lacks RGB, positive finite depth or labeled pixels")
            Image.fromarray(rgb).save(image)
            np.save(image.with_name("depth_m.npy"), depth)
            np.save(image.with_name("semantic_ids.npy"), semantic)
            persist(image.with_name("semantic_labels.json"), segmentation.get("info", {}))
            return {
                "backend": "omni.hydra.rtx",
                "renderer": "RayTracedLighting",
                "native_rtx": True,
                "size": size,
                "requested_spp": capture["spp"],
                "rt_subframes": max(8, capture["spp"]),
                "sampling": "RTX real-time subframes; not Mitsuba path-tracing spp",
                "image": str(image),
                "depth_m": str(image.with_name("depth_m.npy")),
                "depth_convention": "distance_to_image_plane in stage meters; background may be infinity",
                "semantic_ids": str(image.with_name("semantic_ids.npy")),
                "semantic_labels": str(image.with_name("semantic_labels.json")),
                "rgb_mean": float(rgb.mean()),
                "seconds": time.perf_counter() - started,
            }
        finally:
            for annotator in annotators.values():
                annotator.detach([product])
            product.destroy()


def run_smoke(state: RtxQueue, output: Path) -> None:
    """Verify free fall and ground contact using CUDA states and RTX artifacts."""
    import numpy as np

    falling = output / "freefall"
    falling.mkdir(parents=True, exist_ok=True)
    request = state.prepare({"bodies": [{"id": "falling", "position": [0, 0, 3]}], "ground": False, "steps": 60})
    result = state.process(request, falling)
    height = result["bodies"][0]["pose_xyzw"][2]
    expected = 3 - 0.5 * 9.81 * (60 / 120) ** 2
    if abs(height - expected) > 0.08:
        raise RuntimeError(f"Free-fall mismatch: z={height}; expected about {expected}")

    contact = output / "contact"
    contact.mkdir(parents=True, exist_ok=True)
    request = state.prepare({
        "bodies": [
            {"id": "red-cube", "position": [-0.4, 0, 1.2], "color": [0.85, 0.1, 0.05]},
            {"id": "blue-sphere", "shape": "sphere", "radius": 0.15,
             "position": [0.4, 0, 1.6], "color": [0.05, 0.3, 0.9]},
        ],
        "steps": 240,
        "render": {"size": 512, "spp": 16, "distance": 2.8, "elevation": 28, "target": [0, 0, 0.15]},
    })
    settled = state.process(request, contact)
    for body, expected_z in zip(settled["bodies"], (0.1, 0.15)):
        if abs(body["pose_xyzw"][2] - expected_z) > 0.04:
            raise RuntimeError(f"Ground-contact mismatch: {body}")
    depth = np.load(contact / "depth_m.npy")
    finite_depth = depth[np.isfinite(depth) & (depth > 0)]
    labels = json.loads((contact / "semantic_labels.json").read_text())
    if not all(body_id in json.dumps(labels) for body_id in ("red-cube", "blue-sphere")):
        raise RuntimeError("Semantic output does not include both body labels")
    state.ready = True
    report = {
        "status": "succeeded", "health": state.health(),
        "freefall_z": height, "expected_freefall_z": expected,
        "ground_contact_z": [body["pose_xyzw"][2] for body in settled["bodies"]],
        "depth_m_min": float(finite_depth.min()), "depth_m_max": float(finite_depth.max()),
        "contact_result": settled,
    }
    persist(output / "acceptance.json", report)
    print("RTX4090_SMOKE_SUCCEEDED", json.dumps(report), flush=True)


def main() -> None:
    """Run one acceptance workload or the persistent loopback simulation API."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("RTX4090_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RTX4090_PORT", "18409")))
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results/service")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1, all_threads=False)
    app = create_app()
    state = RtxQueue(args.output, app)
    server = None
    exit_code = 0
    pid_file = Path(os.environ["RTX4090_STATE_ROOT"]) / "service.pid"
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        if args.smoke:
            run_smoke(state, args.output)
            return
        warmup = args.output / "warmup"
        warmup.mkdir(parents=True, exist_ok=True)
        state.process(state.prepare({"bodies": [{"id": "warmup"}], "steps": 1,
                                     "render": {"size": 64, "spp": 1}}), warmup)
        state.ready = True
        server = start_http(state, args.host, args.port, "/simulate")
        pid_file.write_text(str(os.getpid()) + "\n")
        print("RTX4090_SERVICE_READY", json.dumps(state.health()), flush=True)
        while not stop.is_set():
            try:
                item = state.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            state.run_one(item)
    except BaseException:
        exit_code = 1
        traceback.print_exc()
        raise
    finally:
        state.ready = False
        if server:
            server.shutdown()
            server.server_close()
        if not args.smoke and pid_file.is_file() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()
        app.close(exit_code=exit_code)


if __name__ == "__main__":
    main()
