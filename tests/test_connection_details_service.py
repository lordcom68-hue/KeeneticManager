from __future__ import annotations

import ast
import unittest
from pathlib import Path

from core.keenetic_client import KeeneticClient


DETAIL_METHODS = (
    "apply_connection_delete_context",
    "clear_connection_delete_context",
    "enrich_wired_pppoe_details",
    "enrich_wired_pptp_details",
    "enrich_wired_l2tp_details",
    "enrich_cable_protocol_change_details",
    "enrich_connection_details",
    "enrich_basic_interface_fields",
    "extract_ipv6_address",
    "normalize_connection_detail",
    "extract_ipv6_details",
)


class ConnectionDetailsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = KeeneticClient(
            username="admin",
            password="secret",
        )

    def test_delete_context_is_applied_and_cleared_consistently(self) -> None:
        connection: dict[str, object] = {}
        context = {
            "port_id": "GigabitEthernet0/1",
            "port_label": "1",
            "port_number": "0",
            "transport_id": "GigabitEthernet0/Vlan101",
            "bridge_id": "Bridge0",
            "home_vlan_id": 1,
            "port_link_known": True,
            "port_link_active": True,
            "port_link": "up",
            "port_speed": 1_000_000_000,
        }

        self.client.apply_connection_delete_context(
            connection,
            context=context,
            delete_kind="ethernet.ipoe",
        )

        self.assertTrue(connection["delete_supported"])
        self.assertEqual(connection["delete_kind"], "ethernet.ipoe")
        self.assertEqual(connection["delete_restore_vlan"], 1)
        self.assertEqual(
            connection["delete_transport_id"],
            "GigabitEthernet0/Vlan101",
        )
        self.assertTrue(connection["delete_port_link_active"])

        self.client.clear_connection_delete_context(connection, "Недоступно")

        self.assertFalse(connection["delete_supported"])
        self.assertEqual(connection["delete_reason"], "Недоступно")
        self.assertNotIn("delete_kind", connection)
        self.assertNotIn("delete_restore_vlan", connection)

    def test_ipv6_helpers_prefer_global_and_ignore_boolean_flags(self) -> None:
        self.assertEqual(
            self.client.extract_ipv6_address(
                {
                    "ipv6": False,
                    "addresses": [
                        "fe80::1/64",
                        {"address": "2001:db8::10/64"},
                    ],
                }
            ),
            "2001:db8::10",
        )
        self.assertIsNone(self.client.extract_ipv6_address(True))
        self.assertIsNone(self.client.extract_ipv6_address("192.0.2.1"))
        self.assertEqual(
            self.client.extract_ipv6_details(
                {
                    "ipv6": {
                        "addresses": [
                            {"address": "fe80::1", "prefix-length": 64},
                            {
                                "address": "2001:db8::20",
                                "prefix-length": 64,
                            },
                        ]
                    }
                }
            ),
            ("2001:db8::20", "2001:db8::20/64"),
        )

    def test_detail_normalization_removes_empty_and_duplicate_values(self) -> None:
        self.assertIsNone(self.client.normalize_connection_detail(" — "))
        self.assertEqual(
            self.client.normalize_connection_detail(
                ["1.1.1.1", "", "1.1.1.1", "8.8.8.8"]
            ),
            ["1.1.1.1", "8.8.8.8"],
        )
        self.assertEqual(
            self.client.normalize_connection_detail(
                {"0": "1.1.1.1", "1": ["8.8.8.8", "1.1.1.1"]}
            ),
            ["1.1.1.1", "8.8.8.8"],
        )

    def test_basic_interface_fields_preserve_existing_topology_values(self) -> None:
        connection = {"address": "198.51.100.10"}
        item = {
            "address": "192.0.2.10",
            "gateway": "192.0.2.1",
            "dns": ["1.1.1.1", "8.8.8.8"],
            "mtu": 1500,
            "mac": "00:11:22:33:44:55",
            "ipv6": {"address": "2001:db8::30/64"},
        }

        self.client.enrich_basic_interface_fields(
            connection=connection,
            item=item,
            statistics_source="ISP",
            interface_data={"ISP": {"speed": 1_000_000_000}},
        )

        self.assertEqual(connection["address"], "198.51.100.10")
        self.assertEqual(connection["gateway"], "192.0.2.1")
        self.assertEqual(connection["dns"], ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(connection["ipv6_address"], "2001:db8::30")
        self.assertEqual(connection["mtu"], 1500)
        self.assertEqual(connection["mac"], "00:11:22:33:44:55")
        self.assertEqual(connection["link_speed_bps"], 1_000_000_000)

    def test_pppoe_enrichment_adds_protocol_and_safe_delete_context(self) -> None:
        interface_id = "PPPoE0"
        transport_id = "GigabitEthernet0/Vlan101"
        config = {
            interface_id: {
                "authentication": {"identity": "provider-user"},
                "pppoe": {"service": "internet"},
                "connect": {"via": transport_id},
                "ip": {"global": {"order": 0}},
                "up": True,
            },
            transport_id: {"ip": {}, "up": True},
            "GigabitEthernet0/1": {
                "rename": "LAN1",
                "role": {"role": "inet", "ifor": transport_id},
                "switchport": {"access": {"vid": 101}},
            },
            "Bridge0": {
                "description": "Home network",
                "security-level": "private",
                "include": {"interface": "GigabitEthernet0/Vlan1"},
            },
        }
        connection = {"interface_id": interface_id}

        self.client.enrich_wired_pppoe_details(
            connections=[connection],
            interface_data={},
            interface_config_data=config,
        )

        self.assertEqual(connection["provider_protocol"], "PPPoE")
        self.assertEqual(connection["provider_username"], "provider-user")
        self.assertEqual(connection["pppoe_service"], "internet")
        self.assertEqual(connection["transport_interface_id"], transport_id)
        self.assertTrue(connection["delete_supported"])
        self.assertEqual(connection["delete_kind"], "ethernet.pppoe")

    def test_pptp_and_l2tp_enrichment_share_safe_delete_contract(self) -> None:
        context = {
            "port_id": "GigabitEthernet0/1",
            "transport_id": "GigabitEthernet0/Vlan101",
            "bridge_id": "Bridge0",
            "home_vlan_id": 1,
        }
        self.client.inspect_wired_pptp_deletion = (
            lambda **_kwargs: (dict(context), "")
        )
        self.client.inspect_wired_l2tp_deletion = (
            lambda **_kwargs: (dict(context), "")
        )
        self.client.inspect_dedicated_wan_deletion = (
            lambda **_kwargs: ({}, "")
        )
        config = {
            "PPTP0": {
                "pptp": {},
                "authentication": {"identity": "pptp-user"},
                "peer": "pptp.example",
                "connect": {"via": "GigabitEthernet0/Vlan101"},
            },
            "L2TP0": {
                "l2tp": {},
                "authentication": {"identity": "l2tp-user"},
                "peer": "l2tp.example",
                "connect": {"via": "GigabitEthernet0/Vlan101"},
            },
        }
        pptp = {"interface_id": "PPTP0"}
        l2tp = {"interface_id": "L2TP0"}

        self.client.enrich_wired_pptp_details(
            connections=[pptp],
            interface_data={},
            interface_config_data=config,
        )
        self.client.enrich_wired_l2tp_details(
            connections=[l2tp],
            interface_data={},
            interface_config_data=config,
        )

        self.assertEqual(pptp["provider_protocol"], "PPTP")
        self.assertEqual(pptp["provider_server"], "pptp.example")
        self.assertEqual(pptp["delete_kind"], "ethernet.pptp")
        self.assertEqual(l2tp["provider_protocol"], "L2TP")
        self.assertEqual(l2tp["provider_server"], "l2tp.example")
        self.assertEqual(l2tp["delete_kind"], "ethernet.l2tp")


class SourceBoundaryTests(unittest.TestCase):
    @staticmethod
    def method_span(path: Path, class_name: str, method_name: str) -> int:
        module = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        return int(method.end_lineno) - int(method.lineno) + 1

    def test_detail_operations_are_physically_in_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = root / "core" / "services" / "connection_details_service.py"
        client = root / "core" / "keenetic_client.py"

        for method_name in DETAIL_METHODS:
            with self.subTest(method=method_name):
                self.assertGreater(
                    self.method_span(
                        service,
                        "ConnectionDetailsService",
                        method_name,
                    ),
                    20,
                )
                self.assertLess(
                    self.method_span(client, "KeeneticClient", method_name),
                    25,
                )

    def test_detail_service_contains_no_router_writes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "services"
            / "connection_details_service.py"
        ).read_text(encoding="utf-8")

        for fragment in (
            "execute_rci_batch",
            "execute_parse_command",
            "set_interface_settings",
            "set_internet_priorities",
        ):
            self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
