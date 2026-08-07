from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.services.operation_history_service import OperationHistoryService


class OperationHistoryServiceTests(unittest.TestCase):
    @staticmethod
    def details(state: str = "confirmed") -> dict[str, object]:
        return {
            "state": state,
            "interface_id": "GigabitEthernet0/2",
            "display_name": "LAN 3",
            "requested_enabled": False,
        }

    def test_history_survives_new_service_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.jsonl"
            def now() -> datetime:
                return datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)

            writer = OperationHistoryService(7, path=path, now=now)

            saved = writer.record(
                "ethernet_port_state",
                "Ethernet-порт выключен.",
                details=self.details(),
            )
            reader = OperationHistoryService(7, path=path)
            entries = reader.journal_entries()

            self.assertTrue(saved)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["level"], "info")
            self.assertEqual(entries[0]["source"], "Keenetic Manager · Ethernet")
            self.assertIn("LAN 3", entries[0]["message"])
            self.assertIn("GigabitEthernet0/2", entries[0]["message"])

    def test_rollback_is_a_warning_and_partial_state_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.jsonl"
            service = OperationHistoryService(2, path=path)
            service.record(
                "ethernet_port_state",
                "Исходное состояние восстановлено.",
                details=self.details("rolled_back"),
            )
            service.record(
                "ethernet_port_state",
                "Откат не подтверждён.",
                details=self.details("partial"),
            )

            entries = service.journal_entries()

            self.assertEqual(
                [entry["level"] for entry in entries],
                ["warning", "error"],
            )

    def test_malformed_lines_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.jsonl"
            path.write_text(
                "not-json\n"
                + json.dumps(
                    {
                        "timestamp": "2026-08-07T21:00:00+03:00",
                        "type": "ethernet_port_state",
                        "message": "ok",
                        "details": self.details(),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            entries = OperationHistoryService(1, path=path).read()

            self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
