from __future__ import annotations

import unittest
from collections import Counter
from unittest.mock import patch

from core.keenetic_client import ConnectionResult, KeeneticClient


class RefreshCadenceTests(unittest.TestCase):
    def test_slow_snapshots_are_cached_and_manual_refresh_forces_them(self) -> None:
        client = KeeneticClient(username="admin", password="secret")
        client.connected = True
        client.base_url = "http://192.168.1.1"
        client.session = object()  # type: ignore[assignment]
        calls: Counter[str] = Counter()
        captured: list[dict[str, object]] = []

        def get_rci_data(endpoint: str, required: bool):
            del required
            calls[endpoint] += 1
            return {}

        client.get_rci_data = get_rci_data  # type: ignore[method-assign]
        client.interface_service.read_runtime = lambda: calls.update(["runtime"]) or {}
        client.interface_service.read_config = lambda: calls.update(["config"]) or {}
        client.ping_check_service.read_config = lambda: calls.update(["ping_config"]) or {}
        client.ping_check_service.read_status = lambda: calls.update(["ping_status"]) or {}
        client.get_usb_data = lambda **_kwargs: calls.update(["usb"]) or {}

        def build_result(**kwargs):
            captured.append(kwargs)
            return ConnectionResult(success=True, message="ok")

        client.build_result = build_result  # type: ignore[method-assign]
        client._cache_successful_snapshot = lambda: None  # type: ignore[method-assign]

        with patch(
            "core.keenetic_client.time.monotonic",
            side_effect=(100.0, 102.0, 106.0, 107.0),
        ):
            self.assertTrue(client.refresh().success)
            self.assertTrue(client.refresh().success)
            self.assertTrue(client.refresh().success)
            self.assertTrue(client.refresh(force_full=True).success)

        self.assertEqual(calls["runtime"], 4)
        self.assertEqual(calls["config"], 2)
        self.assertEqual(calls["ping_config"], 2)
        self.assertEqual(calls["ping_status"], 3)
        self.assertEqual(calls["show/associations"], 3)
        self.assertEqual(calls["show/ip/hotspot"], 3)
        self.assertEqual(calls["usb"], 2)
        self.assertTrue(captured[0]["force_wifi_logical_refresh"])
        self.assertFalse(captured[1]["force_wifi_logical_refresh"])
        self.assertTrue(captured[-1]["force_wifi_logical_refresh"])
        self.assertTrue(captured[0]["client_data_refreshed"])
        self.assertFalse(captured[1]["client_data_refreshed"])
        self.assertTrue(captured[2]["client_data_refreshed"])
        self.assertTrue(captured[-1]["client_data_refreshed"])


if __name__ == "__main__":
    unittest.main()
