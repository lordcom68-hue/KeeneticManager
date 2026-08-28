from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.router_readonly_soak_test import _write_summary, run_readonly_soak


class FakeReadOnlyClient:
    instances: list["FakeReadOnlyClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)
        self.connected = False
        self.connect_calls = 0
        self.refresh_calls = 0
        self.close_calls = 0
        type(self).instances.append(self)

    def connect_to_router(self, **kwargs):
        self.connect_calls += 1
        self.connected = True
        return SimpleNamespace(success=True, failure_kind="")

    def refresh(self, *, force_full: bool = False):
        self.refresh_calls += 1
        self.connected = True
        return SimpleNamespace(success=True, failure_kind="")

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False


class RouterReadOnlySoakTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeReadOnlyClient.instances.clear()

    def test_stress_cycles_reconnect_without_configuration_operations(
        self,
    ) -> None:
        router = {
            "id": 1,
            "username": "admin",
            "password": "fixture-secret",
            "local_ip": "192.0.2.1",
            "keendns": "fixture.example",
            "connection_mode": "auto",
        }

        summary = run_readonly_soak(
            router,
            duration_seconds=0,
            interval_seconds=0,
            reconnect_every=10,
            maximum_cycles=100,
            client_factory=FakeReadOnlyClient,
        )

        client = FakeReadOnlyClient.instances[0]
        self.assertEqual(summary["cycles"], 100)
        self.assertEqual(summary["successful_cycles"], 100)
        self.assertEqual(summary["local_session_reconnects"], 10)
        self.assertEqual(client.connect_calls, 10)
        self.assertEqual(client.refresh_calls, 90)
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["configuration_changed"])
        self.assertNotIn(
            "fixture-secret",
            json.dumps(summary, ensure_ascii=False),
        )

    def test_written_summary_contains_no_router_identity(self) -> None:
        summary = {
            "read_only": True,
            "configuration_changed": False,
            "cycles": 3,
            "contains_credentials": False,
            "contains_router_address": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            _write_summary(path, summary)
            restored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(restored, summary)


if __name__ == "__main__":
    unittest.main()
