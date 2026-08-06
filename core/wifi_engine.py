from __future__ import annotations

import re
from typing import Any

from core.link_rate import (
    collect_rate_like_fields,
    first_link_rate_mbps,
)


class WifiEngine:
    """Собирает Wi-Fi сети и клиентов из ответов Keenetic RCI."""

    def __init__(
        self,
        interface_data: dict[str, Any],
        associations_data: Any,
        hotspot_data: Any,
    ) -> None:
        self.interface_data = (
            interface_data
            if isinstance(interface_data, dict)
            else {}
        )
        self.associations_data = associations_data
        self.hotspot_data = hotspot_data
        self._ethernet_ports = self._build_ethernet_port_index()

    def build(self) -> list[dict[str, Any]]:
        hosts = self._build_hotspot_index()
        wifi_clients = self._build_clients(hosts)
        networks = self._build_networks()

        network_by_ap = {
            str(network["interface_id"]).lower(): network
            for network in networks
        }

        unknown_networks: dict[str, dict[str, Any]] = {}

        for client in wifi_clients:
            ap = str(client.get("ap") or "").strip()
            key = ap.lower()
            network = network_by_ap.get(key)

            if network is None:
                network = unknown_networks.get(key)

            if network is None:
                network = {
                    "interface_id": ap or "unknown",
                    "name": ap or "Неизвестная Wi-Fi сеть",
                    "ssid": "Не определён",
                    "description": "",
                    "group": "Другая Wi-Fi сеть",
                    "network_type": "wifi",
                    "band": self._band_from_text(ap),
                    "channel": "—",
                    "security": "—",
                    "enabled": True,
                    "online": True,
                    "clients": [],
                }
                unknown_networks[key] = network

            network["clients"].append(client)

        networks.extend(unknown_networks.values())

        # Если 2.4 и 5 ГГц используют один SSID, объединяем
        # их в одну карточку. Клиенты сохраняют свой диапазон.
        networks = self._merge_same_ssid_networks(
            networks
        )

        wifi_macs = {
            self._normalize_mac(client.get("mac"))
            for client in wifi_clients
            if client.get("mac")
        }

        wired_clients = self._build_wired_clients(
            hosts=hosts,
            wifi_macs=wifi_macs,
        )

        networks.append(
            {
                "interface_id": "wired-clients",
                "name": "Проводные подключения",
                "ssid": "",
                "description": (
                    "Устройства, подключённые к Ethernet-портам"
                ),
                "group": "Проводные подключения",
                "network_type": "wired",
                "band": "Ethernet",
                "channel": "—",
                "security": "—",
                "enabled": True,
                "online": bool(wired_clients),
                "clients": wired_clients,
            }
        )

        for network in networks:
            network.setdefault(
                "network_type",
                "wifi",
            )
            for client in network["clients"]:
                traffic_bps = (
                    self._number_or_zero(
                        client.get("rx_bps")
                    )
                    + self._number_or_zero(
                        client.get("tx_bps")
                    )
                )
                client["traffic_active"] = (
                    traffic_bps > 1024
                )

            network["clients"].sort(
                key=lambda item: (
                    0
                    if bool(
                        item.get("traffic_active")
                    )
                    else 1,
                    str(
                        item.get("name")
                        or ""
                    ).lower(),
                    str(item.get("mac") or ""),
                )
            )
            network["client_count"] = len(
                network["clients"]
            )
            network["unregistered_count"] = sum(
                1
                for client in network["clients"]
                if not bool(
                    client.get("registered")
                )
            )

        # Настоящие настроенные Wi-Fi сети сохраняются в
        # результате независимо от состояния enabled.
        # Благодаря этому после выключения карточка остаётся
        # доступной и позволяет включить сеть обратно.
        networks = [
            network
            for network in networks
            if (
                network.get("network_type") == "wired"
            )
            or (
                network.get("network_type") == "wifi"
                and (
                    self._valid_ssid(
                        network.get("ssid")
                    )
                    or network.get(
                        "client_count",
                        0,
                    ) > 0
                )
            )
        ]

        group_order = {
            "Основная сеть": 0,
            "Гостевая сеть": 1,
            "Проводные подключения": 2,
            "Другая Wi-Fi сеть": 3,
        }

        networks.sort(
            key=lambda item: (
                group_order.get(
                    str(item.get("group") or ""),
                    9,
                ),
                str(item.get("band") or ""),
                str(item.get("ssid") or ""),
            )
        )
        return networks

    def build_inventory(
        self,
        networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build a deduplicated active and known-client inventory."""
        hosts = self._build_hotspot_index()
        result: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for network in networks:
            network_type = str(
                network.get("network_type") or "wifi"
            ).strip().lower()
            for raw_client in network.get("clients") or []:
                if not isinstance(raw_client, dict):
                    continue
                client = dict(raw_client)
                mac = self._normalize_mac(client.get("mac"))
                if not mac:
                    continue

                host = hosts.get(mac, {})
                client["mac"] = mac
                client["online"] = True
                client["blocked"] = self._host_blocked(host)
                client.setdefault("connection_kind", network_type)
                client.setdefault("network_name", network.get("name"))
                client.setdefault("network_group", network.get("group"))
                client.setdefault("ssid", network.get("ssid"))
                client.setdefault("channel", network.get("channel"))
                if network_type != "wired":
                    client.setdefault("band", network.get("band"))
                client.setdefault(
                    "hostname",
                    self._first_text(
                        host,
                        ("hostname", "host-name"),
                    ),
                )
                client.setdefault(
                    "vendor",
                    self._first_text(
                        host,
                        (
                            "vendor",
                            "manufacturer",
                            "manufacturer-name",
                        ),
                    ),
                )
                client.setdefault(
                    "last_seen",
                    self._host_last_seen(host),
                )
                if mac not in result:
                    result[mac] = client
                    order.append(mac)
                    continue

                existing = result[mac]
                if (
                    existing.get("connection_kind") == "wired"
                    and client.get("connection_kind") == "wifi"
                ):
                    existing, client = client, existing
                    result[mac] = existing

                existing["online"] = bool(
                    existing.get("online")
                    or client.get("online")
                )
                existing["blocked"] = bool(
                    existing.get("blocked")
                    or client.get("blocked")
                )
                for field, value in client.items():
                    if existing.get(field) in (None, "", "-", "—"):
                        if value not in (None, "", "-", "—"):
                            existing[field] = value

        for mac, host in hosts.items():
            if mac in result:
                existing = result[mac]
                existing["blocked"] = self._host_blocked(host)
                segment_id = self._host_segment_id(host)
                if segment_id:
                    existing["segment_id"] = segment_id
                continue

            connection_kind = self._known_connection_kind(host)
            online = self._host_is_active(host)
            segment_id = self._host_segment_id(host)
            result[mac] = {
                "connection_kind": connection_kind,
                "connection_label": {
                    "wifi": "Wi‑Fi",
                    "wired": "Ethernet",
                }.get(connection_kind, "Не определено"),
                "mac": mac,
                "name": self._host_display_name(host, mac),
                "hostname": self._first_text(
                    host,
                    ("hostname", "host-name"),
                ),
                "ip": self._first_text(
                    host,
                    ("ip", "address", "ipv4", "host"),
                ) or "—",
                "registered": self._host_registered(host),
                "online": online,
                "blocked": self._host_blocked(host),
                "traffic_active": False,
                "policy": self._first_text(host, ("policy", "access"))
                or "—",
                "segment": segment_id or self._first_text(
                    host,
                    ("segment", "network", "bridge"),
                ) or "—",
                "segment_id": segment_id,
                "interface": self._first_text(
                    host,
                    (
                        "interface",
                        "bridge-port",
                        "bridge_port",
                        "port",
                    ),
                ) or "—",
                "vendor": self._first_text(
                    host,
                    (
                        "vendor",
                        "manufacturer",
                        "manufacturer-name",
                    ),
                ) or "—",
                "last_seen": self._host_last_seen(host),
            }
            order.append(mac)

        return [result[key] for key in order]

    def _known_connection_kind(
        self,
        host: dict[str, Any],
    ) -> str:
        if self._host_has_wireless_link(host):
            return "wifi"

        values = " ".join(
            str(host.get(key) or "")
            for key in (
                "type",
                "connection",
                "medium",
                "media",
                "link-type",
                "interface",
                "via",
                "port",
            )
        ).lower()
        if any(
            marker in values
            for marker in (
                "wifi",
                "wi-fi",
                "wireless",
                "wlan",
                "accesspoint",
                "wifimaster",
            )
        ):
            return "wifi"
        if any(
            marker in values
            for marker in (
                "ethernet",
                "gigabit",
                "fastethernet",
                "lan",
                "port",
            )
        ):
            return "wired"
        return "unknown"

    @staticmethod
    def _host_blocked(host: dict[str, Any]) -> bool:
        for key in ("blocked", "denied", "rejected"):
            if key in host and WifiEngine._truthy(host.get(key)):
                return True

        policy = str(
            host.get("policy")
            or host.get("access")
            or ""
        ).strip().lower()
        return any(
            marker in policy
            for marker in ("block", "deny", "reject")
        )

    def _merge_same_ssid_networks(
        self,
        networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Объединяет точки доступа 2.4/5/6 ГГц с одинаковым SSID.

        Результирующая карточка хранит список диапазонов и
        интерфейсов, а каждый клиент сохраняет собственный band.
        """
        merged: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        for network in networks:
            if network.get("network_type") != "wifi":
                key = (
                    str(network.get("group") or ""),
                    str(network.get("interface_id") or ""),
                )
                merged[key] = network
                continue

            ssid = str(
                network.get("ssid")
                or ""
            ).strip()

            group = str(
                network.get("group")
                or "Основная сеть"
            ).strip()

            # Не объединяем неизвестные сети без нормального SSID.
            if not self._valid_ssid(ssid):
                key = (
                    group,
                    str(
                        network.get("interface_id")
                        or ""
                    ),
                )
                merged[key] = network
                continue

            key = (
                group.lower(),
                ssid.lower(),
            )

            if key not in merged:
                copy = dict(network)
                initial_band = str(
                    network.get("band")
                    or "Wi-Fi"
                )
                initial_interface = str(
                    network.get("interface_id")
                    or ""
                )
                copy["bands"] = [initial_band]
                copy["channels"] = [
                    str(
                        network.get("channel")
                        or "—"
                    )
                ]
                copy["interfaces"] = [initial_interface]
                copy["band_states"] = {
                    initial_band: {
                        "band": initial_band,
                        "interface_id": initial_interface,
                        "enabled": bool(
                            network.get("enabled", True)
                        ),
                        "online": bool(
                            network.get(
                                "online",
                                network.get("enabled", True),
                            )
                        ),
                        "channel": str(
                            network.get("channel")
                            or "—"
                        ),
                        "security": str(
                            network.get("security")
                            or "—"
                        ),
                    }
                }
                copy["clients"] = list(
                    network.get("clients")
                    or []
                )
                merged[key] = copy
                continue

            target = merged[key]

            band = str(
                network.get("band")
                or "Wi-Fi"
            )

            if band not in target["bands"]:
                target["bands"].append(band)

            channel = str(
                network.get("channel")
                or "—"
            )

            if (
                channel not in target["channels"]
                and channel != "—"
            ):
                target["channels"].append(channel)

            interface_id = str(
                network.get("interface_id")
                or ""
            )

            if (
                interface_id
                and interface_id
                not in target["interfaces"]
            ):
                target["interfaces"].append(
                    interface_id
                )

            band_states = target.setdefault(
                "band_states",
                {},
            )
            if isinstance(band_states, dict):
                band_states[band] = {
                    "band": band,
                    "interface_id": interface_id,
                    "enabled": bool(
                        network.get("enabled", True)
                    ),
                    "online": bool(
                        network.get(
                            "online",
                            network.get("enabled", True),
                        )
                    ),
                    "channel": str(
                        network.get("channel")
                        or "—"
                    ),
                    "security": str(
                        network.get("security")
                        or "—"
                    ),
                }

            target["enabled"] = bool(
                target.get("enabled")
                or network.get("enabled")
            )
            target["online"] = bool(
                target.get("online")
                or network.get("online")
            )

            target["clients"].extend(
                network.get("clients")
                or []
            )

        result: list[dict[str, Any]] = []

        for network in merged.values():
            bands = network.get("bands")

            if isinstance(bands, list) and bands:
                bands.sort(
                    key=self._band_sort_order
                )
                network["band"] = " / ".join(bands)

            channels = network.get("channels")

            if isinstance(channels, list) and channels:
                network["channel"] = " / ".join(
                    channel
                    for channel in channels
                    if channel != "—"
                ) or "—"

            interfaces = network.get("interfaces")

            if isinstance(interfaces, list) and interfaces:
                network["interface_id"] = ", ".join(
                    interface_id
                    for interface_id in interfaces
                    if interface_id
                )

            band_states = network.get("band_states")
            if isinstance(band_states, dict) and band_states:
                enabled_values = [
                    bool(item.get("enabled"))
                    for item in band_states.values()
                    if isinstance(item, dict)
                ]
                network["all_enabled"] = bool(
                    enabled_values
                    and all(enabled_values)
                )
                network["partially_enabled"] = bool(
                    enabled_values
                    and any(enabled_values)
                    and not all(enabled_values)
                )
                network["enabled"] = any(enabled_values)

            result.append(network)

        return result

    @staticmethod
    def _band_sort_order(
        band: str,
    ) -> int:
        text = str(band).lower()

        if "2.4" in text:
            return 0

        if "5" in text:
            return 1

        if "6" in text:
            return 2

        return 3

    def _build_networks(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for key, item in self.interface_data.items():
            if not isinstance(item, dict):
                continue

            interface_id = str(item.get("id") or key).strip()
            interface_type = str(item.get("type") or "").strip()
            text = f"{interface_id} {interface_type}".lower()

            if (
                "accesspoint" not in text
                and "access point" not in text
            ):
                continue

            ssid = self._first_text(
                item,
                (
                    "ssid",
                    "network-name",
                    "network_name",
                ),
            )

            description = str(
                item.get("description")
                or ""
            ).strip()

            # Не создаём карточки для служебных AP-интерфейсов,
            # у которых нет настоящего SSID.
            if not self._valid_ssid(ssid):
                continue

            if self._is_service_access_point(
                interface_id=interface_id,
                interface_type=interface_type,
                ssid=ssid,
                description=description,
                item=item,
            ):
                continue

            group = self._network_group(
                interface_id=interface_id,
                ssid=ssid,
                description=description,
                item=item,
            )

            result.append(
                {
                    "interface_id": interface_id,
                    "name": str(
                        item.get("interface-name")
                        or ssid
                        or interface_id
                    ),
                    "ssid": ssid,
                    "description": description,
                    "group": group,
                    "network_type": "wifi",
                    "band": self._band_from_text(interface_id),
                    "channel": self._extract_channel(item),
                    "security": self._extract_security(item),
                    "enabled": self._interface_enabled(item),
                    "online": self._interface_online(item),
                    "clients": [],
                }
            )

        return result

    def _build_clients(
        self,
        hosts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        stations: list[dict[str, Any]] = []
        self._collect_station_dicts(
            self.associations_data,
            stations,
        )
        self._append_hotspot_stations(
            hosts,
            stations,
        )

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for station in stations:
            mac = self._normalize_mac(
                self._first_text(station, ("mac",))
            )
            ap = self._first_text(
                station,
                ("ap", "interface", "access-point"),
            ) or ""

            if not mac:
                continue

            key = (mac, ap.lower())

            if key in seen:
                continue

            seen.add(key)
            host = hosts.get(mac, {})
            segment_id = self._host_segment_id(host)

            result.append(
                {
                    "connection_kind": "wifi",
                    "mac": mac,
                    "ap": ap,
                    "interface": ap,
                    "band": self._band_from_text(ap),
                    "name": self._host_display_name(host, mac),
                    "ip": self._first_text(
                        host,
                        (
                            "ip",
                            "address",
                            "ipv4",
                            "host",
                        ),
                    ) or "—",
                    "registered": self._host_registered(host),
                    "authenticated": self._truthy(
                        station.get("authenticated")
                    ),
                    "rssi": self._number_or_none(
                        station.get("rssi")
                    ),
                    "signal_quality": self._signal_quality(
                        self._number_or_none(station.get("rssi"))
                    ),
                    # Скорость PHY от роутера к клиенту.
                    "txrate": first_link_rate_mbps(
                        station,
                        (
                            "txrate",
                            "tx-rate",
                            "tx_rate",
                            "transmit-rate",
                            "transmit_rate",
                            "downlink-rate",
                            "downlink_rate",
                            "rate-tx",
                            "rate_tx",
                            "tx-link-rate",
                            "tx_link_rate",
                            "last-tx-rate",
                            "last_tx_rate",
                        ),
                    ),
                    # Скорость PHY от клиента к роутеру.
                    # Набор полей отличается между моделями
                    # и версиями KeeneticOS.
                    "rxrate": first_link_rate_mbps(
                        station,
                        (
                            "rxrate",
                            "rx-rate",
                            "rx_rate",
                            "receive-rate",
                            "receive_rate",
                            "uplink-rate",
                            "uplink_rate",
                            "rate-rx",
                            "rate_rx",
                            "client-rate",
                            "client_rate",
                            "rx-link-rate",
                            "rx_link_rate",
                            "last-rx-rate",
                            "last_rx_rate",
                            "rx-bitrate",
                            "rx_bitrate",
                            "receive-bitrate",
                            "receive_bitrate",
                            "last-rx-bitrate",
                            "last_rx_bitrate",
                            "rx-phy-rate",
                            "rx_phy_rate",
                            "receive-phy-rate",
                            "receive_phy_rate",
                            "uplink-phy-rate",
                            "uplink_phy_rate",
                            "client-tx-rate",
                            "client_tx_rate",
                            "station-tx-rate",
                            "station_tx_rate",
                            "sta-tx-rate",
                            "sta_tx_rate",
                        ),
                    ),
                    "rate_debug": collect_rate_like_fields(
                        station
                    ),
                    "txbytes": self._number_or_zero(
                        station.get("txbytes")
                    ),
                    "rxbytes": self._number_or_zero(
                        station.get("rxbytes")
                    ),
                    "tx_bps": self._first_number(
                        station,
                        (
                            "tx-bps",
                            "tx_bps",
                            "txspeed",
                            "tx-speed",
                            "transmit-bps",
                        ),
                    ) or 0,
                    "rx_bps": self._first_number(
                        station,
                        (
                            "rx-bps",
                            "rx_bps",
                            "rxspeed",
                            "rx-speed",
                            "receive-bps",
                        ),
                    ) or 0,
                    "uptime": self._format_uptime(
                        self._number_or_zero(
                            station.get("uptime")
                        )
                    ),
                    "mode": self._first_text(
                        station,
                        ("mode",),
                    ) or "—",
                    "channel_width": self._first_text(
                        station,
                        ("ht", "width", "channel-width"),
                    ) or "—",
                    "mcs": self._first_text(
                        station,
                        ("mcs",),
                    ) or "—",
                    "policy": self._first_text(
                        host,
                        ("policy",),
                    ) or "—",
                    "segment": self._first_text(
                        host,
                        ("segment", "via"),
                    ) or segment_id or "—",
                    "segment_id": segment_id,
                }
            )

        return result

    def _append_hotspot_stations(
        self,
        hosts: dict[str, dict[str, Any]],
        stations: list[dict[str, Any]],
    ) -> None:
        """
        Дополняет associations данными из show ip hotspot.

        KeeneticOS 5.00 часть активных Wi-Fi клиентов возвращает только
        во вложенном объекте ``mws``. Без этого дополнения такие устройства
        ошибочно попадали в список Ethernet-клиентов.
        """
        known_macs = {
            self._normalize_mac(
                self._first_text(station, ("mac",))
            )
            for station in stations
        }

        for mac, host in hosts.items():
            normalized_mac = self._normalize_mac(mac)

            if (
                not normalized_mac
                or normalized_mac in known_macs
                or not self._host_is_active(host)
            ):
                continue

            station: dict[str, Any] | None = None

            if self._has_wireless_station_fields(host):
                station = dict(host)
            else:
                mws = host.get("mws")

                if (
                    isinstance(mws, dict)
                    and self._has_wireless_station_fields(mws)
                ):
                    # Данные хоста содержат имя, IP и счётчики, а mws —
                    # фактическую радиосвязь. Поля mws имеют приоритет.
                    station = dict(host)
                    station.update(mws)
                    station.pop("mws", None)

            if station is None:
                continue

            station["mac"] = normalized_mac
            stations.append(station)
            known_macs.add(normalized_mac)

    @staticmethod
    def _has_wireless_station_fields(
        data: dict[str, Any],
    ) -> bool:
        lowered = {
            str(key).lower()
            for key in data
        }
        return bool(
            lowered.intersection(
                {
                    "ap",
                    "access-point",
                    "rssi",
                    "txrate",
                    "rxrate",
                    "ssid",
                }
            )
            and (
                "ap" in lowered
                or "access-point" in lowered
            )
        )

    def _build_wired_clients(
        self,
        hosts: dict[str, dict[str, Any]],
        wifi_macs: set[str],
    ) -> list[dict[str, Any]]:
        """
        Возвращает активные проводные устройства из show ip hotspot.

        Клиенты, найденные в show associations, исключаются,
        чтобы не было дублирования Wi-Fi устройств.
        """
        result: list[dict[str, Any]] = []

        for mac, host in hosts.items():
            normalized_mac = self._normalize_mac(mac)

            if not normalized_mac or normalized_mac in wifi_macs:
                continue

            if not self._host_is_active(host):
                continue

            if self._host_has_wireless_link(host):
                continue

            connection_type = self._first_text(
                host,
                (
                    "type",
                    "connection",
                    "medium",
                    "media",
                    "link-type",
                ),
            ) or ""

            port = self._resolve_wired_port(host)
            interface_name = str(
                port.get("id")
                or self._first_text(
                    host,
                    (
                        "bridge-port",
                        "bridge_port",
                    ),
                )
                or "—"
            )

            type_text = (
                f"{connection_type} {interface_name}"
            ).lower()

            # Если хост явно обозначен как Wi-Fi, но отсутствует
            # в associations, не показываем его как проводной.
            if any(
                marker in type_text
                for marker in (
                    "wifi",
                    "wi-fi",
                    "wireless",
                    "wlan",
                    "accesspoint",
                    "wifimaster",
                )
            ):
                continue

            link_speed = (
                first_link_rate_mbps(
                    host,
                    (
                        "speed",
                        "link-speed",
                        "link_speed",
                        "port-speed",
                        "port_speed",
                    ),
                )
                or first_link_rate_mbps(
                    port,
                    ("speed", "speed_mbps"),
                )
            )
            txrate = first_link_rate_mbps(
                host,
                (
                    "txrate",
                    "tx-rate",
                    "tx_rate",
                    "transmit-rate",
                    "transmit_rate",
                ),
            ) or link_speed
            rxrate = first_link_rate_mbps(
                host,
                (
                    "rxrate",
                    "rx-rate",
                    "rx_rate",
                    "receive-rate",
                    "receive_rate",
                ),
            ) or link_speed
            port_label = str(port.get("port_label") or "").strip()

            result.append(
                {
                    "connection_kind": "wired",
                    "mac": normalized_mac,
                    "ap": "",
                    "name": self._host_display_name(
                        host,
                        normalized_mac,
                    ),
                    "ip": self._first_text(
                        host,
                        (
                            "ip",
                            "address",
                            "ipv4",
                            "host",
                        ),
                    ) or "—",
                    "registered": self._host_registered(host),
                    "authenticated": True,
                    "rssi": None,
                    "signal_quality": "—",
                    "txrate": txrate,
                    "rxrate": rxrate,
                    "txbytes": self._first_number(
                        host,
                        (
                            "txbytes",
                            "tx-bytes",
                            "sent",
                            "bytes-sent",
                        ),
                    ) or 0,
                    "rxbytes": self._first_number(
                        host,
                        (
                            "rxbytes",
                            "rx-bytes",
                            "received",
                            "bytes-received",
                        ),
                    ) or 0,
                    "tx_bps": self._first_number(
                        host,
                        (
                            "tx-bps",
                            "tx_bps",
                            "txspeed",
                            "tx-speed",
                            "transmit-bps",
                        ),
                    ) or 0,
                    "rx_bps": self._first_number(
                        host,
                        (
                            "rx-bps",
                            "rx_bps",
                            "rxspeed",
                            "rx-speed",
                            "receive-bps",
                        ),
                    ) or 0,
                    "uptime": self._format_uptime(
                        self._first_number(
                            host,
                            (
                                "uptime",
                                "lease-time",
                                "active-time",
                                "last-seen",
                            ),
                        ) or 0
                    ),
                    "mode": "Ethernet",
                    "channel_width": "—",
                    "mcs": "—",
                    "policy": self._first_text(
                        host,
                        ("policy",),
                    ) or "—",
                    "segment": self._first_text(
                        host,
                        (
                            "segment",
                            "network",
                            "bridge",
                        ),
                    ) or "—",
                    "interface": interface_name,
                    "port_label": port_label,
                    "connection_label": (
                        f"Ethernet, {port_label.lower()}"
                        if port_label
                        else "Ethernet"
                    ),
                    "port_speed": link_speed,
                    "interface_enabled": port.get(
                        "enabled",
                        True,
                    ),
                    "vendor": self._first_text(
                        host,
                        (
                            "vendor",
                            "manufacturer",
                            "manufacturer-name",
                        ),
                    ) or "—",
                    "duplex": self._duplex_value(
                        host.get("duplex")
                        if "duplex" in host
                        else port.get("duplex")
                    ),
                }
            )

        return result

    def _host_has_wireless_link(
        self,
        host: dict[str, Any],
    ) -> bool:
        if self._has_wireless_station_fields(host):
            return True

        mws = host.get("mws")
        return (
            isinstance(mws, dict)
            and self._has_wireless_station_fields(mws)
        )

    def _build_ethernet_port_index(
        self,
    ) -> dict[str, dict[str, Any]]:
        """Строит карту внешних номеров портов и физических интерфейсов."""
        index: dict[str, dict[str, Any]] = {}

        def add_port(
            item: dict[str, Any],
            container_key: Any = None,
        ) -> None:
            if str(item.get("type") or "").lower() != "port":
                return

            port_id = str(item.get("id") or "").strip()

            if not port_id:
                return

            label = str(
                item.get("label")
                or item.get("interface-name")
                or container_key
                or ""
            ).strip()
            record = {
                "id": port_id,
                "label": label,
                "port_label": f"Порт {label}" if label else "",
                "speed": item.get("speed"),
                "duplex": item.get("duplex"),
                "enabled": self._interface_enabled(item),
                "link": item.get("link"),
                "public": bool(item.get("public")),
            }
            aliases = {
                port_id,
                label,
                str(item.get("interface-name") or "").strip(),
                str(container_key or "").strip(),
            }

            for alias in aliases:
                if not alias:
                    continue

                key = alias.lower()
                current = index.get(key)

                # При совпадении внешних номеров LAN-клиенту подходит
                # непубличный порт; полный ID всегда остаётся однозначным.
                if current is None or (
                    current.get("public")
                    and not record.get("public")
                ):
                    index[key] = record

        for key, value in self.interface_data.items():
            if not isinstance(value, dict):
                continue

            add_port(value, key)
            nested_ports = value.get("port")

            if isinstance(nested_ports, dict):
                if str(nested_ports.get("type") or "").lower() == "port":
                    add_port(nested_ports)
                else:
                    for nested_key, nested_value in nested_ports.items():
                        if isinstance(nested_value, dict):
                            add_port(nested_value, nested_key)

            elif isinstance(nested_ports, list):
                for nested_value in nested_ports:
                    if isinstance(nested_value, dict):
                        add_port(nested_value)

        return index

    def _resolve_wired_port(
        self,
        host: dict[str, Any],
    ) -> dict[str, Any]:
        candidates: list[str] = []

        for key in (
            "port",
            "bridge-port",
            "bridge_port",
            "physical-interface",
            "physical_interface",
            "via",
        ):
            value = host.get(key)

            if isinstance(value, (str, int, float)) and not isinstance(
                value,
                bool,
            ):
                text = str(value).strip()

                if text and not self._looks_like_mac(text):
                    candidates.append(text)

        for candidate in candidates:
            found = self._ethernet_ports.get(candidate.lower())

            if found is not None:
                return dict(found)

        for candidate in candidates:
            if self._looks_like_ethernet_interface(candidate):
                return {
                    "id": candidate,
                    "label": "",
                    "port_label": "",
                    "enabled": True,
                }

        numeric = next(
            (
                candidate
                for candidate in candidates
                if candidate.isdigit()
            ),
            "",
        )
        return {
            "id": "",
            "label": numeric,
            "port_label": f"Порт {numeric}" if numeric else "",
            "enabled": True,
        }

    @staticmethod
    def _looks_like_mac(value: str) -> bool:
        compact = str(value).strip().replace(":", "").replace("-", "")
        return len(compact) == 12 and all(
            character in "0123456789abcdefABCDEF"
            for character in compact
        )

    @staticmethod
    def _looks_like_ethernet_interface(value: str) -> bool:
        lowered = str(value).strip().lower()
        return (
            lowered.startswith("gigabitethernet")
            or lowered.startswith("fastethernet")
            or lowered.startswith("ethernet")
        )

    @staticmethod
    def _duplex_value(value: Any) -> str:
        if isinstance(value, bool):
            return "Полный" if value else "Полудуплекс"

        text = str(value or "").strip()

        if text.lower() == "full":
            return "Полный"
        if text.lower() == "half":
            return "Полудуплекс"
        return text or "—"

    def _host_is_active(
        self,
        host: dict[str, Any],
    ) -> bool:
        """
        Определяет активность клиента.

        Некоторые версии Keenetic не возвращают отдельное
        active/connected, но возвращают актуальный IP.
        Явный Offline всегда имеет приоритет.
        """
        positive_found = False

        for key in (
            "active",
            "connected",
            "online",
            "link",
            "state",
            "status",
        ):
            if key not in host:
                continue

            value = host.get(key)

            if isinstance(value, bool):
                if not value:
                    return False
                positive_found = True
                continue

            text = str(value).strip().lower()

            if text in {
                "no",
                "false",
                "0",
                "down",
                "offline",
                "disconnected",
                "inactive",
                "expired",
                "stale",
            }:
                return False

            if text in {
                "yes",
                "true",
                "1",
                "up",
                "online",
                "connected",
                "active",
                "running",
            }:
                positive_found = True

        if positive_found:
            return True

        ip_address = self._first_text(
            host,
            (
                "ip",
                "address",
                "ipv4",
            ),
        )

        return bool(
            ip_address
            and ip_address not in {
                "0.0.0.0",
                "::",
                "—",
            }
        )

    @staticmethod
    def _first_number(
        data: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float | None:
        lowered = {
            str(key).lower(): value
            for key, value in data.items()
        }

        for key in keys:
            value = lowered.get(key.lower())

            if value is None:
                continue

            try:
                return float(value)
            except (TypeError, ValueError):
                continue

        return None

    def _build_hotspot_index(
        self,
    ) -> dict[str, dict[str, Any]]:
        hosts: list[dict[str, Any]] = []
        self._collect_host_dicts(
            self.hotspot_data,
            hosts,
        )

        result: dict[str, dict[str, Any]] = {}

        for host in hosts:
            mac = self._normalize_mac(
                self._first_text(host, ("mac",))
            )

            if mac:
                result[mac] = host

        return result

    def _collect_station_dicts(
        self,
        value: Any,
        output: list[dict[str, Any]],
    ) -> None:
        if isinstance(value, dict):
            keys = {str(key).lower() for key in value}

            if (
                "mac" in keys
                and (
                    "ap" in keys
                    or "rssi" in keys
                    or "txrate" in keys
                )
            ):
                output.append(value)

            for nested in value.values():
                self._collect_station_dicts(nested, output)

        elif isinstance(value, list):
            for nested in value:
                self._collect_station_dicts(nested, output)

    def _collect_host_dicts(
        self,
        value: Any,
        output: list[dict[str, Any]],
    ) -> None:
        if isinstance(value, dict):
            keys = {str(key).lower() for key in value}

            if "mac" in keys and (
                "ip" in keys
                or "address" in keys
                or "name" in keys
                or "hostname" in keys
            ):
                output.append(value)

            for nested in value.values():
                self._collect_host_dicts(nested, output)

        elif isinstance(value, list):
            for nested in value:
                self._collect_host_dicts(nested, output)

    @staticmethod
    def _valid_ssid(value: Any) -> bool:
        if value is None:
            return False

        text = str(value).strip()

        if not text:
            return False

        lowered = text.lower()

        invalid_values = {
            "—",
            "none",
            "null",
            "unknown",
            "не определён",
            "без имени",
            "accesspoint",
            "access point",
        }

        if lowered in invalid_values:
            return False

        # Системные идентификаторы Keenetic не являются SSID.
        if any(
            marker in lowered
            for marker in (
                "wifimaster",
                "/accesspoint",
                "accesspoint0",
                "accesspoint1",
                "accesspoint2",
                "accesspoint3",
            )
        ):
            return False

        return True

    def _is_service_access_point(
        self,
        interface_id: str,
        interface_type: str,
        ssid: str,
        description: str,
        item: dict[str, Any],
    ) -> bool:
        text = " ".join(
            (
                interface_id,
                interface_type,
                description,
                str(item.get("interface-name") or ""),
            )
        ).lower()

        # Клиентский Wireless ISP не является нашей
        # раздаваемой Wi-Fi сетью.
        if any(
            marker in text
            for marker in (
                "wifistation",
                "wirelessisp",
                "wireless isp",
            )
        ):
            return True

        # Скрытые внутренние и mesh/backhaul интерфейсы.
        if any(
            marker in text
            for marker in (
                "backhaul",
                "mesh",
                "extender",
                "repeater",
                "service",
                "internal",
                "hidden",
                "monitor",
                "radio",
            )
        ):
            return True

        # При явно скрытом/служебном SSID показываем сеть
        # только если у неё есть настоящее имя.
        hidden = self._find_recursive(
            item,
            (
                "hidden",
                "hide-ssid",
                "hide_ssid",
            ),
        )

        if self._truthy(hidden) and not self._valid_ssid(ssid):
            return True

        return False

    def _network_group(
        self,
        interface_id: str,
        ssid: str,
        description: str,
        item: dict[str, Any],
    ) -> str:
        text = " ".join(
            (
                interface_id,
                ssid,
                description,
                str(item.get("via") or ""),
            )
        ).lower()

        if any(
            marker in text
            for marker in (
                "guest",
                "гост",
            )
        ):
            return "Гостевая сеть"

        return "Основная сеть"

    @staticmethod
    def _band_from_text(text: str) -> str:
        lowered = str(text).lower()

        if "wifimaster0" in lowered or "2.4" in lowered:
            return "2.4 ГГц"

        if "wifimaster1" in lowered or "5ghz" in lowered or "5 ггц" in lowered:
            return "5 ГГц"

        if "wifimaster2" in lowered or "6ghz" in lowered or "6 ггц" in lowered:
            return "6 ГГц"

        return "Wi-Fi"

    def _extract_channel(
        self,
        item: dict[str, Any],
    ) -> str:
        value = self._find_recursive(
            item,
            (
                "channel",
                "current-channel",
                "current_channel",
            ),
        )
        return str(value) if value not in (None, "") else "—"

    def _extract_security(
        self,
        item: dict[str, Any],
    ) -> str:
        values: list[str] = []

        for key in (
            "security",
            "authentication",
            "auth",
            "encryption",
            "crypto",
            "wpa",
        ):
            value = self._find_recursive(item, (key,))

            if isinstance(value, str) and value.strip():
                values.append(value.strip())

        unique: list[str] = []

        for value in values:
            if value not in unique:
                unique.append(value)

        return ", ".join(unique) if unique else "—"

    @staticmethod
    def _interface_enabled(item: dict[str, Any]) -> bool:
        state = str(item.get("state") or "").lower()
        summary = item.get("summary")
        conf = ""

        if isinstance(summary, dict):
            layer = summary.get("layer")

            if isinstance(layer, dict):
                conf = str(layer.get("conf") or "").lower()

        return conf != "disabled" and not (
            state == "down" and conf == "disabled"
        )

    @staticmethod
    def _interface_online(item: dict[str, Any]) -> bool:
        link = str(item.get("link") or "").lower()
        state = str(item.get("state") or "").lower()
        return link == "up" or state == "up"

    @staticmethod
    def _host_registered(host: dict[str, Any]) -> bool:
        for key in (
            "registered",
            "known",
            "static",
            "permit",
        ):
            if key in host:
                return WifiEngine._truthy(host.get(key))

        return bool(host.get("name") or host.get("hostname"))

    @staticmethod
    def _host_segment_id(host: dict[str, Any]) -> str:
        """Возвращает технический Bridge-интерфейс из show/ip/hotspot."""
        interface = host.get("interface")
        if isinstance(interface, dict):
            return str(
                interface.get("id")
                or interface.get("interface")
                or ""
            ).strip()

        for key in ("segment", "bridge"):
            value = host.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()

        return ""

    def _host_display_name(
        self,
        host: dict[str, Any],
        fallback: str,
    ) -> str:
        hostname = self._first_text(
            host,
            ("hostname", "host-name"),
        )
        if hostname:
            return hostname

        raw_name = self._first_text(
            host,
            ("name", "description"),
        )
        if not raw_name:
            return fallback

        display_name, _last_seen = self._split_history_name(raw_name)
        return display_name or fallback

    def _host_last_seen(
        self,
        host: dict[str, Any],
    ) -> str | None:
        direct = self._first_text(
            host,
            ("last-seen", "last_seen", "seen"),
        )
        if direct:
            return direct

        raw_name = self._first_text(host, ("name",))
        if not raw_name:
            return None
        _display_name, last_seen = self._split_history_name(raw_name)
        return last_seen

    @staticmethod
    def _split_history_name(value: str) -> tuple[str, str | None]:
        text = str(value or "").strip()
        match = re.fullmatch(
            r"(.+?)\s+-\s+.+?\s+-\s+"
            r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            text,
        )
        if not match:
            return text, None
        return match.group(1).strip(), match.group(2)

    @staticmethod
    def _signal_quality(rssi: float | None) -> str:
        if rssi is None:
            return "—"

        if rssi >= -50:
            return "Отличный"
        if rssi >= -60:
            return "Хороший"
        if rssi >= -70:
            return "Средний"
        return "Слабый"

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        seconds_int = max(0, int(seconds))
        days, remainder = divmod(seconds_int, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)

        parts: list[str] = []

        if days:
            parts.append(f"{days} д.")
        if hours:
            parts.append(f"{hours} ч.")
        if minutes:
            parts.append(f"{minutes} мин.")
        if not parts:
            parts.append(f"{secs} сек.")

        return " ".join(parts)

    @staticmethod
    def _normalize_mac(value: str | None) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number_or_zero(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

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

    @staticmethod
    def _first_text(
        data: dict[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        lowered = {
            str(key).lower(): value
            for key, value in data.items()
        }

        for key in keys:
            value = lowered.get(key.lower())

            if isinstance(value, (str, int, float)):
                text = str(value).strip()

                if text:
                    return text

        return None

    def _find_recursive(
        self,
        value: Any,
        keys: tuple[str, ...],
    ) -> Any:
        wanted = {key.lower() for key in keys}

        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in wanted:
                    return nested

            for nested in value.values():
                found = self._find_recursive(nested, keys)

                if found is not None:
                    return found

        elif isinstance(value, list):
            for nested in value:
                found = self._find_recursive(nested, keys)

                if found is not None:
                    return found

        return None
