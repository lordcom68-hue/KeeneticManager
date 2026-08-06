from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TopologyNode:
    """Один физический или логический сетевой интерфейс."""

    interface_id: str
    name: str
    interface_type: str
    description: str = ""

    parent_id: str | None = None
    children: list[str] = field(default_factory=list)

    transport: str = "unknown"
    physical_label: str = "—"
    physical_interface: str = "—"

    online: bool = False
    enabled: bool = True
    global_interface: bool = False
    default_gateway: bool = False

    address: str = "—"
    gateway: str = "—"
    role: str = "other"

    raw: dict[str, Any] = field(
        default_factory=dict,
        repr=False,
    )


@dataclass
class TopologyResult:
    """Результат построения сетевой топологии роутера."""

    nodes: dict[str, TopologyNode]
    connections: list[dict[str, Any]]
    gateway_interface: str = ""
    gateway_address: str = "—"


class TopologyEngine:
    """
    Строит дерево физических и логических интерфейсов Keenetic.

    Движок не использует пользовательское имя вроде ISP для
    определения типа подключения. Он опирается на:
    - type и traits;
    - global/defaultgw;
    - role=inet и поле for;
    - вложенный physical port;
    - родителя WifiMaster для Wireless ISP;
    - поле via для туннелей;
    - текущее состояние show internet status.
    """

    VPN_MARKERS = (
        "openvpn",
        "wireguard",
        "zerotier",
        "l2tp",
        "pptp",
        "sstp",
        "ipsec",
        "gre",
        "vpn",
    )

    MOBILE_MARKERS = (
        "lte",
        "modem",
        "cellular",
        "usbmodem",
        "ndis",
        "qmi",
    )

    LOCAL_MARKERS = (
        "bridge",
        "home network",
        "guest network",
        "accesspoint",
        "access point",
        "wifimaster",
        "loopback",
        "localhost",
        "dummy",
    )

    def __init__(
        self,
        internet_data: dict[str, Any],
        interface_data: dict[str, Any],
        manually_disabled: set[str] | None = None,
    ) -> None:
        self.internet_data = (
            internet_data
            if isinstance(internet_data, dict)
            else {}
        )
        self.interface_data = (
            interface_data
            if isinstance(interface_data, dict)
            else {}
        )
        self.manually_disabled = manually_disabled or set()

        self.gateway_interface = ""
        self.gateway_address = "—"

        gateway = self.internet_data.get("gateway")

        if isinstance(gateway, dict):
            self.gateway_interface = str(
                gateway.get("interface")
                or ""
            ).strip()
            self.gateway_address = str(
                gateway.get("address")
                or "—"
            ).strip()

        self.physical_links = (
            self._build_physical_port_links()
        )

    def build(self) -> TopologyResult:
        nodes: dict[str, TopologyNode] = {}

        for key, item in self.interface_data.items():
            if not isinstance(item, dict):
                continue

            node = self._build_node(
                key=str(key),
                item=item,
            )
            nodes[node.interface_id] = node

        self._link_parent_children(nodes)

        connections = self._build_connections(nodes)

        return TopologyResult(
            nodes=nodes,
            connections=connections,
            gateway_interface=self.gateway_interface,
            gateway_address=self.gateway_address,
        )

    def build_connections(self) -> list[dict[str, Any]]:
        return self.build().connections

    def _build_node(
        self,
        key: str,
        item: dict[str, Any],
    ) -> TopologyNode:
        interface_id = str(
            item.get("id")
            or key
        ).strip()

        name = str(
            item.get("interface-name")
            or interface_id
        ).strip()

        interface_type = str(
            item.get("type")
            or ""
        ).strip()

        description = str(
            item.get("description")
            or ""
        ).strip()

        physical_info = dict(
            self.physical_links.get(
                interface_id,
                {},
            )
        )

        if not physical_info:
            physical_info = (
                self._detect_dedicated_wan_port(
                    interface_id=interface_id,
                    interface_type=interface_type,
                    item=item,
                )
            )

        wireless_isp = self._is_wireless_isp(
            interface_id=interface_id,
            interface_type=interface_type,
            name=name,
        )
        vpn = self._is_vpn(
            interface_id=interface_id,
            interface_type=interface_type,
        )

        parent_id = self._find_parent_id(
            interface_id=interface_id,
            item=item,
            wireless_isp=wireless_isp,
            vpn=vpn,
            physical_info=physical_info,
        )

        transport = self._detect_transport(
            interface_id=interface_id,
            interface_type=interface_type,
            wireless_isp=wireless_isp,
            vpn=vpn,
            physical_info=physical_info,
        )

        online = self._is_online(item)
        enabled = self._is_enabled(item)
        default_gateway = self._truthy(
            item.get("defaultgw")
        )

        current_gateway = (
            interface_id == self.gateway_interface
            or name == self.gateway_interface
        )

        if vpn:
            role = "vpn"
        elif default_gateway or current_gateway:
            role = "primary"
        elif self._is_internet_candidate(
            item=item,
            interface_id=interface_id,
            interface_type=interface_type,
            wireless_isp=wireless_isp,
            has_physical_link=bool(physical_info),
        ):
            role = "backup"
        else:
            role = "other"

        gateway = (
            self.gateway_address
            if role == "primary"
            else self._extract_gateway(item)
        )

        return TopologyNode(
            interface_id=interface_id,
            name=name,
            interface_type=interface_type,
            description=description,
            parent_id=parent_id,
            transport=transport,
            physical_label=self._format_physical_port(
                physical_info
            ),
            physical_interface=str(
                physical_info.get("id")
                or parent_id
                or "—"
            ),
            online=online,
            enabled=enabled,
            global_interface=self._truthy(
                item.get("global")
            ),
            default_gateway=(
                default_gateway
                or current_gateway
            ),
            address=str(
                item.get("address")
                or "—"
            ).strip(),
            gateway=gateway,
            role=role,
            raw=item,
        )

    def _build_connections(
        self,
        nodes: dict[str, TopologyNode],
    ) -> list[dict[str, Any]]:
        primary: list[dict[str, Any]] = []
        backup: list[dict[str, Any]] = []
        vpn: list[dict[str, Any]] = []

        for node in nodes.values():
            if node.role == "other":
                continue

            manually_disabled = (
                node.interface_id
                in self.manually_disabled
            )

            if node.role == "vpn":
                # VPN без установленного туннеля по-прежнему
                # не показываем, кроме выключенных вручную из
                # приложения — для них должна остаться кнопка
                # повторного включения.
                if not node.online and not manually_disabled:
                    continue

            elif not node.online and not manually_disabled:
                # Сохраняем в списке настроенные провайдерские
                # интерфейсы, даже когда кабель отключён или
                # соединение ещё не установлено. Признаком
                # настройки служит global/default gateway либо
                # физическая Ethernet-привязка. Неактивные
                # служебные Wi-Fi Station без global здесь не
                # превращаем в отдельные подключения.
                configured_internet = (
                    node.global_interface
                    or node.default_gateway
                    or node.transport.startswith("Ethernet")
                )

                if not configured_internet:
                    continue

            if (
                node.online
                and node.address == "—"
                and not node.transport.startswith(
                    "Wireless ISP"
                )
                and node.role != "vpn"
            ):
                continue

            card = self._node_to_card(node)

            if node.role == "primary":
                primary.append(card)
            elif node.role == "vpn":
                vpn.append(card)
            else:
                backup.append(card)

        # Если KeeneticOS не передал defaultgw, выбираем первый
        # реально активный интернет-канал, но не VPN.
        if not primary:
            online_backups = [
                item
                for item in backup
                if item.get("status") == "Online"
            ]

            if online_backups:
                online_backups.sort(
                    key=self._connection_sort_key
                )
                selected = online_backups[0]
                backup.remove(selected)
                selected["role"] = "primary"
                primary.append(selected)

        backup.sort(key=self._connection_sort_key)
        vpn.sort(
            key=lambda item: (
                str(item.get("type", "")),
                str(item.get("display_name", "")),
            )
        )

        combined = primary[:1] + backup + vpn
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()

        for connection in combined:
            interface_id = str(
                connection.get("interface_id")
                or ""
            ).lower()

            if not interface_id or interface_id in seen:
                continue

            seen.add(interface_id)
            unique.append(connection)

        return unique

    def _node_to_card(
        self,
        node: TopologyNode,
    ) -> dict[str, Any]:
        display_name = self._display_name(node)

        description = node.description

        if (
            not description
            and node.transport.startswith("Wireless ISP")
        ):
            description = (
                "Беспроводное подключение к провайдеру"
            )

        return {
            "interface_id": node.interface_id,
            "name": display_name,
            "display_name": display_name,
            "interface_name": node.name,
            "description": description,
            "status": (
                "Online"
                if node.online
                else "Offline"
            ),
            "address": node.address,
            "gateway": node.gateway,
            "role": node.role,
            "type": node.transport,
            "port": node.physical_label,
            "physical_interface": (
                node.physical_interface
            ),
            "parent_interface": (
                node.parent_id or "—"
            ),
            "enabled": node.enabled,
            "topology_path": self._topology_path(node),
        }

    def _display_name(
        self,
        node: TopologyNode,
    ) -> str:
        # Для проводных и других классических провайдерских
        # интерфейсов поле description — это название, заданное
        # пользователем в Keenetic или в мастере создания. Оно
        # имеет приоритет над автоматически сформированным
        # названием по типу и номеру порта. Для Wireless ISP
        # сохраняем привычное отображение диапазона 2.4/5 ГГц.
        if (
            node.description
            and not node.transport.startswith("Wireless ISP")
            and node.role != "vpn"
        ):
            return node.description

        if node.transport.startswith("Wireless ISP"):
            return node.transport

        if node.transport == "Mobile":
            return "Мобильный интернет"

        if node.transport == "PPPoE":
            return "PPPoE-подключение"

        if node.role == "vpn":
            return node.name or node.interface_id

        if node.physical_label != "—":
            return node.physical_label

        if node.transport.startswith("Ethernet"):
            return "Ethernet-подключение"

        return node.name or node.interface_id

    def _topology_path(
        self,
        node: TopologyNode,
    ) -> list[str]:
        result: list[str] = []

        if node.physical_interface != "—":
            result.append(node.physical_interface)

        if (
            node.parent_id
            and node.parent_id not in result
        ):
            result.append(node.parent_id)

        if node.interface_id not in result:
            result.append(node.interface_id)

        return result

    def _find_parent_id(
        self,
        interface_id: str,
        item: dict[str, Any],
        wireless_isp: bool,
        vpn: bool,
        physical_info: dict[str, str],
    ) -> str | None:
        if physical_info.get("id"):
            return str(physical_info["id"])

        via = item.get("via")

        if isinstance(via, str) and via.strip():
            return via.strip()

        if wireless_isp and "/WifiStation" in interface_id:
            return interface_id.split(
                "/WifiStation",
                maxsplit=1,
            )[0]

        if "/Vlan" in interface_id:
            return interface_id.split(
                "/Vlan",
                maxsplit=1,
            )[0]

        if vpn:
            tunnel_via = item.get("via")

            if isinstance(tunnel_via, str):
                return tunnel_via.strip() or None

        return None

    def _link_parent_children(
        self,
        nodes: dict[str, TopologyNode],
    ) -> None:
        for node in nodes.values():
            if not node.parent_id:
                continue

            parent = nodes.get(node.parent_id)

            if (
                parent is not None
                and node.interface_id
                not in parent.children
            ):
                parent.children.append(
                    node.interface_id
                )

    def _build_physical_port_links(
        self,
    ) -> dict[str, dict[str, str]]:
        result: dict[
            str,
            dict[str, str],
        ] = {}

        for item in self.interface_data.values():
            if not isinstance(item, dict):
                continue

            roles = item.get("role")

            if not isinstance(roles, list):
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

            for role_item in roles:
                if not isinstance(role_item, dict):
                    continue

                if str(
                    role_item.get("role")
                    or ""
                ).strip().lower() != "inet":
                    continue

                target = str(
                    role_item.get("for")
                    or ""
                ).strip()

                if not target:
                    continue

                result[target] = {
                    "id": physical_id,
                    "label": label,
                    "port_role": (
                        "WAN"
                        if self._truthy(
                            item.get("public")
                        )
                        else "Интернет-порт"
                    ),
                }

        return result

    def _detect_dedicated_wan_port(
        self,
        interface_id: str,
        interface_type: str,
        item: dict[str, Any],
    ) -> dict[str, str]:
        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if not any(
            marker in text
            for marker in (
                "gigabitethernet",
                "ethernet",
            )
        ):
            return {}

        if not (
            self._truthy(item.get("global"))
            or self._truthy(item.get("defaultgw"))
            or interface_id == self.gateway_interface
        ):
            return {}

        port = item.get("port")

        if not isinstance(port, dict):
            return {}

        return {
            "id": str(
                port.get("id")
                or interface_id
            ).strip(),
            "label": "",
            "port_role": "WAN-порт",
        }

    def _detect_transport(
        self,
        interface_id: str,
        interface_type: str,
        wireless_isp: bool,
        vpn: bool,
        physical_info: dict[str, str],
    ) -> str:
        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if vpn:
            return interface_type or "VPN"

        if wireless_isp:
            if "wifimaster0" in text:
                return "Wireless ISP 2.4 ГГц"
            if "wifimaster1" in text:
                return "Wireless ISP 5 ГГц"
            return "Wireless ISP"

        if "pppoe" in text:
            return "PPPoE"

        if any(
            marker in text
            for marker in self.MOBILE_MARKERS
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

    def _is_internet_candidate(
        self,
        item: dict[str, Any],
        interface_id: str,
        interface_type: str,
        wireless_isp: bool,
        has_physical_link: bool,
    ) -> bool:
        if wireless_isp or has_physical_link:
            return True

        if self._truthy(item.get("global")):
            return True

        if self._truthy(item.get("defaultgw")):
            return True

        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        return any(
            marker in text
            for marker in (
                "pppoe",
                *self.MOBILE_MARKERS,
            )
        )

    def _is_wireless_isp(
        self,
        interface_id: str,
        interface_type: str,
        name: str,
    ) -> bool:
        text = " ".join(
            (
                interface_id,
                interface_type,
                name,
            )
        ).lower()

        return any(
            marker in text
            for marker in (
                "wifistation",
                "wirelessisp",
                "wireless isp",
            )
        )

    def _is_vpn(
        self,
        interface_id: str,
        interface_type: str,
    ) -> bool:
        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        return any(
            marker in text
            for marker in self.VPN_MARKERS
        )

    def _is_online(
        self,
        item: dict[str, Any],
    ) -> bool:
        link = str(
            item.get("link")
            or ""
        ).lower()
        connected = str(
            item.get("connected")
            or ""
        ).lower()
        state = str(
            item.get("state")
            or ""
        ).lower()

        summary_link = ""
        summary = item.get("summary")

        if isinstance(summary, dict):
            layer = summary.get("layer")

            if isinstance(layer, dict):
                summary_link = str(
                    layer.get("link")
                    or ""
                ).lower()

        return (
            link == "up"
            and connected in {
                "yes",
                "true",
                "1",
            }
            and state == "up"
            and summary_link in {
                "",
                "running",
            }
        )

    def _is_enabled(
        self,
        item: dict[str, Any],
    ) -> bool:
        state = str(
            item.get("state")
            or ""
        ).lower()

        conf = ""
        summary = item.get("summary")

        if isinstance(summary, dict):
            layer = summary.get("layer")

            if isinstance(layer, dict):
                conf = str(
                    layer.get("conf")
                    or ""
                ).lower()

        return not (
            state == "down"
            and conf == "disabled"
        ) and conf != "disabled"

    def _extract_gateway(
        self,
        item: dict[str, Any],
    ) -> str:
        for key in (
            "gateway",
            "peer",
            "remote-address",
            "remote-endpoint-address",
            "via",
        ):
            value = item.get(key)

            if isinstance(value, str) and value.strip():
                return value.strip()

        return "—"

    def _format_physical_port(
        self,
        physical_info: dict[str, str],
    ) -> str:
        if not physical_info:
            return "—"

        role = str(
            physical_info.get("port_role")
            or "Интернет-порт"
        ).strip()

        label = str(
            physical_info.get("label")
            or ""
        ).strip()

        physical_id = str(
            physical_info.get("id")
            or ""
        ).strip()

        if not label:
            match = re.search(
                r"/(\d+)$",
                physical_id,
            )

            if match and role != "WAN-порт":
                label = str(
                    int(match.group(1)) + 1
                )

        if label:
            if "порт" in role.lower():
                return f"{role} {label}"
            return f"{role}, порт {label}"

        return role

    def _connection_sort_key(
        self,
        connection: dict[str, Any],
    ) -> tuple[int, str]:
        connection_type = str(
            connection.get("type")
            or ""
        ).lower()

        if "ethernet" in connection_type:
            order = 0
        elif "wireless isp" in connection_type:
            order = 1
        elif "mobile" in connection_type:
            order = 2
        elif "pppoe" in connection_type:
            order = 3
        else:
            order = 4

        return (
            order,
            str(
                connection.get("display_name")
                or ""
            ),
        )

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value != 0

        if isinstance(value, str):
            return value.strip().lower() in {
                "yes",
                "true",
                "1",
                "on",
                "enabled",
            }

        return False
