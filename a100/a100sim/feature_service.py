"""Serve verified robotics and sensor operations on one persistent CUDA world."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import queue
import signal
import time
from pathlib import Path
from typing import Any

from .service_queue import JobQueue, finite, integer, persist, start_http


def json_value(value: Any) -> Any:
    """Convert native sensor arrays to strict JSON, using null for missed rays."""
    if hasattr(value, "tolist"):
        return json_value(value.tolist())
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class FeatureQueue(JobQueue):
    """Validate JSON at the HTTP boundary; execute all Kit work on its owner thread."""

    def __init__(self, output: Path, num_arms: int) -> None:
        super().__init__(output, capacity=32, engine="Isaac Sim 6.2 CUDA robotics")
        self.num_arms = num_arms
        self.world: Any = None
        self.actions = [
            "snapshot",
            "step",
            "command",
            "dynamics",
            "scan",
            "gripper",
            "capture",
            "trajectory",
            "differential_drive",
            "rl_step",
            "export_scene",
            "inspect_gains",
        ]
        if importlib.util.find_spec("newton") is not None:
            self.actions.append("cloth")

    def prepare(self, value: dict[str, Any]) -> dict[str, Any]:
        action = value.get("action", "snapshot")
        if action not in self.actions:
            raise ValueError("action must be one of: " + ", ".join(self.actions))
        result = {"action": action}
        if action in ("step", "command", "rl_step"):
            result["steps"] = integer(value.get("steps", 120 if action == "command" else 1), "steps", 1, 1200)
        if action in ("command", "rl_step"):
            mode = value.get("mode", "position")
            if mode not in ("position", "velocity", "effort"):
                raise ValueError("mode must be position, velocity, or effort")
            result["mode"] = mode
            result["arm_index"] = integer(value.get("arm_index", 0), "arm_index", 0, self.num_arms - 1)
            values = value.get("targets")
            if not isinstance(values, list) or not values:
                raise ValueError("targets must be a pair or an N-by-2 list")
            rows = values if isinstance(values[0], list) else [values]
            if len(rows) not in (1, self.num_arms) or (isinstance(values[0], list) and len(rows) != self.num_arms):
                raise ValueError(f"Batch targets must contain {self.num_arms} pairs")
            limit = {"position": 2.96, "velocity": 5.0, "effort": 20.0}[mode]
            for row in rows:
                if not isinstance(row, list) or len(row) != 2:
                    raise ValueError("Each target must contain exactly two values")
            checked = [[finite(v, "target", -limit, limit) for v in row] for row in rows]
            result["targets"] = checked if isinstance(values[0], list) else checked[0]
        if action == "gripper":
            result["operation"] = value.get("operation", "status")
            if result["operation"] not in ("status", "close", "lift", "open", "cycle"):
                raise ValueError("Unknown gripper operation")
        if action == "capture":
            result.update(
                size=integer(value.get("size", 512), "size", 64, 1024), spp=integer(value.get("spp", 32), "spp", 1, 256)
            )
        if action == "cloth":
            result["frames"] = integer(value.get("frames", 90), "frames", 1, 300)
        if action == "trajectory":
            points = value.get("points")
            if not isinstance(points, list) or not 2 <= len(points) <= 128:
                raise ValueError("points must contain 2 to 128 joint-position pairs")
            if any(not isinstance(row, list) or len(row) != 2 for row in points):
                raise ValueError("Every waypoint must contain two positions")
            result["points"] = [[finite(v, "position", -2.96, 2.96) for v in row] for row in points]
            result["arm_index"] = integer(value.get("arm_index", 0), "arm_index", 0, self.num_arms - 1)
            result["max_velocity"] = finite(value.get("max_velocity", 1.0), "max_velocity", 0.05, 3.0)
            result["max_acceleration"] = finite(value.get("max_acceleration", 2.0), "max_acceleration", 0.1, 10.0)
        if action == "differential_drive":
            result.update(
                linear_velocity=finite(value.get("linear_velocity", 0.5), "linear_velocity", -5, 5),
                angular_velocity=finite(value.get("angular_velocity", 0.5), "angular_velocity", -5, 5),
                wheel_radius=finite(value.get("wheel_radius", 0.1), "wheel_radius", 0.01, 2),
                wheel_base=finite(value.get("wheel_base", 0.4), "wheel_base", 0.01, 5),
            )
        return result

    def process(self, payload: dict[str, Any], directory: Path) -> dict[str, Any]:
        action, world = payload["action"], self.world
        result = {"action": action, "physics_device": world.device, "native_rtx": False}
        if action in ("command", "rl_step"):
            world.apply_control(payload["mode"], payload["targets"], payload["arm_index"])
        if action in ("step", "command", "rl_step"):
            world.step(payload["steps"])
        if action == "dynamics":
            result["dynamics"] = world.dynamics()
        elif action == "scan":
            result["scan"] = world.scan()
        elif action == "gripper":
            result["gripper"] = world.grasp(payload["operation"])
        elif action == "capture":
            result["capture"] = self.capture(directory, payload)
        elif action == "export_scene":
            scene = directory / "robot-scene.usda"
            world.save(scene)
            result["scene"] = str(scene)
        elif action == "inspect_gains":
            from isaacsim.robot_setup.gain_tuner.gains_tuner import GainTuner

            tuner = GainTuner()
            tuner.setup(world.arm_paths[0])
            tuner.compute_joints_accumulated_inertia()
            result["gains"] = {
                "scope": "joint discovery and accumulated inertia; no automatic tuning",
                "joints": [
                    {
                        "name": joint.display_name,
                        "index": joint.dof_index,
                        "inertia": tuner.get_joint_accumulated_inertia(joint.joint),
                    }
                    for joint in tuner.get_joint_entries()
                ],
            }
        elif action == "cloth":
            from .newton_cloth import simulate_cloth

            result["cloth"] = simulate_cloth(directory, payload["frames"])
        elif action == "trajectory":
            from isaacsim.robot_motion.experimental.motion_generation import Path as JointPath

            trajectory = JointPath(payload["points"]).to_minimal_time_joint_trajectory(
                max_velocities=[payload["max_velocity"]] * 2,
                max_accelerations=[payload["max_acceleration"]] * 2,
                robot_joint_space=["joint1", "joint2"],
                active_joints=["joint1", "joint2"],
            )
            steps = math.ceil(trajectory.duration / world.dt)
            if steps > 7200:
                raise ValueError("Trajectory exceeds the 60-second simulation limit")
            trace = []
            for number in range(steps + 1):
                target = trajectory.get_target_state(min(number * world.dt, trajectory.duration))
                world.apply_control(
                    "position", target.joints.positions.numpy().reshape(-1).tolist(), payload["arm_index"]
                )
                world.step()
                if number % 12 == 0:
                    trace.append(world.arm.get_dof_positions().numpy()[payload["arm_index"]].tolist())
            world.step(120)
            result["trajectory"] = {
                "duration": trajectory.duration,
                "sampled_positions": trace,
                "waypoints": payload["points"],
                "executed": True,
            }
        elif action == "differential_drive":
            import warp as wp
            from isaacsim.robot_motion import controllers
            from isaacsim.robot_motion.experimental import motion_generation as motion

            controller = controllers.DifferentialDriveController(
                robot_joint_space=["left", "right"],
                left_wheel_joint="left",
                right_wheel_joint="right",
                wheel_radius=payload["wheel_radius"],
                wheel_base=payload["wheel_base"],
                device=world.device,
            )
            target = motion.RobotState(
                sites=motion.SpatialState.from_name(
                    spatial_space=["control_point"],
                    linear_velocities=(
                        ["control_point"],
                        wp.array([[payload["linear_velocity"], 0, 0]], dtype=wp.float32, device=world.device),
                    ),
                    angular_velocities=(
                        ["control_point"],
                        wp.array([[0, 0, payload["angular_velocity"]]], dtype=wp.float32, device=world.device),
                    ),
                )
            )
            command = controller.forward(motion.RobotState(), target, 0.0)
            result["controller"] = {
                "wheel_velocities_rad_s": command.joints.velocities.numpy().tolist(),
                "buffer_device": str(command.joints.velocities.device),
                "scope": "controller output only; no wheeled robot is spawned",
            }
        state = world.snapshot()
        result["snapshot"] = state
        if action == "rl_step":
            positions, velocities = world.np.asarray(state["joint_positions"]), world.np.asarray(
                state["joint_velocities"]
            )
            result["observations"] = world.np.concatenate([positions, velocities], axis=1).tolist()
            result["rewards"] = (-world.np.square(positions).sum(axis=1)).tolist()
            result["terminated"] = [False] * world.num_arms
            result["scope"] = "batched observation/reward step; no Isaac Lab or training algorithm"
        cleaned = json_value(result)
        persist(directory / "result.json", cleaned)
        cleaned["result_file"] = str(directory / "result.json")
        return cleaned

    def capture(self, directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
        """Render the live robot world and custom metric/ID annotation arrays."""
        from pxr import Gf, Sdf, UsdGeom, UsdLux

        from .usd_renderer import render_stage

        stage = self.world.stage
        for path, label in [
            ("/World/Ground", "floor"),
            ("/World/DropBox", "box"),
            ("/World/ScanObstacle", "obstacle"),
            ("/World/Grasp", "gripper"),
        ]:
            stage.GetPrimAtPath(path).CreateAttribute("a100sim:semanticLabel", Sdf.ValueTypeNames.String).Set(label)
        colors = [(0.85, 0.23, 0.05), (0.03, 0.55, 0.44), (0.08, 0.3, 0.8), (0.65, 0.12, 0.32)]
        for index, path in enumerate(self.world.arm_paths):
            stage.GetPrimAtPath(path).CreateAttribute("a100sim:semanticLabel", Sdf.ValueTypeNames.String).Set("robot")
            for name in ("link1", "link2"):
                UsdGeom.Gprim(stage.GetPrimAtPath(path + "/" + name + "/Visual")).CreateDisplayColorAttr(
                    [Gf.Vec3f(*colors[index % len(colors)])]
                )
            UsdGeom.Gprim(stage.GetPrimAtPath(path + "/base/Visual")).CreateDisplayColorAttr(
                [Gf.Vec3f(0.12, 0.14, 0.18)]
            )
            # A visual pedestal fills the fixed base's gap above the floor; it has no collision or dynamics.
            pedestal = UsdGeom.Cube.Define(stage, path + "/Pedestal")
            pedestal.CreateSizeAttr(1.0)
            pedestal.ClearXformOpOrder()
            pedestal.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.25))
            pedestal.AddScaleOp().Set(Gf.Vec3f(0.22, 0.22, 0.5))
            pedestal.CreateDisplayColorAttr([Gf.Vec3f(0.12, 0.14, 0.18)])
        for path, color in [
            ("/World/Ground/Visual", (0.3, 0.32, 0.35)),
            ("/World/ScanObstacle/Visual", (0.18, 0.21, 0.25)),
            ("/World/DropBox/Visual", (0.95, 0.52, 0.04)),
        ]:
            UsdGeom.Gprim(stage.GetPrimAtPath(path)).CreateDisplayColorAttr([Gf.Vec3f(*color)])
        camera = UsdGeom.Camera.Define(stage, "/World/FeatureCamera")
        camera.CreateFocalLengthAttr(35)
        camera.CreateHorizontalApertureAttr(28)
        transform = Gf.Matrix4d().SetLookAt(Gf.Vec3d(6, -8, 5.5), Gf.Vec3d(0, -0.5, 0.4), Gf.Vec3d(0, 0, 1))
        camera.ClearXformOpOrder()
        camera.AddTransformOp().Set(transform.GetInverse())
        dome = UsdLux.DomeLight.Define(stage, "/World/FeatureDome")
        dome.CreateColorAttr(Gf.Vec3f(0.65, 0.7, 0.8))
        dome.CreateIntensityAttr(0.35)
        light = UsdLux.RectLight.Define(stage, "/World/FeatureKey")
        light.CreateWidthAttr(4)
        light.CreateHeightAttr(4)
        light.CreateIntensityAttr(14)
        light.CreateColorAttr(Gf.Vec3f(1.0, 0.86, 0.72))
        light.ClearXformOpOrder()
        light.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(-2, -4, 7), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 0, 1)).GetInverse()
        )
        poses = self.world.read_body_poses()
        exported = self.world.save(directory / "scene.usdc", poses)
        rendered = render_stage(
            exported,
            "/World/FeatureCamera",
            "mesh",
            directory / "render.png",
            size=payload["size"],
            spp=payload["spp"],
            annotations=True,
        )
        return {"pose_source": "CUDA articulation and rigid-body tensors", "body_poses_xyzw": poses, **rendered}


def main() -> None:
    """Start one persistent world, a bounded loopback queue, and optional ROS 2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8322)
    parser.add_argument("--arms", type=int, default=4, choices=range(1, 17))
    parser.add_argument("--ros2", action="store_true")
    arguments = parser.parse_args()
    state = FeatureQueue(arguments.output, arguments.arms)
    server = start_http(state, arguments.host, arguments.port, "/robotics")
    from .app import create_app

    app = create_app()
    world = bridge = None
    running = True

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        from .robotics import RoboticsWorld

        world = RoboticsWorld(num_arms=arguments.arms)
        state.world = world
        if arguments.ros2:
            from .ros2_bridge import PhysicsROSBridge

            bridge = PhysicsROSBridge(world)
            bridge.publish()
        state.details = {
            "arm_count": world.num_arms,
            "physics_device": world.device,
            "controls": ["position", "velocity", "effort"],
            "sensors": ["imu", "contact", "joint_state", "effort", "raycast"],
            "custom_camera_outputs": ["rgb", "depth_m", "normal_world", "instance_id", "semantic_id"],
            "scan_beams": 180,
            "ros2": bool(bridge),
            "ros2_domain_id": os.environ.get("ROS_DOMAIN_ID"),
            "gripper_backend": world.gripper["backend"],
            "native_surface_gripper_verified": False,
            "official_rtx_ros_bridge": False,
            "replicator": False,
            "actions": state.actions,
        }
        state.ready = True
        print("FEATURE_SERVICE_READY", json.dumps(state.health()), flush=True)
        next_tick = next_publish = time.monotonic()
        while running:
            try:
                state.run_one(state.jobs.get(timeout=0.005 if bridge else 0.25))
            except queue.Empty:
                pass
            if bridge:
                bridge.spin()
                now = time.monotonic()
                if now >= next_tick:
                    world.step(2)
                    next_tick = now + 1 / 60
                if now >= next_publish:
                    bridge.publish()
                    state.details.update(
                        ros2_commands_received=bridge.commands_received,
                        ros2_commands_rejected=bridge.commands_rejected,
                        ros2_publish_cycles=bridge.messages_published,
                    )
                    next_publish = now + 0.05
    finally:
        state.ready = False
        server.shutdown()
        server.server_close()
        if bridge:
            bridge.close()
        if world:
            world.close()
        app.close()


if __name__ == "__main__":
    main()
