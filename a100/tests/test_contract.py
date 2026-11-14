"""Verify request bounds, asset containment and durable job recovery without a GPU."""

from __future__ import annotations

import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from a100sim import service_queue
from a100sim.physics_service import PhysicsQueue
from a100sim.render_service import RenderState


class ServiceContractTests(unittest.TestCase):
    def test_preloaded_render_rejects_invalid_values_without_silent_clamping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = RenderState(root / "assets", root / "results", 256, 4)
            state.renderers = {"mesh": object()}
            for request in (
                {"spp": 0},
                {"spp": 257},
                {"seed": -1},
                {"distance": 0},
                {"elevation": 90},
                {"azimuth": float("nan")},
                {"spp": True},
                {"size": 512},
            ):
                with self.subTest(request=request), self.assertRaises(ValueError):
                    state.submit(request)
            self.assertTrue(state.jobs.empty())
            state.submit({"seed": 0, "elevation": 0})
            job = state.jobs.get_nowait()
            self.assertEqual(job["seed"], 0)
            self.assertEqual(job["elevation"], 0)

    def test_queue_rejects_overflow_without_creating_another_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = service_queue.JobQueue(directory, capacity=1)
            state.ready = True
            record = state.submit({"value": 1})
            with self.assertRaises(queue.Full):
                state.submit({"value": 2})
            jobs = list(Path(directory).glob("*/job.json"))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(json.loads(jobs[0].read_text())["id"], record["id"])

    def test_restart_marks_unfinished_job_interrupted_without_replaying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = service_queue.JobQueue(directory)
            state.ready = True
            record = state.submit({"value": 1})
            restarted = service_queue.JobQueue(directory)
            self.assertEqual(restarted.status(record["id"])["state"], "interrupted")
            self.assertTrue(restarted.jobs.empty())

    def test_assets_reject_paths_and_symlinks_outside_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()
            with patch.object(service_queue, "ASSET_ROOT", root.resolve()):
                self.assertEqual(service_queue.asset_directory(str(root)), root.resolve())
                with self.assertRaises(ValueError):
                    service_queue.asset_directory(str(outside))
                with self.assertRaises(ValueError):
                    service_queue.asset_directory(str(root / ".." / "outside"))
                try:
                    (root / "escape").symlink_to(outside, target_is_directory=True)
                except OSError:
                    return  # Windows may not allow creating symlinks; verified on Linux as well.
                with self.assertRaises(ValueError):
                    service_queue.asset_directory(str(root / "escape"))

    def test_request_rejects_nonfinite_and_boolean_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = PhysicsQueue(directory)
            for request in (
                {"steps": True},
                {"gravity": float("nan")},
                {"bodies": []},
                {"steps": 1201},
                {"robot_command": "unsupported"},
            ):
                with self.subTest(request=request), self.assertRaises((ValueError, KeyError)):
                    state.prepare(request)

    def test_mesh_requires_prepared_asset_and_normalizes_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mesh.npz").touch()
            state = PhysicsQueue(root / "jobs")
            with patch.object(service_queue, "ASSET_ROOT", root):
                request = state.prepare(
                    {"bodies": [{"shape": "mesh", "asset": str(root), "orientation_xyzw": [0, 0, 0, 0.5]}]}
                )
                self.assertEqual(request["bodies"][0]["orientation_xyzw"], [0, 0, 0, 1])


if __name__ == "__main__":
    unittest.main()
