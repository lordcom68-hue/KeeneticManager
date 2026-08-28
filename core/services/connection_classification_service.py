from __future__ import annotations

import re
from typing import Any


class ConnectionClassificationService:
    """Классификация, имя и активность интернет-подключений."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def is_internet_candidate(
        self,
        item: dict[str, Any],
        interface_id: str,
        interface_type: str,
        is_wireless_isp: bool,
        has_inet_port: bool,
    ) -> bool:
        """
        Определяет именно интернет-подключение, а не любой
        Ethernet-интерфейс.

        Пользовательское имя вроде "ISP" не используется
        как доказательство WAN-роли.
        """
        if is_wireless_isp:
            return True

        if self.client.truthy(item.get("global")):
            return True

        if self.client.truthy(item.get("defaultgw")):
            return True

        if has_inet_port:
            return True

        type_text = interface_type.lower()
        id_text = interface_id.lower()

        # Эти типы сами по себе являются подключениями к
        # провайдеру, даже без физического Ethernet-порта.
        return any(
            marker in f"{type_text} {id_text}"
            for marker in (
                "pppoe",
                "lte",
                "modem",
                "usbmodem",
                "cellular",
            )
        )

    def connection_display_name(
        self,
        interface_id: str,
        interface_name: str,
        interface_type: str,
        is_wireless_isp: bool,
        item: dict[str, Any],
        physical_info: dict[str, str],
    ) -> str:
        """
        Формирует название по фактической конфигурации.

        Номер порта не задаётся вручную. Он берётся из
        физического интерфейса, связанного через role=inet.
        """
        if is_wireless_isp:
            return self.client.wireless_isp_display_name(
                interface_id=interface_id,
                interface_name=interface_name,
                item=item,
            )

        if physical_info:
            return self.format_physical_port(
                physical_info
            )

        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if "pppoe" in text:
            return "PPPoE-подключение"

        if any(
            marker in text
            for marker in (
                "lte",
                "modem",
                "cellular",
            )
        ):
            return "Мобильный интернет"

        return interface_name or interface_id

    def connection_transport_type(
        self,
        interface_id: str,
        interface_type: str,
        is_wireless_isp: bool,
        item: dict[str, Any],
        physical_info: dict[str, str],
    ) -> str:
        """
        Возвращает понятный тип физического подключения.
        """
        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if is_wireless_isp:
            if "wifimaster0" in text:
                return "Wireless ISP 2.4 ГГц"
            if "wifimaster1" in text:
                return "Wireless ISP 5 ГГц"
            return "Wireless ISP"

        if "pppoe" in text:
            return "PPPoE"

        if any(
            marker in text
            for marker in (
                "lte",
                "modem",
                "cellular",
            )
        ):
            return "Mobile"

        if physical_info:
            role = physical_info.get(
                "port_role",
                "WAN",
            )
            return f"Ethernet ({role})"

        if any(
            marker in text
            for marker in (
                "gigabitethernet",
                "ethernet",
                "vlan",
            )
        ):
            return "Ethernet"

        return interface_type or "Интернет"

    @staticmethod
    def format_physical_port(
        physical_info: dict[str, str],
    ) -> str:
        if not physical_info:
            return "—"

        role = physical_info.get(
            "port_role",
            "Интернет-порт",
        )

        label = str(
            physical_info.get("label")
            or ""
        ).strip()

        physical_id = str(
            physical_info.get("id")
            or ""
        ).strip()

        # Если Keenetic не передал label, пытаемся получить
        # номер из физического ID, например:
        # GigabitEthernet0/3 -> порт 4.
        if not label or label == "—":
            match = re.search(
                r"/(\d+)$",
                physical_id,
            )

            if match:
                label = str(
                    int(match.group(1)) + 1
                )

        if label and label != "—":
            if "порт" in role.lower():
                return f"{role} {label}"

            return f"{role}, порт {label}"

        return role

    def build_physical_port_map(
        self,
        interface_data: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        """
        Строит связь логического интернет-интерфейса
        с физическим Ethernet-портом.

        Пример:
            GigabitEthernet0/3
                role: inet
                for: GigabitEthernet0/Vlan4

        Результат:
            GigabitEthernet0/Vlan4 ->
                WAN, порт 4
        """
        result: dict[
            str,
            dict[str, str],
        ] = {}

        for item in interface_data.values():
            if not isinstance(item, dict):
                continue

            role_entries = item.get("role")

            if not isinstance(role_entries, list):
                continue

            physical_id = str(
                item.get("id")
                or ""
            ).strip()

            label = str(
                item.get("label")
                or item.get("interface-name")
                or ""
            ).strip()

            is_public = self.client.truthy(
                item.get("public")
            )

            for role_item in role_entries:
                if not isinstance(role_item, dict):
                    continue

                role_name = str(
                    role_item.get("role")
                    or ""
                ).strip().lower()

                if role_name != "inet":
                    continue

                target = str(
                    role_item.get("for")
                    or ""
                ).strip()

                if not target:
                    continue

                # Порт, назначенный для inet, считаем WAN.
                # Даже если физически это переназначенный LAN.
                port_role = (
                    "WAN"
                    if is_public
                    else "Интернет-порт"
                )

                result[target] = {
                    "id": physical_id,
                    "label": label,
                    "port_role": port_role,
                }

        return result

    def collect_connection_candidates(
        self,
        data: Any,
        output: list[dict[str, Any]],
    ) -> None:
        if isinstance(data, dict):
            keys = {
                str(key).lower()
                for key in data.keys()
            }

            connection_markers = {
                "interface",
                "name",
                "ifname",
                "address",
                "ip",
                "status",
                "state",
                "connected",
                "online",
                "gateway",
                "via",
            }

            if keys & connection_markers:
                output.append(data)

            for value in data.values():
                self.collect_connection_candidates(
                    value,
                    output,
                )

        elif isinstance(data, list):
            for item in data:
                self.collect_connection_candidates(
                    item,
                    output,
                )

    def connection_is_active(
        self,
        item: dict[str, Any],
        status: str | None,
    ) -> bool:
        positive = {
            "up",
            "online",
            "connected",
            "running",
            "ready",
            "yes",
            "true",
            "1",
            "active",
        }

        negative = {
            "down",
            "offline",
            "disconnected",
            "stopped",
            "no",
            "false",
            "0",
            "inactive",
        }

        if status:
            normalized = status.strip().lower()

            if normalized in positive:
                return True

            if normalized in negative:
                return False

        for key in (
            "connected",
            "online",
            "up",
            "running",
            "active",
        ):
            value = item.get(key)

            if isinstance(value, bool):
                return value

            if isinstance(value, (int, float)):
                return value != 0

            if isinstance(value, str):
                normalized = value.strip().lower()

                if normalized in positive:
                    return True

                if normalized in negative:
                    return False

        # Наличие адреса обычно означает активный интерфейс.
        address = self.client.first_text(
            item,
            (
                "address",
                "ip",
                "ip-address",
                "ip_address",
                "local-address",
                "local_address",
            ),
        )

        if address and address not in {
            "0.0.0.0",
            "::",
            "—",
        }:
            gateway = self.client.first_text(
                item,
                (
                    "gateway",
                    "default-gateway",
                    "default_gateway",
                    "peer",
                    "remote-address",
                ),
            )

            if gateway and gateway not in {
                "0.0.0.0",
                "::",
                "—",
            }:
                return True

            for key in (
                "default",
                "default-route",
                "default_route",
                "primary",
                "main",
            ):
                value = item.get(key)

                if isinstance(value, bool) and value:
                    return True

                if isinstance(value, (int, float)) and value != 0:
                    return True

                if isinstance(value, str):
                    if value.strip().lower() in {
                        "yes",
                        "true",
                        "1",
                        "primary",
                        "main",
                        "default",
                    }:
                        return True

            # Для физического WAN/LAN-порта с назначенной
            # ролью интернет-подключения IP уже достаточен.
            name = self.client.first_text(
                item,
                (
                    "interface",
                    "name",
                    "id",
                    "ifname",
                    "via",
                    "device",
                ),
            ) or ""

            description = self.client.first_text(
                item,
                (
                    "description",
                    "title",
                    "label",
                    "provider",
                    "type",
                ),
            ) or ""

            interface_text = (
                f"{name} {description}"
            ).lower()

            if any(
                marker in interface_text
                for marker in (
                    "wan",
                    "ethernet",
                    "gigabitethernet",
                    "pppoe",
                    "isp",
                    "internet",
                    "интернет",
                )
            ):
                return True

        return False

    def filter_real_internet_connections(
        self,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Оставляет только реальные интернет-каналы.

        Wireless ISP сохраняется, если WifiStation действительно
        настроен или активен. Пустые служебные WifiStation без
        адреса, подключения, global/defaultgw и priority исключаются.
        """
        result: list[dict[str, Any]] = []

        for connection in connections:
            if not isinstance(connection, dict):
                continue

            interface_id = str(
                connection.get("interface_id")
                or connection.get("id")
                or ""
            ).strip()

            item = interface_data.get(
                interface_id,
                {},
            )

            if not isinstance(item, dict):
                item = {}

            interface_type = str(
                item.get("type")
                or connection.get("type")
                or connection.get("connection_type")
                or ""
            ).strip()

            interface_name = str(
                item.get("interface-name")
                or connection.get("interface_name")
                or ""
            ).strip()

            if self.client.is_wireless_isp_interface(
                interface_type,
                interface_id,
                interface_name,
            ):
                if self.client.is_configured_wireless_isp(
                    item,
                    connection,
                ):
                    result.append(connection)
                continue

            if self.is_real_wan_connection(
                item,
                connection,
                interface_id,
                interface_type,
            ):
                result.append(connection)

        return result

    def is_real_wan_connection(
        self,
        item: dict[str, Any],
        connection: dict[str, Any],
        interface_id: str,
        interface_type: str,
    ) -> bool:
        text = " ".join(
            (
                interface_id,
                interface_type,
                str(item.get("description") or ""),
                str(connection.get("type") or ""),
                str(connection.get("role") or ""),
            )
        ).lower()

        if any(
            marker in text
            for marker in (
                "pppoe",
                "openvpn",
                "wireguard",
                "zerotier",
                "l2tp",
                "pptp",
                "sstp",
                "ipsec",
                "lte",
                "modem",
                "cellular",
            )
        ):
            return True

        if self.client.truthy(item.get("global")):
            return True

        if self.client.truthy(item.get("defaultgw")):
            return True

        security_level = str(
            item.get("security-level")
            or ""
        ).strip().lower()

        if security_level == "public":
            return True

        role = item.get("role")

        if isinstance(role, list):
            for role_item in role:
                if isinstance(role_item, dict):
                    role_name = str(
                        role_item.get("role")
                        or ""
                    ).strip().lower()

                    if role_name == "inet":
                        return True

        return False
