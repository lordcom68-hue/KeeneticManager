from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.connection_worker import RouterMonitor
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

    def test_wifi_and_rescue_operations_have_specific_journal_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.jsonl"
            service = OperationHistoryService(1, path=path)
            service.record(
                "wifi_network",
                "Сеть включена.",
                details={
                    "state": "confirmed",
                    "operation": "state",
                    "display_name": "Home",
                },
            )
            service.record(
                "rescue_mode",
                "Режим подтверждён.",
                details={
                    "state": "confirmed",
                    "display_name": "Спасатель",
                },
            )

            entries = service.journal_entries()

            self.assertEqual(entries[0]["source"], "Keenetic Manager · Wi-Fi")
            self.assertIn("Home", entries[0]["message"])
            self.assertEqual(entries[1]["source"], "Keenetic Manager · Режим")

    def test_worker_persists_wifi_action_without_credentials(self) -> None:
        recorded: list[tuple[str, str, dict[str, object]]] = []
        client = SimpleNamespace(
            wifi_network_service=SimpleNamespace(
                create_network=lambda settings: (
                    True,
                    "Сеть создана.",
                    {
                        "ssid": settings["ssid"],
                        "mws_command_fields": [
                            "band",
                            "encryption",
                            "ssid",
                        ],
                    },
                )
            ),
            operation_history_service=SimpleNamespace(
                record=lambda event_type, message, details: recorded.append(
                    (event_type, message, details)
                )
            ),
        )
        monitor = RouterMonitor({"id": 7})

        refresh = monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="create_wifi_network",
            payload={"ssid": "Home", "password": "top-secret"},
        )

        self.assertTrue(refresh)
        self.assertEqual(recorded[0][0], "wifi_network")
        self.assertEqual(recorded[0][2]["display_name"], "Home")
        self.assertEqual(
            recorded[0][2]["mws_command_fields"],
            ["band", "encryption", "ssid"],
        )
        self.assertNotIn("password", recorded[0][2])
        self.assertNotIn("top-secret", str(recorded[0]))

    def test_worker_records_cable_and_dedicated_wan_diagnostics(self) -> None:
        history: list[tuple[str, str, dict[str, object]]] = []
        diagnostics: list[
            tuple[str, str, str, dict[str, object]]
        ] = []

        def record_event(
            event_type: str,
            message: str,
            *,
            endpoint: str,
            details: dict[str, object],
        ) -> None:
            diagnostics.append((event_type, message, endpoint, details))

        wired = SimpleNamespace(
            create_cable_internet=lambda _settings: (
                True,
                "Dedicated WAN создан.",
                {
                    "dedicated_wan": True,
                    "interface_id": "PPTP0",
                    "transport_interface_id": "GigabitEthernet1",
                },
            ),
            change_cable_internet_protocol=lambda **_kwargs: (
                True,
                "Протокол изменён.",
                {
                    "interface_id": "PPTP0",
                    "old_protocol": "none",
                    "transient_recovered": False,
                },
            ),
            delete_dedicated_wan=lambda **_kwargs: (
                True,
                "Dedicated WAN удалён.",
                {
                    "transport_interface_id": "GigabitEthernet1",
                    "bridge_id": "Bridge0",
                },
            ),
        )
        client = SimpleNamespace(
            wired_internet_service=wired,
            operation_history_service=SimpleNamespace(
                record=lambda event_type, message, details: history.append(
                    (event_type, message, details)
                )
            ),
            diagnostics=SimpleNamespace(record_event=record_event),
        )
        monitor = RouterMonitor({"id": 7})

        monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="create_cable_internet",
            payload={
                "mode": "dedicated_wan",
                "description": "Provider",
                "protocol": {
                    "type": "pptp",
                    "username": "user",
                    "password": "top-secret",
                },
            },
        )
        monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="change_cable_protocol",
            payload=(
                "GigabitEthernet1",
                {
                    "target_protocol": "pptp",
                    "password": "top-secret",
                },
            ),
        )
        monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="delete_dedicated_wan",
            payload="GigabitEthernet1",
        )

        self.assertEqual(
            [item[0] for item in history],
            ["dedicated_wan", "cable_protocol_change", "dedicated_wan"],
        )
        self.assertEqual(
            [item[0] for item in diagnostics],
            ["dedicated_wan", "cable_protocol_change", "dedicated_wan"],
        )
        self.assertEqual(
            [item[2] for item in diagnostics],
            [
                "internet/cable/create",
                "internet/cable/protocol",
                "internet/dedicated-wan/delete",
            ],
        )
        self.assertNotIn("top-secret", str(history))
        self.assertNotIn("top-secret", str(diagnostics))


if __name__ == "__main__":
    unittest.main()
