from __future__ import annotations

import unittest
from typing import Any

from core.wifi_engine import WifiEngine
from ui.widgets.client_card import ClientCard


class WifiEngineClientTests(unittest.TestCase):
    @staticmethod
    def _clients(
        *,
        interface_data: dict[str, Any],
        associations: Any,
        hosts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        networks = WifiEngine(
            interface_data=interface_data,
            associations_data=associations,
            hotspot_data={"host": hosts},
        ).build()
        return [
            client
            for network in networks
            for client in network["clients"]
        ]

    def test_nested_mws_host_is_wifi_and_wired_port_is_resolved(self) -> None:
        interfaces = {
            "WifiMaster0/AccessPoint0": {
                "id": "WifiMaster0/AccessPoint0",
                "type": "AccessPoint",
                "ssid": "Home",
                "state": "up",
                "link": "up",
            },
            "WifiMaster1/AccessPoint0": {
                "id": "WifiMaster1/AccessPoint0",
                "type": "AccessPoint",
                "ssid": "Home",
                "state": "up",
                "link": "up",
            },
            "GigabitEthernet0": {
                "id": "GigabitEthernet0",
                "type": "GigabitEthernet",
                "port": {
                    "3": {
                        "id": "GigabitEthernet0/2",
                        "type": "Port",
                        "interface-name": "3",
                        "label": "3",
                        "link": "up",
                        "speed": "1000",
                        "duplex": "full",
                    }
                },
            },
        }
        clients = self._clients(
            interface_data=interfaces,
            associations={
                "station": [
                    {
                        "mac": "00:00:00:00:00:01",
                        "ap": "WifiMaster1/AccessPoint0",
                        "txrate": 867,
                        "rssi": -55,
                    }
                ]
            },
            hosts=[
                {
                    "mac": "00:00:00:00:00:01",
                    "ip": "192.168.3.10",
                    "hostname": "Direct",
                    "active": True,
                },
                {
                    "mac": "00:00:00:00:00:02",
                    "via": "00:00:00:00:00:02",
                    "ip": "192.168.3.11",
                    "hostname": "Nested",
                    "active": True,
                    "mws": {
                        "ap": "WifiMaster0/AccessPoint0",
                        "txrate": 72,
                        "rssi": -67,
                        "mode": "11n",
                    },
                },
                {
                    "mac": "00:00:00:00:00:03",
                    "via": "00:00:00:00:00:03",
                    "ip": "192.168.3.12",
                    "hostname": "Cable",
                    "active": True,
                    "port": "3",
                    "speed": 1000,
                    "duplex": True,
                },
            ],
        )
        by_mac = {client["mac"]: client for client in clients}

        nested = by_mac["00:00:00:00:00:02"]
        self.assertEqual(nested["connection_kind"], "wifi")
        self.assertEqual(nested["band"], "2.4 ГГц")
        self.assertEqual(nested["txrate"], 72.0)
        self.assertIsNone(nested["rxrate"])

        wired = by_mac["00:00:00:00:00:03"]
        self.assertEqual(wired["connection_kind"], "wired")
        self.assertEqual(wired["interface"], "GigabitEthernet0/2")
        self.assertEqual(wired["port_label"], "Порт 3")
        self.assertEqual(wired["connection_label"], "Ethernet, порт 3")
        self.assertEqual(wired["txrate"], 1000.0)
        self.assertEqual(wired["rxrate"], 1000.0)
        self.assertEqual(wired["duplex"], "Полный")

    def test_hotspot_interface_id_is_preserved_as_client_segment(self) -> None:
        clients = self._clients(
            interface_data={
                "WifiMaster1/AccessPoint0": {
                    "id": "WifiMaster1/AccessPoint0",
                    "type": "AccessPoint",
                    "ssid": "Home",
                    "state": "up",
                    "link": "up",
                }
            },
            associations={
                "station": {
                    "mac": "00:00:00:00:00:81",
                    "ap": "WifiMaster1/AccessPoint0",
                }
            },
            hosts=[
                {
                    "mac": "00:00:00:00:00:81",
                    "ip": "192.168.1.81",
                    "active": True,
                    "interface": {
                        "id": "Bridge0",
                        "name": "Home",
                    },
                }
            ],
        )

        self.assertEqual(clients[0]["segment_id"], "Bridge0")

    def test_router_supplied_wifi_rates_keep_their_directions(self) -> None:
        clients = self._clients(
            interface_data={
                "WifiMaster1/AccessPoint0": {
                    "id": "WifiMaster1/AccessPoint0",
                    "type": "AccessPoint",
                    "ssid": "Home",
                    "state": "up",
                    "link": "up",
                }
            },
            associations={
                "station": {
                    "mac": "00:00:00:00:00:04",
                    "ap": "WifiMaster1/AccessPoint0",
                    "txrate": "1.201 Gbit/s",
                    "rxrate": "286 Mbps",
                }
            },
            hosts=[
                {
                    "mac": "00:00:00:00:00:04",
                    "ip": "192.168.1.4",
                    "active": True,
                }
            ],
        )
        client = next(
            item
            for item in clients
            if item["mac"] == "00:00:00:00:00:04"
        )
        self.assertEqual(client["txrate"], 1201.0)
        self.assertEqual(client["rxrate"], 286.0)

    def test_fast_ethernet_external_port_number_is_supported(self) -> None:
        clients = self._clients(
            interface_data={
                "FastEthernet0": {
                    "id": "FastEthernet0",
                    "type": "FastEthernet",
                    "port": {
                        "1": {
                            "id": "FastEthernet0/1",
                            "type": "Port",
                            "label": "1",
                            "speed": "100 Mbit/s",
                            "duplex": "half",
                        }
                    },
                }
            },
            associations={},
            hosts=[
                {
                    "mac": "00:00:00:00:00:05",
                    "via": "00:00:00:00:00:05",
                    "ip": "192.168.1.5",
                    "active": True,
                    "port": "1",
                }
            ],
        )
        client = next(
            item
            for item in clients
            if item["mac"] == "00:00:00:00:00:05"
        )
        self.assertEqual(client["interface"], "FastEthernet0/1")
        self.assertEqual(client["txrate"], 100.0)
        self.assertEqual(client["rxrate"], 100.0)
        self.assertEqual(client["duplex"], "Полудуплекс")

    def test_via_mac_is_not_presented_as_ethernet_interface(self) -> None:
        clients = self._clients(
            interface_data={},
            associations={},
            hosts=[
                {
                    "mac": "00:00:00:00:00:06",
                    "via": "00:00:00:00:00:06",
                    "ip": "192.168.1.6",
                    "active": True,
                }
            ],
        )
        client = next(
            item
            for item in clients
            if item["mac"] == "00:00:00:00:00:06"
        )
        self.assertEqual(client["interface"], "—")
        self.assertEqual(client["port_label"], "")

    def test_missing_phy_rate_is_explicit(self) -> None:
        self.assertEqual(
            ClientCard._format_link_rate(None),
            "нет данных",
        )

    def test_inventory_includes_offline_and_blocked_known_hosts(self) -> None:
        engine = WifiEngine(
            interface_data={
                "WifiMaster0/AccessPoint0": {
                    "id": "WifiMaster0/AccessPoint0",
                    "type": "AccessPoint",
                    "ssid": "Home",
                    "state": "up",
                    "link": "up",
                }
            },
            associations_data={
                "station": {
                    "mac": "00:00:00:00:00:11",
                    "ap": "WifiMaster0/AccessPoint0",
                    "txrate": 433,
                }
            },
            hotspot_data={
                "host": [
                    {
                        "mac": "00:00:00:00:00:11",
                        "ip": "192.168.1.11",
                        "name": "Online phone",
                        "active": True,
                        "permit": True,
                    },
                    {
                        "mac": "00:00:00:00:00:12",
                        "ip": "192.168.1.12",
                        "name": "Known offline",
                        "active": False,
                        "permit": True,
                        "last-seen": "2 ч. назад",
                    },
                    {
                        "mac": "00:00:00:00:00:13",
                        "ip": "192.168.1.13",
                        "name": "Blocked host",
                        "active": False,
                        "blocked": True,
                        "permit": False,
                    },
                ]
            },
        )

        inventory = engine.build_inventory(engine.build())
        by_mac = {client["mac"]: client for client in inventory}

        self.assertEqual(len(inventory), 3)
        self.assertTrue(by_mac["00:00:00:00:00:11"]["online"])
        self.assertEqual(
            by_mac["00:00:00:00:00:11"]["connection_kind"],
            "wifi",
        )
        self.assertFalse(by_mac["00:00:00:00:00:12"]["online"])
        self.assertEqual(
            by_mac["00:00:00:00:00:12"]["connection_kind"],
            "unknown",
        )
        self.assertTrue(by_mac["00:00:00:00:00:13"]["blocked"])

    def test_inventory_deduplicates_wifi_and_wired_views(self) -> None:
        mac = "00:00:00:00:00:21"
        engine = WifiEngine(
            interface_data={},
            associations_data={},
            hotspot_data={},
        )
        networks = [
            {
                "network_type": "wired",
                "clients": [{"mac": mac, "connection_kind": "wired"}],
            },
            {
                "network_type": "wifi",
                "clients": [{"mac": mac, "connection_kind": "wifi"}],
            },
        ]

        inventory = engine.build_inventory(networks)

        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["connection_kind"], "wifi")

    def test_inventory_cleans_history_suffix_from_client_name(self) -> None:
        engine = WifiEngine(
            interface_data={},
            associations_data={},
            hotspot_data={
                "host": {
                    "mac": "00:00:00:00:00:51",
                    "ip": "192.168.1.51",
                    "name": (
                        "Bogdan - Home network - "
                        "2026-08-05 17:05"
                    ),
                    "active": False,
                    "registered": True,
                }
            },
        )

        inventory = engine.build_inventory(engine.build())

        self.assertEqual(inventory[0]["name"], "Bogdan")
        self.assertEqual(
            inventory[0]["last_seen"],
            "2026-08-05 17:05",
        )


if __name__ == "__main__":
    unittest.main()
