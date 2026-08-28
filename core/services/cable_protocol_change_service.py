from __future__ import annotations

from typing import Any


class CableProtocolChangeService:
    """Безопасная смена протокола проводного подключения."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def change_cable_internet_protocol(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        if not isinstance(settings, dict):
            return False, "Параметры изменения переданы неверно.", {}

        source_id = str(interface_id or "").strip()
        target_protocol = str(
            settings.get("target_protocol") or "none"
        ).strip().lower()
        description = str(
            settings.get("description") or source_id
        ).strip()

        if target_protocol not in {"none", "pppoe", "pptp", "l2tp"}:
            return False, "Выбранный протокол не поддерживается.", {}
        if not source_id or not description:
            return False, "Не указано подключение или его название.", {}

        username = str(settings.get("username") or "").strip()
        password = str(settings.get("password") or "")

        def safe_message(value: Any) -> str:
            return self.client.redact_secrets_from_text(
                str(value or ""),
                password,
            )

        service = str(settings.get("service") or "").strip()
        server = self.client.normalize_host(
            str(settings.get("server") or "")
        )

        if target_protocol != "none":
            if not username or not password:
                return False, "Для нового протокола нужны логин и пароль.", {}
            if len(username) > 128 or len(password) > 256:
                return False, "Учётные данные слишком длинные.", {}
            if any(ch in "\r\n\x00" for ch in username + password + service + server):
                return False, "Параметры содержат недопустимые символы.", {}
            if target_protocol == "pppoe":
                if len(service) > 64:
                    return False, "Имя сервиса PPPoE слишком длинное.", {}
            elif not server or len(server) > 253:
                return False, "Укажите корректный сервер провайдера.", {}

        default_mtu = {
            "none": 1500,
            "pppoe": 1492,
            "pptp": 1400,
            "l2tp": 1500,
        }[target_protocol]
        try:
            target_mtu = int(settings.get("mtu", default_mtu))
        except (TypeError, ValueError):
            return False, "Некорректный MTU нового протокола.", {}

        max_mtu = 1492 if target_protocol == "pppoe" else 1500
        if not 576 <= target_mtu <= max_mtu:
            return (
                False,
                f"MTU должен быть от 576 до {max_mtu}.",
                {},
            )

        runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return False, "Не удалось прочитать конфигурацию интерфейсов.", {}

        context, reason = self.client.inspect_cable_protocol_change(
            interface_id=source_id,
            interface_config=config,
            interface_runtime=(runtime if isinstance(runtime, dict) else {}),
        )

        if not context:
            return False, reason or "Смена типа недоступна.", {}

        current_protocol = str(context["current_protocol"])
        transport_id = str(context["transport_id"])
        expected_order = int(context["global_order"])

        if current_protocol == target_protocol:
            return False, "Выбран текущий тип подключения.", {
                "preflight_rejected": True,
            }

        source_item = config.get(source_id)
        transport_item = config.get(transport_id)

        if not isinstance(source_item, dict) or not isinstance(transport_item, dict):
            return False, "Конфигурация подключения изменилась.", {}

        old_priorities = self.client.priority_service.current_global_orders(config)
        use_provider_dns, manual_dns = self.client.configured_name_server_settings(
            source_item
        )
        ping_profile = self.client.ping_check_service.assigned_ping_profile(source_item)
        transport_ping_profile = self.client.ping_check_service.assigned_ping_profile(transport_item)

        if (
            target_protocol == "none"
            and transport_ping_profile
            and transport_ping_profile != ping_profile
        ):
            return (
                False,
                "На транспорте уже назначен другой Ping Check; "
                "автоматическая смена типа заблокирована.",
                {"preflight_rejected": True},
            )

        transport_use_dns, transport_manual_dns = (
            self.client.configured_name_server_settings(transport_item)
        )
        transport_ip = transport_item.get("ip")
        try:
            transport_mtu = int(transport_ip.get("mtu"))
        except (TypeError, ValueError, AttributeError):
            transport_mtu = 1500
        transport_restore_settings = {
            "description": str(
                transport_item.get("description")
                or transport_item.get("rename")
                or transport_id
            ).strip(),
            "mtu": transport_mtu,
            "use_provider_dns": transport_use_dns,
            "manual_dns": transport_manual_dns,
        }

        if target_protocol == "none":
            target_id = transport_id
        else:
            prefix = {
                "pppoe": "PPPoE",
                "pptp": "PPTP",
                "l2tp": "L2TP",
            }[target_protocol]
            target_id = self.client.next_numbered_interface_id(config, prefix)
            success, message = self.client.create_protocol_on_existing_transport(
                target_protocol=target_protocol,
                target_id=target_id,
                transport_id=transport_id,
                description=description,
                username=username,
                password=password,
                service=service,
                server=server,
            )
            message = safe_message(message)
            if not success:
                cleanup_success, cleanup_message = (
                    self.client.execute_parse_command(
                        "no interface "
                        + self.client.quote_cli_argument(target_id)
                    )
                )
                self.client.execute_parse_command(
                    "system configuration save"
                )
                cleanup_message = safe_message(cleanup_message)
                return (
                    False,
                    message
                    + (
                        " Откат: незавершённый интерфейс удалён."
                        if cleanup_success
                        else " Откат не завершён: " + cleanup_message
                    ),
                    {"rollback_success": cleanup_success},
                )

            success, message = self.client.set_interface_settings(
                interface_id=target_id,
                settings={
                    "description": description,
                    "mtu": target_mtu,
                    "use_provider_dns": use_provider_dns,
                    "manual_dns": manual_dns,
                },
            )
            message = self.client.redact_secrets_from_text(message, password)
            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_cable_protocol_change(
                        source_id=source_id,
                        target_id=target_id,
                        target_protocol=target_protocol,
                        transport_id=transport_id,
                        old_priorities=old_priorities,
                        ping_profile=ping_profile,
                        transport_restore_settings=None,
                    )
                )
                return (
                    False,
                    message + ". Откат: " + rollback_message,
                    {"rollback_success": rollback_success},
                )

        if target_protocol == "none":
            success, message = self.client.set_interface_settings(
                interface_id=transport_id,
                settings={
                    "description": description,
                    "mtu": target_mtu,
                    "use_provider_dns": use_provider_dns,
                    "manual_dns": manual_dns,
                },
            )
            message = safe_message(message)
            if not success:
                restore_success, restore_message = (
                    self.client.set_interface_settings(
                        interface_id=transport_id,
                        settings=dict(transport_restore_settings),
                    )
                )
                return (
                    False,
                    "Не удалось подготовить IPoE: "
                    + message
                    + (
                        ". Исходные параметры транспорта восстановлены."
                        if restore_success
                        else ". Не удалось восстановить транспорт: "
                        + safe_message(restore_message)
                    ),
                    {"rollback_success": restore_success},
                )

        source_arg = self.client.quote_cli_argument(source_id)
        target_arg = self.client.quote_cli_argument(target_id)

        if ping_profile and source_id != target_id:
            success, message = self.client.execute_parse_command(
                f"interface {source_arg} no ping-check profile"
            )
            if success:
                success, message = self.client.execute_parse_command(
                    f"interface {target_arg} ping-check profile "
                    + self.client.quote_cli_argument(ping_profile)
                )
            message = safe_message(message)
            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_cable_protocol_change(
                        source_id=source_id,
                        target_id=target_id,
                        target_protocol=target_protocol,
                        transport_id=transport_id,
                        old_priorities=old_priorities,
                        ping_profile=ping_profile,
                        transport_restore_settings=(
                            transport_restore_settings
                            if target_protocol == "none"
                            else None
                        ),
                    )
                )
                return (
                    False,
                    "Не удалось перенести Ping Check: "
                    + message
                    + ". Откат: "
                    + rollback_message,
                    {"rollback_success": rollback_success},
                )

        success, message = self.client.set_interface_global_enabled(
            source_id,
            False,
        )
        message = safe_message(message)
        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_cable_protocol_change(
                    source_id=source_id,
                    target_id=target_id,
                    target_protocol=target_protocol,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                    ping_profile=ping_profile,
                    transport_restore_settings=(
                        transport_restore_settings
                        if target_protocol == "none"
                        else None
                    ),
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        new_priorities = {
            current_id: order
            for current_id, order in old_priorities.items()
            if current_id != source_id
        }
        new_priorities[target_id] = expected_order
        normalized_ordered = [
            current_id
            for current_id, _order in sorted(
                new_priorities.items(),
                key=lambda item: item[1],
            )
        ]
        new_priorities = {
            current_id: order
            for order, current_id in enumerate(normalized_ordered)
        }
        expected_order = new_priorities[target_id]

        success, message = self.client.priority_service.set_internet_priorities(new_priorities)
        message = safe_message(message)
        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_cable_protocol_change(
                    source_id=source_id,
                    target_id=target_id,
                    target_protocol=target_protocol,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                    ping_profile=ping_profile,
                    transport_restore_settings=(
                        transport_restore_settings
                        if target_protocol == "none"
                        else None
                    ),
                )
            )
            return (
                False,
                "Не удалось перенести приоритет: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        success, message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "interface "
                        + self.client.quote_cli_argument(transport_id)
                        + " up"
                    )
                },
                {"parse": f"interface {target_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить новый протокол",
        )
        message = self.client.redact_secrets_from_text(message, password)
        enable_had_transient_failure = (
            not success
            and self.client.is_transient_rci_failure(message)
        )

        if not success and not enable_had_transient_failure:
            rollback_success, rollback_message = (
                self.client.rollback_cable_protocol_change(
                    source_id=source_id,
                    target_id=target_id,
                    target_protocol=target_protocol,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                    ping_profile=ping_profile,
                    transport_restore_settings=(
                        transport_restore_settings
                        if target_protocol == "none"
                        else None
                    ),
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message, verification_recovered = (
            self.client.verify_changed_cable_protocol(
                source_id=source_id,
                target_id=target_id,
                target_protocol=target_protocol,
                transport_id=transport_id,
                expected_order=expected_order,
                expected_username=username,
                expected_service=service,
                expected_server=server,
                expected_mtu=target_mtu,
                expected_ping_profile=ping_profile,
            )
        )
        transient_recovered = bool(
            verification_recovered
            or (enable_had_transient_failure and verified)
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_cable_protocol_change(
                    source_id=source_id,
                    target_id=target_id,
                    target_protocol=target_protocol,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                    ping_profile=ping_profile,
                    transport_restore_settings=(
                        transport_restore_settings
                        if target_protocol == "none"
                        else None
                    ),
                )
            )
            failure_message = verify_message
            if enable_had_transient_failure and message:
                failure_message = message + ". " + failure_message
            return (
                False,
                failure_message + " Откат: " + rollback_message,
                {
                    "rollback_success": rollback_success,
                    "transient_connection_failure": (
                        enable_had_transient_failure
                    ),
                },
            )

        if current_protocol != "none":
            success, message = self.client.execute_rci_batch(
                [
                    {"parse": f"interface {source_arg} no up"},
                    {"parse": f"no interface {source_arg}"},
                    {"system": {"configuration": {"save": {}}}},
                ],
                operation_name="Не удалось удалить прежний протокол",
            )
            message = safe_message(message)
            if not success:
                removed_after_failure = False
                cleanup_recovered = False

                if self.client.is_transient_rci_failure(message):
                    (
                        removed_after_failure,
                        _cleanup_message,
                        cleanup_recovered,
                    ) = self.client.verify_old_protocol_removed_after_transient_failure(
                        source_id=source_id,
                        target_id=target_id,
                    )

                if removed_after_failure:
                    transient_recovered = True
                    transient_recovered = bool(
                        transient_recovered or cleanup_recovered
                    )
                else:
                    return (
                        False,
                        "Новый протокол уже включён, но прежний интерфейс "
                        "не удалось удалить: " + message,
                        {
                            "partial_success": True,
                            "target_interface_id": target_id,
                            "old_interface_id": source_id,
                            "transient_connection_failure": (
                                self.client.is_transient_rci_failure(message)
                            ),
                        },
                    )

        self.client.traffic_service.clear()
        self.client.ping_check_service.runtime_cache.clear()
        protocol_title = {
            "none": "IPoE",
            "pppoe": "PPPoE",
            "pptp": "PPTP",
            "l2tp": "L2TP",
        }[target_protocol]
        success_message = (
            f"Тип подключения изменён на {protocol_title}. "
            f"Интерфейс: {target_id}; транспорт: {transport_id}; "
            f"приоритет: {expected_order + 1}."
        )
        if transient_recovered:
            success_message += (
                " Во время применения роутер временно закрыл "
                "RCI-соединение; результат подтверждён после "
                "повторного подключения."
            )

        return (
            True,
            success_message,
            {
                "old_interface_id": source_id,
                "interface_id": target_id,
                "transport_interface_id": transport_id,
                "old_protocol": current_protocol,
                "protocol": target_protocol,
                "priority_order": expected_order,
                "transient_recovered": transient_recovered,
            },
        )

