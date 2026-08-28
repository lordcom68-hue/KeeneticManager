from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.keenetic_client import KeeneticClient


class ConnectionCreationOptionsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = KeeneticClient(
            username="admin",
            password="secret",
        )

    def test_empty_snapshot_has_stable_safe_structure(self) -> None:
        result = (
            self.client.connection_creation_options_service
            .build_connection_creation_options({}, {})
        )

        self.assertFalse(result["ethernet.cable"]["available"])
        self.assertEqual(result["ethernet.cable"]["ports"], [])
        self.assertEqual(result["ethernet.ipoe"]["ports"], [])
        self.assertEqual(
            [band["id"] for band in result["wireless_isp"]["bands"]],
            ["2.4", "5"],
        )
        self.assertFalse(result["wireless_isp"]["available"])

    def test_compatibility_alias_is_an_independent_copy(self) -> None:
        result = (
            self.client.connection_creation_options_service
            .build_connection_creation_options({}, {})
        )

        result["ethernet.cable"]["ports"].append({"port_id": "test"})

        self.assertEqual(result["ethernet.ipoe"]["ports"], [])

    def test_switch_and_dedicated_wan_ports_are_detected_and_sorted(self) -> None:
        runtime = {
            "GigabitEthernet0": {
                "type": "GigabitEthernet",
                "traits": ["Switch"],
                "port": {
                    "0": {
                        "id": "GigabitEthernet0/0",
                        "label": "1",
                        "link": "up",
                    }
                },
            },
            "GigabitEthernet1": {
                "type": "GigabitEthernet",
                "port": {
                    "id": "GigabitEthernet1/0",
                    "label": "0",
                    "link": "down",
                },
            },
            "WifiMaster0/WifiStation0": {
                "type": "WifiStation",
                "link": "down",
            },
        }
        config = {
            "Bridge0": {
                "iseg": {"free-port": "1"},
                "include": ["GigabitEthernet1"],
            },
            "GigabitEthernet0/0": {
                "switchport": {"access": {"vid": "1"}},
            },
            "GigabitEthernet1": {
                "ip": {},
                "security-level": "private",
            },
            "ISP": {"ip": {"global": {"order": 0}}},
            "WifiMaster0/WifiStation0": {
                "ssid": "Provider",
                "ip": {},
            },
        }

        result = (
            self.client.connection_creation_options_service
            .build_connection_creation_options(runtime, config)
        )

        ports = result["ethernet.cable"]["ports"]
        self.assertEqual(
            [port["mode"] for port in ports],
            ["dedicated_wan", "switch_vlan"],
        )
        self.assertEqual(ports[1]["current_access_vid"], 1)
        self.assertEqual(
            result["ethernet.cable"]["existing_priority_count"],
            1,
        )
        self.assertTrue(result["wireless_isp"]["bands"][0]["supported"])

    def test_client_facade_delegates_without_changing_arguments(self) -> None:
        captured: dict[str, Any] = {}

        class OptionsStub:
            @staticmethod
            def build_connection_creation_options(
                interface_data: dict[str, Any],
                interface_config_data: dict[str, Any],
            ) -> dict[str, Any]:
                captured["runtime"] = interface_data
                captured["config"] = interface_config_data
                return {"delegated": True}

        self.client.connection_creation_options_service = OptionsStub()
        runtime = {"ISP": {"link": "up"}}
        config = {"ISP": {"global": True}}

        self.assertEqual(
            self.client.build_connection_creation_options(runtime, config),
            {"delegated": True},
        )
        self.assertEqual(
            captured,
            {"runtime": runtime, "config": config},
        )


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

    def test_large_operation_is_physically_in_options_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root
            / "core"
            / "services"
            / "connection_creation_options_service.py"
        )
        client = root / "core" / "keenetic_client.py"
        method = "build_connection_creation_options"

        self.assertGreater(
            self.method_span(
                service,
                "ConnectionCreationOptionsService",
                method,
            ),
            300,
        )
        self.assertLess(
            self.method_span(client, "KeeneticClient", method),
            20,
        )


if __name__ == "__main__":
    unittest.main()
