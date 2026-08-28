from __future__ import annotations

from typing import Any

from core.services.wired_connection_dispatcher import WiredConnectionDispatcher


class DedicatedWanService:
    """Создание и удаление физического WAN с проверкой и откатом."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def create_dedicated_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Восстанавливает отдельный WAN из Bridge и настраивает его."""
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        port = settings.get("port")

        if not isinstance(port, dict):
            return False, "Не выбран аппаратный WAN-порт.", {}

        port_id = str(port.get("port_id") or "").strip()
        port_label = str(port.get("port_label") or "WAN").strip()
        transport_id = str(
            port.get("transport_id")
            or port.get("parent_id")
            or ""
        ).strip()
        bridge_id = str(port.get("bridge_id") or "").strip()

        if not port_id or not transport_id or not bridge_id:
            return False, "Неполные данные аппаратного WAN-порта.", {}

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

        options = self.client.build_connection_creation_options(
            interface_data=(runtime if isinstance(runtime, dict) else {}),
            interface_config_data=config,
        )
        current_port = next(
            (
                candidate
                for candidate in options.get("ethernet.cable", {}).get(
                    "ports", []
                )
                if str(candidate.get("port_id") or "") == port_id
                and str(candidate.get("mode") or "") == "dedicated_wan"
            ),
            None,
        )

        if not isinstance(current_port, dict):
            return (
                False,
                "Аппаратный WAN больше не свободен или уже назначен провайдеру.",
                {"preflight_rejected": True},
            )

        conflict = self.client.find_static_ipv4_network_conflict(
            ipv4_settings=settings.get("ipv4"),
            interface_config=config,
            interface_runtime=(runtime if isinstance(runtime, dict) else {}),
        )

        if conflict:
            return (
                False,
                "Статическая сеть "
                f"{conflict['target_network']} уже используется "
                f"интерфейсом {conflict['interface_id']} "
                f"({conflict['network']}).",
                {"preflight_rejected": True},
            )

        old_priorities = self.client.priority_service.current_global_orders(config)
        try:
            requested_order = int(
                settings.get("priority_order", len(old_priorities))
            )
        except (TypeError, ValueError):
            return False, "Некорректный приоритет подключения.", {}

        protocol = settings.get("protocol")
        protocol_type = (
            str(protocol.get("type") or "none")
            if isinstance(protocol, dict)
            else "none"
        ).strip().lower()

        if protocol_type not in {"none", "pppoe", "pptp", "l2tp"}:
            return False, "Выбранный протокол не поддерживается.", {}

        description = str(
            settings.get("description")
            or "Кабельный Интернет — WAN"
        ).strip()

        restored, restore_message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "no interface "
                        + self.client.quote_cli_argument(bridge_id)
                        + " include "
                        + self.client.quote_cli_argument(transport_id)
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось исключить WAN из домашней сети",
        )

        if not restored:
            return False, restore_message, {"rollback_success": True}

        # После исключения из Bridge Keenetic возвращает прежнюю
        # IP-конфигурацию физического WAN. Далее полностью
        # перезаписываем пользовательские параметры мастера.
        interface_settings = WiredConnectionDispatcher.interface_settings(
            settings,
            description=description,
        )
        settings_success, settings_message = self.client.set_interface_settings(
            interface_id=transport_id,
            settings=interface_settings,
        )

        if not settings_success:
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_creation(
                    bridge_id=bridge_id,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось настроить аппаратный WAN: "
                + settings_message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        desired_priorities = self.client.priority_service.priorities_with_inserted_interface(
            old_priorities,
            interface_id=transport_id,
            requested_order=requested_order,
        )
        priority_success, priority_message = self.client.priority_service.set_internet_priorities(
            desired_priorities
        )

        if not priority_success:
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_creation(
                    bridge_id=bridge_id,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось назначить приоритет: "
                + priority_message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        expected_order = desired_priorities[transport_id]
        enabled, enable_message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "interface "
                        + self.client.quote_cli_argument(transport_id)
                        + " up"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить аппаратный WAN",
        )

        if not enabled:
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_creation(
                    bridge_id=bridge_id,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                enable_message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        if protocol_type == "none":
            verified, verify_message = self.client.verify_dedicated_wan_active(
                transport_id=transport_id,
                bridge_id=bridge_id,
                expected_order=expected_order,
            )

            if not verified:
                rollback_success, rollback_message = (
                    self.client.rollback_dedicated_wan_creation(
                        bridge_id=bridge_id,
                        transport_id=transport_id,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    verify_message + " Откат: " + rollback_message,
                    {"rollback_success": rollback_success},
                )

            return (
                True,
                "Аппаратный WAN восстановлен как IPoE. "
                f"Интерфейс: {transport_id}; приоритет: "
                f"{expected_order + 1}.",
                {
                    "interface_id": transport_id,
                    "port_id": port_id,
                    "port_label": port_label,
                    "priority_order": expected_order,
                    "dedicated_wan": True,
                    "protocol": "none",
                },
            )

        protocol_data = protocol if isinstance(protocol, dict) else {}
        change_settings = {
            "target_protocol": protocol_type,
            "description": description,
            "username": str(protocol_data.get("username") or ""),
            "password": str(protocol_data.get("password") or ""),
            "service": str(protocol_data.get("service") or ""),
            "server": str(protocol_data.get("server") or ""),
            "mtu": protocol_data.get(
                "mtu",
                {
                    "pppoe": 1492,
                    "pptp": 1400,
                    "l2tp": 1500,
                }[protocol_type],
            ),
        }
        success, message, details = self.client.change_cable_internet_protocol(
            interface_id=transport_id,
            settings=change_settings,
        )

        if not success:
            target_id = str(
                details.get("target_interface_id")
                or details.get("interface_id")
                or ""
            ) if isinstance(details, dict) else ""
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_creation(
                    bridge_id=bridge_id,
                    transport_id=transport_id,
                    old_priorities=old_priorities,
                    target_interface_id=target_id,
                )
            )
            return (
                False,
                message + " Возврат WAN в LAN: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        result = dict(details)
        result.update(
            {
                "port_id": port_id,
                "port_label": port_label,
                "dedicated_wan": True,
            }
        )
        return True, message, result

    def delete_dedicated_wan(
        self,
        *,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Переводит отдельный физический WAN в домашний LAN."""
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

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

        context, reason = self.client.inspect_dedicated_wan_deletion(
            interface_id=interface_id,
            interface_config=config,
            interface_runtime=(runtime if isinstance(runtime, dict) else {}),
        )

        if not context:
            return False, reason or "Удаление аппаратного WAN недоступно.", {}

        source_id = str(context["interface_id"])
        transport_id = str(context["transport_id"])
        bridge_id = str(context["bridge_id"])
        old_priorities = self.client.priority_service.current_global_orders(config)
        ordered_remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
            if current_id != source_id
        ]
        remaining_priorities = {
            current_id: order
            for order, current_id in enumerate(ordered_remaining)
        }
        source_arg = self.client.quote_cli_argument(source_id)
        ping_profile = str(context.get("ping_profile") or "").strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {"parse": f"interface {source_arg} no up"},
        ]

        if manager_profile:
            prepare_commands.append(
                {"parse": f"interface {source_arg} no ping-check profile"}
            )

        prepare_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name="Не удалось подготовить аппаратный WAN",
        )

        if not success:
            return False, message, {"rollback_success": True}

        # Убираем источник из глобальной политики до перестройки
        # остальных приоритетов. Для прямого IPoE это будет
        # окончательно подтверждено включением транспорта в Bridge.
        success, message = self.client.set_interface_global_enabled(
            source_id,
            False,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        included, include_message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "interface "
                        + self.client.quote_cli_argument(bridge_id)
                        + " include "
                        + self.client.quote_cli_argument(transport_id)
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить WAN-порт в домашнюю сеть",
        )

        if not included:
            rollback_success, rollback_message = (
                self.client.rollback_dedicated_wan_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                include_message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        if remaining_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                remaining_priorities
            )

            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_dedicated_wan_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    "Не удалось перестроить приоритеты: "
                    + message
                    + ". Откат: "
                    + rollback_message,
                    {"rollback_success": rollback_success},
                )

        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if source_id != transport_id and (
            isinstance(current_config, dict)
            and source_id in current_config
        ):
            deleted, delete_message = self.client.execute_rci_batch(
                [
                    {"parse": f"no interface {source_arg}"},
                    {"system": {"configuration": {"save": {}}}},
                ],
                operation_name="Не удалось удалить протокол провайдера",
            )

            if not deleted:
                rollback_success, rollback_message = (
                    self.client.rollback_dedicated_wan_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    delete_message + ". Откат: " + rollback_message,
                    {"rollback_success": rollback_success},
                )

        verified, verify_message = self.client.verify_dedicated_wan_deleted(
            source_id=source_id,
            transport_id=transport_id,
            bridge_id=bridge_id,
            remaining_priorities=remaining_priorities,
        )

        if not verified:
            return (
                False,
                verify_message,
                {
                    "partial_success": True,
                    "rollback_success": False,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(profile_config, ping_profile):
                success, cleanup_warning = self.client.execute_rci_batch(
                    [
                        {
                            "parse": (
                                "no ping-check profile "
                                + self.client.quote_cli_argument(ping_profile)
                            )
                        },
                        {"system": {"configuration": {"save": {}}}},
                    ],
                    operation_name="Не удалось удалить профиль Ping Check",
                )

                if success:
                    cleanup_warning = ""

        self.client.traffic_service.clear()
        self.client.ping_check_service.runtime_cache.clear()
        message = (
            "Подключение удалено. Физический WAN-порт включён "
            f"в домашнюю сеть {bridge_id} и теперь работает как LAN0."
        )

        if cleanup_warning:
            message += " Профиль Ping Check не очищен: " + cleanup_warning

        return (
            True,
            message,
            {
                "interface_id": source_id,
                "transport_interface_id": transport_id,
                "bridge_id": bridge_id,
                "port_id": context["port_id"],
                "port_label": "WAN",
                "port_number": context.get("port_number", "0"),
                "dedicated_wan": True,
                "cleanup_warning": cleanup_warning,
            },
        )

