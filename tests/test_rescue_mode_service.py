from __future__ import annotations

import unittest

from core.connection_worker import RouterMonitor
from core.rci import DiagnosticCollector
from core.services.rescue_mode_service import (
    RESCUED_MODE,
    RESCUER_MODE,
    RescueModeService,
)


def wisp(enabled: bool = True) -> dict:
    return {
        "interface_id": "WifiMaster0/WifiStation0",
        "name": "Wireless ISP 2.4 ГГц",
        "wireless_isp_settings_supported": True,
        "enabled": enabled,
    }


def wifi(enabled: bool = True) -> dict:
    return {
        "network_type": "wifi",
        "name": "1234",
        "enabled": enabled,
        "bands": ["2.4 ГГц", "5 ГГц"],
        "interfaces": [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint1",
        ],
        "mws_profile_id": "wlan0",
    }


class FakeInterfaceService:
    def __init__(
        self,
        log: list[tuple],
        *,
        fail_when_enabled: bool | None = None,
        confirm_after_failure: bool = False,
    ) -> None:
        self.log = log
        self.fail_when_enabled = fail_when_enabled
        self.confirm_after_failure = confirm_after_failure

    def set_enabled(
        self,
        *,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        self.log.append(("wisp", interface_id, enabled))
        if self.fail_when_enabled is enabled:
            return False, "Ответ на команду WISP не получен."
        return True, "Интерфейс изменён."

    def verify_admin_state(
        self,
        *,
        interface_id: str,
        expected_enabled: bool,
    ) -> tuple[bool, str]:
        del interface_id, expected_enabled
        return self.confirm_after_failure, "проверено"


class FakeWifiService:
    def __init__(
        self,
        log: list[tuple],
        *,
        fail_when_enabled: bool | None = None,
        confirm_after_failure: bool = False,
    ) -> None:
        self.log = log
        self.fail_when_enabled = fail_when_enabled
        self.confirm_after_failure = confirm_after_failure

    def set_network_enabled(
        self,
        reference: object,
        enabled: bool,
    ) -> tuple[bool, str, dict]:
        name = (
            str(reference.get("name") or "")
            if isinstance(reference, dict)
            else str(reference)
        )
        self.log.append(("wifi", name, enabled))
        if self.fail_when_enabled is enabled:
            return False, "Состояние не подтверждено.", {}
        return True, "Wi-Fi изменён.", {}

    def verify_logical_enabled(
        self,
        profile_id: str,
        expected_enabled: bool,
        *,
        interfaces: list[str] | None = None,
    ) -> tuple[bool, str]:
        del profile_id, expected_enabled, interfaces
        return self.confirm_after_failure, "проверено"


class FakeClient:
    def __init__(
        self,
        *,
        fail_wifi_when: bool | None = None,
        fail_wisp_when: bool | None = None,
        confirm_wifi_after_failure: bool = False,
        confirm_wisp_after_failure: bool = False,
    ) -> None:
        self.log: list[tuple] = []
        self.diagnostics = DiagnosticCollector()
        self.interface_service = FakeInterfaceService(
            self.log,
            fail_when_enabled=fail_wisp_when,
            confirm_after_failure=confirm_wisp_after_failure,
        )
        self.wifi_network_service = FakeWifiService(
            self.log,
            fail_when_enabled=fail_wifi_when,
            confirm_after_failure=confirm_wifi_after_failure,
        )
        self.rescue_mode_service = RescueModeService(self)


class RescueModeInspectionTests(unittest.TestCase):
    def test_mixed_state_is_not_reported_as_active_mode(self) -> None:
        context = RescueModeService.inspect(
            [wisp(True)],
            [wifi(True)],
        )

        self.assertTrue(context.available)
        self.assertEqual(context.mode, "mixed")
        self.assertIn("одновременно", context.message)

    def test_detects_both_active_modes(self) -> None:
        rescuer = RescueModeService.inspect(
            [wisp(False)],
            [wifi(True)],
        )
        rescued = RescueModeService.inspect(
            [wisp(True)],
            [wifi(False)],
        )

        self.assertEqual(rescuer.mode, RESCUER_MODE)
        self.assertEqual(rescued.mode, RESCUED_MODE)

    def test_bandless_network_is_not_selected_for_distribution(self) -> None:
        bandless = wifi(False)
        bandless["bands"] = []
        bandless["interfaces"] = []

        context = RescueModeService.inspect(
            [wisp(True)],
            [bandless],
        )

        self.assertFalse(context.available)
        self.assertIn("назначенным диапазоном", context.message)

    def test_wired_pseudo_network_is_ignored(self) -> None:
        wired = {
            "network_type": "wired",
            "name": "Проводные подключения",
            "enabled": True,
        }
        context = RescueModeService.inspect(
            [wisp(True)],
            [wired, wifi(False)],
        )

        self.assertEqual(len(context.wifi_networks), 1)
        self.assertEqual(context.wifi_names, ("1234",))


class RescueModeOperationTests(unittest.TestCase):
    def test_rescuer_enables_wifi_before_disabling_wisp(self) -> None:
        client = FakeClient()
        context = RescueModeService.inspect(
            [wisp(True)],
            [wifi(False)],
        )

        success, message, details = (
            client.rescue_mode_service.apply(
                RESCUER_MODE,
                context.payload(),
            )
        )

        self.assertTrue(success, message)
        self.assertEqual(
            client.log,
            [
                ("wifi", "1234", True),
                (
                    "wisp",
                    "WifiMaster0/WifiStation0",
                    False,
                ),
            ],
        )
        self.assertEqual(details["mode"], RESCUER_MODE)
        event = client.diagnostics.events[-1]
        self.assertEqual(event["type"], "rescue_mode_switch")
        self.assertEqual(event["endpoint"], "rescue/mode")
        self.assertTrue(event["details"]["success"])
        self.assertEqual(
            event["details"]["initial_mode"],
            RESCUED_MODE,
        )
        self.assertEqual(
            [
                step["kind"]
                for step in event["details"]["completed_steps"]
            ],
            ["wifi", "wisp"],
        )

    def test_rescued_enables_wisp_before_disabling_wifi(self) -> None:
        client = FakeClient()
        context = RescueModeService.inspect(
            [wisp(False)],
            [wifi(True)],
        )

        success, message, _details = (
            client.rescue_mode_service.apply(
                RESCUED_MODE,
                context.payload(),
            )
        )

        self.assertTrue(success, message)
        self.assertEqual(
            client.log,
            [
                (
                    "wisp",
                    "WifiMaster0/WifiStation0",
                    True,
                ),
                ("wifi", "1234", False),
            ],
        )

    def test_second_step_failure_rolls_back_first_step(self) -> None:
        client = FakeClient(fail_wisp_when=False)
        context = RescueModeService.inspect(
            [wisp(True)],
            [wifi(False)],
        )

        success, message, details = (
            client.rescue_mode_service.apply(
                RESCUER_MODE,
                context.payload(),
            )
        )

        self.assertFalse(success)
        self.assertTrue(details["rolled_back"])
        self.assertIn("восстановлено", message)
        self.assertEqual(
            client.log,
            [
                ("wifi", "1234", True),
                (
                    "wisp",
                    "WifiMaster0/WifiStation0",
                    False,
                ),
                (
                    "wisp",
                    "WifiMaster0/WifiStation0",
                    True,
                ),
                ("wifi", "1234", False),
            ],
        )
        event = client.diagnostics.events[-1]
        self.assertEqual(event["type"], "rescue_mode_switch")
        self.assertFalse(event["details"]["success"])
        self.assertTrue(event["details"]["rollback_attempted"])
        self.assertTrue(event["details"]["rolled_back"])
        self.assertEqual(
            len(event["details"]["planned_steps"]),
            2,
        )
        self.assertEqual(
            len(event["details"]["completed_steps"]),
            1,
        )

    def test_lost_wifi_response_is_resolved_by_state_readback(self) -> None:
        client = FakeClient(
            fail_wifi_when=True,
            confirm_wifi_after_failure=True,
        )
        context = RescueModeService.inspect(
            [wisp(True)],
            [wifi(False)],
        )

        success, message, details = client.rescue_mode_service.apply(
            RESCUER_MODE,
            context.payload(),
        )

        self.assertTrue(success, message)
        self.assertEqual(
            client.log,
            [
                ("wifi", "1234", True),
                (
                    "wisp",
                    "WifiMaster0/WifiStation0",
                    False,
                ),
            ],
        )
        self.assertEqual(
            [
                item["kind"]
                for item in details["confirmed_after_error"]
            ],
            ["wifi"],
        )

    def test_preflight_rejection_is_recorded(self) -> None:
        client = FakeClient()
        bandless = wifi(False)
        bandless["bands"] = []
        bandless["interfaces"] = []

        success, message, details = (
            client.rescue_mode_service.apply(
                RESCUER_MODE,
                {
                    "wisp_connections": [wisp(True)],
                    "wifi_networks": [bandless],
                },
            )
        )

        self.assertFalse(success, message)
        self.assertTrue(details["preflight_rejected"])
        event = client.diagnostics.events[-1]
        self.assertEqual(event["type"], "rescue_mode_switch")
        self.assertFalse(event["details"]["success"])
        self.assertTrue(event["details"]["preflight_rejected"])
        self.assertEqual(
            event["details"]["initial_mode"],
            "unavailable",
        )

    def test_monitor_processes_mode_as_one_queued_action(self) -> None:
        client = FakeClient()
        monitor = RouterMonitor(
            {"id": 44, "status": True},
            paused=True,
        )
        events: list[tuple] = []
        monitor.rescue_mode_action_completed.connect(
            lambda *args: events.append(args)
        )
        context = RescueModeService.inspect(
            [wisp(True)],
            [wifi(False)],
        )

        refresh_required = monitor.process_action(
            client=client,
            action_type="rescue_mode",
            payload=(RESCUER_MODE, context.payload()),
        )

        self.assertTrue(refresh_required)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0:3], (44, RESCUER_MODE, True))


if __name__ == "__main__":
    unittest.main()
