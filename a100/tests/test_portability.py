"""Protect device allocation boundaries and capability-based Newton configuration."""

from __future__ import annotations

import unittest

from a100sim.host import GPU, select_gpu
from a100sim.network import require_loopback, require_worker_url
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

    def test_all_http_workers_reject_remote_bindings_before_opening_a_socket(self) -> None:
        from a100sim.render_service import serve
        from a100sim.resident_service import start_resident_http
        from a100sim.service_queue import start_http

        for host in ("0.0.0.0", "::", "192.0.2.1", "example.invalid"):
            for function, arguments in (
                (start_http, (None, host, 0, "/simulate")),
                (serve, (None, host, 0)),
                (start_resident_http, (None, host, 0)),
            ):
                with self.subTest(host=host, function=function.__name__), self.assertRaisesRegex(
                    ValueError, "loopback"
                ):
                    function(*arguments)
        self.assertEqual(require_loopback("localhost"), "127.0.0.1")
        self.assertEqual(require_loopback("::1"), "::1")

    def test_internal_worker_origins_reject_external_or_ambiguous_routes(self) -> None:
        for value in (
            "http://192.0.2.1:8321",
            "http://example.invalid:8321",
            "https://127.0.0.1:8321",
            "http://user:pass@127.0.0.1:8321",
            "http://127.0.0.1:8321/path",
            "http://127.0.0.1:8321?q=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_worker_url(value)
        self.assertEqual(require_worker_url("http://localhost:8321/"), "http://127.0.0.1:8321")


if __name__ == "__main__":
    unittest.main()
