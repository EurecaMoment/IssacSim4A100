"""Protect device allocation boundaries and capability-based Newton configuration."""

from __future__ import annotations

import unittest

from a100sim.host import GPU, select_gpu
from a100sim.newton_compat import conditional_graphs_enabled


class PortabilityTests(unittest.TestCase):
    """Keep auto detection from bypassing allocations or requiring a newer graph API."""

    def test_selection_respects_masks_even_with_an_explicit_gpu(self) -> None:
        devices = [GPU("0", "GPU-a", "NVIDIA A100", "test"), GPU("1", "GPU-b", "NVIDIA A100", "test")]
        self.assertEqual(select_gpu(devices, visible="1").uuid, "GPU-b")
        self.assertEqual(select_gpu(devices, visible="GPU-b").uuid, "GPU-b")
        with self.assertRaises(RuntimeError):
            select_gpu(devices, selection="0", visible="GPU-b")
        for mask in ("", "-1", "GPU-unallocated"):
            with self.subTest(mask=mask), self.assertRaises(RuntimeError):
                select_gpu(devices, visible=mask)

    def test_conditional_graphs_follow_the_driver_api(self) -> None:
        self.assertFalse(conditional_graphs_enabled(12020))
        self.assertTrue(conditional_graphs_enabled(12040))
        self.assertFalse(conditional_graphs_enabled(13000, "off"))
        with self.assertRaises(RuntimeError):
            conditional_graphs_enabled(12020, "on")
        with self.assertRaises(ValueError):
            conditional_graphs_enabled(12040, "guess")


if __name__ == "__main__":
    unittest.main()
