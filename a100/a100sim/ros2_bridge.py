"""A small rclpy data bridge for the A100 physics service.

Uses the official bundled ROS 2 libraries without loading RTX-dependent
OmniGraph bridge extensions. This is intentionally a separate implementation.
"""

from __future__ import annotations

import math
from typing import Any


class PhysicsROSBridge:
    """Publish simulation state and accept bounded commands on the Kit thread."""

    def __init__(self, world: Any) -> None:
        import rclpy
        from geometry_msgs.msg import TransformStamped
        from rosgraph_msgs.msg import Clock
        from sensor_msgs.msg import Imu, JointState, LaserScan
        from tf2_msgs.msg import TFMessage

        self.world, self.rclpy = world, rclpy
        self.Clock, self.Imu, self.JointState = Clock, Imu, JointState
        self.LaserScan, self.TFMessage, self.TransformStamped = LaserScan, TFMessage, TransformStamped
        rclpy.init()
        self.node = rclpy.create_node("isaacsim_a100_physics")
        self.joints = self.node.create_publisher(JointState, "/a100/joint_states", 10)
        self.imu = self.node.create_publisher(Imu, "/a100/imu", 10)
        self.clock = self.node.create_publisher(Clock, "/clock", 10)
        self.scan = self.node.create_publisher(LaserScan, "/a100/scan", 10)
        self.transforms = self.node.create_publisher(TFMessage, "/tf", 10)
        self.subscription = self.node.create_subscription(JointState, "/a100/joint_commands", self.command, 10)
        self.commands_received = 0
        self.commands_rejected = 0
        self.messages_published = 0

    def command(self, message: Any) -> None:
        """Accept arm0/joint1, arm0/joint2 pairs with one unambiguous command mode."""
        try:
            if len(message.name) != 2:
                raise ValueError("Exactly two named joints are required")
            prefix = message.name[0].split("/")[0]
            if not prefix.startswith("arm"):
                raise ValueError("Joint names must start with armN/")
            index = int(prefix[3:])
            if set(message.name) != {prefix + "/joint1", prefix + "/joint2"}:
                raise ValueError("Both joints must belong to the same arm")
            fields = [
                (mode, list(values))
                for mode, values in (
                    ("position", message.position),
                    ("velocity", message.velocity),
                    ("effort", message.effort),
                )
                if values
            ]
            if len(fields) != 1 or len(fields[0][1]) != 2:
                raise ValueError("Supply exactly one position, velocity, or effort pair")
            mode, values = fields[0]
            by_name = dict(zip(message.name, values))
            self.world.apply_control(mode, [by_name[prefix + "/joint1"], by_name[prefix + "/joint2"]], index)
            self.commands_received += 1
        except (ValueError, TypeError, RuntimeError) as error:
            self.commands_rejected += 1
            self.node.get_logger().warning("Rejected joint command: " + str(error))

    def publish(self) -> None:
        """Publish native readings with the simulation clock and explicit frames."""
        state = self.world.snapshot()
        stamp = self.Clock().clock
        stamp.sec = int(state["simulation_time"])
        stamp.nanosec = int((state["simulation_time"] - stamp.sec) * 1e9)
        clock = self.Clock()
        clock.clock = stamp
        self.clock.publish(clock)
        joints = self.JointState()
        joints.header.stamp = stamp
        joints.header.frame_id = "world"
        joints.name = [f"arm{index}/joint{joint}" for index in range(self.world.num_arms) for joint in (1, 2)]
        joints.position = [float(v) for row in state["joint_positions"] for v in row]
        joints.velocity = [float(v) for row in state["joint_velocities"] for v in row]
        forces = self.world.arm.get_dof_projected_joint_forces().numpy()
        joints.effort = [float(v) for row in forces for v in row]
        self.joints.publish(joints)
        imu = self.Imu()
        imu.header.stamp, imu.header.frame_id = stamp, "drop_box_imu"
        reading = state["imu"]
        if reading["valid"]:
            w, x, y, z = [float(v) for v in reading["orientation"]]
            imu.orientation.w, imu.orientation.x, imu.orientation.y, imu.orientation.z = w, x, y, z
            for name, vector in (
                ("angular_velocity", imu.angular_velocity),
                ("linear_acceleration", imu.linear_acceleration),
            ):
                vector.x, vector.y, vector.z = [float(v) for v in reading[name]]
            self.imu.publish(imu)
        data = self.world.scan()
        scan = self.LaserScan()
        scan.header.stamp, scan.header.frame_id = stamp, "scan"
        scan.angle_min, scan.angle_increment = data["angle_min"], data["angle_increment"]
        scan.angle_max = scan.angle_min + scan.angle_increment * (len(data["depths"]) - 1)
        scan.range_min, scan.range_max = data["range_min"], data["range_max"]
        scan.ranges = [
            float(v) if math.isfinite(float(v)) and float(v) >= data["range_min"] else float("inf")
            for v in data["depths"]
        ]
        self.scan.publish(scan)
        poses = self.world.arm.get_link_transforms().numpy()
        frames = []
        for index, links in enumerate(poses):
            for number, pose in enumerate(links):
                frame = self.TransformStamped()
                frame.header.stamp, frame.header.frame_id = stamp, "world"
                frame.child_frame_id = f"arm{index}/" + ("base" if number == 0 else f"link{number}")
                frame.transform.translation.x, frame.transform.translation.y, frame.transform.translation.z = [
                    float(v) for v in pose[:3]
                ]
                rotation = frame.transform.rotation
                rotation.x, rotation.y, rotation.z, rotation.w = [float(v) for v in pose[3:7]]
                frames.append(frame)
        for name, position in (
            ("scan", [0, -4, 0.5]),
            ("drop_box_imu", self.world.drop_box.get_transforms().numpy()[0, :3]),
        ):
            frame = self.TransformStamped()
            frame.header.stamp, frame.header.frame_id = stamp, "world"
            frame.child_frame_id = name
            frame.transform.translation.x, frame.transform.translation.y, frame.transform.translation.z = [
                float(v) for v in position
            ]
            if name == "drop_box_imu":
                pose = self.world.drop_box.get_transforms().numpy()[0]
                (
                    frame.transform.rotation.x,
                    frame.transform.rotation.y,
                    frame.transform.rotation.z,
                    frame.transform.rotation.w,
                ) = [float(v) for v in pose[3:7]]
            else:
                frame.transform.rotation.w = 1.0
            frames.append(frame)
        self.transforms.publish(self.TFMessage(transforms=frames))
        self.messages_published += 1

    def spin(self) -> None:
        """Run pending DDS callbacks on the thread that owns the physics world."""
        self.rclpy.spin_once(self.node, timeout_sec=0.0)

    def close(self) -> None:
        """Release the node and DDS context."""
        self.node.destroy_node()
        self.rclpy.shutdown()
