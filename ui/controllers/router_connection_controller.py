from __future__ import annotations

import sqlite3
from typing import Any, Callable

from PySide6.QtWidgets import QMessageBox

from core.connection_worker import RouterMonitor
from core.credential_store import CredentialStoreError
from core.router_status_worker import RouterStatusWorker


class RouterConnectionController:
    """Own router monitor threads and their UI lifecycle."""

    def __init__(
        self,
        window: Any,
        *,
        monitor_factory: Callable[..., RouterMonitor] = RouterMonitor,
        status_worker_factory: Callable[..., RouterStatusWorker] = (
            RouterStatusWorker
        ),
    ) -> None:
        self.window = window
        self.monitor_factory = monitor_factory
        self.monitors: dict[int, RouterMonitor] = {}
        self.stopping_monitors: dict[int, RouterMonitor] = {}
        self.status_worker = status_worker_factory(
            interval_seconds=3.0,
            timeout_seconds=1.2,
            paused=(not window.reachability_checks_enabled),
            parent=window,
        )
        self.status_worker.status_changed.connect(
            window.router_reachability_changed
        )

    def start_status_worker(self, routers: list[dict]) -> None:
        self.status_worker.set_routers(routers)
        self.status_worker.start()

    def router_selected(self, current: Any, previous: Any) -> None:
        del previous
        window = self.window

        if current is None:
            window.clear_router_information()
            window.sync_developer_mode_for_selected_router()
            return

        router_id = current.data(window.ROUTER_ID_ROLE)
        router = window.database.get_router(router_id)

        if router is None:
            window.clear_router_information()
            return

        result = window.connection_results.get(router_id)
        has_connected_snapshot = bool(
            result is not None and getattr(result, "success", False)
        )
        window.show_router_information(
            router,
            preserve_live_content=has_connected_snapshot,
        )

        if hasattr(window, "internet_tab"):
            window.internet_tab.set_router(router_id)
        if hasattr(window, "wifi_tab"):
            window.wifi_tab.set_router(router_id)
        if hasattr(window, "journal_tab"):
            window.journal_tab.set_router(router_id)

        window.sync_developer_mode_for_selected_router()
        if has_connected_snapshot:
            window.show_connection_result(result)
        window.set_router_monitor_ui_state(
            window.router_monitor_ui_state(router_id)
        )

    def toggle_auto_refresh(self, enabled: bool) -> None:
        window = self.window
        enabled = bool(enabled)
        window.auto_refresh_enabled = enabled
        window.settings.setValue("monitor/auto_refresh_enabled", enabled)
        window.settings.sync()

        for monitor in self.monitors.values():
            monitor.set_paused(not enabled)

        window.statusBar().showMessage(
            (
                "Автообновление данных включено"
                if enabled
                else "Автообновление данных приостановлено"
            ),
            3000,
        )

    def toggle_reachability_checks(self, enabled: bool) -> None:
        window = self.window
        enabled = bool(enabled)
        window.reachability_checks_enabled = enabled
        window.settings.setValue("monitor/reachability_enabled", enabled)
        window.settings.sync()
        self.status_worker.set_paused(not enabled)

        window.router_reachability = {
            int(router["id"]): None for router in window.routers
        }
        window.router_reachable_address.clear()
        window.filter_routers(window.search_input.text())
        window.statusBar().showMessage(
            (
                "Проверка доступности включена"
                if enabled
                else "Проверка доступности приостановлена"
            ),
            3000,
        )

    def change_refresh_interval(self, interval_name: str) -> None:
        window = self.window
        interval_ms = window.monitor_intervals.get(interval_name)
        if interval_ms is None:
            return

        window.monitor_interval_ms = interval_ms
        window.settings.setValue("monitor/interval_ms", interval_ms)
        window.settings.sync()
        for monitor in self.monitors.values():
            monitor.set_interval(interval_ms)
        window.statusBar().showMessage(
            f"Интервал обновления: {interval_name}",
            3000,
        )

    def refresh_all(
        self,
        checked: bool = False,
        *,
        announce: bool = True,
    ) -> None:
        del checked
        window = self.window
        if not self.monitors:
            if announce:
                window.statusBar().showMessage(
                    "Нет подключённых роутеров", 3000
                )
            return

        for monitor in self.monitors.values():
            monitor.request_refresh()
        if announce:
            window.statusBar().showMessage(
                "Запрошено обновление подключённых роутеров",
                3000,
            )

    def refresh_selected(self) -> None:
        window = self.window
        router_id = window.get_selected_router_id()
        monitor = (
            self.monitors.get(router_id) if router_id is not None else None
        )
        if monitor is None:
            window.statusBar().showMessage(
                "Выбранный роутер не подключён", 3000
            )
            return

        monitor.request_refresh()
        window.statusBar().showMessage(
            "Запрошено обновление клиентов", 3000
        )

    def _bind_monitor(self, router_id: int, monitor: RouterMonitor) -> None:
        window = self.window
        monitor.updated.connect(window.connection_completed)
        monitor.interface_action_completed.connect(
            window.interface_action_completed
        )
        monitor.interface_reconnect_completed.connect(
            window.interface_reconnect_completed
        )
        monitor.interface_settings_completed.connect(
            window.interface_settings_completed
        )
        monitor.connection_creation_completed.connect(
            window.connection_creation_completed
        )
        monitor.wireless_scan_completed.connect(window.wireless_scan_completed)
        monitor.connection_delete_completed.connect(
            window.connection_delete_completed
        )
        monitor.connection_protocol_change_completed.connect(
            window.connection_protocol_change_completed
        )
        monitor.wifi_network_action_completed.connect(
            window.wifi_network_action_completed
        )
        monitor.wifi_client_access_completed.connect(
            window.wifi_client_access_completed
        )
        monitor.rescue_mode_action_completed.connect(
            window.rescue_mode_action_completed
        )
        monitor.priorities_action_completed.connect(
            window.internet_priorities_action_completed
        )
        monitor.diagnostic_action_completed.connect(
            window.diagnostic_action_completed
        )
        monitor.system_log_completed.connect(window.system_log_completed)
        monitor.router_self_check_progress.connect(
            window.router_self_check_controller.progress
        )
        monitor.router_self_check_completed.connect(
            window.router_self_check_controller.completed
        )
        monitor.monitor_stopped.connect(window.monitor_stopped)
        monitor.finished.connect(
            lambda rid=router_id, thread=monitor: (
                window.router_monitor_finished(rid, thread)
            )
        )

    def connect_selected(self) -> None:
        window = self.window
        router_id = window.get_selected_router_id()
        if router_id is None:
            return
        if router_id in self.monitors:
            QMessageBox.information(
                window,
                "Подключение",
                "Этот роутер уже подключён.",
            )
            return
        if router_id in self.stopping_monitors:
            QMessageBox.information(
                window,
                "Отключение завершается",
                (
                    "Дождитесь полной остановки мониторинга, "
                    "затем подключите роутер снова."
                ),
            )
            return

        try:
            router = window.database.get_router(
                router_id,
                include_password=True,
            )
        except CredentialStoreError as error:
            QMessageBox.critical(
                window,
                "Пароль недоступен",
                str(error),
            )
            return

        if router is None:
            QMessageBox.warning(
                window,
                "Ошибка",
                "Роутер не найден в базе данных.",
            )
            return

        window.pending_rescue_modes.pop(router_id, None)
        window.clear_router_state()
        window.status_label.setText("● Подключение...")
        window.connected_address_label.setText(
            "Фактический адрес: —\nПодключение: —"
        )

        monitor = self.monitor_factory(
            router=router,
            interval_ms=window.monitor_interval_ms,
            paused=(not window.auto_refresh_checkbox.isChecked()),
            parent=window,
        )
        self._bind_monitor(router_id, monitor)
        self.monitors[router_id] = monitor
        window.set_router_monitor_ui_state("connecting")
        monitor.request_refresh()
        monitor.start()
        window.statusBar().showMessage(
            f"Подключение к «{router['name']}»..."
        )

    def connection_completed(self, router_id: int, result: Any) -> None:
        window = self.window
        if router_id not in self.monitors:
            return

        window.last_monitor_results[router_id] = result
        current_router_id = window.get_selected_router_id()
        confirmed_offline = bool(
            getattr(result, "confirmed_offline", not bool(result.success))
        )
        transient_failure = bool(
            not result.success
            and getattr(result, "transient_failure_suppressed", False)
        )
        connection_state = str(
            getattr(result, "connection_state", "") or ""
        ).strip().lower()
        independently_reachable = (
            window.router_reachability.get(router_id) is True
        )

        try:
            if result.success:
                window.persist_router_monitor_status(
                    router_id=router_id,
                    is_online=True,
                )
            elif confirmed_offline and not independently_reachable:
                window.persist_router_monitor_status(
                    router_id=router_id,
                    is_online=False,
                )
        except sqlite3.Error as error:
            QMessageBox.warning(
                window,
                "Ошибка базы данных",
                str(error),
            )

        if result.success:
            window.update_router_item_status(router_id, True)
            window.connection_results[router_id] = result
            window.update_active_cable_dialog(router_id, result)
            window.update_active_wireless_isp_dialog(router_id, result)
        elif confirmed_offline:
            window.update_router_item_status(
                router_id,
                independently_reachable,
            )
            window.connection_results.pop(router_id, None)
            window.clear_router_traffic_history(router_id)

        if current_router_id != router_id:
            return

        if transient_failure:
            retrying = connection_state == "retrying"
            window.set_router_monitor_ui_state(
                "retrying" if retrying else "unstable"
            )
            window.status_label.setText(
                "● Повторная попытка" if retrying else "● Связь нестабильна"
            )
            window.statusBar().showMessage(result.message, 5000)
            window.sync_developer_mode_for_selected_router()
            return

        if connection_state == "authentication_required":
            window.set_router_monitor_ui_state("authentication_required")
            window.status_label.setText("● Требуется авторизация")
            window.connected_address_label.setText(
                "Фактический адрес: роутер доступен\n"
                "Подключение: проверьте логин и пароль"
            )
            window.clear_router_state()
            window.statusBar().showMessage(
                result.message + " Повторные попытки приостановлены.",
                8000,
            )
            window.sync_developer_mode_for_selected_router()
            return

        if not result.success:
            window.set_router_monitor_ui_state("reconnecting")
            window.status_label.setText("● Offline")
            window.connected_address_label.setText(
                "Фактический адрес: —\nПодключение: —"
            )
            window.clear_router_state()
            window.statusBar().showMessage(result.message, 5000)
            window.sync_developer_mode_for_selected_router()
            return

        window.set_router_monitor_ui_state("connected")
        window.show_connection_result(result)
        window.sync_developer_mode_for_selected_router()

    def disconnect_selected(self) -> None:
        window = self.window
        router_id = window.get_selected_router_id()
        if router_id is None:
            return
        router = window.database.get_router(router_id)
        if router is None:
            QMessageBox.warning(
                window,
                "Роутер не найден",
                "Запись отсутствует в базе данных.",
            )
            return

        try:
            window.database.update_router_status(
                router_id=router_id,
                status=False,
                touch_last_seen=False,
            )
            window.persisted_router_status[router_id] = False
        except sqlite3.Error as error:
            QMessageBox.critical(
                window,
                "Ошибка базы данных",
                str(error),
            )
            return

        monitor = self.monitors.pop(router_id, None)
        window.connection_results.pop(router_id, None)
        window.pending_rescue_modes.pop(router_id, None)
        window.router_self_check_controller.forget_router(router_id)
        window.clear_router_traffic_history(router_id)
        window.last_monitor_results.pop(router_id, None)
        window.developer_mode_by_router.pop(router_id, None)
        window.set_router_content_connected(False)
        window.sync_developer_mode_for_selected_router()

        if monitor is not None:
            self.stopping_monitors[router_id] = monitor
            try:
                monitor.updated.disconnect(window.connection_completed)
            except (RuntimeError, TypeError):
                pass
            monitor.stop()

        for router_item in window.routers:
            if int(router_item.get("id", -1)) == router_id:
                router_item["status"] = 0
                break

        window.set_router_item_disconnected(router_id)
        window.status_label.setText("● Отключено")
        window.connected_address_label.setText(
            "Фактический адрес: —\nПодключение: —"
        )
        window.clear_router_state()
        window.set_router_monitor_ui_state(
            "stopping" if monitor is not None else "disconnected"
        )
        window.sync_developer_mode_for_selected_router()
        window.statusBar().showMessage(
            f"Роутер «{router['name']}» отключён",
            5000,
        )

    def reachability_changed(
        self,
        router_id: int,
        is_online: bool,
        checked_address: str,
    ) -> None:
        window = self.window
        if not window.reachability_checkbox.isChecked():
            return

        previous = window.router_reachability.get(router_id)
        authenticated_result = window.connection_results.get(router_id)
        authenticated_online = bool(
            authenticated_result is not None
            and getattr(authenticated_result, "success", False)
        )
        previous_effective = bool(previous is True or authenticated_online)
        window.router_reachability[router_id] = is_online

        if checked_address:
            window.router_reachable_address[router_id] = checked_address
        else:
            window.router_reachable_address.pop(router_id, None)

        window.update_router_item_status(
            router_id,
            bool(is_online or authenticated_online),
        )
        effective_online = bool(is_online or authenticated_online)
        if previous_effective == effective_online:
            return

        router = window.database.get_router(router_id)
        if router is not None:
            state_text = "доступен" if effective_online else "недоступен"
            window.statusBar().showMessage(
                f"Роутер «{router['name']}» {state_text}",
                3000,
            )

    def refresh_statuses(self) -> None:
        self.status_worker.set_routers(self.window.routers)
        self.status_worker.request_refresh()

    def monitor_stopped(self, router_id: int) -> None:
        window = self.window
        window.disable_developer_mode_for_router(router_id)
        window.pending_rescue_modes.pop(router_id, None)
        window.router_self_check_controller.monitor_stopped(router_id)
        monitor = self.monitors.pop(router_id, None)
        if monitor is not None:
            self.stopping_monitors[router_id] = monitor

        if window.get_selected_router_id() == router_id:
            window.connection_results.pop(router_id, None)
            window.clear_router_state()
            window.status_label.setText("● Не подключён")
            window.connected_address_label.setText(
                "Фактический адрес: —\nПодключение: —"
            )
            window.set_router_monitor_ui_state("stopping")

    def monitor_finished(
        self,
        router_id: int,
        monitor: RouterMonitor,
    ) -> None:
        window = self.window
        if self.monitors.get(router_id) is monitor:
            self.monitors.pop(router_id, None)
        if self.stopping_monitors.get(router_id) is monitor:
            self.stopping_monitors.pop(router_id, None)
        monitor.deleteLater()

        if window.get_selected_router_id() == router_id:
            window.set_router_monitor_ui_state(
                "connecting" if router_id in self.monitors else "disconnected"
            )
        else:
            window._update_router_management_actions()
            window._update_global_router_actions()

