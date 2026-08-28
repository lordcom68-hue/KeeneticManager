from __future__ import annotations

from typing import Any

from core.services.wired_protocol_service_base import WiredProtocolServiceBase


class WiredPeerTunnelService(WiredProtocolServiceBase):
    """Shared implementation for PPTP and L2TP client tunnels."""

    def _create_wired_peer_tunnel(
        self,
        settings: dict[str, Any],
        *,
        protocol_name: str,
        protocol_slug: str,
        default_mtu: int,
        max_mtu: int,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        if not isinstance(settings, dict):
            return False, "Параметры создания переданы неверно.", {}

        protocol = settings.get("protocol")

        if not isinstance(protocol, dict):
            return False, f"Параметры {protocol_name} не переданы.", {}

        username = str(protocol.get("username") or "").strip()
        password = str(protocol.get("password") or "")
        server = str(protocol.get("server") or "").strip()

        try:
            protocol_mtu = int(protocol.get("mtu", default_mtu))
        except (TypeError, ValueError):
            return False, f"Некорректный MTU {protocol_name}.", {}

        if not username or not password or not server:
            return (
                False,
                f"Для {protocol_name} нужны адрес сервера, имя пользователя "
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
                f"Укажите корректный адрес {protocol_name}-сервера.",
                {"preflight_rejected": True},
            )

        if len(username) > 128:
            return (
                False,
                f"Имя пользователя {protocol_name} слишком длинное.",
                {"preflight_rejected": True},
            )

        if len(password) > 256:
            return (
                False,
                f"Пароль {protocol_name} слишком длинный.",
                {"preflight_rejected": True},
            )

        if len(server) > 253:
            return (
                False,
                f"Адрес {protocol_name}-сервера слишком длинный.",
                {"preflight_rejected": True},
            )

        if not 576 <= protocol_mtu <= max_mtu:
            return False, f"MTU {protocol_name} должен быть от 576 до {max_mtu}.", {}

        if any(
            character in "\r\n\x00"
            for character in username + password + server
        ):
            return (
                False,
                f"Параметры {protocol_name} содержат недопустимые символы.",
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
        protocol_id = self.client.next_numbered_interface_id(config, protocol_name)
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
        protocol_arg = self.client.quote_cli_argument(protocol_id)

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
            operation_name=f"Не удалось создать транспорт {protocol_name}",
        )

        if not success:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
            or f"{protocol_name}-подключение — порт {port_label}"
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
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
                f"Не удалось настроить транспорт {protocol_name}: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        protocol_commands = [
            {"parse": f"interface {protocol_arg}"},
            {"parse": f"interface {protocol_arg} no up"},
            {
                "parse": (
                    f"interface {protocol_arg} description "
                    + self.client.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {protocol_arg} security-level public"
                )
            },
            {"parse": f"interface {protocol_arg} role inet"},
            {
                "parse": (
                    f"interface {protocol_arg} authentication identity "
                    + self.client.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {protocol_arg} authentication password "
                    + self.client.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {protocol_arg} no ccp"},
            {"parse": f"interface {protocol_arg} lcp echo 30 3"},
            {"parse": f"interface {protocol_arg} ipcp address"},
            {"parse": f"interface {protocol_arg} ipcp default-route"},
            {"parse": f"interface {protocol_arg} ipcp dns-routes"},
            {"parse": f"interface {protocol_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {protocol_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {protocol_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]

        protocol_commands.append(
            {
                "parse": (
                    f"interface {protocol_arg} peer "
                    + self.client.quote_cli_argument(server)
                )
            }
        )

        protocol_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            protocol_commands,
            operation_name=f"Не удалось создать {protocol_name}",
        )
        message = self.client.redact_secrets_from_text(
            message,
            password,
        )

        if not success:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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

        protocol_settings = {
            "description": description,
            "mtu": protocol_mtu,
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
            interface_id=protocol_id,
            settings=protocol_settings,
        )

        if not success:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
                f"Не удалось применить параметры {protocol_name}: "
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
        ordered.insert(priority_order, protocol_id)
        new_priorities = {
            interface_id: order
            for order, interface_id in enumerate(ordered)
        }
        success, message = self.client.priority_service.set_internet_priorities(
            new_priorities
        )

        if not success:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
                f"Не удалось назначить приоритет {protocol_name}: "
                + message
                + ". Откат: "
                + rollback_message,
                {"rollback_success": rollback_success},
            )

        success, message = self.client.execute_rci_batch(
            [
                {"parse": f"interface {transport_arg} up"},
                {"parse": f"interface {protocol_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name=f"Не удалось включить {protocol_name}",
        )

        if not success:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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

        verified, verify_message = self._invoke_protocol_interface_method(
            "verify_created_wired_{protocol}",
            protocol_slug,
            protocol_id,
                        transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=priority_order,
            expected_identity=username,
            expected_server=server,
            expected_mtu=protocol_mtu,
        )

        if not verified:
            rollback_success, rollback_message = (
                self._invoke_protocol_interface_method(
                    "rollback_wired_{protocol}_creation",
                    protocol_slug,
                    protocol_id,
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
            f"{protocol_name} создано и сохранено. "
            f"Порт: {port_label}; интерфейс: {protocol_id}; "
            f"транспорт: {transport_id}; "
            f"приоритет: {priority_order + 1}.",
            {
                "interface_id": protocol_id,
                "transport_interface_id": transport_id,
                "port_id": port_id,
                "port_label": port_label,
                "vlan_id": vlan_id,
                "priority_order": priority_order,
                "protocol": protocol_slug,
            },
        )


