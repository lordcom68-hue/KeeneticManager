from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from ui.controllers.router_connection_controller import (  # noqa: E402
    RouterConnectionController,
)


class FakeSignal:
    def __init__(self) -> None:
        self.slots: list[object] = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def disconnect(self, slot) -> None:
        self.slots = [item for item in self.slots if item != slot]


class FakeMonitor:
    SIGNALS = (
        "updated",
        "interface_action_completed",
        "interface_reconnect_completed",
        "interface_settings_completed",
        "connection_creation_completed",
        "wireless_scan_completed",
        "connection_delete_completed",
        "connection_protocol_change_completed",
        "wifi_network_action_completed",
        "wifi_client_access_completed",
        "rescue_mode_action_completed",
        "priorities_action_completed",
        "diagnostic_action_completed",
        "system_log_completed",
        "router_self_check_progress",
        "router_self_check_completed",
        "monitor_stopped",
        "finished",
    )

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.refresh_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.delete_calls = 0
        self.paused: list[bool] = []
        self.intervals: list[int] = []
        for name in self.SIGNALS:
            setattr(self, name, FakeSignal())

    def request_refresh(self) -> None:
        self.refresh_calls += 1

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def deleteLater(self) -> None:
        self.delete_calls += 1

    def set_paused(self, paused: bool) -> None:
        self.paused.append(bool(paused))

    def set_interval(self, interval: int) -> None:
        self.intervals.append(int(interval))


class FakeStatusWorker:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.status_changed = FakeSignal()
        self.routers: list[dict] = []
        self.start_calls = 0
        self.refresh_calls = 0
        self.paused: list[bool] = []

    def set_routers(self, routers: list[dict]) -> None:
        self.routers = list(routers)

    def start(self) -> None:
        self.start_calls += 1

    def request_refresh(self) -> None:
        self.refresh_calls += 1

    def set_paused(self, paused: bool) -> None:
        self.paused.append(bool(paused))


class Recorder:
    def __init__(self) -> None:
        self.values: list[object] = []

    def setText(self, value: object) -> None:
        self.values.append(value)

    def showMessage(self, *values: object) -> None:
        self.values.append(values)


class FakeDatabase:
    def __init__(self) -> None:
        self.password_reads = 0
        self.status_updates: list[dict] = []

    def get_router(
        self,
        router_id: int,
        *,
        include_password: bool = False,
    ) -> dict:
        if include_password:
            self.password_reads += 1
        return {
            "id": router_id,
            "name": "Synthetic router",
            "username": "admin",
            "password": "fixture-only",
        }

    def update_router_status(self, **kwargs) -> None:
        self.status_updates.append(dict(kwargs))


class RouterConnectionControllerTests(unittest.TestCase):
    def make_window(self) -> tuple[SimpleNamespace, dict[str, list]]:
        calls: dict[str, list] = {
            "ui_state": [],
            "shown": [],
            "persisted": [],
            "item_status": [],
            "clear": [],
        }
        status = Recorder()
        window = SimpleNamespace(
            reachability_checks_enabled=True,
            auto_refresh_enabled=True,
            monitor_interval_ms=2_000,
            monitor_intervals={"2 секунды": 2_000, "5 секунд": 5_000},
            settings=SimpleNamespace(
                setValue=lambda *_args: None,
                sync=lambda: None,
            ),
            database=FakeDatabase(),
            routers=[{"id": 1, "name": "Synthetic router", "status": 0}],
            router_reachability={},
            router_reachable_address={},
            connection_results={},
            last_monitor_results={},
            persisted_router_status={},
            pending_rescue_modes={},
            developer_mode_by_router={},
            auto_refresh_checkbox=SimpleNamespace(isChecked=lambda: True),
            reachability_checkbox=SimpleNamespace(isChecked=lambda: True),
            search_input=SimpleNamespace(text=lambda: ""),
            status_label=Recorder(),
            connected_address_label=Recorder(),
            statusBar=lambda: status,
            get_selected_router_id=lambda: 1,
            router_reachability_changed=lambda *_args: None,
            clear_router_state=lambda: calls["clear"].append(True),
            set_router_monitor_ui_state=(
                lambda state: calls["ui_state"].append(state)
            ),
            sync_developer_mode_for_selected_router=lambda: None,
            set_router_content_connected=lambda _connected: None,
            persist_router_monitor_status=(
                lambda **values: calls["persisted"].append(values)
            ),
            update_router_item_status=(
                lambda *values: calls["item_status"].append(values)
            ),
            set_router_item_disconnected=lambda _router_id: None,
            clear_router_traffic_history=lambda _router_id: None,
            update_active_cable_dialog=lambda *_args: None,
            update_active_wireless_isp_dialog=lambda *_args: None,
            show_connection_result=lambda result: calls["shown"].append(result),
            disable_developer_mode_for_router=lambda _router_id: None,
            _update_router_management_actions=lambda: None,
            _update_global_router_actions=lambda: None,
            filter_routers=lambda _text: None,
            router_self_check_controller=SimpleNamespace(
                progress=lambda *_args: None,
                completed=lambda *_args: None,
                forget_router=lambda _router_id: None,
                monitor_stopped=lambda _router_id: None,
            ),
        )
        for name in (
            "interface_action_completed",
            "interface_reconnect_completed",
            "interface_settings_completed",
            "connection_creation_completed",
            "wireless_scan_completed",
            "connection_delete_completed",
            "connection_protocol_change_completed",
            "wifi_network_action_completed",
            "wifi_client_access_completed",
            "rescue_mode_action_completed",
            "internet_priorities_action_completed",
            "diagnostic_action_completed",
            "system_log_completed",
        ):
            setattr(window, name, lambda *_args: None)
        return window, calls

    def make_controller(
        self,
    ) -> tuple[RouterConnectionController, SimpleNamespace, dict[str, list]]:
        window, calls = self.make_window()
        controller = RouterConnectionController(
            window,
            monitor_factory=FakeMonitor,
            status_worker_factory=FakeStatusWorker,
        )
        window.connection_completed = controller.connection_completed
        window.monitor_stopped = controller.monitor_stopped
        window.router_monitor_finished = controller.monitor_finished
        return controller, window, calls

    def test_connect_requests_one_initial_snapshot(self) -> None:
        controller, window, calls = self.make_controller()

        controller.connect_selected()

        monitor = controller.monitors[1]
        self.assertEqual(window.database.password_reads, 1)
        self.assertEqual(monitor.refresh_calls, 1)
        self.assertEqual(monitor.start_calls, 1)
        self.assertEqual(calls["ui_state"], ["connecting"])
        self.assertTrue(monitor.kwargs["paused"] is False)

    def test_reconnect_is_blocked_until_old_thread_finishes(self) -> None:
        controller, _window, _calls = self.make_controller()
        controller.connect_selected()
        old_monitor = controller.monitors[1]
        controller.disconnect_selected()

        with patch.object(QMessageBox, "information") as information:
            controller.connect_selected()

        self.assertEqual(old_monitor.stop_calls, 1)
        self.assertIs(controller.stopping_monitors[1], old_monitor)
        self.assertNotIn(1, controller.monitors)
        information.assert_called_once()

        controller.monitor_finished(1, old_monitor)
        self.assertNotIn(1, controller.stopping_monitors)
        self.assertEqual(old_monitor.delete_calls, 1)

    def test_late_result_from_disconnected_monitor_is_ignored(self) -> None:
        controller, window, calls = self.make_controller()
        controller.connect_selected()
        controller.disconnect_selected()
        calls["shown"].clear()

        controller.connection_completed(
            1,
            SimpleNamespace(
                success=True,
                message="late",
                connection_state="connected",
            ),
        )

        self.assertEqual(window.connection_results, {})
        self.assertEqual(calls["shown"], [])

    def test_transient_failure_preserves_snapshot_then_recovers(self) -> None:
        controller, window, calls = self.make_controller()
        controller.monitors[1] = FakeMonitor()
        connected = SimpleNamespace(
            success=True,
            message="ok",
            connection_state="connected",
            confirmed_offline=False,
            transient_failure_suppressed=False,
        )
        transient = SimpleNamespace(
            success=False,
            message="retry",
            connection_state="retrying",
            confirmed_offline=False,
            transient_failure_suppressed=True,
        )

        controller.connection_completed(1, connected)
        controller.connection_completed(1, transient)
        controller.connection_completed(1, connected)

        self.assertIs(window.connection_results[1], connected)
        self.assertIn("retrying", calls["ui_state"])
        self.assertEqual(calls["ui_state"][-1], "connected")
        self.assertIs(calls["shown"][-1], connected)

    def test_status_worker_starts_with_current_inventory(self) -> None:
        controller, window, _calls = self.make_controller()

        controller.start_status_worker(window.routers)
        controller.refresh_statuses()

        self.assertEqual(controller.status_worker.routers, window.routers)
        self.assertEqual(controller.status_worker.start_calls, 1)
        self.assertEqual(controller.status_worker.refresh_calls, 1)

    def test_two_hundred_reconnect_cycles_leave_no_stale_monitor(self) -> None:
        controller, window, calls = self.make_controller()

        for cycle in range(200):
            controller.connect_selected()
            monitor = controller.monitors[1]
            controller.connection_completed(
                1,
                SimpleNamespace(
                    success=True,
                    message=f"cycle {cycle}",
                    connection_state="connected",
                    confirmed_offline=False,
                    transient_failure_suppressed=False,
                ),
            )
            controller.disconnect_selected()
            controller.connection_completed(
                1,
                SimpleNamespace(
                    success=True,
                    message="late result",
                    connection_state="connected",
                ),
            )
            controller.monitor_finished(1, monitor)

        self.assertEqual(controller.monitors, {})
        self.assertEqual(controller.stopping_monitors, {})
        self.assertEqual(window.connection_results, {})
        self.assertEqual(window.database.password_reads, 200)
        self.assertEqual(calls["shown"][-1].message, "cycle 199")

    def test_transient_failure_storm_keeps_last_good_snapshot(self) -> None:
        controller, window, _calls = self.make_controller()
        controller.monitors[1] = FakeMonitor()
        connected = SimpleNamespace(
            success=True,
            message="stable snapshot",
            connection_state="connected",
            confirmed_offline=False,
            transient_failure_suppressed=False,
        )
        transient = SimpleNamespace(
            success=False,
            message="temporary failure",
            connection_state="retrying",
            confirmed_offline=False,
            transient_failure_suppressed=True,
        )
        controller.connection_completed(1, connected)

        for _index in range(1_000):
            controller.connection_completed(1, transient)

        self.assertIs(window.connection_results[1], connected)
        self.assertEqual(window.database.status_updates, [])


if __name__ == "__main__":
    unittest.main()
