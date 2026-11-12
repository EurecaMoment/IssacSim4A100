"""Check RTX adapter protocol differences without loading Isaac Sim."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtx4090.service import RtxQueue


class RtxContractTests(unittest.TestCase):
    """Verify truthful runtime metadata and unsupported capture rejection."""

    def test_runtime_identity_and_sensors_are_reported_from_the_installation(self) -> None:
        """Report the selected physical GPU and actual installed build string."""
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "VERSION").write_text("6.0.1-test-build\n")
            with patch.dict(os.environ, {"ISAAC_SIM_DIR": directory, "ISAAC_GPU": "3"}):
                state = RtxQueue(runtime / "jobs", app=None)
                health = state.health()
                self.assertEqual(health["isaac_sim"], "6.0.1-test-build")
                self.assertEqual(health["physical_gpu"], 3)
                self.assertTrue(health["native_rtx"])
                self.assertIn("distance_to_image_plane", health["annotators"])
                self.assertFalse(health["cpu_fallback"])

    def test_common_scene_contract_is_accepted_but_gaussian_is_rejected(self) -> None:
        """Keep bounded A100 scene inputs while refusing unsupported render kinds."""
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "VERSION").write_text("6.0.1\n")
            with patch.dict(os.environ, {"ISAAC_SIM_DIR": directory}):
                state = RtxQueue(runtime / "jobs", app=None)
                request = state.prepare({"bodies": [{"id": "cube"}], "render": {"size": 128}})
                self.assertEqual(request["bodies"][0]["id"], "cube")
                with self.assertRaisesRegex(ValueError, "Gaussian"):
                    state.prepare({"render": {"kind": "gaussian"}})
                with self.assertRaises(ValueError):
                    state.prepare({"steps": 1201})


if __name__ == "__main__":
    unittest.main()
