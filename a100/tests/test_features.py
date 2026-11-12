"""Check robot command boundaries before any request can reach the physics thread."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from a100sim.feature_service import FeatureQueue
from a100sim.ros2_bridge import PhysicsROSBridge


class FeatureContractTests(unittest.TestCase):
    """Keep malformed physical commands and unsupported optional jobs out of the queue."""

    def test_robot_commands_reject_invalid_shapes_modes_and_nonfinite_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = FeatureQueue(Path(directory), 4)
            invalid = [
                {"targets": [0.1]},
                {"targets": [0.1, 0.2, 0.3]},
                {"targets": [[0.1, 0.2]]},
                {"targets": [float("nan"), 0]},
                {"targets": [float("inf"), 0]},
                {"targets": [True, 0]},
                {"targets": [2.97, 0]},
                {"targets": [0, 0], "mode": "teleport"},
                {"targets": [0, 0], "arm_index": 4},
                {"targets": [0, 0], "arm_index": -1},
                {"targets": [0, 0], "steps": 1201},
                {"targets": [0, 0], "steps": 0},
                {"targets": [0, 0], "steps": 2.5},
            ]
            for command in invalid:
                with self.subTest(command=command), self.assertRaises((ValueError, TypeError)):
                    state.prepare({"action": "command", **command})
            for mode, target in [("position", 1.0), ("velocity", 4.0), ("effort", 15.0)]:
                state.prepare({"action": "command", "mode": mode, "targets": [target, -target]})
            state.prepare({"action": "command", "targets": [[0.1, -0.1]] * 4})
            self.assertTrue(state.jobs.empty())

    def test_optional_cloth_is_advertised_only_when_newton_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("a100sim.feature_service.importlib.util.find_spec", return_value=None):
                state = FeatureQueue(Path(directory), 1)
            with self.assertRaises(ValueError):
                state.prepare({"action": "cloth"})
            self.assertNotIn("cloth", state.actions)

    def test_bounded_capture_scan_and_trajectory_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = FeatureQueue(Path(directory), 4)
            invalid = [
                {"action": "capture", "size": 1025},
                {"action": "capture", "spp": 0},
                {"action": "trajectory", "points": [[0, 0]]},
                {"action": "trajectory", "points": [[0, 0], [0, float("nan")]]},
                {"action": "trajectory", "points": [[0, 0], [0.1, 0.2]], "max_velocity": 0},
                {"action": "gripper", "operation": "arbitrary_code"},
                {"action": "differential_drive", "wheel_radius": 0},
            ]
            for request in invalid:
                with self.subTest(request=request), self.assertRaises((ValueError, TypeError)):
                    state.prepare(request)

    def test_ros_commands_map_reversed_names_and_reject_ambiguous_modes(self) -> None:
        bridge = PhysicsROSBridge.__new__(PhysicsROSBridge)
        bridge.world, bridge.node = Mock(), Mock()
        bridge.commands_received = bridge.commands_rejected = 0
        command = SimpleNamespace(name=["arm2/joint2", "arm2/joint1"], position=[-0.2, 0.4], velocity=[], effort=[])
        bridge.command(command)
        bridge.world.apply_control.assert_called_once_with("position", [0.4, -0.2], 2)
        for bad in [
            SimpleNamespace(name=["arm0/joint1", "arm1/joint2"], position=[0, 0], velocity=[], effort=[]),
            SimpleNamespace(name=["arm0/joint1", "arm0/joint2"], position=[0, 0], velocity=[1, 1], effort=[]),
        ]:
            bridge.command(bad)
        self.assertEqual(bridge.commands_received, 1)
        self.assertEqual(bridge.commands_rejected, 2)
        bridge.world.apply_control.assert_called_once()


if __name__ == "__main__":
    unittest.main()
