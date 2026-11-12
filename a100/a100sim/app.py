"""Keep the Isaac runtime and its GPU physics independent of the RTX renderer."""
from pathlib import Path
import os


def create_app():
    from isaacsim import SimulationApp

    base = Path(os.environ.get("A100SIM_ROOT", Path(__file__).resolve().parents[1]))
    root = base / "packages/isaacsim"
    extensions = [
        "omni.physx", "omni.physx.tensors", "omni.physx.fabric",
        "omni.warp.core", "usdrt.scenegraph", "omni.kit.loop",
        "omni.usd", "omni.kit.usd.layers", "omni.hydra.usdrt_delegate",
    ]
    args = [
        "--/app/extensions/registryEnabled=false",
        "--/app/renderer/enabled=false", "--/app/window/enabled=false",
        "--/app/audio/enabled=false", "--/telemetry/enableAnonymousData=false",
        "--/exts/omni.kit.renderer.core/compatibilityMode=true",
        "--/exts/omni.kit.renderer.core/raytracingEnabled=false",
        "--/physics/cudaDevice=0", "--/physics/suppressReadback=true",
        "--/plugins/carb.memorytracking.plugin/memoryScope/trackAddress/enabled=false",
        "--portable-root", os.environ.get("A100SIM_KIT_ROOT", str(base / "state/kit")),
    ]
    for folder in ("exts", "extscache", "extsDeprecated"):
        args += ["--ext-folder", str(root / folder)]
    for name in extensions:
        args += ["--enable", name]
    return SimulationApp({
        "headless": True, "multi_gpu": False, "limit_cpu_threads": 8,
        "extra_args": args, "enable_crashreporter": False,
        "fast_shutdown": False,
    }, experience=None)


class Physics:
    def __init__(self, stage):
        import carb
        import omni.usd
        import omni.physx
        import omni.physics.tensors as tensors
        from pxr import UsdPhysics, PhysxSchema, Gf

        self.stage = stage
        scenes = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
        if not scenes:
            scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
            scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
            scene.CreateGravityMagnitudeAttr(9.81)
            scenes = [scene.GetPrim()]
        for prim in scenes:
            settings = PhysxSchema.PhysxSceneAPI.Apply(prim)
            settings.CreateEnableGPUDynamicsAttr(True)
            settings.CreateBroadphaseTypeAttr("GPU")
        settings = carb.settings.get_settings()
        settings.set("/physics/cudaDevice", 0)
        settings.set("/physics/suppressReadback", True)
        settings.set("/physics/updateToUsd", True)
        self.physx = omni.physx.get_physx_interface()
        self.simulation = omni.physx.get_physx_simulation_interface()
        self.simulation.attach_stage(omni.usd.get_context().get_stage_id())
        self.physx.start_simulation()
        self.view = tensors.create_simulation_view("warp")
        self.view.set_subspace_roots("/World")
        if not str(self.view.device).startswith("cuda"):
            raise RuntimeError(f"GPU physics required; received {self.view.device}")
        self.time = 0.0

    def step(self, steps=1, dt=1 / 120):
        for _ in range(steps):
            self.simulation.simulate(dt, self.time)
            self.simulation.fetch_results()
            self.time += dt

    def sync_usd(self):
        self.physx.update_transformations(False, True, True, False)

    def close(self):
        self.physx.reset_simulation()
        self.simulation.detach_stage()
