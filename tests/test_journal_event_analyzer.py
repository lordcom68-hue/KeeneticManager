from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.services.journal_event_analyzer import JournalEventAnalyzer


class JournalEventAnalyzerTests(unittest.TestCase):
    NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)

    def test_temporary_cloud_failure_is_not_reported_as_router_error(self) -> None:
        entries = JournalEventAnalyzer.analyze(
            [
                {
                    "timestamp": "Aug  7 19:31:24",
                    "level": "error",
                    "level_label": "Ошибка",
                    "source": "coalagent",
                    "message": (
                        "[SecLayer_OnSend] Coala_Send: Network unreachable."
                    ),
                }
            ],
            now=self.NOW,
        )

        self.assertEqual(entries[0]["level"], "warning")
        self.assertEqual(entries[0]["original_level"], "error")
        self.assertEqual(entries[0]["category"], "connectivity")
        self.assertTrue(entries[0]["transient"])
        self.assertIn("не означает неисправность", entries[0]["explanation"])

    def test_router_repeat_marker_is_merged_into_previous_event(self) -> None:
        entries = JournalEventAnalyzer.analyze(
            [
                {
                    "timestamp": "Aug  7 19:31:24",
                    "level": "error",
                    "source": "coalagent",
                    "message": "Network unreachable.",
                },
                {
                    "timestamp": "Aug  7 19:31:34",
                    "level": "error",
                    "source": "coalagent",
                    "message": "Core::Syslog: last message repeated 2 times.",
                },
            ],
            now=self.NOW,
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["repeat_count"], 3)
        self.assertIn("Повторений: 3", entries[0]["display_message"])

    def test_router_error_is_linked_to_nearby_matching_application_action(self) -> None:
        entries = JournalEventAnalyzer.analyze(
            [
                {
                    "id": "router-1",
                    "timestamp": "Aug  7 19:59:30",
                    "level": "error",
                    "source": "ndm",
                    "message": (
                        'Network::Interface::EthernetIp: "Bridge0": '
                        "MAC address aa:bb:cc:dd:ee:ff is unknown."
                    ),
                },
                {
                    "id": "app-1",
                    "timestamp": "2026-08-07T19:59:00+00:00",
                    "level": "warning",
                    "source": "Keenetic Manager · Клиенты",
                    "message": "Отключение Wi-Fi клиента",
                    "operation_type": "wifi_client_access",
                    "operation_state": "rejected",
                    "application_event": True,
                },
            ],
            now=self.NOW,
        )

        router_entry = next(entry for entry in entries if entry["id"] == "router-1")
        self.assertEqual(router_entry["related_action_id"], "app-1")
        self.assertIn("После действия", router_entry["display_message"])

    def test_unsupported_command_gets_compatibility_explanation(self) -> None:
        entry = JournalEventAnalyzer.enrich(
            {
                "level": "error",
                "source": "ndm",
                "message": 'Core::Configurator: not found: "show/log".',
            },
            now=self.NOW,
        )

        self.assertEqual(entry["category"], "compatibility")
        self.assertEqual(entry["level"], "warning")
        self.assertIn("отсутствует", entry["explanation"])


if __name__ == "__main__":
    unittest.main()
