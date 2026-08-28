from __future__ import annotations

import copy
import re
from typing import Any


class WiredDeletionInspectionService:
    """Проверка возможности безопасного удаления проводных подключений."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def inspect_dedicated_wan_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """
        Проверяет удаление подключения на отдельном физическом WAN.

        Сам аппаратный Ethernet-интерфейс не удаляется. Он
        включается в домашний Bridge и становится LAN-портом,
        как это делает официальный веб-интерфейс Keenetic.
        """
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация подключения не найдена."

        source_ip = item.get("ip")
        source_global = (
            source_ip.get("global")
            if isinstance(source_ip, dict)
            else None
        )

        if not isinstance(source_global, dict):
            return {}, "Подключение не является активным интернет-каналом."

        protocol = self.client.cable_protocol_from_config(name, item)

        if protocol == "none":
            transport_id = name
        else:
            connect = item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

        if not re.fullmatch(
            r"(?:GigabitEthernet|FastEthernet|Ethernet)\d+",
            transport_id,
            flags=re.IGNORECASE,
        ):
            return {}, "Подключение не использует отдельный аппаратный WAN."

        transport_item = interface_config.get(transport_id)

        if not isinstance(transport_item, dict):
            return {}, "Конфигурация аппаратного WAN не найдена."

        runtime_map = (
            interface_runtime
            if isinstance(interface_runtime, dict)
            else {}
        )
        runtime_info = self.client.dedicated_wan_runtime_info(
            transport_id=transport_id,
            interface_data=runtime_map,
        )

        if not runtime_info:
            return {}, "Не удалось подтвердить физический WAN-порт."

        for _candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = self.client.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                return {}, "Аппаратный WAN уже включён в локальный Bridge."

        references: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            connect = candidate.get("connect")
            via = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if protocol == "none":
            if references:
                return (
                    {},
                    "Аппаратный WAN используется протоколом "
                    + ", ".join(references)
                    + ".",
                )
        elif set(references) != {name}:
            return (
                {},
                "Аппаратный WAN используется несколькими протоколами; "
                "автоматическое удаление заблокировано.",
            )

        bridge_id = self.client.find_home_bridge_id(interface_config)

        if not bridge_id:
            return {}, "Не удалось определить домашний сетевой Bridge."

        runtime_transport = runtime_map.get(transport_id)
        link = str(runtime_info.get("link") or "unknown").lower()
        connected = ""
        state = ""

        if isinstance(runtime_transport, dict):
            connected = str(
                runtime_transport.get("connected") or ""
            ).strip().lower()
            state = str(
                runtime_transport.get("state") or ""
            ).strip().lower()

        link_known = bool(
            link in {"up", "down"}
            or connected in {"yes", "no", "true", "false", "1", "0"}
            or state in {"up", "down", "connected", "disconnected"}
        )
        link_active = bool(
            link == "up"
            or connected in {"yes", "true", "1"}
            or state in {"up", "connected"}
        )

        ping_profile = self.client.assigned_ping_profile(item)

        try:
            global_order = int(source_global.get("order"))
        except (TypeError, ValueError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "protocol": protocol,
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "bridge_id": bridge_id,
                "port_id": str(runtime_info.get("port_id") or transport_id),
                "port_label": "WAN",
                "port_number": str(runtime_info.get("port_number") or "0"),
                "port_link_known": link_known,
                "port_link_active": link_active,
                "port_link": link or state or "unknown",
                "port_speed": runtime_info.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": (
                    self.client.interface_is_enabled(
                        runtime_map.get(name)
                    )
                    if isinstance(runtime_map.get(name), dict)
                    else item.get("up") is not False
                ),
                "transport_was_up": (
                    self.client.interface_is_enabled(runtime_transport)
                    if isinstance(runtime_transport, dict)
                    else transport_item.get("up") is not False
                ),
                "description": str(
                    item.get("description")
                    or item.get("rename")
                    or name
                ).strip(),
            },
            "",
        )

    def inspect_wired_pppoe_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация PPPoE не найдена."

        is_pppoe = bool(item.get("pppoe")) or bool(
            re.fullmatch(r"PPPoE\d+", name)
        )

        if not is_pppoe:
            return {}, "Это подключение не является PPPoE."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = self.client.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт PPPoE.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт PPPoE не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт PPPoE одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = self.client.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт PPPoE включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in self.client.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт PPPoE.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "PPPoE использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "PPPoE использует неподдерживаемый порт."

        access_vid = self.client.extract_port_access_vid(port_item)
        switch_mode = self.client.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом PPPoE.",
            )

        home_vlan_id = self.client.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = self.client.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": self.client.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": self.client.truthy(item.get("up")),
                "transport_was_up": self.client.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )

    def inspect_wired_pptp_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация PPTP не найдена."

        is_pptp = bool(
            re.fullmatch(r"PPTP\d+", name)
        )

        if not is_pptp:
            return {}, "Это подключение не является PPTP."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = self.client.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт PPTP.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт PPTP не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт PPTP одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = self.client.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт PPTP включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in self.client.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт PPTP.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "PPTP использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "PPTP использует неподдерживаемый порт."

        access_vid = self.client.extract_port_access_vid(port_item)
        switch_mode = self.client.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом PPTP.",
            )

        home_vlan_id = self.client.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = self.client.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": self.client.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": self.client.truthy(item.get("up")),
                "transport_was_up": self.client.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )

    def inspect_wired_l2tp_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация L2TP не найдена."

        is_l2tp = bool(
            re.fullmatch(r"L2TP\d+", name)
        )

        if not is_l2tp:
            return {}, "Это подключение не является L2TP."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = self.client.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт L2TP.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт L2TP не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт L2TP одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = self.client.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт L2TP включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in self.client.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт L2TP.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "L2TP использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "L2TP использует неподдерживаемый порт."

        access_vid = self.client.extract_port_access_vid(port_item)
        switch_mode = self.client.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом L2TP.",
            )

        home_vlan_id = self.client.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = self.client.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": self.client.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": self.client.truthy(item.get("up")),
                "transport_was_up": self.client.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )

    def inspect_wired_ipoe_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        parts = self.client.extract_vlan_interface_parts(name)

        unsupported = (
            "В этой версии можно удалить только Проводное "
            "IPoE на переназначенном LAN-порту."
        )

        if parts is None:
            return {}, unsupported

        parent_id, vlan_id = parts
        interface_item = interface_config.get(name)

        if not isinstance(interface_item, dict):
            return {}, "Конфигурация подключения не найдена."

        security = interface_item.get("security-level")
        is_public = False

        if isinstance(security, dict):
            is_public = self.client.truthy(
                security.get("public")
            )
        else:
            is_public = (
                str(security or "")
                .strip()
                .lower()
                == "public"
            )

        ip_config = interface_item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        if not is_public or not isinstance(
            global_config,
            dict,
        ):
            return {}, unsupported

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in self.client.extract_port_roles(port_item):
                role_name = str(
                    role.get("role")
                    or ""
                ).strip().lower()
                target = str(
                    role.get("ifor")
                    or ""
                ).strip()

                if role_name == "inet" and target == name:
                    bindings.append(
                        (str(port_id), port_item)
                    )
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт подключения.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, unsupported

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, unsupported

        access_vid = self.client.extract_port_access_vid(
            port_item
        )
        switch_mode = self.client.extract_port_switch_mode(
            port_item
        )

        if switch_mode != "access":
            return (
                {},
                "Удаление trunk-порта этой версией не поддерживается.",
            )

        if access_vid != vlan_id:
            return (
                {},
                "VLAN интерфейса и назначение LAN-порта не совпадают.",
            )

        home_vlan_id = self.client.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(
            port_item.get("rename")
            or ""
        ).strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = self.client.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )

        assignment = interface_item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile")
                or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        try:
            global_order = int(
                global_config.get("order")
            )
        except (TypeError, ValueError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(
                    interface_item
                ),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": self.client.extract_port_roles(
                    port_item
                ),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(
                    runtime_state.get("known")
                ),
                "port_link_active": bool(
                    runtime_state.get("active")
                ),
                "port_link": str(
                    runtime_state.get("link")
                    or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": self.client.truthy(
                    interface_item.get("up")
                ),
                "description": str(
                    interface_item.get("description")
                    or name
                ).strip(),
            },
            "",
        )
