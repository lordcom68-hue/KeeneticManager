from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from core.connection_worker import RouterMonitor
from core.services.system_log_service import SystemLogService


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class FakeTransport:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.request_kwargs: dict[str, Any] = {}

    def request(self, **kwargs: Any) -> FakeResponse:
        self.request_kwargs = dict(kwargs)
        return FakeResponse(self.payload)


class FakeClient:
    def __init__(self, payload: Any) -> None:
        self.timeout = 3
        self.transport = FakeTransport(payload)

    @staticmethod
    def extract_batch_rci_errors(_payload: Any) -> list[str]:
        return []


class SystemLogServiceTests(unittest.TestCase):
    @staticmethod
    def payload() -> list[dict[str, Any]]:
        return [
            {
                "parse": {
                    "log": {
                        "41": {
                            "id": 41,
                            "timestamp": "Aug  6 14:10:01",
                            "ident": "ndm",
                            "message": {
                                "level": "Info",
                                "label": "I",
                                "message": "Interface is up.",
                            },
                        },
                        "42": {
                            "id": 42,
                            "timestamp": "Aug  6 14:10:02",
                            "ident": "kernel",
                            "message": {
                                "level": "Warning",
                                "label": "W",
                                "message": (
                                    "Login https://admin:secret@example.test "
                                    "failed."
                                ),
                            },
                        },
                    },
                    "prompt": "(config)",
                }
            }
        ]

    def test_normalizes_keeneticos_4_and_5_log_shape(self) -> None:
        entries = SystemLogService.normalize_response(self.payload())

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["level"], "info")
        self.assertEqual(entries[1]["level"], "warning")
        self.assertEqual(entries[1]["source"], "kernel")
        self.assertNotIn("admin:secret", entries[1]["message"])
        self.assertIn("[REDACTED]", entries[1]["message"])

    def test_read_uses_finite_read_only_log_command(self) -> None:
        client = FakeClient(self.payload())
        service = SystemLogService(client)

        success, message, entries = service.read(99999)

        self.assertTrue(success, message)
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            client.transport.request_kwargs["json_payload"],
            [{"parse": "show log 2000 once"}],
        )
        self.assertEqual(
            client.transport.request_kwargs["endpoint"],
            "show/log",
        )
        self.assertEqual(
            client.transport.request_kwargs["path"],
            "/rci/",
        )

    def test_worker_routes_system_log_without_full_refresh(self) -> None:
        fake_client = SimpleNamespace(
            system_log_service=SimpleNamespace(
                read=lambda limit: (
                    True,
                    f"limit={limit}",
                    [{"id": "1"}],
                )
            )
        )
        monitor = RouterMonitor({"id": 19})
        emitted: list[tuple[Any, ...]] = []
        monitor.system_log_completed.connect(
            lambda *args: emitted.append(args)
        )

        refresh_required = monitor.process_action(
            client=fake_client,  # type: ignore[arg-type]
            action_type="system_log",
            payload=250,
        )

        self.assertFalse(refresh_required)
        self.assertEqual(emitted, [(19, True, "limit=250", [{"id": "1"}])])


if __name__ == "__main__":
    unittest.main()
