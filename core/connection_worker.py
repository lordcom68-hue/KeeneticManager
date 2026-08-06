from __future__ import annotations

import queue
import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

from core.keenetic_client import (
    ConnectionResult,
    KeeneticClient,
)
from core.services.reachability import ReachabilityTracker


class RouterMonitor(QThread):
    """
    Постоянный фоновый монитор одного роутера.

    Поток владеет KeeneticClient и его requests.Session.
    Интерфейс получает только сигналы с готовыми результатами.

    Начиная с 0.19.3 каждое действие из очереди изолировано:
    ошибка одной команды не завершает поток мониторинга. Ручное
    обновление также выполняет один цикл даже при паузе.
    """

    updated = Signal(int, object)
    interface_action_completed = Signal(
        int,
        str,
        bool,
        str,
    )
    interface_reconnect_completed = Signal(
        int,
        str,
        bool,
        str,
    )
    interface_settings_completed = Signal(
        int,
        str,
        bool,
        str,
    )
    connection_creation_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    wireless_scan_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    connection_delete_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    wifi_network_action_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    wifi_client_access_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    rescue_mode_action_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    connection_protocol_change_completed = Signal(
        int,
        str,
        bool,
        str,
        object,
    )
    priorities_action_completed = Signal(
        int,
        bool,
        str,
        object,
    )
    diagnostic_action_completed = Signal(
        int,
        str,
        bool,
        str,
    )
    system_log_completed = Signal(
        int,
        bool,
        str,
        object,
    )
    monitor_stopped = Signal(int)
    OFFLINE_RETRY_DELAYS_MS = (
        5_000,
        10_000,
        20_000,
        30_000,
    )

    def __init__(
        self,
        router: dict,
        interval_ms: int = 2000,
        paused: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.router = dict(router)
        self.router_id = int(router["id"])

        self._interval_ms = max(
            1000,
            int(interval_ms),
        )
        self._running = True
        self._paused = bool(paused)
        self._refresh_requested = False
        self._offline_retry_level = 0
        self._authentication_blocked = False

        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._actions: queue.Queue[
            tuple[str, object]
        ] = queue.Queue()

        self._reachability = ReachabilityTracker(
            failure_threshold=3
        )
        self._reachability.seed(
            router_id=self.router_id,
            online=bool(self.router.get("status", False)),
            address=str(
                self.router.get("last_connected_url")
                or self.router.get("local_ip")
                or self.router.get("keendns")
                or ""
            ),
        )

    def apply_reachability_state(
        self,
        result: ConnectionResult,
        client: KeeneticClient,
    ) -> ConnectionResult:
        """Подтверждает offline только после трёх подряд сбоев."""
        authentication_required = (
            str(getattr(result, "failure_kind", "") or "")
            .strip()
            .lower()
            == "authentication"
        )
        address = str(
            result.connected_url
            or client.base_url
            or ""
        )
        reachability = self._reachability.update(
            router_id=self.router_id,
            # Ответ 401/403 подтверждает доступность роутера, но
            # требует исправить учётные данные.
            probe_online=(
                True
                if authentication_required
                else bool(result.success)
            ),
            address=address,
        )

        result.consecutive_failures = (
            reachability.consecutive_failures
        )
        result.last_result_message = str(
            result.message or client.transport.last_result_message or ""
        )

        if result.success:
            result.connection_state = "connected"
            result.confirmed_offline = False
            result.transient_failure_suppressed = False
            client.transport.record_monitor_state(
                "connected",
                result.last_result_message or "Связь подтверждена.",
            )
            return result

        if authentication_required:
            result.connection_state = "authentication_required"
            result.confirmed_offline = False
            result.transient_failure_suppressed = False
            client.transport.record_monitor_state(
                "authentication_required",
                result.last_result_message
                or "Требуется проверить логин и пароль.",
            )
            return result

        if reachability.transient_failure_suppressed:
            result.connection_state = (
                "unstable"
                if reachability.consecutive_failures == 1
                else "retrying"
            )
            result.confirmed_offline = False
            result.transient_failure_suppressed = True
            client.transport.record_monitor_state(
                result.connection_state,
                result.last_result_message,
            )
            return result

        result.connection_state = "offline"
        result.confirmed_offline = True
        result.transient_failure_suppressed = False
        client.transport.record_monitor_state(
            "offline",
            result.last_result_message or "Роутер недоступен.",
        )
        return result

    def run(self) -> None:
        client = KeeneticClient(
            username=self.router.get(
                "username"
            ) or "",
            password=self.router.get(
                "password"
            ) or "",
            router_id=self.router_id,
        )

        first_cycle = True

        try:
            while self.is_running():
                # Событие лишь будит поток. Устойчивое состояние
                # хранится во флагах и очереди, поэтому очищаем
                # старый wake до обработки текущего цикла.
                self._wake_event.clear()
                refreshed_by_action = (
                    self.process_actions(client)
                )

                if not self.is_running():
                    break

                if refreshed_by_action:
                    # Снимок уже отправлен после пакета команд.
                    # Не выполняем второй refresh в том же цикле.
                    if self.is_paused():
                        continue

                    self._wake_event.wait(
                        self.interval_ms() / 1000
                    )
                    continue

                force_refresh = (
                    self.consume_refresh_request()
                )

                if (
                    self._authentication_blocked
                    and not force_refresh
                ):
                    # Постоянная ошибка пароля не должна создавать
                    # бесконечные запросы и засорять журнал роутера.
                    self._wake_event.wait(0.25)
                    continue

                if (
                    self.is_paused()
                    and not force_refresh
                ):
                    self._wake_event.wait(0.25)
                    continue

                try:
                    if (
                        first_cycle
                        or not client.connected
                    ):
                        router = self.router_snapshot()
                        result = client.connect_to_router(
                            local_ip=router.get(
                                "local_ip"
                            ),
                            keendns=router.get(
                                "keendns"
                            ),
                            connection_mode=router.get(
                                "connection_mode",
                                "auto",
                            ),
                        )
                        first_cycle = False
                    else:
                        result = client.refresh()

                except Exception as error:
                    # Ошибка обычного цикла обновления не должна
                    # уничтожать QThread. Закрываем клиент и даём
                    # следующему циклу выполнить переподключение.
                    result = ConnectionResult(
                        success=False,
                        message=(
                            "Непредвиденная ошибка "
                            f"обновления: {error}"
                        ),
                    )

                if not self.is_running():
                    break

                result = self.apply_reachability_state(
                    result,
                    client,
                )
                self.updated.emit(
                    self.router_id,
                    result,
                )

                if not result.success:
                    client.close()

                if result.success:
                    self._authentication_blocked = False
                elif (
                    str(
                        getattr(result, "failure_kind", "")
                        or ""
                    ).strip().lower()
                    == "authentication"
                ):
                    self._authentication_blocked = True
                    continue

                # После одноразового обновления при паузе сразу
                # возвращаемся к короткому ожиданию, а не ждём
                # весь период автообновления.
                if self.is_paused():
                    continue

                self._wake_event.wait(
                    self.next_cycle_delay_ms(result) / 1000
                )

        finally:
            client.close()
            self.monitor_stopped.emit(
                self.router_id
            )

    def request_interface_state(
        self,
        interface_id: str,
        enabled: bool,
    ) -> None:
        self._put_action(
            "interface_state",
            (interface_id, enabled),
        )

    def request_interface_settings(
        self,
        interface_id: str,
        settings: dict,
    ) -> None:
        self._put_action(
            "interface_settings",
            (
                str(interface_id),
                dict(settings),
            ),
        )

    def request_create_wired_ipoe(
        self,
        settings: dict,
    ) -> None:
        self._put_action(
            "create_wired_ipoe",
            dict(settings),
        )

    def request_create_cable_internet(
        self,
        settings: dict,
    ) -> None:
        self._put_action(
            "create_cable_internet",
            dict(settings),
        )

    def request_scan_wireless_isp(
        self,
        band: str,
    ) -> None:
        self._put_action(
            "scan_wireless_isp",
            str(band),
        )

    def request_create_wireless_isp(
        self,
        settings: dict,
    ) -> None:
        self._put_action(
            "create_wireless_isp",
            dict(settings),
        )

    def request_delete_wireless_isp(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_wireless_isp",
            str(interface_id),
        )

    def request_create_wifi_network(
        self,
        settings: dict,
    ) -> None:
        self._put_action(
            "create_wifi_network",
            dict(settings),
        )

    def request_update_wifi_network(
        self,
        interface_ids: list[str],
        settings: dict,
    ) -> None:
        self._put_action(
            "update_wifi_network",
            (
                list(interface_ids),
                dict(settings),
            ),
        )

    def request_set_wifi_network_enabled(
        self,
        network_reference: object,
        enabled: bool,
    ) -> None:
        payload = (
            dict(network_reference)
            if isinstance(network_reference, dict)
            else network_reference
        )
        self._put_action(
            "set_wifi_network_enabled",
            (payload, bool(enabled)),
        )

    def request_rescue_mode(
        self,
        mode: str,
        context: dict[str, Any],
    ) -> None:
        self._put_action(
            "rescue_mode",
            (
                str(mode),
                dict(context),
            ),
        )

    def request_delete_wifi_network(
        self,
        network_reference: object,
    ) -> None:
        payload = (
            dict(network_reference)
            if isinstance(network_reference, dict)
            else list(network_reference)
            if isinstance(network_reference, (list, tuple, set))
            else network_reference
        )
        self._put_action(
            "delete_wifi_network",
            payload,
        )

    def request_wifi_client_access(
        self,
        client_reference: dict[str, Any],
        *,
        allowed: bool,
    ) -> None:
        self._put_action(
            "wifi_client_access",
            (dict(client_reference), bool(allowed)),
        )

    def request_change_cable_protocol(
        self,
        interface_id: str,
        settings: dict,
    ) -> None:
        self._put_action(
            "change_cable_protocol",
            (str(interface_id), dict(settings)),
        )

    def request_delete_dedicated_wan(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_dedicated_wan",
            str(interface_id),
        )

    def request_delete_wired_ipoe(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_wired_ipoe",
            str(interface_id),
        )

    def request_delete_wired_pppoe(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_wired_pppoe",
            str(interface_id),
        )

    def request_delete_wired_pptp(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_wired_pptp",
            str(interface_id),
        )


    def request_delete_wired_l2tp(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "delete_wired_l2tp",
            str(interface_id),
        )

    def request_interface_reconnect(
        self,
        interface_id: str,
    ) -> None:
        self._put_action(
            "interface_reconnect",
            str(interface_id),
        )

    def request_internet_priorities(
        self,
        priorities: dict[str, int],
    ) -> None:
        self._put_action(
            "internet_priorities",
            dict(priorities),
        )

    def request_diagnostic_archive(
        self,
        destination: str,
        *,
        application_settings: dict[str, Any] | None = None,
    ) -> None:
        self._put_action(
            "diagnostic_archive",
            {
                "destination": str(destination),
                "application_settings": dict(
                    application_settings or {}
                ),
            },
        )

    def request_system_log(self, max_lines: int = 500) -> None:
        self._put_action(
            "system_log",
            int(max_lines),
        )

    def request_clear_diagnostics(self) -> None:
        self._put_action(
            "clear_diagnostics",
            None,
        )

    def _put_action(
        self,
        action_type: str,
        payload: object,
    ) -> None:
        self._actions.put(
            (action_type, payload)
        )
        self._wake_event.set()

    def process_actions(
        self,
        client: KeeneticClient,
    ) -> bool:
        """
        Выполняет все накопленные действия и обновляет интерфейс
        один раз после всего пакета команд.

        Возвращает True, если свежий снимок уже был отправлен.
        """
        refresh_required = False

        while self.is_running():
            try:
                action_type, payload = (
                    self._actions.get_nowait()
                )
            except queue.Empty:
                break

            try:
                refresh_required = (
                    self.process_action(
                        client=client,
                        action_type=action_type,
                        payload=payload,
                    )
                    or refresh_required
                )
            except Exception as error:
                self.emit_action_failure(
                    action_type=action_type,
                    payload=payload,
                    error=error,
                )

        if refresh_required and self.is_running():
            self.emit_immediate_refresh(client)

            # Completion-сигнал обрабатывается GUI-потоком и в
            # старых обработчиках мог дополнительно вызвать
            # request_refresh(). Снимок уже свежий, поэтому такой
            # запрос объединяем с выполненным обновлением. Если
            # новый запрос появится позже, следующий цикл увидит его.
            self.consume_refresh_request()
            return True

        return False

    def process_action(
        self,
        *,
        client: KeeneticClient,
        action_type: str,
        payload: object,
    ) -> bool:
        """
        Выполняет одно действие.

        Возвращает True, когда после завершения нужно немедленно
        отправить свежий снимок интерфейсу. Исключения обрабатывает
        process_actions(), поэтому поток продолжает работу.
        """
        if action_type == "interface_state":
            interface_id, enabled = payload
            success, message = (
                client.set_interface_enabled(
                    interface_id=str(interface_id),
                    enabled=bool(enabled),
                )
            )
            self.interface_action_completed.emit(
                self.router_id,
                str(interface_id),
                success,
                message,
            )
            return True

        if action_type == "interface_settings":
            interface_id, settings = payload
            success, message = (
                client.set_interface_settings(
                    interface_id=str(interface_id),
                    settings=dict(settings),
                )
            )
            self.interface_settings_completed.emit(
                self.router_id,
                str(interface_id),
                success,
                message,
            )
            return True

        if action_type == "create_wired_ipoe":
            success, message, details = (
                client.wired_internet_service.create_wired_ipoe(
                    dict(payload)
                )
            )
            self.connection_creation_completed.emit(
                self.router_id,
                "ethernet.ipoe",
                success,
                message,
                details,
            )
            return True

        if action_type == "create_cable_internet":
            settings = dict(payload)
            protocol = str(
                settings.get("protocol", {}).get("type")
                if isinstance(settings.get("protocol"), dict)
                else "none"
            ).strip().lower()
            success, message, details = (
                client.wired_internet_service.create_cable_internet(settings)
            )
            connection_type = {
                "pppoe": "ethernet.pppoe",
                "pptp": "ethernet.pptp",
                "l2tp": "ethernet.l2tp",
            }.get(protocol, "ethernet.ipoe")
            self.connection_creation_completed.emit(
                self.router_id,
                connection_type,
                success,
                message,
                details,
            )
            return True

        if action_type == "scan_wireless_isp":
            band = str(payload)
            success, message, networks = (
                client.wireless_isp_service.scan_wireless_isp(band)
            )
            self.wireless_scan_completed.emit(
                self.router_id,
                band,
                success,
                message,
                networks,
            )
            return False

        if action_type == "create_wireless_isp":
            success, message, details = (
                client.wireless_isp_service.create_wireless_isp(
                    dict(payload)
                )
            )
            self.connection_creation_completed.emit(
                self.router_id,
                "wireless.isp",
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wireless_isp":
            interface_id = str(payload)
            success, message, details = (
                client.wireless_isp_service.delete_wireless_isp(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "create_wifi_network":
            success, message, details = (
                client.wifi_network_service.create_network(
                    dict(payload)
                )
            )
            self.wifi_network_action_completed.emit(
                self.router_id,
                "create",
                success,
                message,
                details,
            )
            return True

        if action_type == "update_wifi_network":
            interface_ids, settings = payload
            success, message, details = (
                client.wifi_network_service.update_network(
                    list(interface_ids),
                    dict(settings),
                )
            )
            self.wifi_network_action_completed.emit(
                self.router_id,
                "update",
                success,
                message,
                details,
            )
            return True

        if action_type == "set_wifi_network_enabled":
            network_reference, enabled = payload
            success, message, details = (
                client.wifi_network_service.set_network_enabled(
                    network_reference,
                    bool(enabled),
                )
            )
            self.wifi_network_action_completed.emit(
                self.router_id,
                "state",
                success,
                message,
                details,
            )
            return True

        if action_type == "rescue_mode":
            mode, context = payload
            success, message, details = (
                client.rescue_mode_service.apply(
                    str(mode),
                    dict(context),
                )
            )
            self.rescue_mode_action_completed.emit(
                self.router_id,
                str(mode),
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wifi_network":
            success, message, details = (
                client.wifi_network_service.delete_network(payload)
            )
            self.wifi_network_action_completed.emit(
                self.router_id,
                "delete",
                success,
                message,
                details,
            )
            return True

        if action_type == "wifi_client_access":
            client_reference, allowed = payload
            success, message, details = (
                client.wifi_client_service.set_access(
                    dict(client_reference),
                    allowed=bool(allowed),
                )
            )
            self.wifi_client_access_completed.emit(
                self.router_id,
                str(client_reference.get("mac") or ""),
                success,
                message,
                details,
            )
            return True

        if action_type == "change_cable_protocol":
            interface_id, settings = payload
            success, message, details = (
                client.wired_internet_service.change_cable_internet_protocol(
                    interface_id=str(interface_id),
                    settings=dict(settings),
                )
            )
            self.connection_protocol_change_completed.emit(
                self.router_id,
                str(interface_id),
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_dedicated_wan":
            interface_id = str(payload)
            success, message, details = (
                client.wired_internet_service.delete_dedicated_wan(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wired_ipoe":
            interface_id = str(payload)
            success, message, details = (
                client.wired_internet_service.delete_wired_ipoe(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wired_pppoe":
            interface_id = str(payload)
            success, message, details = (
                client.wired_internet_service.delete_wired_pppoe(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wired_pptp":
            interface_id = str(payload)
            success, message, details = (
                client.wired_internet_service.delete_wired_pptp(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "delete_wired_l2tp":
            interface_id = str(payload)
            success, message, details = (
                client.wired_internet_service.delete_wired_l2tp(
                    interface_id=interface_id,
                )
            )
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
                details,
            )
            return True

        if action_type == "interface_reconnect":
            interface_id = str(payload)
            success, message = (
                client.reconnect_interface(
                    interface_id=interface_id,
                )
            )
            self.interface_reconnect_completed.emit(
                self.router_id,
                interface_id,
                success,
                message,
            )
            return True

        if action_type == "internet_priorities":
            priorities = dict(payload)
            success, message = (
                client.priority_service.set_internet_priorities(
                    priorities
                )
            )
            self.priorities_action_completed.emit(
                self.router_id,
                success,
                message,
                priorities,
            )
            return True

        if action_type == "diagnostic_archive":
            diagnostic_payload = (
                dict(payload)
                if isinstance(payload, dict)
                else {"destination": str(payload)}
            )
            success, message = (
                client.create_diagnostic_archive(
                    str(
                        diagnostic_payload.get(
                            "destination",
                            "",
                        )
                    ),
                    application_settings=dict(
                        diagnostic_payload.get(
                            "application_settings",
                            {},
                        )
                        or {}
                    ),
                )
            )
            self.diagnostic_action_completed.emit(
                self.router_id,
                "create",
                success,
                message,
            )
            return False

        if action_type == "system_log":
            success, message, entries = (
                client.system_log_service.read(int(payload))
            )
            self.system_log_completed.emit(
                self.router_id,
                success,
                message,
                entries,
            )
            return False

        if action_type == "clear_diagnostics":
            client.clear_diagnostics()
            self.diagnostic_action_completed.emit(
                self.router_id,
                "clear",
                True,
                "Диагностические данные очищены.",
            )
            return False

        raise ValueError(
            f"Неизвестное действие мониторинга: {action_type}"
        )

    def emit_action_failure(
        self,
        *,
        action_type: str,
        payload: object,
        error: Exception,
    ) -> None:
        message = (
            "Операция не выполнена из-за "
            f"непредвиденной ошибки: {error}"
        )

        if action_type == "interface_state":
            interface_id = self._payload_interface_id(
                payload
            )
            self.interface_action_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
            )
            return

        if action_type == "interface_settings":
            interface_id = self._payload_interface_id(
                payload
            )
            self.interface_settings_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
            )
            return

        if action_type == "create_wired_ipoe":
            self.connection_creation_completed.emit(
                self.router_id,
                "ethernet.ipoe",
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "create_cable_internet":
            settings = (
                dict(payload)
                if isinstance(payload, dict)
                else {}
            )
            protocol = settings.get("protocol")
            protocol_type = (
                str(protocol.get("type") or "none")
                if isinstance(protocol, dict)
                else "none"
            ).strip().lower()
            self.connection_creation_completed.emit(
                self.router_id,
                (
                    {
                        "pppoe": "ethernet.pppoe",
                        "pptp": "ethernet.pptp",
                        "l2tp": "ethernet.l2tp",
                    }.get(protocol_type, "ethernet.ipoe")
                ),
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "scan_wireless_isp":
            self.wireless_scan_completed.emit(
                self.router_id,
                str(payload or ""),
                False,
                message,
                [],
            )
            return

        if action_type == "create_wireless_isp":
            self.connection_creation_completed.emit(
                self.router_id,
                "wireless.isp",
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "delete_wireless_isp":
            interface_id = str(payload or "")
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type in {
            "create_wifi_network",
            "update_wifi_network",
            "set_wifi_network_enabled",
            "delete_wifi_network",
        }:
            operation = {
                "create_wifi_network": "create",
                "update_wifi_network": "update",
                "set_wifi_network_enabled": "state",
                "delete_wifi_network": "delete",
            }[action_type]
            self.wifi_network_action_completed.emit(
                self.router_id,
                operation,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "wifi_client_access":
            if isinstance(payload, tuple) and payload:
                reference = payload[0]
            else:
                reference = {}
            mac = (
                str(reference.get("mac") or "")
                if isinstance(reference, dict)
                else ""
            )
            self.wifi_client_access_completed.emit(
                self.router_id,
                mac,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "rescue_mode":
            if (
                isinstance(payload, tuple)
                and payload
            ):
                mode = str(payload[0] or "")
            else:
                mode = ""
            self.rescue_mode_action_completed.emit(
                self.router_id,
                mode,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "change_cable_protocol":
            if (
                isinstance(payload, tuple)
                and len(payload) >= 1
            ):
                interface_id = str(payload[0] or "")
            else:
                interface_id = ""
            self.connection_protocol_change_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type in {
            "delete_dedicated_wan",
            "delete_wired_ipoe",
            "delete_wired_pppoe",
            "delete_wired_pptp",
            "delete_wired_l2tp",
        }:
            interface_id = str(payload or "")
            self.connection_delete_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
                {"exception": type(error).__name__},
            )
            return

        if action_type == "interface_reconnect":
            interface_id = str(payload or "")
            self.interface_reconnect_completed.emit(
                self.router_id,
                interface_id,
                False,
                message,
            )
            return

        if action_type == "internet_priorities":
            priorities = (
                dict(payload)
                if isinstance(payload, dict)
                else {}
            )
            self.priorities_action_completed.emit(
                self.router_id,
                False,
                message,
                priorities,
            )
            return

        if action_type == "system_log":
            self.system_log_completed.emit(
                self.router_id,
                False,
                message,
                [],
            )
            return

        operation = (
            "clear"
            if action_type == "clear_diagnostics"
            else "create"
        )
        self.diagnostic_action_completed.emit(
            self.router_id,
            operation,
            False,
            message,
        )

    @staticmethod
    def _payload_interface_id(
        payload: object,
    ) -> str:
        if (
            isinstance(payload, tuple)
            and payload
        ):
            return str(payload[0])
        return str(payload or "")

    def emit_immediate_refresh(
        self,
        client: KeeneticClient,
    ) -> None:
        """
        Отправляет свежий снимок сразу после изменения,
        даже когда автообновление выключено.
        """
        if not self.is_running():
            return

        try:
            if client.connected:
                result = client.refresh()
            else:
                router = self.router_snapshot()
                result = client.connect_to_router(
                    local_ip=router.get("local_ip"),
                    keendns=router.get("keendns"),
                    connection_mode=router.get(
                        "connection_mode",
                        "auto",
                    ),
                )
        except Exception as error:
            result = ConnectionResult(
                success=False,
                message=(
                    "Операция завершена, но немедленное "
                    f"обновление не выполнено: {error}"
                ),
            )

        result = self.apply_reachability_state(
            result,
            client,
        )

        if self.is_running():
            self.updated.emit(
                self.router_id,
                result,
            )

        if not result.success:
            client.close()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._wake_event.set()

    def request_refresh(self) -> None:
        """Запрашивает один цикл даже при выключенном автообновлении."""
        with self._lock:
            self._refresh_requested = True
        self._wake_event.set()

    def next_cycle_delay_ms(
        self,
        result: ConnectionResult,
    ) -> int:
        """Возвращает паузу с backoff для подтверждённого offline."""
        base_interval = self.interval_ms()
        if (
            bool(result.success)
            or not bool(result.confirmed_offline)
        ):
            self._offline_retry_level = 0
            return base_interval

        delay_index = min(
            self._offline_retry_level,
            len(self.OFFLINE_RETRY_DELAYS_MS) - 1,
        )
        self._offline_retry_level += 1
        return max(
            base_interval,
            self.OFFLINE_RETRY_DELAYS_MS[delay_index],
        )

    def consume_refresh_request(self) -> bool:
        with self._lock:
            requested = self._refresh_requested
            self._refresh_requested = False
        return requested

    def set_interval(
        self,
        interval_ms: int,
    ) -> None:
        with self._lock:
            self._interval_ms = max(
                1000,
                int(interval_ms),
            )
        self._wake_event.set()

    def set_paused(
        self,
        paused: bool,
    ) -> None:
        with self._lock:
            self._paused = bool(paused)
        self._wake_event.set()

    def update_router_data(
        self,
        router: dict,
    ) -> None:
        with self._lock:
            self.router = dict(router)
        self._wake_event.set()

    def router_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.router)

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def interval_ms(self) -> int:
        with self._lock:
            return self._interval_ms
