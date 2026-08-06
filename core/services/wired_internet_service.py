from __future__ import annotations

from typing import Any


class WiredInternetService:
    """
    Создание, изменение и удаление проводных интернет-подключений.

    В сервис физически перенесены большие операции IPoE, PPPoE,
    PPTP, L2TP и аппаратного WAN. Остальные низкоуровневые помощники
    пока доступны через совместимый объект KeeneticClient.
    """

    SUPPORTED_PROTOCOLS = frozenset(
        {"none", "ipoe", "pppoe", "pptp", "l2tp"}
    )

    def __init__(self, client: Any) -> None:
        self.client = client

    def _deletion_priorities(
        self,
        interface_config: dict[str, Any],
        interface_id: str,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        old_priorities = (
            self.client.priority_service.current_global_orders(
                interface_config
            )
        )
        ordered_remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
            if current_id != interface_id
        ]
        remaining_priorities = {
            current_id: order
            for order, current_id in enumerate(ordered_remaining)
        }
        pre_delete_priorities = dict(remaining_priorities)
        pre_delete_priorities[interface_id] = len(remaining_priorities)
        return (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        )

    def _forget_interfaces(self, *interface_ids: str) -> None:
        for interface_id in interface_ids:
            self.client._manually_disabled_interfaces.discard(interface_id)
            self.client._traffic_counters.pop(interface_id, None)
            self.client.ping_check_service.runtime_cache.pop(
                interface_id,
                None,
            )

    @staticmethod
    def protocol_type(settings: dict[str, Any]) -> str:
        protocol = settings.get("protocol")

        if not isinstance(protocol, dict):
            return "none"

        value = str(
            protocol.get("type")
            or "none"
        ).strip().lower()

        return value or "none"

    @classmethod
    def connection_type(
        cls,
        settings: dict[str, Any],
    ) -> str:
        protocol = cls.protocol_type(settings)

        return {
            "pppoe": "ethernet.pppoe",
            "pptp": "ethernet.pptp",
            "l2tp": "ethernet.l2tp",
        }.get(protocol, "ethernet.ipoe")

    def create_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        port = settings.get("port")

        if (
            isinstance(port, dict)
            and str(port.get("mode") or "").strip().lower()
            == "dedicated_wan"
        ):
            return self.client.create_dedicated_cable_internet(settings)

        protocol = settings.get("protocol")
        protocol_type = (
            str(protocol.get("type") or "none")
            if isinstance(protocol, dict)
            else "none"
        ).strip().lower()

        if protocol_type in {"", "none", "ipoe"}:
            ipoe_settings = dict(settings)
            ipoe_settings.pop("protocol", None)
            return self.client.create_wired_ipoe(ipoe_settings)

        if protocol_type == "pppoe":
            return self.client.create_wired_pppoe(settings)

        if protocol_type == "pptp":
            return self.client.create_wired_pptp(settings)

        if protocol_type == "l2tp":
            return self.client.create_wired_l2tp(settings)

        return (
            False,
            (
                f"Создание протокола {protocol_type.upper()} "
                "пока не поддерживается."
            ),
            {"preflight_rejected": True},
        )

    @staticmethod
    def cable_settings_for_interface(
        settings: dict[str, Any],
        *,
        description: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "description": description,
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(
                settings.get("ipv4")
                or {"dhcp": True}
            ),
            "use_provider_dns": bool(
                settings.get("use_provider_dns", True)
            ),
            "manual_dns": list(
                settings.get("manual_dns") or []
            ),
            "mac": dict(
                settings.get("mac")
                or {"mode": "factory"}
            ),
            "ping_check": dict(
                settings.get("ping_check")
                or {"mode": "disabled"}
            ),
        }

        if result["ipv4"].get("dhcp"):
            result["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        return result

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
        interface_settings = self.client.cable_settings_for_interface(
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

        self.client._traffic_counters.clear()
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

    def create_wired_pppoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        if not isinstance(settings, dict):
            return False, "Параметры создания переданы неверно.", {}

        protocol = settings.get("protocol")

        if not isinstance(protocol, dict):
            return False, "Параметры PPPoE не переданы.", {}

        username = str(protocol.get("username") or "").strip()
        password = str(protocol.get("password") or "")
        service = str(protocol.get("service") or "").strip()

        try:
            ppp_mtu = int(protocol.get("mtu", 1492))
        except (TypeError, ValueError):
            return False, "Некорректный MTU PPPoE.", {}

        if not username or not password:
            return (
                False,
                "Для PPPoE нужны имя пользователя и пароль.",
                {"preflight_rejected": True},
            )

        if len(username) > 128:
            return (
                False,
                "Имя пользователя PPPoE слишком длинное.",
                {"preflight_rejected": True},
            )

        if len(password) > 256:
            return (
                False,
                "Пароль PPPoE слишком длинный.",
                {"preflight_rejected": True},
            )

        if len(service) > 64:
            return (
                False,
                "Имя сервиса PPPoE слишком длинное.",
                {"preflight_rejected": True},
            )

        if not 576 <= ppp_mtu <= 1492:
            return False, "MTU PPPoE должен быть от 576 до 1492.", {}

        if any(
            character in "\r\n\x00"
            for character in username + password + service
        ):
            return (
                False,
                "Параметры PPPoE содержат недопустимые символы.",
                {"preflight_rejected": True},
            )

        port = settings.get("port")

        if not isinstance(port, dict):
            return False, "Не выбран Ethernet-порт.", {}

        port_id = str(port.get("port_id") or "").strip()
        port_label = str(port.get("port_label") or "").strip()
        parent_id = str(port.get("parent_id") or "").strip()

        if not port_id or not port_label or not parent_id:
            return False, "Неполные данные Ethernet-порта.", {}

        runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
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
                f"({conflict['network']}). Конфигурация не изменялась.",
                {
                    "rollback_success": True,
                    "preflight_rejected": True,
                },
            )

        options = self.client.build_connection_creation_options(
            interface_data=(runtime if isinstance(runtime, dict) else {}),
            interface_config_data=config,
        )
        current_ports = options.get("ethernet.cable", {}).get(
            "ports",
            [],
        )
        current_port = next(
            (
                item
                for item in current_ports
                if str(item.get("port_id") or "") == port_id
            ),
            None,
        )

        if not isinstance(current_port, dict):
            return (
                False,
                f"Порт {port_label} больше не свободен.",
                {"preflight_rejected": True},
            )


        port_config = config.get(port_id)

        if not isinstance(port_config, dict):
            return False, "Конфигурация выбранного порта не найдена.", {}

        vlan_id = self.client.choose_internal_vlan_id(config)

        if vlan_id is None:
            return False, "Не удалось подобрать внутренний VLAN.", {}

        transport_id = f"{parent_id}/Vlan{vlan_id}"
        pppoe_id = self.client.next_numbered_interface_id(config, "PPPoE")
        old_access_vid = self.client.extract_port_access_vid(port_config)
        old_switch_mode = self.client.extract_port_switch_mode(port_config)
        old_roles = self.client.extract_port_roles(port_config)
        old_priorities = self.client.priority_service.current_global_orders(config)

        try:
            priority_order = int(
                settings.get("priority_order", len(old_priorities))
            )
        except (TypeError, ValueError):
            return False, "Некорректный приоритет подключения.", {}

        priority_order = max(
            0,
            min(len(old_priorities), priority_order),
        )
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(port_id)
        pppoe_arg = self.client.quote_cli_argument(pppoe_id)

        skeleton_commands = [
            {"parse": f"interface {transport_arg}"},
            {"parse": f"interface {transport_arg} no up"},
            {
                "parse": (
                    f"interface {transport_arg} "
                    "security-level public"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport mode access"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport access vlan "
                    f"{vlan_id}"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} role inet for "
                    f"{transport_arg}"
                )
            },
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            skeleton_commands,
            operation_name="Не удалось создать транспорт PPPoE",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_wired_ipoe_skeleton(
            interface_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                verify_message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        description = str(
            settings.get("description")
            or f"PPPoE-подключение — порт {port_label}"
        ).strip()
        transport_settings: dict[str, Any] = {
            "description": f"Транспорт — {description}",
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(settings.get("ipv4") or {"dhcp": True}),
            "use_provider_dns": False,
            "manual_dns": [],
            "mac": dict(settings.get("mac") or {"mode": "factory"}),
            "ping_check": {"mode": "disabled"},
        }

        if transport_settings["ipv4"].get("dhcp"):
            transport_settings["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        success, message = self.client.set_interface_settings(
            interface_id=transport_id,
            settings=transport_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось настроить транспорт PPPoE: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        pppoe_commands = [
            {"parse": f"interface {pppoe_arg}"},
            {"parse": f"interface {pppoe_arg} no up"},
            {
                "parse": (
                    f"interface {pppoe_arg} description "
                    + self.client.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {pppoe_arg} security-level public"
                )
            },
            {"parse": f"interface {pppoe_arg} role inet"},
            {
                "parse": (
                    f"interface {pppoe_arg} authentication identity "
                    + self.client.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {pppoe_arg} authentication password "
                    + self.client.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {pppoe_arg} no ccp"},
            {"parse": f"interface {pppoe_arg} lcp echo 30 3"},
            {"parse": f"interface {pppoe_arg} ipcp address"},
            {"parse": f"interface {pppoe_arg} ipcp default-route"},
            {"parse": f"interface {pppoe_arg} ipcp dns-routes"},
            {"parse": f"interface {pppoe_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {pppoe_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {pppoe_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]

        if service:
            pppoe_commands.append(
                {
                    "parse": (
                        f"interface {pppoe_arg} pppoe service "
                        + self.client.quote_cli_argument(service)
                    )
                }
            )

        pppoe_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            pppoe_commands,
            operation_name="Не удалось создать PPPoE",
        )
        message = self.client.redact_secrets_from_text(
            message,
            password,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        pppoe_settings = {
            "description": description,
            "mtu": ppp_mtu,
            "use_provider_dns": bool(
                settings.get("use_provider_dns", True)
            ),
            "manual_dns": list(settings.get("manual_dns") or []),
            "ping_check": dict(
                settings.get("ping_check")
                or {"mode": "disabled"}
            ),
        }
        success, message = self.client.set_interface_settings(
            interface_id=pppoe_id,
            settings=pppoe_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось применить параметры PPPoE: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        ordered = [
            interface_id
            for interface_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
        ]
        ordered.insert(priority_order, pppoe_id)
        new_priorities = {
            interface_id: order
            for order, interface_id in enumerate(ordered)
        }
        success, message = self.client.priority_service.set_internet_priorities(
            new_priorities
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось назначить приоритет PPPoE: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        success, message = self.client.execute_rci_batch(
            [
                {"parse": f"interface {transport_arg} up"},
                {"parse": f"interface {pppoe_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить PPPoE",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_created_wired_pppoe(
            pppoe_id=pppoe_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=priority_order,
            expected_identity=username,
            expected_service=service,
            expected_mtu=ppp_mtu,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_creation(
                    pppoe_id=pppoe_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
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
            "PPPoE создано и сохранено. "
            f"Порт: {port_label}; интерфейс: {pppoe_id}; "
            f"транспорт: {transport_id}; "
            f"приоритет: {priority_order + 1}.",
            {
                "interface_id": pppoe_id,
                "transport_interface_id": transport_id,
                "port_id": port_id,
                "port_label": port_label,
                "vlan_id": vlan_id,
                "priority_order": priority_order,
                "protocol": "pppoe",
            },
        )

    def create_wired_pptp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        if not isinstance(settings, dict):
            return False, "Параметры создания переданы неверно.", {}

        protocol = settings.get("protocol")

        if not isinstance(protocol, dict):
            return False, "Параметры PPTP не переданы.", {}

        username = str(protocol.get("username") or "").strip()
        password = str(protocol.get("password") or "")
        server = str(protocol.get("server") or "").strip()

        try:
            ppp_mtu = int(protocol.get("mtu", 1400))
        except (TypeError, ValueError):
            return False, "Некорректный MTU PPTP.", {}

        if not username or not password or not server:
            return (
                False,
                "Для PPTP нужны адрес сервера, имя пользователя "
                "и пароль.",
                {"preflight_rejected": True},
            )

        server = self.client.normalize_host(server)

        if (
            not server
            or len(server) > 253
            or any(character.isspace() for character in server)
            or "/" in server
        ):
            return (
                False,
                "Укажите корректный адрес PPTP-сервера.",
                {"preflight_rejected": True},
            )

        if len(username) > 128:
            return (
                False,
                "Имя пользователя PPTP слишком длинное.",
                {"preflight_rejected": True},
            )

        if len(password) > 256:
            return (
                False,
                "Пароль PPTP слишком длинный.",
                {"preflight_rejected": True},
            )

        if len(server) > 253:
            return (
                False,
                "Адрес PPTP-сервера слишком длинный.",
                {"preflight_rejected": True},
            )

        if not 576 <= ppp_mtu <= 1492:
            return False, "MTU PPTP должен быть от 576 до 1492.", {}

        if any(
            character in "\r\n\x00"
            for character in username + password + server
        ):
            return (
                False,
                "Параметры PPTP содержат недопустимые символы.",
                {"preflight_rejected": True},
            )

        port = settings.get("port")

        if not isinstance(port, dict):
            return False, "Не выбран Ethernet-порт.", {}

        port_id = str(port.get("port_id") or "").strip()
        port_label = str(port.get("port_label") or "").strip()
        parent_id = str(port.get("parent_id") or "").strip()

        if not port_id or not port_label or not parent_id:
            return False, "Неполные данные Ethernet-порта.", {}

        runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
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
                f"({conflict['network']}). Конфигурация не изменялась.",
                {
                    "rollback_success": True,
                    "preflight_rejected": True,
                },
            )

        options = self.client.build_connection_creation_options(
            interface_data=(runtime if isinstance(runtime, dict) else {}),
            interface_config_data=config,
        )
        current_ports = options.get("ethernet.cable", {}).get(
            "ports",
            [],
        )
        current_port = next(
            (
                item
                for item in current_ports
                if str(item.get("port_id") or "") == port_id
            ),
            None,
        )

        if not isinstance(current_port, dict):
            return (
                False,
                f"Порт {port_label} больше не свободен.",
                {"preflight_rejected": True},
            )


        port_config = config.get(port_id)

        if not isinstance(port_config, dict):
            return False, "Конфигурация выбранного порта не найдена.", {}

        vlan_id = self.client.choose_internal_vlan_id(config)

        if vlan_id is None:
            return False, "Не удалось подобрать внутренний VLAN.", {}

        transport_id = f"{parent_id}/Vlan{vlan_id}"
        pptp_id = self.client.next_numbered_interface_id(config, "PPTP")
        old_access_vid = self.client.extract_port_access_vid(port_config)
        old_switch_mode = self.client.extract_port_switch_mode(port_config)
        old_roles = self.client.extract_port_roles(port_config)
        old_priorities = self.client.priority_service.current_global_orders(config)

        try:
            priority_order = int(
                settings.get("priority_order", len(old_priorities))
            )
        except (TypeError, ValueError):
            return False, "Некорректный приоритет подключения.", {}

        priority_order = max(
            0,
            min(len(old_priorities), priority_order),
        )
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(port_id)
        pptp_arg = self.client.quote_cli_argument(pptp_id)

        skeleton_commands = [
            {"parse": f"interface {transport_arg}"},
            {"parse": f"interface {transport_arg} no up"},
            {
                "parse": (
                    f"interface {transport_arg} "
                    "security-level public"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport mode access"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport access vlan "
                    f"{vlan_id}"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} role inet for "
                    f"{transport_arg}"
                )
            },
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            skeleton_commands,
            operation_name="Не удалось создать транспорт PPTP",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_wired_ipoe_skeleton(
            interface_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                verify_message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        description = str(
            settings.get("description")
            or f"PPTP-подключение — порт {port_label}"
        ).strip()
        transport_settings: dict[str, Any] = {
            "description": f"Транспорт — {description}",
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(settings.get("ipv4") or {"dhcp": True}),
            "use_provider_dns": False,
            "manual_dns": [],
            "mac": dict(settings.get("mac") or {"mode": "factory"}),
            "ping_check": {"mode": "disabled"},
        }

        if transport_settings["ipv4"].get("dhcp"):
            transport_settings["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        success, message = self.client.set_interface_settings(
            interface_id=transport_id,
            settings=transport_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось настроить транспорт PPTP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        pptp_commands = [
            {"parse": f"interface {pptp_arg}"},
            {"parse": f"interface {pptp_arg} no up"},
            {
                "parse": (
                    f"interface {pptp_arg} description "
                    + self.client.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {pptp_arg} security-level public"
                )
            },
            {"parse": f"interface {pptp_arg} role inet"},
            {
                "parse": (
                    f"interface {pptp_arg} authentication identity "
                    + self.client.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {pptp_arg} authentication password "
                    + self.client.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {pptp_arg} no ccp"},
            {"parse": f"interface {pptp_arg} lcp echo 30 3"},
            {"parse": f"interface {pptp_arg} ipcp address"},
            {"parse": f"interface {pptp_arg} ipcp default-route"},
            {"parse": f"interface {pptp_arg} ipcp dns-routes"},
            {"parse": f"interface {pptp_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {pptp_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {pptp_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]

        pptp_commands.append(
            {
                "parse": (
                    f"interface {pptp_arg} peer "
                    + self.client.quote_cli_argument(server)
                )
            }
        )

        pptp_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            pptp_commands,
            operation_name="Не удалось создать PPTP",
        )
        message = self.client.redact_secrets_from_text(
            message,
            password,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        pptp_settings = {
            "description": description,
            "mtu": ppp_mtu,
            "use_provider_dns": bool(
                settings.get("use_provider_dns", True)
            ),
            "manual_dns": list(settings.get("manual_dns") or []),
            "ping_check": dict(
                settings.get("ping_check")
                or {"mode": "disabled"}
            ),
        }
        success, message = self.client.set_interface_settings(
            interface_id=pptp_id,
            settings=pptp_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось применить параметры PPTP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        ordered = [
            interface_id
            for interface_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
        ]
        ordered.insert(priority_order, pptp_id)
        new_priorities = {
            interface_id: order
            for order, interface_id in enumerate(ordered)
        }
        success, message = self.client.priority_service.set_internet_priorities(
            new_priorities
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось назначить приоритет PPTP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        success, message = self.client.execute_rci_batch(
            [
                {"parse": f"interface {transport_arg} up"},
                {"parse": f"interface {pptp_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить PPTP",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_created_wired_pptp(
            pptp_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=priority_order,
            expected_identity=username,
            expected_server=server,
            expected_mtu=ppp_mtu,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_creation(
                    pptp_id=pptp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
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
            "PPTP создано и сохранено. "
            f"Порт: {port_label}; интерфейс: {pptp_id}; "
            f"транспорт: {transport_id}; "
            f"приоритет: {priority_order + 1}.",
            {
                "interface_id": pptp_id,
                "transport_interface_id": transport_id,
                "port_id": port_id,
                "port_label": port_label,
                "vlan_id": vlan_id,
                "priority_order": priority_order,
                "protocol": "pptp",
            },
        )

    def create_wired_l2tp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        if not isinstance(settings, dict):
            return False, "Параметры создания переданы неверно.", {}

        protocol = settings.get("protocol")

        if not isinstance(protocol, dict):
            return False, "Параметры L2TP не переданы.", {}

        username = str(protocol.get("username") or "").strip()
        password = str(protocol.get("password") or "")
        server = str(protocol.get("server") or "").strip()

        try:
            ppp_mtu = int(protocol.get("mtu", 1500))
        except (TypeError, ValueError):
            return False, "Некорректный MTU L2TP.", {}

        if not username or not password or not server:
            return (
                False,
                "Для L2TP нужны адрес сервера, имя пользователя "
                "и пароль.",
                {"preflight_rejected": True},
            )

        server = self.client.normalize_host(server)

        if (
            not server
            or len(server) > 253
            or any(character.isspace() for character in server)
            or "/" in server
        ):
            return (
                False,
                "Укажите корректный адрес L2TP-сервера.",
                {"preflight_rejected": True},
            )

        if len(username) > 128:
            return (
                False,
                "Имя пользователя L2TP слишком длинное.",
                {"preflight_rejected": True},
            )

        if len(password) > 256:
            return (
                False,
                "Пароль L2TP слишком длинный.",
                {"preflight_rejected": True},
            )

        if len(server) > 253:
            return (
                False,
                "Адрес L2TP-сервера слишком длинный.",
                {"preflight_rejected": True},
            )

        if not 576 <= ppp_mtu <= 1500:
            return False, "MTU L2TP должен быть от 576 до 1500.", {}

        if any(
            character in "\r\n\x00"
            for character in username + password + server
        ):
            return (
                False,
                "Параметры L2TP содержат недопустимые символы.",
                {"preflight_rejected": True},
            )

        port = settings.get("port")

        if not isinstance(port, dict):
            return False, "Не выбран Ethernet-порт.", {}

        port_id = str(port.get("port_id") or "").strip()
        port_label = str(port.get("port_label") or "").strip()
        parent_id = str(port.get("parent_id") or "").strip()

        if not port_id or not port_label or not parent_id:
            return False, "Неполные данные Ethernet-порта.", {}

        runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
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
                f"({conflict['network']}). Конфигурация не изменялась.",
                {
                    "rollback_success": True,
                    "preflight_rejected": True,
                },
            )

        options = self.client.build_connection_creation_options(
            interface_data=(runtime if isinstance(runtime, dict) else {}),
            interface_config_data=config,
        )
        current_ports = options.get("ethernet.cable", {}).get(
            "ports",
            [],
        )
        current_port = next(
            (
                item
                for item in current_ports
                if str(item.get("port_id") or "") == port_id
            ),
            None,
        )

        if not isinstance(current_port, dict):
            return (
                False,
                f"Порт {port_label} больше не свободен.",
                {"preflight_rejected": True},
            )


        port_config = config.get(port_id)

        if not isinstance(port_config, dict):
            return False, "Конфигурация выбранного порта не найдена.", {}

        vlan_id = self.client.choose_internal_vlan_id(config)

        if vlan_id is None:
            return False, "Не удалось подобрать внутренний VLAN.", {}

        transport_id = f"{parent_id}/Vlan{vlan_id}"
        l2tp_id = self.client.next_numbered_interface_id(config, "L2TP")
        old_access_vid = self.client.extract_port_access_vid(port_config)
        old_switch_mode = self.client.extract_port_switch_mode(port_config)
        old_roles = self.client.extract_port_roles(port_config)
        old_priorities = self.client.priority_service.current_global_orders(config)

        try:
            priority_order = int(
                settings.get("priority_order", len(old_priorities))
            )
        except (TypeError, ValueError):
            return False, "Некорректный приоритет подключения.", {}

        priority_order = max(
            0,
            min(len(old_priorities), priority_order),
        )
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(port_id)
        l2tp_arg = self.client.quote_cli_argument(l2tp_id)

        skeleton_commands = [
            {"parse": f"interface {transport_arg}"},
            {"parse": f"interface {transport_arg} no up"},
            {
                "parse": (
                    f"interface {transport_arg} "
                    "security-level public"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport mode access"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} switchport access vlan "
                    f"{vlan_id}"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} role inet for "
                    f"{transport_arg}"
                )
            },
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            skeleton_commands,
            operation_name="Не удалось создать транспорт L2TP",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_wired_ipoe_skeleton(
            interface_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                verify_message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        description = str(
            settings.get("description")
            or f"L2TP-подключение — порт {port_label}"
        ).strip()
        transport_settings: dict[str, Any] = {
            "description": f"Транспорт — {description}",
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(settings.get("ipv4") or {"dhcp": True}),
            "use_provider_dns": False,
            "manual_dns": [],
            "mac": dict(settings.get("mac") or {"mode": "factory"}),
            "ping_check": {"mode": "disabled"},
        }

        if transport_settings["ipv4"].get("dhcp"):
            transport_settings["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        success, message = self.client.set_interface_settings(
            interface_id=transport_id,
            settings=transport_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось настроить транспорт L2TP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        l2tp_commands = [
            {"parse": f"interface {l2tp_arg}"},
            {"parse": f"interface {l2tp_arg} no up"},
            {
                "parse": (
                    f"interface {l2tp_arg} description "
                    + self.client.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {l2tp_arg} security-level public"
                )
            },
            {"parse": f"interface {l2tp_arg} role inet"},
            {
                "parse": (
                    f"interface {l2tp_arg} authentication identity "
                    + self.client.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {l2tp_arg} authentication password "
                    + self.client.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {l2tp_arg} no ccp"},
            {"parse": f"interface {l2tp_arg} lcp echo 30 3"},
            {"parse": f"interface {l2tp_arg} ipcp address"},
            {"parse": f"interface {l2tp_arg} ipcp default-route"},
            {"parse": f"interface {l2tp_arg} ipcp dns-routes"},
            {"parse": f"interface {l2tp_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {l2tp_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {l2tp_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]

        l2tp_commands.append(
            {
                "parse": (
                    f"interface {l2tp_arg} peer "
                    + self.client.quote_cli_argument(server)
                )
            }
        )

        l2tp_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            l2tp_commands,
            operation_name="Не удалось создать L2TP",
        )
        message = self.client.redact_secrets_from_text(
            message,
            password,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        l2tp_settings = {
            "description": description,
            "mtu": ppp_mtu,
            "use_provider_dns": bool(
                settings.get("use_provider_dns", True)
            ),
            "manual_dns": list(settings.get("manual_dns") or []),
            "ping_check": dict(
                settings.get("ping_check")
                or {"mode": "disabled"}
            ),
        }
        success, message = self.client.set_interface_settings(
            interface_id=l2tp_id,
            settings=l2tp_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось применить параметры L2TP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        ordered = [
            interface_id
            for interface_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
        ]
        ordered.insert(priority_order, l2tp_id)
        new_priorities = {
            interface_id: order
            for order, interface_id in enumerate(ordered)
        }
        success, message = self.client.priority_service.set_internet_priorities(
            new_priorities
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                "Не удалось назначить приоритет L2TP: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        success, message = self.client.execute_rci_batch(
            [
                {"parse": f"interface {transport_arg} up"},
                {"parse": f"interface {l2tp_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить L2TP",
        )

        if not success:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                message + ". Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        verified, verify_message = self.client.verify_created_wired_l2tp(
            l2tp_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=priority_order,
            expected_identity=username,
            expected_server=server,
            expected_mtu=ppp_mtu,
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_creation(
                    l2tp_id=l2tp_id,
                    transport_id=transport_id,
                    port_id=port_id,
                    old_access_vid=old_access_vid,
                    old_switch_mode=old_switch_mode,
                    old_roles=old_roles,
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
            "L2TP создано и сохранено. "
            f"Порт: {port_label}; интерфейс: {l2tp_id}; "
            f"транспорт: {transport_id}; "
            f"приоритет: {priority_order + 1}.",
            {
                "interface_id": l2tp_id,
                "transport_interface_id": transport_id,
                "port_id": port_id,
                "port_label": port_label,
                "vlan_id": vlan_id,
                "priority_order": priority_order,
                "protocol": "l2tp",
            },
        )

    def create_wired_ipoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Создаёт проводной IPoE на свободном порту коммутатора.

        Операция выполняется поэтапно:
        1. создаётся выключенный VLAN-интерфейс и назначается порт;
        2. применяются параметры подключения;
        3. задаётся приоритет и интерфейс включается;
        4. результат проверяется через show/sc/interface.

        При ошибке порт и приоритеты возвращаются в исходное
        состояние, а незавершённый интерфейс удаляется.
        """
        if self.client.session is None or self.client.base_url is None:
            return (
                False,
                "Нет активной сессии с роутером.",
                {},
            )

        if not isinstance(settings, dict):
            return (
                False,
                "Параметры создания переданы неверно.",
                {},
            )

        port = settings.get("port")

        if not isinstance(port, dict):
            return (
                False,
                "Не выбран Ethernet-порт.",
                {},
            )

        port_id = str(
            port.get("port_id")
            or ""
        ).strip()
        port_label = str(
            port.get("port_label")
            or ""
        ).strip()
        parent_id = str(
            port.get("parent_id")
            or ""
        ).strip()

        if (
            not port_id
            or not port_label
            or not parent_id
        ):
            return (
                False,
                "Неполные данные Ethernet-порта.",
                {},
            )

        current_interface_data = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        conflict = self.client.find_static_ipv4_network_conflict(
            ipv4_settings=settings.get("ipv4"),
            interface_config=current_config,
            interface_runtime=(
                current_interface_data
                if isinstance(
                    current_interface_data,
                    dict,
                )
                else {}
            ),
        )

        if conflict:
            return (
                False,
                (
                    "Статическая сеть "
                    f"{conflict['target_network']} уже используется "
                    "включённым интерфейсом "
                    f"{conflict['interface_id']} "
                    f"({conflict['network']}). Выключите этот "
                    "интерфейс или выберите другую подсеть. "
                    "Конфигурация порта не изменялась."
                ),
                {
                    "rollback_success": True,
                    "preflight_rejected": True,
                },
            )

        options = self.client.build_connection_creation_options(
            interface_data=(
                current_interface_data
                if isinstance(
                    current_interface_data,
                    dict,
                )
                else {}
            ),
            interface_config_data=current_config,
        )
        current_ports = (
            options.get(
                "ethernet.ipoe",
                {},
            ).get(
                "ports",
                [],
            )
        )

        current_port = next(
            (
                item
                for item in current_ports
                if str(
                    item.get("port_id")
                    or ""
                ) == port_id
            ),
            None,
        )

        if not isinstance(current_port, dict):
            return (
                False,
                (
                    f"Порт {port_label} больше не свободен "
                    "или не поддерживается мастером."
                ),
                {},
            )


        port_config = current_config.get(
            port_id
        )

        if not isinstance(port_config, dict):
            return (
                False,
                "Конфигурация выбранного порта не найдена.",
                {},
            )

        vlan_id = self.client.choose_internal_vlan_id(
            current_config
        )

        if vlan_id is None:
            return (
                False,
                "Не удалось подобрать внутренний VLAN.",
                {},
            )

        interface_id = (
            f"{parent_id}/Vlan{vlan_id}"
        )

        if interface_id in current_config:
            return (
                False,
                (
                    "Выбранный служебный интерфейс уже "
                    "существует. Обновите данные роутера."
                ),
                {},
            )

        old_access_vid = (
            self.client.extract_port_access_vid(
                port_config
            )
        )
        old_switch_mode = (
            self.client.extract_port_switch_mode(
                port_config
            )
        )
        old_roles = self.client.extract_port_roles(
            port_config
        )
        old_priorities = self.client.priority_service.current_global_orders(
            current_config
        )

        priority_order = settings.get(
            "priority_order",
            len(old_priorities),
        )

        try:
            priority_order = int(
                priority_order
            )
        except (TypeError, ValueError):
            return (
                False,
                "Некорректный приоритет подключения.",
                {},
            )

        priority_order = max(
            0,
            min(
                len(old_priorities),
                priority_order,
            ),
        )

        port_arg = self.client.quote_cli_argument(
            port_id
        )
        interface_arg = self.client.quote_cli_argument(
            interface_id
        )

        skeleton_commands = [
            {
                "parse": (
                    f"interface {interface_arg}"
                )
            },
            {
                "parse": (
                    f"interface {interface_arg} no up"
                )
            },
            {
                "parse": (
                    f"interface {interface_arg} "
                    "security-level public"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} "
                    "switchport mode access"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} "
                    "switchport access vlan "
                    f"{vlan_id}"
                )
            },
            {
                "parse": (
                    f"interface {port_arg} "
                    "role inet for "
                    f"{interface_arg}"
                )
            },
            {
                "system": {
                    "configuration": {
                        "save": {},
                    }
                }
            },
        ]

        success, message = self.client.execute_rci_batch(
            skeleton_commands,
            operation_name=(
                "Не удалось создать каркас IPoE"
            ),
        )

        if not success:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            suffix = (
                f" Откат: {rollback_message}"
                if rollback_message
                else ""
            )
            return (
                False,
                message + suffix,
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        verified, verify_message = (
            self.client.verify_wired_ipoe_skeleton(
                interface_id=interface_id,
                port_id=port_id,
                vlan_id=vlan_id,
            )
        )

        if not verified:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            return (
                False,
                (
                    verify_message
                    + f" Откат: {rollback_message}"
                ),
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        interface_settings: dict[str, Any] = {
            "description": str(
                settings.get("description")
                or (
                    "Проводное подключение "
                    f"— порт {port_label}"
                )
            ).strip(),
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(
                settings.get("ipv4")
                or {"dhcp": True}
            ),
            "use_provider_dns": bool(
                settings.get(
                    "use_provider_dns",
                    True,
                )
            ),
            "manual_dns": list(
                settings.get("manual_dns")
                or []
            ),
            "mac": dict(
                settings.get("mac")
                or {"mode": "factory"}
            ),
            "ping_check": dict(
                settings.get("ping_check")
                or {"mode": "disabled"}
            ),
        }

        if interface_settings["ipv4"].get(
            "dhcp"
        ):
            interface_settings[
                "dhcp_client"
            ] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        settings_success, settings_message = (
            self.client.set_interface_settings(
                interface_id=interface_id,
                settings=interface_settings,
            )
        )

        if not settings_success:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            return (
                False,
                (
                    "Не удалось настроить новое IPoE: "
                    f"{settings_message}. "
                    f"Откат: {rollback_message}"
                ),
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        ordered_existing = [
            interface
            for interface, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
        ]
        ordered_existing.insert(
            priority_order,
            interface_id,
        )
        new_priorities = {
            interface: order
            for order, interface in enumerate(
                ordered_existing
            )
        }

        priority_success, priority_message = (
            self.client.priority_service.set_internet_priorities(
                new_priorities
            )
        )

        if not priority_success:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            return (
                False,
                (
                    "Не удалось назначить приоритет: "
                    f"{priority_message}. "
                    f"Откат: {rollback_message}"
                ),
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        enable_success, enable_message = (
            self.client.execute_rci_batch(
                [
                    {
                        "parse": (
                            f"interface {interface_arg} up"
                        )
                    },
                    {
                        "system": {
                            "configuration": {
                                "save": {},
                            }
                        }
                    },
                ],
                operation_name=(
                    "Не удалось включить новое IPoE"
                ),
            )
        )

        if not enable_success:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            return (
                False,
                (
                    f"{enable_message}. "
                    f"Откат: {rollback_message}"
                ),
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        final_verified, final_message = (
            self.client.verify_created_wired_ipoe(
                interface_id=interface_id,
                port_id=port_id,
                vlan_id=vlan_id,
                expected_order=priority_order,
            )
        )

        if not final_verified:
            (
                rollback_success,
                rollback_message,
            ) = self.client.rollback_wired_ipoe_creation(
                interface_id=interface_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
            return (
                False,
                (
                    f"{final_message} "
                    f"Откат: {rollback_message}"
                ),
                {
                    "rollback_success": (
                        rollback_success
                    )
                },
            )

        return (
            True,
            (
                "Проводное IPoE создано и сохранено. "
                f"Порт: {port_label}; "
                f"интерфейс: {interface_id}; "
                f"приоритет: {priority_order + 1}."
            ),
            {
                "interface_id": interface_id,
                "port_id": port_id,
                "port_label": port_label,
                "vlan_id": vlan_id,
                "priority_order": priority_order,
            },
        )

    def change_cable_internet_protocol(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

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
                        f"interface "
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

        self.client._traffic_counters.clear()
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

    def delete_wired_pppoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указан интерфейс PPPoE.", {}

        current_runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        context, reason = self.client.inspect_wired_pppoe_deletion(
            interface_id=name,
            interface_config=current_config,
            interface_runtime=current_runtime,
        )

        if not context:
            return (
                False,
                reason
                or "Это PPPoE нельзя безопасно удалить автоматически.",
                {},
            )

        transport_id = str(context["transport_id"])
        (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        ) = self._deletion_priorities(current_config, name)

        pppoe_arg = self.client.quote_cli_argument(name)
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(str(context["port_id"]))
        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {"parse": f"interface {pppoe_arg} no up"},
            {"parse": f"interface {transport_arg} no up"},
        ]

        if manager_profile:
            prepare_commands.append(
                {
                    "parse": (
                        f"interface {pppoe_arg} "
                        "no ping-check profile"
                    )
                }
            )

        prepare_commands.extend(
            (
                {"parse": f"interface {port_arg} no role"},
                {
                    "parse": (
                        f"interface {port_arg} switchport mode access"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} switchport access vlan "
                        f"{context['home_vlan_id']}"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            )
        )

        prepared, prepare_message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name="Не удалось подготовить PPPoE к удалению",
        )

        if not prepared:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{prepare_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        if pre_delete_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                pre_delete_priorities
            )

            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_pppoe_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    "Не удалось перестроить приоритеты: "
                    f"{message}. Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, verify_message = (
            self.client.verify_wired_pppoe_delete_preparation(
                pppoe_id=name,
                transport_id=transport_id,
                port_id=str(context["port_id"]),
                home_vlan_id=int(context["home_vlan_id"]),
                manager_profile_detached=manager_profile,
            )
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pppoe_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{verify_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        deleted, delete_message = self.client.execute_rci_batch(
            [
                {"parse": f"no interface {pppoe_arg}"},
                {"parse": f"no interface {transport_arg}"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось удалить PPPoE и транспорт",
        )

        if not deleted:
            after_delete = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            pppoe_exists = (
                isinstance(after_delete, dict)
                and name in after_delete
            )
            transport_exists = (
                isinstance(after_delete, dict)
                and transport_id in after_delete
            )

            if pppoe_exists and transport_exists:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_pppoe_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    f"{delete_message} Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, final_message = self.client.verify_deleted_wired_pppoe(
            pppoe_id=name,
            transport_id=transport_id,
            port_id=str(context["port_id"]),
            home_vlan_id=int(context["home_vlan_id"]),
            remaining_priorities=remaining_priorities,
        )

        if not verified:
            final_config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            return (
                False,
                final_message,
                {
                    "rollback_success": False,
                    "interface_removed": (
                        isinstance(final_config, dict)
                        and name not in final_config
                    ),
                    "transport_removed": (
                        isinstance(final_config, dict)
                        and transport_id not in final_config
                    ),
                    "port_restored": True,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(
                profile_config,
                ping_profile,
            ):
                profile_deleted, profile_message = (
                    self.client.execute_rci_batch(
                        [
                            {
                                "parse": (
                                    "no ping-check profile "
                                    + self.client.quote_cli_argument(
                                        ping_profile
                                    )
                                )
                            },
                            {
                                "system": {
                                    "configuration": {"save": {}}
                                }
                            },
                        ],
                        operation_name=(
                            "Не удалось удалить профиль Ping Check"
                        ),
                    )
                )

                if not profile_deleted:
                    cleanup_warning = profile_message

        self._forget_interfaces(name, transport_id)

        message = (
            f"PPPoE «{context['description']}» и транспорт "
            f"{transport_id} удалены. Порт {context['port_label']} "
            "возвращён в домашнюю сеть "
            f"(VLAN {context['home_vlan_id']})."
        )

        if cleanup_warning:
            message += (
                " Подключение удалено, но служебный профиль "
                "Ping Check не очищен: " + cleanup_warning
            )

        return (
            True,
            message,
            {
                "interface_id": name,
                "transport_interface_id": transport_id,
                "port_id": context["port_id"],
                "port_label": context["port_label"],
                "home_vlan_id": context["home_vlan_id"],
                "cleanup_warning": cleanup_warning,
            },
        )

    def delete_wired_pptp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указан интерфейс PPTP.", {}

        current_runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        context, reason = self.client.inspect_wired_pptp_deletion(
            interface_id=name,
            interface_config=current_config,
            interface_runtime=current_runtime,
        )

        if not context:
            return (
                False,
                reason
                or "Это PPTP нельзя безопасно удалить автоматически.",
                {},
            )

        transport_id = str(context["transport_id"])
        (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        ) = self._deletion_priorities(current_config, name)

        pptp_arg = self.client.quote_cli_argument(name)
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(str(context["port_id"]))
        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {"parse": f"interface {pptp_arg} no up"},
            {"parse": f"interface {transport_arg} no up"},
        ]

        if manager_profile:
            prepare_commands.append(
                {
                    "parse": (
                        f"interface {pptp_arg} "
                        "no ping-check profile"
                    )
                }
            )

        prepare_commands.extend(
            (
                {"parse": f"interface {port_arg} no role"},
                {
                    "parse": (
                        f"interface {port_arg} switchport mode access"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} switchport access vlan "
                        f"{context['home_vlan_id']}"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            )
        )

        prepared, prepare_message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name="Не удалось подготовить PPTP к удалению",
        )

        if not prepared:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{prepare_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        if pre_delete_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                pre_delete_priorities
            )

            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_pptp_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    "Не удалось перестроить приоритеты: "
                    f"{message}. Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, verify_message = (
            self.client.verify_wired_pptp_delete_preparation(
                pptp_id=name,
                transport_id=transport_id,
                port_id=str(context["port_id"]),
                home_vlan_id=int(context["home_vlan_id"]),
                manager_profile_detached=manager_profile,
            )
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_pptp_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{verify_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        deleted, delete_message = self.client.execute_rci_batch(
            [
                {"parse": f"no interface {pptp_arg}"},
                {"parse": f"no interface {transport_arg}"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось удалить PPTP и транспорт",
        )

        if not deleted:
            after_delete = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            pptp_exists = (
                isinstance(after_delete, dict)
                and name in after_delete
            )
            transport_exists = (
                isinstance(after_delete, dict)
                and transport_id in after_delete
            )

            if pptp_exists and transport_exists:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_pptp_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    f"{delete_message} Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, final_message = self.client.verify_deleted_wired_pptp(
            pptp_id=name,
            transport_id=transport_id,
            port_id=str(context["port_id"]),
            home_vlan_id=int(context["home_vlan_id"]),
            remaining_priorities=remaining_priorities,
        )

        if not verified:
            final_config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            return (
                False,
                final_message,
                {
                    "rollback_success": False,
                    "interface_removed": (
                        isinstance(final_config, dict)
                        and name not in final_config
                    ),
                    "transport_removed": (
                        isinstance(final_config, dict)
                        and transport_id not in final_config
                    ),
                    "port_restored": True,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(
                profile_config,
                ping_profile,
            ):
                profile_deleted, profile_message = (
                    self.client.execute_rci_batch(
                        [
                            {
                                "parse": (
                                    "no ping-check profile "
                                    + self.client.quote_cli_argument(
                                        ping_profile
                                    )
                                )
                            },
                            {
                                "system": {
                                    "configuration": {"save": {}}
                                }
                            },
                        ],
                        operation_name=(
                            "Не удалось удалить профиль Ping Check"
                        ),
                    )
                )

                if not profile_deleted:
                    cleanup_warning = profile_message

        self._forget_interfaces(name, transport_id)

        message = (
            f"PPTP «{context['description']}» и транспорт "
            f"{transport_id} удалены. Порт {context['port_label']} "
            "возвращён в домашнюю сеть "
            f"(VLAN {context['home_vlan_id']})."
        )

        if cleanup_warning:
            message += (
                " Подключение удалено, но служебный профиль "
                "Ping Check не очищен: " + cleanup_warning
            )

        return (
            True,
            message,
            {
                "interface_id": name,
                "transport_interface_id": transport_id,
                "port_id": context["port_id"],
                "port_label": context["port_label"],
                "home_vlan_id": context["home_vlan_id"],
                "cleanup_warning": cleanup_warning,
            },
        )

    def delete_wired_l2tp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указан интерфейс L2TP.", {}

        current_runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        context, reason = self.client.inspect_wired_l2tp_deletion(
            interface_id=name,
            interface_config=current_config,
            interface_runtime=current_runtime,
        )

        if not context:
            return (
                False,
                reason
                or "Это L2TP нельзя безопасно удалить автоматически.",
                {},
            )

        transport_id = str(context["transport_id"])
        (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        ) = self._deletion_priorities(current_config, name)

        l2tp_arg = self.client.quote_cli_argument(name)
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(str(context["port_id"]))
        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {"parse": f"interface {l2tp_arg} no up"},
            {"parse": f"interface {transport_arg} no up"},
        ]

        if manager_profile:
            prepare_commands.append(
                {
                    "parse": (
                        f"interface {l2tp_arg} "
                        "no ping-check profile"
                    )
                }
            )

        prepare_commands.extend(
            (
                {"parse": f"interface {port_arg} no role"},
                {
                    "parse": (
                        f"interface {port_arg} switchport mode access"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} switchport access vlan "
                        f"{context['home_vlan_id']}"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            )
        )

        prepared, prepare_message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name="Не удалось подготовить L2TP к удалению",
        )

        if not prepared:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{prepare_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        if pre_delete_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                pre_delete_priorities
            )

            if not success:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_l2tp_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    "Не удалось перестроить приоритеты: "
                    f"{message}. Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, verify_message = (
            self.client.verify_wired_l2tp_delete_preparation(
                l2tp_id=name,
                transport_id=transport_id,
                port_id=str(context["port_id"]),
                home_vlan_id=int(context["home_vlan_id"]),
                manager_profile_detached=manager_profile,
            )
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_l2tp_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{verify_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        deleted, delete_message = self.client.execute_rci_batch(
            [
                {"parse": f"no interface {l2tp_arg}"},
                {"parse": f"no interface {transport_arg}"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось удалить L2TP и транспорт",
        )

        if not deleted:
            after_delete = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            l2tp_exists = (
                isinstance(after_delete, dict)
                and name in after_delete
            )
            transport_exists = (
                isinstance(after_delete, dict)
                and transport_id in after_delete
            )

            if l2tp_exists and transport_exists:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_l2tp_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    f"{delete_message} Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, final_message = self.client.verify_deleted_wired_l2tp(
            l2tp_id=name,
            transport_id=transport_id,
            port_id=str(context["port_id"]),
            home_vlan_id=int(context["home_vlan_id"]),
            remaining_priorities=remaining_priorities,
        )

        if not verified:
            final_config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            return (
                False,
                final_message,
                {
                    "rollback_success": False,
                    "interface_removed": (
                        isinstance(final_config, dict)
                        and name not in final_config
                    ),
                    "transport_removed": (
                        isinstance(final_config, dict)
                        and transport_id not in final_config
                    ),
                    "port_restored": True,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(
                profile_config,
                ping_profile,
            ):
                profile_deleted, profile_message = (
                    self.client.execute_rci_batch(
                        [
                            {
                                "parse": (
                                    "no ping-check profile "
                                    + self.client.quote_cli_argument(
                                        ping_profile
                                    )
                                )
                            },
                            {
                                "system": {
                                    "configuration": {"save": {}}
                                }
                            },
                        ],
                        operation_name=(
                            "Не удалось удалить профиль Ping Check"
                        ),
                    )
                )

                if not profile_deleted:
                    cleanup_warning = profile_message

        self._forget_interfaces(name, transport_id)

        message = (
            f"L2TP «{context['description']}» и транспорт "
            f"{transport_id} удалены. Порт {context['port_label']} "
            "возвращён в домашнюю сеть "
            f"(VLAN {context['home_vlan_id']})."
        )

        if cleanup_warning:
            message += (
                " Подключение удалено, но служебный профиль "
                "Ping Check не очищен: " + cleanup_warning
            )

        return (
            True,
            message,
            {
                "interface_id": name,
                "transport_interface_id": transport_id,
                "port_id": context["port_id"],
                "port_label": context["port_label"],
                "home_vlan_id": context["home_vlan_id"],
                "cleanup_warning": cleanup_warning,
            },
        )

    def delete_wired_ipoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return (
                False,
                "Нет активной сессии с роутером.",
                {},
            )

        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указан интерфейс.", {}

        current_runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        context, reason = self.client.inspect_wired_ipoe_deletion(
            interface_id=name,
            interface_config=current_config,
            interface_runtime=current_runtime,
        )

        if not context:
            return (
                False,
                reason or (
                    "Это подключение нельзя безопасно удалить "
                    "в текущей версии."
                ),
                {},
            )

        old_priorities = self.client.priority_service.current_global_orders(
            current_config
        )
        ordered_remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
            if current_id != name
        ]
        remaining_priorities = {
            current_id: order
            for order, current_id in enumerate(
                ordered_remaining
            )
        }
        pre_delete_priorities = dict(
            remaining_priorities
        )
        pre_delete_priorities[name] = len(
            remaining_priorities
        )

        interface_arg = self.client.quote_cli_argument(name)
        port_arg = self.client.quote_cli_argument(
            str(context["port_id"])
        )
        ping_profile = str(
            context.get("ping_profile")
            or ""
        ).strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {
                "parse": (
                    f"interface {interface_arg} no up"
                )
            },
        ]

        if manager_profile:
            prepare_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} "
                        "no ping-check profile"
                    )
                }
            )

        prepare_commands.extend(
            (
                {
                    "parse": (
                        f"interface {port_arg} no role"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} "
                        "switchport mode access"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} "
                        "switchport access vlan "
                        f"{context['home_vlan_id']}"
                    )
                },
                {
                    "system": {
                        "configuration": {
                            "save": {},
                        }
                    }
                },
            )
        )

        prepared, prepare_message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name=(
                "Не удалось подготовить Проводное IPoE "
                "к удалению"
            ),
        )

        if not prepared:
            rollback_success, rollback_message = (
                self.client.rollback_wired_ipoe_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                (
                    f"{prepare_message} Откат: "
                    f"{rollback_message}"
                ),
                {
                    "rollback_success": rollback_success,
                },
            )

        if pre_delete_priorities:
            priority_success, priority_message = (
                self.client.priority_service.set_internet_priorities(
                    pre_delete_priorities
                )
            )

            if not priority_success:
                rollback_success, rollback_message = (
                    self.client.rollback_wired_ipoe_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    (
                        "Не удалось перестроить приоритеты: "
                        f"{priority_message}. Откат: "
                        f"{rollback_message}"
                    ),
                    {
                        "rollback_success": rollback_success,
                    },
                )

        verified, verify_message = (
            self.client.verify_wired_ipoe_delete_preparation(
                interface_id=name,
                port_id=str(context["port_id"]),
                home_vlan_id=int(
                    context["home_vlan_id"]
                ),
                manager_profile_detached=manager_profile,
            )
        )

        if not verified:
            rollback_success, rollback_message = (
                self.client.rollback_wired_ipoe_deletion(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                (
                    f"{verify_message} Откат: "
                    f"{rollback_message}"
                ),
                {
                    "rollback_success": rollback_success,
                },
            )

        deleted, delete_message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        f"no interface {interface_arg}"
                    )
                },
                {
                    "system": {
                        "configuration": {
                            "save": {},
                        }
                    }
                },
            ],
            operation_name=(
                "Не удалось удалить интерфейс Проводного IPoE"
            ),
        )

        if not deleted:
            after_delete = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not (
                isinstance(after_delete, dict)
                and name not in after_delete
            ):
                rollback_success, rollback_message = (
                    self.client.rollback_wired_ipoe_deletion(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    (
                        f"{delete_message} Откат: "
                        f"{rollback_message}"
                    ),
                    {
                        "rollback_success": rollback_success,
                    },
                )

        verified, final_message = (
            self.client.verify_deleted_wired_ipoe(
                interface_id=name,
                port_id=str(context["port_id"]),
                home_vlan_id=int(
                    context["home_vlan_id"]
                ),
                remaining_priorities=remaining_priorities,
            )
        )

        if not verified:
            return (
                False,
                final_message,
                {
                    "rollback_success": False,
                    "interface_removed": True,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(
                profile_config,
                ping_profile,
            ):
                profile_deleted, profile_message = (
                    self.client.execute_rci_batch(
                        [
                            {
                                "parse": (
                                    "no ping-check profile "
                                    + self.client.quote_cli_argument(
                                        ping_profile
                                    )
                                )
                            },
                            {
                                "system": {
                                    "configuration": {
                                        "save": {},
                                    }
                                }
                            },
                        ],
                        operation_name=(
                            "Не удалось удалить профиль Ping Check"
                        ),
                    )
                )

                if not profile_deleted:
                    cleanup_warning = profile_message

        self.client._manually_disabled_interfaces.discard(name)
        self.client._traffic_counters.pop(name, None)
        self.client.ping_check_service.runtime_cache.pop(name, None)

        message = (
            f"Подключение «{context['description']}» удалено. "
            f"Порт {context['port_label']} возвращён в домашнюю "
            f"сеть (VLAN {context['home_vlan_id']})."
        )

        if cleanup_warning:
            message += (
                " Подключение удалено, но служебный профиль "
                f"Ping Check не очищен: {cleanup_warning}"
            )

        return (
            True,
            message,
            {
                "interface_id": name,
                "port_id": context["port_id"],
                "port_label": context["port_label"],
                "home_vlan_id": context["home_vlan_id"],
                "cleanup_warning": cleanup_warning,
            },
        )
