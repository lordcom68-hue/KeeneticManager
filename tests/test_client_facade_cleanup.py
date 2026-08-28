from __future__ import annotations

import ast
import unittest
from pathlib import Path


class ClientFacadeCleanupTests(unittest.TestCase):
    def test_confirmed_unused_facades_are_not_reintroduced(self) -> None:
        path = Path(__file__).resolve().parents[1] / "core" / "keenetic_client.py"
        module = ast.parse(path.read_text(encoding="utf-8"))
        client = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "KeeneticClient"
        )
        methods = {
            node.name
            for node in client.body
            if isinstance(node, ast.FunctionDef)
        }

        removed = {
            "priority_log_path",
            "write_priority_log",
            "detect_usb_capabilities_from_version",
            "detect_usb_rci_endpoint",
            "update_wifi_client_activity",
            "merge_known_wifi_networks",
            "get_interface_statistics",
            "normalize_interface_statistics",
            "verify_interface_admin_state",
        }
        required_compatibility = {
            "create_wifi_network",
            "update_wifi_network",
            "set_wifi_network_enabled",
            "delete_wifi_network",
            "set_interface_enabled",
            "reconnect_interface",
        }

        self.assertTrue(removed.isdisjoint(methods))
        self.assertTrue(required_compatibility.issubset(methods))


if __name__ == "__main__":
    unittest.main()
