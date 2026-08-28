from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RESCUER_MODE = "rescuer"
RESCUED_MODE = "rescued"
MIXED_MODE = "mixed"


@dataclass
class RescueModeContext:
    """Снимок состояний WISP-клиента и точки раздачи роутера."""

    mode: str
    available: bool
    message: str
    wisp_connections: list[dict[str, Any]]
    wifi_networks: list[dict[str, Any]]

    @property
    def wisp_names(self) -> tuple[str, ...]:
        return tuple(
            str(
                item.get("name")
                or item.get("description")
                or item.get("interface_id")
                or "Wireless ISP"
            )
            for item in self.wisp_connections
        )

    @property
    def wifi_names(self) -> tuple[str, ...]:
        return tuple(
            str(
                item.get("name")
                or item.get("ssid")
                or item.get("mws_profile_id")
                or "Wi-Fi"
            )
            for item in self.wifi_networks
        )

    def payload(self) -> dict[str, Any]:
        return {
            "wisp_connections": [
                dict(item) for item in self.wisp_connections
            ],
            "wifi_networks": [
                dict(item) for item in self.wifi_networks
            ],
        }


class RescueModeService:
    """
    Управляет сочетанием WISP-клиента и точки раздачи Wi-Fi.

    Операции выполняются одной последовательностью в потоке роутера.
    Если второй шаг не подтверждён, уже применённые изменения
    возвращаются в исходное состояние.
    """

    VALID_MODES = {RESCUER_MODE, RESCUED_MODE, MIXED_MODE}

    @classmethod
    def with_confirmed_mode(
        cls,
        context: RescueModeContext,
        mode: str,
    ) -> RescueModeContext:
        """Возвращает подтверждённое состояние после успешной операции."""
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in cls.VALID_MODES:
            return context

        wisp_enabled = normalized_mode in {
            RESCUED_MODE,
            MIXED_MODE,
        }
        wifi_enabled = normalized_mode in {
            RESCUER_MODE,
            MIXED_MODE,
        }
        wisp_connections = [
            {**item, "enabled": wisp_enabled}
            for item in context.wisp_connections
        ]
        wifi_networks = [
            {**item, "enabled": wifi_enabled}
            for item in context.wifi_networks
        ]
        return cls.inspect(wisp_connections, wifi_networks)

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "up",
            "enabled",
        }

    @classmethod
    def _item_enabled(cls, item: dict[str, Any]) -> bool:
        for key in ("enabled", "up"):
            if key in item and item.get(key) is not None:
                return cls._truthy(item.get(key))
        return False

    @staticmethod
    def _is_wisp(connection: dict[str, Any]) -> bool:
        interface_id = str(
            connection.get("interface_id") or ""
        ).strip()
        delete_kind = str(
            connection.get("delete_kind") or ""
        ).strip().lower()
        connection_type = " ".join(
            str(connection.get(key) or "")
            for key in (
                "type",
                "provider_protocol",
                "description",
            )
        ).lower()
        return bool(
            interface_id
            and (
                connection.get("wireless_isp_settings_supported")
                or delete_kind == "wireless.isp"
                or (
                    "/wifistation" in interface_id.lower()
                    and "wireless isp" in connection_type
                )
            )
        )

    @staticmethod
    def _assigned_wifi_network(network: dict[str, Any]) -> bool:
        if str(
            network.get("network_type") or "wifi"
        ).strip().lower() != "wifi":
            return False

        interfaces = network.get("interfaces")
        assigned_interfaces = bool(
            isinstance(interfaces, (list, tuple, set))
            and any(
                "/accesspoint" in str(item).lower()
                for item in interfaces
            )
        )
        bands = network.get("bands")
        assigned_bands = bool(
            isinstance(bands, (list, tuple, set))
            and any(str(item or "").strip() for item in bands)
        )
        manageable = bool(
            str(network.get("mws_profile_id") or "").strip()
            or network.get("legacy_managed")
            or assigned_interfaces
        )
        return manageable and (assigned_interfaces or assigned_bands)

    @classmethod
    def inspect(
        cls,
        internet_connections: Any,
        wifi_networks: Any,
    ) -> RescueModeContext:
        wisp = [
            dict(item)
            for item in (internet_connections or [])
            if isinstance(item, dict) and cls._is_wisp(item)
        ]
        wifi = [
            dict(item)
            for item in (wifi_networks or [])
            if isinstance(item, dict)
            and cls._assigned_wifi_network(item)
        ]

        if not wisp:
            return RescueModeContext(
                mode="unavailable",
                available=False,
                message="Wireless ISP не настроен.",
                wisp_connections=[],
                wifi_networks=wifi,
            )
        if not wifi:
            return RescueModeContext(
                mode="unavailable",
                available=False,
                message=(
                    "Нет Wi-Fi сети с назначенным диапазоном."
                ),
                wisp_connections=wisp,
                wifi_networks=[],
            )

        wisp_enabled = [cls._item_enabled(item) for item in wisp]
        wifi_enabled = [cls._item_enabled(item) for item in wifi]
        any_wisp = any(wisp_enabled)
        any_wifi = any(wifi_enabled)

        if not any_wisp and any_wifi:
            mode = RESCUER_MODE
            message = "Активен режим «Спасатель»."
        elif any_wisp and not any_wifi:
            mode = RESCUED_MODE
            message = "Активен режим «Спасаемый»."
        elif not any_wisp and not any_wifi:
            mode = "inactive"
            message = "WISP и раздача Wi-Fi выключены."
        else:
            mode = MIXED_MODE
            message = "WISP и раздача Wi-Fi включены одновременно."

        return RescueModeContext(
            mode=mode,
            available=True,
            message=message,
            wisp_connections=wisp,
            wifi_networks=wifi,
        )

    def apply(
        self,
        mode: str,
        payload: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        requested_mode = str(mode or "").strip().lower()
        if requested_mode not in self.VALID_MODES:
            message = "Неизвестный режим работы."
            details = {
                "preflight_rejected": True,
            }
            self._record_mode_event(
                message,
                requested_mode=requested_mode,
                context=None,
                success=False,
                operations=[],
                completed=[],
                extra_details=details,
            )
            return False, message, details

        source = dict(payload) if isinstance(payload, dict) else {}
        context = self.inspect(
            source.get("wisp_connections"),
            source.get("wifi_networks"),
        )
        if not context.available:
            details = {
                "preflight_rejected": True,
                "mode": context.mode,
            }
            self._record_mode_event(
                context.message,
                requested_mode=requested_mode,
                context=context,
                success=False,
                operations=[],
                completed=[],
                extra_details=details,
            )
            return False, context.message, details

        # Make the replacement path available before disabling the current
        # one. This is especially important over KeenDNS: disabling WISP
        # first can terminate the same connection that must enable Wi-Fi.
        # Completed steps are still rolled back in reverse order.
        desired_wisp = requested_mode in {
            RESCUED_MODE,
            MIXED_MODE,
        }
        desired_wifi = requested_mode in {
            RESCUER_MODE,
            MIXED_MODE,
        }
        wisp_operations: list[
            tuple[str, object, str, bool, bool]
        ] = []
        wifi_operations: list[
            tuple[str, object, str, bool, bool]
        ] = []

        for item in context.wisp_connections:
            original = self._item_enabled(item)
            if original == desired_wisp:
                continue
            interface_id = str(
                item.get("interface_id") or ""
            ).strip()
            wisp_operations.append(
                (
                    "wisp",
                    interface_id,
                    str(
                        item.get("name")
                        or item.get("description")
                        or interface_id
                    ),
                    original,
                    desired_wisp,
                )
            )

        for item in context.wifi_networks:
            original = self._item_enabled(item)
            if original == desired_wifi:
                continue
            wifi_operations.append(
                (
                    "wifi",
                    dict(item),
                    str(
                        item.get("name")
                        or item.get("ssid")
                        or item.get("mws_profile_id")
                        or "Wi-Fi"
                    ),
                    original,
                    desired_wifi,
                )
            )

        operations = (
            wifi_operations + wisp_operations
            if requested_mode == RESCUER_MODE
            else wisp_operations + wifi_operations
        )

        changed: list[tuple[str, object, str, bool, bool]] = []
        confirmed_after_error: list[
            tuple[str, object, str, bool, bool]
        ] = []
        handoff_pending = False
        handoff_connection_mode = ""
        for operation_index, operation in enumerate(operations):
            kind, target, title, original, desired = operation
            expected_handoff_mode = ""
            if operation_index == len(operations) - 1:
                expected_handoff_mode = self._expected_handoff_mode(
                    kind,
                    desired,
                )
            success, message, recovered, pending = self._set_state_confirmed(
                kind,
                target,
                desired,
                expected_handoff_mode=expected_handoff_mode,
            )
            if success:
                changed.append(operation)
                if recovered:
                    confirmed_after_error.append(operation)
                if pending:
                    handoff_pending = True
                    handoff_connection_mode = expected_handoff_mode
                continue

            # Даже операция, вернувшая ошибку, могла примениться
            # частично. Поэтому пробуем вернуть и её, и все
            # подтверждённые предыдущие шаги.
            rollback_items = changed + [operation]
            rollback_failures = self._rollback(rollback_items)
            rollback_text = (
                " Исходное состояние восстановлено."
                if not rollback_failures
                else (
                    " Не удалось полностью восстановить состояние: "
                    + "; ".join(rollback_failures)
                )
            )
            result_message = (
                f"Не удалось изменить «{title}»: {message}"
                + rollback_text
            )
            result_details = {
                "mode": requested_mode,
                "failed_kind": kind,
                "failed_target": title,
                "rolled_back": not rollback_failures,
                "rollback_attempted": True,
                "rollback_failures": rollback_failures,
                "confirmed_after_error": [
                    self._operation_diagnostic(item)
                    for item in confirmed_after_error
                ],
            }
            self._record_mode_event(
                result_message,
                requested_mode=requested_mode,
                context=context,
                success=False,
                operations=operations,
                completed=changed,
                extra_details=result_details,
            )
            return (
                False,
                result_message,
                result_details,
            )

        title = {
            RESCUER_MODE: "Спасатель",
            RESCUED_MODE: "Спасаемый",
            MIXED_MODE: "Смешанный",
        }[requested_mode]
        if not operations:
            message = f"Режим «{title}» уже включён."
        elif requested_mode == RESCUER_MODE:
            message = (
                "Режим «Спасатель» включён: Wireless ISP выключен, "
                "раздача Wi-Fi включена."
            )
        elif requested_mode == RESCUED_MODE:
            message = (
                "Режим «Спасаемый» включён: Wireless ISP включён, "
                "раздача Wi-Fi выключена."
            )
        else:
            message = (
                "Режим «Смешанный» включён: Wireless ISP и "
                "раздача Wi-Fi включены одновременно."
            )
        details = {
            "mode": requested_mode,
            "changed": len(operations),
            "state": "unknown" if handoff_pending else "confirmed",
            "handoff_pending": handoff_pending,
            "handoff_connection_mode": handoff_connection_mode,
            "wisp_interfaces": [
                str(item.get("interface_id") or "")
                for item in context.wisp_connections
            ],
            "wifi_names": list(context.wifi_names),
            "confirmed_after_error": [
                self._operation_diagnostic(item)
                for item in confirmed_after_error
            ],
        }
        if handoff_pending:
            target_title = (
                "KeenDNS"
                if handoff_connection_mode == "keendns"
                else "локальный адрес"
            )
            message = (
                "Команда переключения отправлена. Текущий канал управления "
                f"ожидаемо отключился; проверяю роутер через {target_title}."
            )
        self._record_mode_event(
            message,
            requested_mode=requested_mode,
            context=context,
            success=True,
            operations=operations,
            completed=changed,
            extra_details=details,
        )
        return True, message, details

    @staticmethod
    def _operation_diagnostic(
        operation: tuple[str, object, str, bool, bool],
    ) -> dict[str, Any]:
        kind, target, title, original, desired = operation
        if kind == "wisp":
            target_id = str(target)
            interfaces: list[str] = []
        else:
            network = target if isinstance(target, dict) else {}
            target_id = str(
                network.get("mws_profile_id")
                or network.get("name")
                or title
            )
            raw_interfaces = network.get("interfaces")
            interfaces = (
                [str(item) for item in raw_interfaces]
                if isinstance(raw_interfaces, (list, tuple, set))
                else []
            )
        details: dict[str, Any] = {
            "kind": kind,
            "target": target_id,
            "title": str(title),
            "original_enabled": bool(original),
            "requested_enabled": bool(desired),
        }
        if interfaces:
            details["interfaces"] = interfaces
        return details

    def _record_mode_event(
        self,
        message: str,
        *,
        requested_mode: str,
        context: RescueModeContext | None,
        success: bool,
        operations: list[tuple[str, object, str, bool, bool]],
        completed: list[tuple[str, object, str, bool, bool]],
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_event = getattr(diagnostics, "record_event", None)
        if not callable(record_event):
            return

        context_details: dict[str, Any] = {}
        if context is not None:
            context_details = {
                "initial_mode": context.mode,
                "wisp_interfaces": [
                    str(item.get("interface_id") or "")
                    for item in context.wisp_connections
                ],
                "wifi_profiles": [
                    str(
                        item.get("mws_profile_id")
                        or item.get("name")
                        or ""
                    )
                    for item in context.wifi_networks
                ],
                "wifi_names": list(context.wifi_names),
            }

        try:
            record_event(
                "rescue_mode_switch",
                str(message),
                endpoint="rescue/mode",
                details={
                    "requested_mode": str(requested_mode),
                    "success": bool(success),
                    **context_details,
                    "planned_steps": [
                        self._operation_diagnostic(item)
                        for item in operations
                    ],
                    "completed_steps": [
                        self._operation_diagnostic(item)
                        for item in completed
                    ],
                    **dict(extra_details or {}),
                },
            )
        except Exception:
            # Диагностика не должна влиять на переключение режима.
            return

    def _set_state(
        self,
        kind: str,
        target: object,
        enabled: bool,
    ) -> tuple[bool, str]:
        if kind == "wisp":
            return self.client.interface_service.set_enabled(
                interface_id=str(target),
                enabled=bool(enabled),
            )

        success, message, _details = (
            self.client.wifi_network_service.set_network_enabled(
                target,
                bool(enabled),
            )
        )
        return bool(success), str(message)

    def _set_state_confirmed(
        self,
        kind: str,
        target: object,
        enabled: bool,
        *,
        expected_handoff_mode: str = "",
    ) -> tuple[bool, str, bool, bool]:
        """Apply a state and resolve an ambiguous lost HTTP response."""
        attempts = getattr(
            getattr(self.client, "diagnostics", None),
            "connection_attempts",
            None,
        )
        previous_attempt = (
            attempts[-1]
            if isinstance(attempts, list) and attempts
            else None
        )
        success, message = self._set_state(kind, target, enabled)
        if success:
            return (
                True,
                message,
                False,
                bool(expected_handoff_mode),
            )

        if (
            expected_handoff_mode
            and self._last_failure_allows_handoff(previous_attempt)
        ):
            return True, message, False, True

        verified, verification_message = self._verify_state(
            kind,
            target,
            enabled,
        )
        if verified:
            return (
                True,
                (
                    "Ответ на команду не получен, но требуемое "
                    "состояние подтверждено повторным чтением."
                ),
                True,
                False,
            )

        verification_suffix = (
            f" Проверка состояния: {verification_message}"
            if verification_message
            else ""
        )
        return False, str(message) + verification_suffix, False, False

    def _expected_handoff_mode(
        self,
        kind: str,
        enabled: bool,
    ) -> str:
        """Return the alternate management path lost by this operation."""
        if enabled:
            return ""

        connection_type = str(
            getattr(self.client, "connection_type", "") or ""
        ).strip().lower()
        if kind == "wifi" and "keendns" not in connection_type:
            return "keendns"
        if kind == "wisp" and "keendns" in connection_type:
            return "local"
        return ""

    def _last_failure_allows_handoff(
        self,
        previous_attempt: object,
    ) -> bool:
        diagnostics = getattr(self.client, "diagnostics", None)
        attempts = getattr(diagnostics, "connection_attempts", None)
        if not isinstance(attempts, list) or not attempts:
            return False
        latest = attempts[-1]
        if latest is previous_attempt or not isinstance(latest, dict):
            return False
        failure_kind = str(latest.get("failure_kind") or "").lower()
        return failure_kind in {"connection_error", "timeout"}

    def _verify_state(
        self,
        kind: str,
        target: object,
        enabled: bool,
    ) -> tuple[bool, str]:
        if kind == "wisp":
            verify = getattr(
                self.client.interface_service,
                "verify_admin_state",
                None,
            )
            if not callable(verify):
                return False, "проверка WISP недоступна"
            return verify(
                interface_id=str(target),
                expected_enabled=bool(enabled),
            )

        network = target if isinstance(target, dict) else {}
        profile_id = str(
            network.get("mws_profile_id") or ""
        ).strip()
        if not profile_id:
            return False, "не определён MWS-профиль"

        verify = getattr(
            self.client.wifi_network_service,
            "verify_logical_enabled",
            None,
        )
        if not callable(verify):
            return False, "проверка Wi-Fi недоступна"

        raw_interfaces = network.get("interfaces")
        interfaces = (
            [str(item) for item in raw_interfaces]
            if isinstance(raw_interfaces, (list, tuple, set))
            else []
        )
        return verify(
            profile_id,
            bool(enabled),
            interfaces=interfaces,
        )

    def _rollback(
        self,
        operations: list[tuple[str, object, str, bool, bool]],
    ) -> list[str]:
        failures: list[str] = []
        for kind, target, title, original, _desired in reversed(
            operations
        ):
            success, message, _recovered, _pending = self._set_state_confirmed(
                kind,
                target,
                original,
            )
            if not success:
                failures.append(f"{title}: {message}")
        return failures
