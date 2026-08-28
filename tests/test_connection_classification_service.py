from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.keenetic_client import KeeneticClient


CLASSIFICATION_METHODS = (
    "is_internet_candidate",
    "connection_display_name",
    "connection_transport_type",
    "format_physical_port",
    "build_physical_port_map",
    "collect_connection_candidates",
    "connection_is_active",
    "filter_real_internet_connections",
    "is_real_wan_connection",
)


class ConnectionClassificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = KeeneticClient(
            username="admin",
            password="secret",
        )

    def test_internet_candidate_evidence_is_explicit(self) -> None:
        cases = (
            ({}, "WifiStation0", "wifi", True, False, True),
            ({"global": True}, "ISP", "ethernet", False, False, True),
            ({"defaultgw": "yes"}, "ISP", "ethernet", False, False, True),
            ({}, "ISP", "ethernet", False, True, True),
            ({}, "PPPoE0", "pppoe", False, False, True),
            ({}, "Bridge0", "bridge", False, False, False),
        )

        for item, interface_id, kind, wisp, inet_port, expected in cases:
            with self.subTest(interface_id=interface_id):
                self.assertEqual(
                    self.client.is_internet_candidate(
                        item,
                        interface_id,
                        kind,
                        wisp,
                        inet_port,
                    ),
                    expected,
                )

    def test_activity_uses_status_flags_and_routing_evidence(self) -> None:
        cases = (
            ({}, "up", True),
            ({}, "offline", False),
            ({"connected": True}, None, True),
            ({"online": "no"}, None, False),
            (
                {"address": "192.0.2.2", "gateway": "192.0.2.1"},
                None,
                True,
            ),
            ({"address": "0.0.0.0"}, None, False),
            ({}, None, False),
        )

        for item, status, expected in cases:
            with self.subTest(item=item, status=status):
                self.assertEqual(
                    self.client.connection_is_active(item, status),
                    expected,
                )

    def test_physical_port_mapping_and_formatting(self) -> None:
        mapping = self.client.build_physical_port_map(
            {
                "port": {
                    "id": "GigabitEthernet0/3",
                    "label": "4",
                    "public": True,
                    "role": [
                        {
                            "role": "inet",
                            "for": "GigabitEthernet0/Vlan4",
                        }
                    ],
                }
            }
        )

        physical = mapping["GigabitEthernet0/Vlan4"]
        self.assertEqual(physical["id"], "GigabitEthernet0/3")
        self.assertEqual(physical["port_role"], "WAN")
        self.assertIn("WAN", self.client.format_physical_port(physical))
        self.assertIn("4", self.client.format_physical_port(physical))
        self.assertEqual(
            self.client.format_physical_port(
                {
                    "id": "GigabitEthernet0/1",
                    "port_role": "Интернет-порт",
                }
            ),
            "Интернет-порт 2",
        )

    def test_candidates_are_collected_recursively(self) -> None:
        first = {"interface": "ISP", "status": "up"}
        second = {"name": "PPPoE0", "address": "192.0.2.2"}
        output: list[dict[str, Any]] = []

        self.client.collect_connection_candidates(
            {"outer": [first, {"nested": second}]},
            output,
        )

        self.assertIn(first, output)
        self.assertIn(second, output)

    def test_transport_names_and_real_wan_filtering(self) -> None:
        self.assertEqual(
            self.client.connection_transport_type(
                "PPPoE0",
                "pppoe",
                False,
                {},
                {},
            ),
            "PPPoE",
        )
        self.assertIn(
            "5",
            self.client.connection_transport_type(
                "WifiMaster1/WifiStation0",
                "wifi",
                True,
                {},
                {},
            ),
        )
        self.assertEqual(
            self.client.connection_display_name(
                "PPPoE0",
                "",
                "pppoe",
                False,
                {},
                {},
            ),
            "PPPoE-подключение",
        )

        connections = [
            {"interface_id": "PPPoE0", "type": "pppoe"},
            {"interface_id": "Bridge0", "type": "bridge"},
        ]
        result = self.client.filter_real_internet_connections(
            connections,
            {
                "PPPoE0": {"type": "pppoe"},
                "Bridge0": {"type": "bridge"},
            },
        )

        self.assertEqual(result, [connections[0]])


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

    def test_classification_is_physically_in_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root
            / "core"
            / "services"
            / "connection_classification_service.py"
        )
        client = root / "core" / "keenetic_client.py"

        for method_name in CLASSIFICATION_METHODS:
            with self.subTest(method=method_name):
                self.assertGreater(
                    self.method_span(
                        service,
                        "ConnectionClassificationService",
                        method_name,
                    ),
                    30,
                )
                self.assertLess(
                    self.method_span(client, "KeeneticClient", method_name),
                    25,
                )

    def test_classification_service_contains_no_router_writes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "services"
            / "connection_classification_service.py"
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
