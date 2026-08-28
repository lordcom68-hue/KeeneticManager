from __future__ import annotations

from typing import Any


class WiredIpoeService:
    """Создание и удаление проводных IPoE-подключений с откатом."""

    def __init__(self, client: Any) -> None:
        self.client = client

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
        self.client.traffic_service.reset(name)
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

