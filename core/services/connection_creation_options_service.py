from __future__ import annotations

import copy
from typing import Any


class ConnectionCreationOptionsService:
    """Анализ доступных вариантов создания интернет-подключений."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def build_connection_creation_options(
        self,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Возвращает безопасные варианты создания подключений.

        Поддерживаются:
        - свободные access-порты встроенного коммутатора;
        - отдельный физический WAN, если он включён в домашний
          Bridge и в данный момент логически является LAN-портом.
        """
        free_labels: set[str] = set()

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            ipoe = item.get("ipoe")

            if isinstance(ipoe, dict):
                free_labels.update(
                    self.client.split_config_tokens(
                        ipoe.get("free-port")
                    )
                )

        if not free_labels:
            bridge0 = interface_config_data.get(
                "Bridge0"
            )

            if isinstance(bridge0, dict):
                iseg = bridge0.get("iseg")

                if isinstance(iseg, dict):
                    free_labels.update(
                        self.client.split_config_tokens(
                            iseg.get("free-port")
                        )
                    )

        port_runtime_by_label: dict[
            str,
            dict[str, Any],
        ] = {}

        for parent_id, parent in interface_data.items():
            if not isinstance(parent, dict):
                continue

            raw_ports = parent.get("port")
            ports: list[dict[str, Any]] = []

            if isinstance(raw_ports, dict):
                if raw_ports.get("id"):
                    ports = [raw_ports]
                else:
                    ports = [
                        candidate
                        for candidate in raw_ports.values()
                        if isinstance(candidate, dict)
                    ]

            for port in ports:
                label = str(
                    port.get("label")
                    or port.get("interface-name")
                    or ""
                ).strip()
                port_id = str(
                    port.get("id")
                    or ""
                ).strip()

                if not label or not port_id:
                    continue

                port_runtime_by_label[label] = {
                    "port_label": label,
                    "port_display_name": f"LAN{label}",
                    "port_id": port_id,
                    "parent_id": str(parent_id),
                    "link": str(
                        port.get("link")
                        or "unknown"
                    ).strip().lower(),
                    "speed": port.get("speed"),
                    "duplex": port.get("duplex"),
                }

        available_ports: list[dict[str, Any]] = []

        for label in free_labels:
            runtime = port_runtime_by_label.get(label)

            if not runtime:
                continue

            port_id = str(runtime["port_id"])
            port_config = interface_config_data.get(
                port_id
            )

            if not isinstance(port_config, dict):
                continue

            switchport = port_config.get(
                "switchport"
            )

            if not isinstance(switchport, dict):
                continue

            # Порт с Provider-ролью уже является WAN, даже если
            # старое поле free-port ещё не успело обновиться.
            if any(
                str(role.get("role") or "").strip().lower()
                in {"inet", "internet", "wan"}
                for role in self.client.extract_port_roles(port_config)
            ):
                continue

            access = switchport.get("access")
            old_vid = None

            if isinstance(access, dict):
                try:
                    old_vid = int(
                        access.get("vid")
                    )
                except (TypeError, ValueError):
                    old_vid = None

            option = dict(runtime)
            option["mode"] = "switch_vlan"
            option["current_access_vid"] = old_vid
            available_ports.append(option)

        # Отдельный WAN-контроллер становится доступен в мастере,
        # когда он включён в домашний Bridge. Это ровно то
        # состояние, которое создаёт официальный интерфейс после
        # удаления Wired-подключения.
        protocol_transports: set[str] = set()

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            connect = item.get("connect")

            if isinstance(connect, dict):
                via = str(
                    connect.get("via") or ""
                ).strip()

                if via:
                    protocol_transports.add(via)

        for bridge_id, bridge_item in interface_config_data.items():
            if not isinstance(bridge_item, dict):
                continue

            if not str(bridge_id).lower().startswith("bridge"):
                continue

            for included_id in self.client.collect_included_interfaces(
                bridge_item.get("include")
            ):
                if included_id in protocol_transports:
                    continue

                runtime = self.client.dedicated_wan_runtime_info(
                    transport_id=included_id,
                    interface_data=interface_data,
                )

                if not runtime:
                    continue

                config_item = interface_config_data.get(included_id)

                if not isinstance(config_item, dict):
                    continue

                ip_config = config_item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    continue

                security = config_item.get("security-level")
                is_public = False

                if isinstance(security, dict):
                    is_public = self.client.truthy(
                        security.get("public")
                    )
                else:
                    is_public = (
                        str(security or "").strip().lower()
                        == "public"
                    )

                if is_public:
                    continue

                option = dict(runtime)
                option.update(
                    {
                        "mode": "dedicated_wan",
                        "bridge_id": str(bridge_id),
                        "current_access_vid": None,
                    }
                )
                available_ports.append(option)

        def port_sort_key(
            item: dict[str, Any],
        ) -> tuple[int, int, str]:
            if str(item.get("mode") or "") == "dedicated_wan":
                return (0, 0, "WAN")

            label = str(
                item.get("port_label")
                or ""
            )

            try:
                return (1, int(label), label)
            except ValueError:
                return (1, 9999, label)

        # Удаляем возможные дубли, если одна и та же runtime-запись
        # встретилась одновременно в нескольких представлениях.
        unique_ports: dict[tuple[str, str], dict[str, Any]] = {}

        for option in available_ports:
            key = (
                str(option.get("mode") or ""),
                str(option.get("port_id") or ""),
            )
            unique_ports[key] = option

        available_ports = list(unique_ports.values())
        available_ports.sort(key=port_sort_key)

        priority_count = 0

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            ip_config = item.get("ip")

            if not isinstance(ip_config, dict):
                continue

            global_config = ip_config.get(
                "global"
            )

            if (
                isinstance(global_config, dict)
                and global_config.get("order")
                is not None
            ):
                priority_count += 1

        cable_options = {
            "available": bool(available_ports),
            "ports": available_ports,
            "existing_priority_count": priority_count,
            "scope": "switch_and_dedicated_wan",
            "protocols": [
                {
                    "id": "none",
                    "title": "Не использовать",
                    "available": True,
                },
                {
                    "id": "pppoe",
                    "title": "PPPoE",
                    "available": True,
                },
                {
                    "id": "pptp",
                    "title": "PPTP",
                    "available": True,
                },
                {
                    "id": "l2tp",
                    "title": "L2TP",
                    "available": True,
                },
            ],
        }

        wireless_bands: list[dict[str, Any]] = []

        for band_id, title, master_id in (
            ("2.4", "2,4 ГГц", "WifiMaster0"),
            ("5", "5 ГГц", "WifiMaster1"),
        ):
            station_id = f"{master_id}/WifiStation0"
            runtime_item = interface_data.get(station_id)
            config_item = interface_config_data.get(station_id)
            supported = isinstance(runtime_item, dict) or isinstance(
                config_item,
                dict,
            )
            item = config_item if isinstance(config_item, dict) else {}
            runtime = runtime_item if isinstance(runtime_item, dict) else {}
            ip_config = item.get("ip")
            global_config = (
                ip_config.get("global")
                if isinstance(ip_config, dict)
                else None
            )
            ssid = str(
                item.get("ssid")
                or runtime.get("ssid")
                or ""
            ).strip()
            configured = self.client.wireless_isp_configured_item(
                item,
                runtime,
            )
            wireless_bands.append(
                {
                    "id": band_id,
                    "title": title,
                    "interface_id": station_id,
                    "master_id": master_id,
                    "supported": supported,
                    "configured": configured,
                    "available": bool(supported and not configured),
                    "ssid": ssid,
                    "description": str(
                        item.get("description")
                        or runtime.get("description")
                        or ""
                    ).strip(),
                    "link": str(
                        runtime.get("link")
                        or "unknown"
                    ).strip().lower(),
                }
            )

        wireless_options = {
            "available": any(
                bool(item.get("available"))
                for item in wireless_bands
            ),
            "bands": wireless_bands,
            "existing_priority_count": priority_count,
        }

        return {
            "ethernet.cable": copy.deepcopy(cable_options),
            # Совместимость с мастером и тестами ветки 0.19.x.
            "ethernet.ipoe": copy.deepcopy(cable_options),
            "wireless_isp": wireless_options,
        }
