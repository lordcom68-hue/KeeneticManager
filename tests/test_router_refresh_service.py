from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from core.rci import RCI_ENDPOINTS
from core.services.router_refresh_service import RouterRefreshService


class StepClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class RecordedRefreshClient:
    def __init__(self, responses: dict[str, object], clock: StepClock) -> None:
        self.responses = responses
        self.calls: Counter[str] = Counter()
        self.captured: list[dict[str, object]] = []
        self.version_data = {"release": "5.1-fixture"}
        self.interface_service = SimpleNamespace(
            read_runtime=lambda: self._load("interface_runtime"),
            read_config=lambda: self._load("interface_config"),
        )
        self.ping_check_service = SimpleNamespace(
            read_config=lambda: self._load("ping_config"),
            read_status=lambda: self._load("ping_status"),
        )
        self.snapshot_writes = 0
        self.refresh_service = RouterRefreshService(self, monotonic=clock)

    def _load(self, key: str) -> object:
        self.calls[key] += 1
        return self.responses[key]

    def get_rci_data(self, *, endpoint: str, required: bool) -> object:
        del required
        keys = {
            RCI_ENDPOINTS["system"].path: "system",
            RCI_ENDPOINTS["internet_status"].path: "internet_status",
            RCI_ENDPOINTS["associations"].path: "associations",
            RCI_ENDPOINTS["hotspot"].path: "hotspot",
        }
        return self._load(keys[endpoint])

    def get_usb_data(self, **_kwargs: object) -> object:
        return self._load("usb")

    def build_result(self, **kwargs: object) -> SimpleNamespace:
        self.captured.append(kwargs)
        return SimpleNamespace(success=True, message="Готово")

    def _cache_successful_snapshot(self) -> None:
        self.snapshot_writes += 1


class RouterRefreshServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "keeneticos_refresh_snapshot.json"
        )
        cls.fixture_text = fixture_path.read_text(encoding="utf-8")
        cls.fixture = json.loads(cls.fixture_text)

    def test_recorded_snapshot_obeys_cache_cadence_and_force_refresh(
        self,
    ) -> None:
        client = RecordedRefreshClient(
            self.fixture["responses"],
            StepClock(100.0, 102.0, 106.0, 107.0),
        )
        service = client.refresh_service

        results = [
            service.refresh(),
            service.refresh(),
            service.refresh(),
            service.refresh(force_full=True),
        ]

        self.assertTrue(all(result.success for result in results))
        self.assertEqual(client.calls["system"], 4)
        self.assertEqual(client.calls["interface_runtime"], 4)
        self.assertEqual(client.calls["interface_config"], 2)
        self.assertEqual(client.calls["ping_config"], 2)
        self.assertEqual(client.calls["ping_status"], 3)
        self.assertEqual(client.calls["associations"], 3)
        self.assertEqual(client.calls["hotspot"], 3)
        self.assertEqual(client.calls["usb"], 2)
        self.assertEqual(client.snapshot_writes, 4)
        self.assertTrue(client.captured[0]["client_data_refreshed"])
        self.assertFalse(client.captured[1]["client_data_refreshed"])
        self.assertTrue(client.captured[-1]["force_wifi_logical_refresh"])

    def test_recorded_fixture_is_explicitly_synthetic_and_secret_free(
        self,
    ) -> None:
        privacy = self.fixture["privacy"]

        self.assertTrue(privacy["synthetic"])
        self.assertFalse(privacy["contains_credentials"])
        self.assertFalse(privacy["contains_real_addresses"])
        self.assertNotIn("password", self.fixture_text.casefold())
        self.assertNotIn("192.168.", self.fixture_text)

    def test_keeneticos_4_skips_unsupported_monitoring_trees(self) -> None:
        client = RecordedRefreshClient(
            self.fixture["responses"],
            StepClock(100.0),
        )
        client.capabilities = SimpleNamespace(keeneticos_major=4)

        result = client.refresh_service.refresh(force_full=True)

        self.assertTrue(result.success)
        self.assertEqual(client.calls["ping_config"], 0)
        self.assertEqual(client.calls["ping_status"], 0)
        self.assertEqual(client.calls["hotspot"], 0)
        self.assertEqual(client.calls["associations"], 1)
        self.assertEqual(
            client.captured[-1]["ping_check_config_data"],
            {},
        )
        self.assertEqual(
            client.captured[-1]["ping_check_status_data"],
            {},
        )
        self.assertEqual(client.captured[-1]["hotspot_data"], {})


if __name__ == "__main__":
    unittest.main()
