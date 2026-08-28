from __future__ import annotations

import ipaddress
import re
from typing import Any


class ConnectionDetailsService:
    """Формирование диагностических подробностей интернет-подключений."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def apply_connection_delete_context(
        connection: dict[str, Any],
        *,
        context: dict[str, Any],
        delete_kind: str,
        dedicated: bool = False,
    ) -> None:
        """Записывает единые метаданные безопасного удаления WAN."""
        connection["delete_supported"] = True
        connection["delete_kind"] = str(delete_kind)
        connection["delete_port_id"] = context.get("port_id")
        connection["delete_port_label"] = context.get("port_label")
        connection["delete_port_number"] = context.get("port_number")
        connection["delete_port_display_name"] = (
            context.get("port_display_name")
            or (
                "WAN (порт "
                + str(context.get("port_number") or "0")
                + ")"
                if dedicated
                else "LAN" + str(context.get("port_label") or "?")
            )
        )
        connection["provider_port_label"] = context.get("port_label")
        connection["provider_port_id"] = context.get("port_id")
        connection["provider_port_number"] = context.get("port_number")
        connection["delete_transport_id"] = context.get("transport_id")
        connection["delete_bridge_id"] = context.get("bridge_id")
        connection["delete_dedicated_wan"] = bool(dedicated)

        if context.get("home_vlan_id") is not None:
            connection["delete_restore_vlan"] = context.get("home_vlan_id")
        else:
            connection.pop("delete_restore_vlan", None)

        connection["delete_port_link_known"] = bool(
            context.get("port_link_known")
        )
        connection["delete_port_link_active"] = bool(
            context.get("port_link_active")
        )
        connection["delete_port_link"] = str(
            context.get("port_link") or "unknown"
        )
        connection["delete_port_speed"] = context.get("port_speed")
        connection["delete_reason"] = ""

    @staticmethod
    def clear_connection_delete_context(
        connection: dict[str, Any],
        reason: str,
    ) -> None:
        for key in (
            "delete_kind",
            "delete_port_id",
            "delete_port_label",
            "delete_port_number",
            "delete_port_display_name",
            "delete_restore_vlan",
            "delete_port_link_known",
            "delete_port_link_active",
            "delete_port_link",
            "delete_port_speed",
            "delete_transport_id",
            "delete_bridge_id",
            "delete_dedicated_wan",
        ):
            connection.pop(key, None)

        connection["delete_supported"] = False
        connection["delete_reason"] = str(reason or "")

    def enrich_wired_pppoe_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_pppoe = (
                bool(config_item.get("pppoe"))
                or "pppoe" in runtime_type
                or bool(re.fullmatch(r"PPPoE\d+", interface_id))
            )

            if not is_pppoe:
                continue

            connection["provider_protocol"] = "PPPoE"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            pppoe = config_item.get("pppoe")

            if isinstance(pppoe, dict):
                service = str(pppoe.get("service") or "").strip()

                if service:
                    connection["pppoe_service"] = service

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.client.inspect_wired_pppoe_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.client.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.pppoe"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )

    def enrich_wired_pptp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_pptp = (
                bool(config_item.get("pptp"))
                or "pptp" in runtime_type
                or bool(re.fullmatch(r"PPTP\d+", interface_id))
            )

            if not is_pptp:
                continue

            connection["provider_protocol"] = "PPTP"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            server = str(
                config_item.get("peer") or ""
            ).strip()

            if server:
                connection["provider_server"] = server

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.client.inspect_wired_pptp_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.client.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.pptp"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )

    def enrich_wired_l2tp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_l2tp = (
                bool(config_item.get("l2tp"))
                or "l2tp" in runtime_type
                or bool(re.fullmatch(r"L2TP\d+", interface_id))
            )

            if not is_l2tp:
                continue

            connection["provider_protocol"] = "L2TP"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            server = str(
                config_item.get("peer") or ""
            ).strip()

            if server:
                connection["provider_server"] = server

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.client.inspect_wired_l2tp_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.client.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.l2tp"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )

    def enrich_cable_protocol_change_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            context, reason = self.client.inspect_cable_protocol_change(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            connection["protocol_change_supported"] = bool(context)

            if not context:
                connection["protocol_change_reason"] = reason
                continue

            connection["protocol_change_reason"] = ""
            connection["protocol_change_current"] = context[
                "current_protocol"
            ]
            connection["protocol_change_transport_id"] = context[
                "transport_id"
            ]
            connection["protocol_change_port_id"] = context["port_id"]
            connection["protocol_change_port_label"] = context[
                "port_label"
            ]
            connection["protocol_change_link_known"] = bool(
                context.get("port_link_known")
            )
            connection["protocol_change_link_active"] = bool(
                context.get("port_link_active")
            )
            connection["protocol_change_link"] = str(
                context.get("port_link") or "unknown"
            )

    def enrich_connection_details(
        self,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
        ping_check_config_data: dict[str, Any],
        ping_check_status_data: dict[str, Any],
    ) -> None:
        # Все каналы одного ответа получают одинаковую метку. UI
        # отличает новое измерение от повторной отрисовки кэша.
        traffic_sample_time = self.client.traffic_service.sample_time()

        for connection in connections:
            interface_id = str(
                connection.get("interface_id")
                or ""
            )

            item = interface_data.get(
                interface_id,
                {},
            )

            if not isinstance(item, dict):
                item = {}

            # Передаём реальный ip global priority из
            # show/interface во вкладку «Интернет».
            try:
                connection["priority"] = int(
                    float(item.get("priority"))
                )
            except (TypeError, ValueError):
                connection.pop(
                    "priority",
                    None,
                )

            # Роль канала берём только из конфигурации роутера:
            # show/sc/interface -> ip -> global -> order.
            config_item = interface_config_data.get(
                interface_id,
                {},
            )
            global_config: dict[str, Any] = {}

            if isinstance(config_item, dict):
                self.client.ping_check_service.enrich_connection(
                    connection=connection,
                    interface_id=interface_id,
                    interface_item=item,
                    config_item=config_item,
                    config_data=ping_check_config_data,
                    status_data=ping_check_status_data,
                )

                (
                    mac_supported,
                    mac_mode,
                    configured_mac,
                ) = self.client.extract_configured_mac_settings(
                    config_item
                )

                connection[
                    "mac_settings_supported"
                ] = mac_supported
                connection[
                    "mac_mode"
                ] = mac_mode

                if configured_mac:
                    connection[
                        "configured_mac"
                    ] = configured_mac
                else:
                    connection.pop(
                        "configured_mac",
                        None,
                    )
            else:
                connection[
                    "mac_settings_supported"
                ] = False
                connection.pop(
                    "configured_mac",
                    None,
                )

            if isinstance(config_item, dict):
                ip_config = config_item.get("ip")

                if isinstance(ip_config, dict):
                    candidate = ip_config.get("global")

                    if isinstance(candidate, dict):
                        global_config = candidate

            if isinstance(config_item, dict):
                ip_config = config_item.get("ip")

                if isinstance(ip_config, dict):
                    connection[
                        "use_provider_dns"
                    ] = self.client.truthy(
                        ip_config.get(
                            "name-servers",
                            True,
                        )
                    )

                    configured_dns: list[str] = []
                    name_server_items = ip_config.get(
                        "name-server"
                    )

                    if isinstance(
                        name_server_items,
                        dict,
                    ):
                        name_server_items = [
                            name_server_items
                        ]

                    if isinstance(
                        name_server_items,
                        list,
                    ):
                        for dns_item in name_server_items:
                            if isinstance(
                                dns_item,
                                dict,
                            ):
                                dns_value = str(
                                    dns_item.get(
                                        "name-server"
                                    )
                                    or dns_item.get(
                                        "address"
                                    )
                                    or ""
                                ).strip()
                            else:
                                dns_value = str(
                                    dns_item
                                    or ""
                                ).strip()

                            if (
                                dns_value
                                and dns_value
                                not in configured_dns
                            ):
                                configured_dns.append(
                                    dns_value
                                )

                    if configured_dns:
                        connection[
                            "configured_dns"
                        ] = configured_dns
                    else:
                        connection.pop(
                            "configured_dns",
                            None,
                        )

                    dhcp_config = ip_config.get(
                        "dhcp"
                    )
                    dhcp_client_config: dict[str, Any] = {}

                    if isinstance(dhcp_config, dict):
                        candidate = dhcp_config.get(
                            "client"
                        )

                        if isinstance(candidate, dict):
                            dhcp_client_config = candidate

                    if dhcp_client_config:
                        connection[
                            "dhcp_client_supported"
                        ] = True
                        connection[
                            "dhcp_client_hostname"
                        ] = str(
                            dhcp_client_config.get(
                                "hostname"
                            )
                            or "Keenetic"
                        ).strip()
                        connection[
                            "dhcp_client_routes"
                        ] = self.client.truthy(
                            dhcp_client_config.get(
                                "routes",
                                True,
                            )
                        )
                        connection[
                            "dhcp_client_dns_routes"
                        ] = self.client.truthy(
                            dhcp_client_config.get(
                                "dns-routes",
                                True,
                            )
                        )
                    else:
                        connection[
                            "dhcp_client_supported"
                        ] = False
                        connection.pop(
                            "dhcp_client_hostname",
                            None,
                        )
                        connection.pop(
                            "dhcp_client_routes",
                            None,
                        )
                        connection.pop(
                            "dhcp_client_dns_routes",
                            None,
                        )

                    address_config = ip_config.get(
                        "address"
                    )

                    if isinstance(
                        address_config,
                        (dict, str),
                    ):
                        (
                            ipv4_dhcp,
                            configured_address,
                            configured_prefix,
                        ) = self.client.extract_configured_ipv4(
                            address_config
                        )

                        connection[
                            "ipv4_settings_supported"
                        ] = True
                        connection[
                            "ipv4_dhcp"
                        ] = ipv4_dhcp

                        if configured_address:
                            connection[
                                "configured_ipv4_address"
                            ] = configured_address
                        else:
                            connection.pop(
                                "configured_ipv4_address",
                                None,
                            )

                        if configured_prefix is not None:
                            connection[
                                "configured_ipv4_prefix"
                            ] = configured_prefix
                        else:
                            connection.pop(
                                "configured_ipv4_prefix",
                                None,
                            )

                        if not ipv4_dhcp:
                            configured_gateway = (
                                self.client.normalize_ipv4_text(
                                    connection.get(
                                        "gateway"
                                    )
                                )
                            )

                            if configured_gateway:
                                connection[
                                    "configured_ipv4_gateway"
                                ] = configured_gateway
                            else:
                                connection.pop(
                                    "configured_ipv4_gateway",
                                    None,
                                )
                        else:
                            connection.pop(
                                "configured_ipv4_gateway",
                                None,
                            )
                    else:
                        connection[
                            "ipv4_settings_supported"
                        ] = False
                        connection.pop(
                            "configured_ipv4_address",
                            None,
                        )
                        connection.pop(
                            "configured_ipv4_prefix",
                            None,
                        )
                        connection.pop(
                            "configured_ipv4_gateway",
                            None,
                        )

            delete_context, delete_reason = (
                self.client.inspect_wired_ipoe_deletion(
                    interface_id=interface_id,
                    interface_config=interface_config_data,
                    interface_runtime=interface_data,
                )
            )
            dedicated = False

            if not delete_context:
                delete_context, dedicated_reason = (
                    self.client.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(delete_context)

                if not delete_context and dedicated_reason:
                    delete_reason = delete_reason or dedicated_reason

            if delete_context:
                self.apply_connection_delete_context(
                    connection,
                    context=delete_context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.ipoe"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    delete_reason,
                )

            try:
                global_order = int(
                    global_config.get("order")
                )
            except (TypeError, ValueError):
                connection.pop(
                    "global_order",
                    None,
                )
            else:
                if global_order >= 0:
                    connection[
                        "global_order"
                    ] = global_order
                else:
                    connection.pop(
                        "global_order",
                        None,
                    )

            traffic_sample = self.client.traffic_service.measure_connection(
                connection=connection,
                interface_data=interface_data,
                sampled_at=traffic_sample_time,
            )
            self.client.traffic_service.apply_sample(
                connection,
                traffic_sample,
            )
            statistics_source = traffic_sample.source_id

            # Дополнительные параметры для раскрытой карточки
            # во вкладке «Интернет». Используем фактические
            # данные show/interface и не подставляем значения,
            # которых роутер не передал.
            self.enrich_basic_interface_fields(
                connection=connection,
                item=item,
                statistics_source=statistics_source,
                interface_data=interface_data,
            )

    def enrich_basic_interface_fields(
        self,
        *,
        connection: dict[str, Any],
        item: dict[str, Any],
        statistics_source: str,
        interface_data: dict[str, Any],
    ) -> None:
        """
        Добавляет безопасные диагностические поля интернет-
        подключения из show/interface.

        Значение записывается только когда оно реально найдено.
        Уже подготовленные TopologyEngine поля не перезаписываются.
        """
        source_item = interface_data.get(
            statistics_source,
            {},
        )

        if not isinstance(source_item, dict):
            source_item = {}

        combined = {
            "logical": item,
            "traffic": source_item,
        }

        field_keys: dict[str, tuple[str, ...]] = {
            "address": (
                "address",
                "ipv4",
                "ip-address",
                "ip_address",
                "local-address",
                "local_address",
            ),
            "ipv6_address": (
                "ipv6",
                "ipv6-address",
                "ipv6_address",
                "global-address",
                "global_address",
            ),
            "gateway": (
                "gateway",
                "default-gateway",
                "default_gateway",
                "peer",
                "remote-address",
                "remote_address",
            ),
            "dns": (
                "dns",
                "dns-server",
                "dns_server",
                "dns-servers",
                "dns_servers",
                "nameserver",
                "nameservers",
            ),
            "mtu": (
                "mtu",
            ),
            "mac": (
                "mac",
                "mac-address",
                "mac_address",
                "hwaddr",
                "hardware-address",
            ),
            "uptime": (
                "uptime",
                "connected-time",
                "connected_time",
                "session-time",
                "session_time",
            ),
        }

        for target_key, keys in field_keys.items():
            existing = connection.get(target_key)

            if existing not in (
                None,
                "",
                "-",
                "—",
                [],
                {},
            ):
                continue

            if target_key == "ipv6_address":
                # Не используем find_first_value: оно может
                # первым найти служебный флаг `ipv6: false`
                # и остановить поиск до настоящего адреса.
                # Просматриваем всю структуру интерфейса.
                normalized = self.extract_ipv6_address(
                    combined
                )
            else:
                value = self.client.find_first_value(
                    combined,
                    keys,
                )
                normalized = self.normalize_connection_detail(
                    value
                )

            if normalized is not None:
                connection[target_key] = normalized

        link_speed = self.client.extract_link_speed_bps(
            source_item
        )

        if link_speed is None:
            link_speed = self.client.extract_link_speed_bps(
                item
            )

        if link_speed is not None and link_speed > 0:
            connection["link_speed_bps"] = link_speed

    @classmethod
    def extract_ipv6_address(
        cls,
        value: Any,
    ) -> str | None:
        """
        Извлекает только сам IPv6-адрес из строки или
        вложенной структуры show/interface.

        Булевы значения True/False полностью игнорируются.
        Глобальный адрес имеет приоритет над link-local.
        """
        global_addresses: list[str] = []
        link_local_addresses: list[str] = []

        def collect(candidate: Any) -> None:
            if candidate is None or isinstance(candidate, bool):
                return

            if isinstance(candidate, dict):
                for key in (
                    "address",
                    "ipv6-address",
                    "ipv6_address",
                    "global-address",
                    "global_address",
                ):
                    if key in candidate:
                        collect(candidate.get(key))

                for key, nested in candidate.items():
                    if key in {
                        "address",
                        "ipv6-address",
                        "ipv6_address",
                        "global-address",
                        "global_address",
                    }:
                        continue
                    collect(nested)
                return

            if isinstance(candidate, (list, tuple, set)):
                for nested in candidate:
                    collect(nested)
                return

            if not isinstance(candidate, str):
                return

            for part in candidate.replace(
                "\n",
                " ",
            ).replace(
                ",",
                " ",
            ).replace(
                ";",
                " ",
            ).split():
                address_text = (
                    part.strip("[]() ")
                    .split("/", 1)[0]
                    .split("%", 1)[0]
                    .strip()
                )

                try:
                    address = ipaddress.ip_address(
                        address_text
                    )
                except ValueError:
                    continue

                if not isinstance(
                    address,
                    ipaddress.IPv6Address,
                ):
                    continue

                normalized = str(address)

                if address.is_link_local:
                    if normalized not in link_local_addresses:
                        link_local_addresses.append(normalized)
                elif normalized not in global_addresses:
                    global_addresses.append(normalized)

        collect(value)

        if global_addresses:
            return global_addresses[0]

        if link_local_addresses:
            return link_local_addresses[0]

        return None

    @classmethod
    def normalize_connection_detail(
        cls,
        value: Any,
    ) -> Any | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            text = value.strip()

            if text in {
                "",
                "-",
                "—",
                "None",
                "null",
            }:
                return None

            return text

        if isinstance(value, (list, tuple, set)):
            items: list[str] = []

            for item in value:
                normalized = cls.normalize_connection_detail(
                    item
                )

                if normalized is None:
                    continue

                text = str(normalized).strip()

                if text and text not in items:
                    items.append(text)

            return items or None

        if isinstance(value, dict):
            # Часто DNS и адреса приходят как словари с
            # произвольными числовыми ключами.
            items: list[str] = []

            for nested_value in value.values():
                normalized = cls.normalize_connection_detail(
                    nested_value
                )

                if normalized is None:
                    continue

                if isinstance(normalized, list):
                    candidates = normalized
                else:
                    candidates = [normalized]

                for candidate in candidates:
                    text = str(candidate).strip()

                    if text and text not in items:
                        items.append(text)

            return items or None

        text = str(value).strip()
        return text or None

    @staticmethod
    def extract_ipv6_details(
        item: dict[str, Any],
    ) -> tuple[str, str]:
        ipv6 = item.get("ipv6")

        if not isinstance(ipv6, dict):
            return "—", "—"

        addresses = ipv6.get("addresses")

        if not isinstance(addresses, list):
            return "—", "—"

        for address_item in addresses:
            if not isinstance(address_item, dict):
                continue

            address = str(
                address_item.get("address")
                or ""
            )

            if not address or address.startswith("fe80:"):
                continue

            prefix_length = address_item.get(
                "prefix-length"
            )

            prefix = (
                f"{address}/{prefix_length}"
                if prefix_length is not None
                else address
            )

            return address, prefix

        for address_item in addresses:
            if not isinstance(address_item, dict):
                continue

            address = str(
                address_item.get("address")
                or ""
            )

            if address:
                prefix_length = address_item.get(
                    "prefix-length"
                )
                prefix = (
                    f"{address}/{prefix_length}"
                    if prefix_length is not None
                    else address
                )
                return address, prefix

        return "—", "—"

