from __future__ import annotations

from typing import Any

from core.services.wired_protocol_service_base import (
    WiredProtocolServiceBase,
)


class WiredL2tpService(WiredProtocolServiceBase):
    """Создание и удаление проводных L2TP-подключений."""

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

