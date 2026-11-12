"""Small GPU robot and native physics sensors for the A100 feature service."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def make_box(stage: Any, path: str, position: list[float], size: list[float], mass: float = 0.0) -> Any:
    """Author a box with a separate visual so rigid-body transforms stay unscaled."""
    from pxr import Gf, UsdGeom, UsdPhysics

    body = UsdGeom.Xform.Define(stage, path)
    body.AddTranslateOp().Set(Gf.Vec3d(*position))
    body.AddOrientOp().Set(Gf.Quatf(1.0, Gf.Vec3f(0.0)))
    if mass:
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
        mass_api.CreateMassAttr(mass)
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(0.02, 0.06, 0.06))
    visual = UsdGeom.Cube.Define(stage, path + "/Visual")
    visual.CreateSizeAttr(1.0)
    visual.AddScaleOp().Set(Gf.Vec3f(*size))
    visual.CreateDisplayColorAttr([Gf.Vec3f(0.22, 0.48, 0.72)])
    UsdPhysics.CollisionAPI.Apply(visual.GetPrim())
    return body.GetPrim()


def make_arm(stage: Any, path: str = "/World/Arm", origin: tuple[float, float, float] = (0, 0, 0)) -> None:
    """Author a fixed-base two-joint planar arm with SI units and force drives."""
    from pxr import Gf, UsdGeom, UsdPhysics

    root = UsdGeom.Xform.Define(stage, path)
    root.AddTranslateOp().Set(Gf.Vec3d(*origin))
    UsdPhysics.ArticulationRootAPI.Apply(root.GetPrim())
    base = make_box(stage, path + "/base", [0, 0, 0.65], [0.12, 0.12, 0.3], 1.0)
    links = [
        make_box(stage, path + "/link1", [0.35, 0, 0.8], [0.7, 0.08, 0.08], 1.0),
        make_box(stage, path + "/link2", [1.05, 0, 0.8], [0.7, 0.07, 0.07], 0.7),
    ]
    fixed = UsdPhysics.FixedJoint.Define(stage, path + "/fixed_base")
    fixed.CreateBody1Rel().SetTargets([base.GetPath()])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(*[origin[0], origin[1], origin[2] + 0.65]))
    for index, (body0, body1, anchor0) in enumerate(
        [
            (base, links[0], [0, 0, 0.15]),
            (links[0], links[1], [0.35, 0, 0]),
        ],
        start=1,
    ):
        joint = UsdPhysics.RevoluteJoint.Define(stage, path + f"/joint{index}")
        joint.CreateBody0Rel().SetTargets([body0.GetPath()])
        joint.CreateBody1Rel().SetTargets([body1.GetPath()])
        joint.CreateLocalPos0Attr(Gf.Vec3f(*anchor0))
        joint.CreateLocalPos1Attr(Gf.Vec3f(-0.35, 0, 0))
        joint.CreateAxisAttr("Z")
        joint.CreateLowerLimitAttr(-170.0)
        joint.CreateUpperLimitAttr(170.0)
        joint.CreateCollisionEnabledAttr(False)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(150.0)
        drive.CreateDampingAttr(15.0)
        drive.CreateMaxForceAttr(100.0)
        drive.CreateTargetPositionAttr(0.0)


class RoboticsWorld:
    """Own the stage, CUDA articulation, and native physics sensor lifecycles."""

    def __init__(self, *, dt: float = 1 / 120, num_arms: int = 1, gripper_backend: str = "constraint") -> None:
        import numpy as np
        import omni.kit.app
        import omni.timeline
        import omni.usd
        import warp as wp
        from isaacsim.core.simulation_manager import SimulationManager
        from isaacsim.sensors.experimental.physics import (
            IMU,
            Contact,
            ContactSensor,
            EffortSensor,
            IMUSensor,
            JointStateSensor,
            Raycast,
            RaycastSensor,
        )
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

        self.np, self.wp, self.manager = np, wp, SimulationManager
        if not 1 <= num_arms <= 16:
            raise ValueError("num_arms must be between 1 and 16")
        self.dt, self.num_arms = dt, num_arms
        self.timeline = omni.timeline.get_timeline_interface()
        self.context = omni.usd.get_context()
        self.context.new_stage()
        self.stage = stage = self.context.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityMagnitudeAttr(9.81)
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        settings = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
        settings.CreateEnableGPUDynamicsAttr(True)
        settings.CreateBroadphaseTypeAttr("GPU")
        settings.CreateSolverTypeAttr("TGS")
        make_box(stage, "/World/Ground", [0, 0, -0.05], [20, 20, 0.1])
        make_box(stage, "/World/DropBox", [0, -1, 0.6], [0.2, 0.2, 0.2], 1.0)
        make_box(stage, "/World/ScanObstacle", [0, -2, 0.5], [1, 0.2, 1])
        make_arm(stage)
        self.arm_paths = ["/World/Arm"] + [f"/World/Arm{index}" for index in range(1, num_arms)]
        if num_arms > 1:
            from isaacsim.core.cloner import GridCloner

            # Clone the complete joint hierarchy, then move each world-side fixed anchor.
            cloner = GridCloner(spacing=3.0, stage=stage)
            positions = cloner.clone("/World/Arm", self.arm_paths, copy_from_source=True)
            for path, position in zip(self.arm_paths, positions):
                joint = UsdPhysics.FixedJoint.Get(stage, path + "/fixed_base")
                joint.GetLocalPos0Attr().Set(Gf.Vec3f(float(position[0]), float(position[1]), 0.65))
        from .grasping import create_gripper

        self.gripper = create_gripper(stage, backend=gripper_backend)
        self.imu = IMUSensor(IMU.create("/World/DropBox/imu"))
        self.contact = ContactSensor(
            Contact.create(
                "/World/DropBox/contact",
                radius=-1.0,
                min_threshold=0.0,
                max_threshold=1e7,
            )
        )
        self.joint_sensor = JointStateSensor("/World/Arm")
        self.effort_sensor = EffortSensor("/World/Arm/joint1")
        self.ray_sensor = RaycastSensor(
            Raycast.create(
                "/World/Scan",
                positions=[[2, -1, 1.5]],
                min_range=0.01,
                max_range=10.0,
                ray_origins=[[x, 0, 0] for x in (-0.4, 0, 0.4)],
                ray_directions=[[0, 0, -1]] * 3,
                report_hit_prim_paths=True,
            )
        )
        angles = np.linspace(-np.pi, np.pi, 180, endpoint=False)
        self.scan_sensor = RaycastSensor(
            Raycast.create(
                "/World/PlanarScan",
                positions=[[0, -4, 0.5]],
                min_range=0.05,
                max_range=15.0,
                ray_origins=[[0, 0, 0]] * len(angles),
                ray_directions=np.column_stack([np.cos(angles), np.sin(angles), np.zeros_like(angles)]).tolist(),
                report_hit_prim_paths=True,
            )
        )
        SimulationManager.set_backend("warp")
        # Native grippers receive stage attachments and USD drive edits during Kit updates.
        omni.kit.app.get_app().update()
        SimulationManager.setup_simulation(dt=dt, device="cuda:0")
        self.timeline.play()
        SimulationManager.initialize_physics()
        self.view = SimulationManager.get_physics_simulation_view()
        self.device = str(self.view.device)
        if not self.device.startswith("cuda"):
            raise RuntimeError(f"CUDA physics required; received {self.device}")
        self.arm = self.view.create_articulation_view("/World/Arm*")
        if self.arm.count != num_arms or self.arm.max_dofs != 2:
            raise RuntimeError(f"Expected {num_arms} two-joint arms; got {self.arm.count}, {self.arm.max_dofs}")
        self.indices = wp.array(list(range(num_arms)), dtype=wp.uint32, device=self.device)
        self.control_modes = ["position"] * num_arms
        self.grasp_target = self.view.create_rigid_body_view(self.gripper["target"])
        self.grasp_body = self.view.create_rigid_body_view(self.gripper["body"])
        self.drop_box = self.view.create_rigid_body_view("/World/DropBox")
        self.step(240)

    def step(self, steps: int = 1) -> None:
        """Advance native simulation and sensor events without rendering."""
        self.manager.step(steps=steps, update_fabric=False)

    def snapshot(self) -> dict[str, Any]:
        """Read native articulation state and all physics sensors."""
        return {
            "physics_device": self.device,
            "arm_count": self.num_arms,
            "arm_paths": list(self.arm.prim_paths),
            "control_modes": self.control_modes.copy(),
            "simulation_time": self.manager.get_simulation_time(),
            "joint_positions": self.arm.get_dof_positions().numpy().tolist(),
            "joint_velocities": self.arm.get_dof_velocities().numpy().tolist(),
            "imu": {"valid": bool(self.imu.get_sensor_reading().is_valid), **self.imu.get_data()},
            "contact": {"valid": bool(self.contact.get_sensor_reading().is_valid), **self.contact.get_data()},
            "joint_state": {
                "valid": bool(self.joint_sensor.get_sensor_reading().is_valid),
                **self.joint_sensor.get_data(),
            },
            "effort": {
                "valid": bool(self.effort_sensor.get_sensor_reading().is_valid),
                **self.effort_sensor.get_data(),
            },
            "raycast": {"valid": bool(self.ray_sensor.get_sensor_reading().is_valid), **self.ray_sensor.get_data()},
        }

    def scan(self) -> dict[str, Any]:
        """Read 180 native geometric rays; this is not an RTX LiDAR sensor."""
        return {
            "valid": bool(self.scan_sensor.get_sensor_reading().is_valid),
            "backend": "native physics raycast",
            "native_rtx": False,
            "origin_world": [0, -4, 0.5],
            "angle_min": -float(self.np.pi),
            "angle_increment": float(2 * self.np.pi / 180),
            "range_min": 0.05,
            "range_max": 15.0,
            **self.scan_sensor.get_data(),
        }

    def command(self, target: list[float], steps: int) -> dict[str, Any]:
        """Drive actual PhysX joint positions, in radians, then read the result."""
        self.apply_control("position", target)
        self.step(steps)
        return self.snapshot()

    def apply_control(self, mode: str, targets: list[float] | list[list[float]], arm_index: int = 0) -> None:
        """Apply position (rad), velocity (rad/s), or effort (N m) commands on CUDA.

        One pair controls ``arm_index``; an N-by-2 array controls every cloned arm.
        Mode switches also clear stale forces and change the physical drive gains.
        """
        if mode not in ("position", "velocity", "effort"):
            raise ValueError("mode must be position, velocity, or effort")
        if not 0 <= arm_index < self.num_arms:
            raise ValueError("arm_index is out of range")
        values = self.np.asarray(targets, dtype=self.np.float32)
        if values.shape == (2,):
            selected = [arm_index]
            values = values.reshape(1, 2)
        elif values.shape == (self.num_arms, 2):
            selected = list(range(self.num_arms))
        else:
            raise ValueError(f"targets must contain two values or {self.num_arms} pairs")
        limit = {"position": 2.96, "velocity": 5.0, "effort": 20.0}[mode]
        if not self.np.isfinite(values).all() or self.np.abs(values).max() > limit:
            raise ValueError(f"{mode} target absolute value must not exceed {limit}")
        # Tensor setters take a full articulation buffer plus a subset of row indices.
        shape = (self.num_arms, 2)
        indices = self.wp.array(selected, dtype=self.wp.uint32, device=self.device)

        def buffer(number: float = 0.0, device: str | None = None) -> Any:
            return self.wp.array(self.np.full(shape, number, self.np.float32), device=device or self.device)

        # PhysX parameter edits require host buffers; target/force tensors and stepping stay on CUDA.
        parameter_indices = self.wp.array(selected, dtype=self.wp.uint32, device="cpu")
        self.arm.set_dof_stiffnesses(buffer(150.0 if mode == "position" else 0.0, "cpu"), parameter_indices)
        damping = 15.0 if mode == "position" else 20.0 if mode == "velocity" else 0.0
        self.arm.set_dof_dampings(buffer(damping, "cpu"), parameter_indices)
        self.arm.set_dof_actuation_forces(buffer(), indices)
        self.arm.set_dof_velocity_targets(buffer(), indices)
        full_targets = self.np.zeros(shape, self.np.float32)
        full_targets[selected] = values
        data = self.wp.array(full_targets, device=self.device)
        setter = {
            "position": self.arm.set_dof_position_targets,
            "velocity": self.arm.set_dof_velocity_targets,
            "effort": self.arm.set_dof_actuation_forces,
        }[mode]
        setter(data, indices)
        for index in selected:
            self.control_modes[index] = mode

    def dynamics(self) -> dict[str, Any]:
        """Read CUDA articulation Jacobians, mass matrices, and measured forces."""
        return {
            "jacobians": self.arm.get_jacobians().numpy().tolist(),
            "mass_matrices": self.arm.get_generalized_mass_matrices().numpy().tolist(),
            "actuation_forces": self.arm.get_dof_actuation_forces().numpy().tolist(),
            "projected_joint_forces": self.arm.get_dof_projected_joint_forces().numpy().tolist(),
        }

    def grasp(self, action: str) -> dict[str, Any]:
        """Run physical close, lift, open, or a full grasp/release cycle."""
        import omni.kit.app

        from .grasping import read_gripper

        def advance(steps: int) -> None:
            for _ in range(steps):
                omni.kit.app.get_app().update()

        if action not in ("close", "lift", "open", "cycle", "status"):
            raise ValueError("action must be close, lift, open, cycle, or status")
        states = {"before": read_gripper(self.gripper, self.grasp_target)}
        if action in ("close", "cycle"):
            self.gripper["drive"].Set(0.07 - 0.4)
            advance(120)
            if "view" in self.gripper:
                self.gripper["view"].apply_gripper_action([0.5])
            else:
                from .grasping import close_constraint_gripper

                close_constraint_gripper(self.stage, self.gripper, self.grasp_body, self.grasp_target)
            advance(90)
            states["closed"] = read_gripper(self.gripper, self.grasp_target)
        if action in ("lift", "cycle"):
            for height in self.np.linspace(0.07, 0.3, 50):
                self.gripper["drive"].Set(float(height) - 0.4)
                advance(2)
            advance(90)
            states["lifted"] = read_gripper(self.gripper, self.grasp_target)
        if action in ("open", "cycle"):
            if "view" in self.gripper:
                self.gripper["view"].apply_gripper_action([-0.5])
            else:
                self.stage.RemovePrim("/World/Grasp/CustomAttachment")
                self.gripper["held"] = False
            advance(120)
            states["released"] = read_gripper(self.gripper, self.grasp_target)
        return states

    def read_body_poses(self) -> dict[str, list[float]]:
        """Read world-space link and rigid-body poses directly from CUDA tensors."""
        poses = {}
        for paths, values in zip(self.arm.link_paths, self.arm.get_link_transforms().numpy()):
            poses.update({path: value.tolist() for path, value in zip(paths, values)})
        for path, view in [
            ("/World/DropBox", self.drop_box),
            (self.gripper["target"], self.grasp_target),
            (self.gripper["body"], self.grasp_body),
        ]:
            poses[path] = view.get_transforms().numpy()[0].tolist()
        return poses

    def save(self, path: Path, poses: dict[str, list[float]] | None = None) -> Any:
        """Export CUDA poses to a separate USD stage, preserving the live simulation."""
        from pxr import Gf, Usd, UsdGeom

        # Suppressed GPU readback leaves live USD transforms at their authored poses.
        # Write a flattened render/export copy instead of teleporting the running bodies.
        exported = Usd.Stage.Open(self.stage.Flatten())
        cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        for name, pose in (poses if poses is not None else self.read_body_poses()).items():
            prim = exported.GetPrimAtPath(name)
            x, y, z, w = [float(value) for value in pose[3:7]]
            world = Gf.Matrix4d().SetRotate(Gf.Quatd(w, Gf.Vec3d(x, y, z)))
            world.SetTranslateOnly(Gf.Vec3d(*[float(value) for value in pose[:3]]))
            local = world * cache.GetLocalToWorldTransform(prim.GetParent()).GetInverse()
            transform = UsdGeom.Xformable(prim)
            transform.ClearXformOpOrder()
            transform.AddTransformOp().Set(local)
            cache.Clear()
        exported.GetRootLayer().Export(str(path))
        return exported

    def close(self) -> None:
        """Stop physics before releasing the owning Kit application."""
        for sensor in (
            self.imu,
            self.contact,
            self.joint_sensor,
            self.effort_sensor,
            self.ray_sensor,
            self.scan_sensor,
        ):
            sensor.reset()
        self.timeline.stop()
        self.manager.invalidate_physics()
