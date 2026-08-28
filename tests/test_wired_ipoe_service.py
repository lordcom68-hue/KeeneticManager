from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.wired_internet_service import WiredInternetService
from core.services.wired_ipoe_service import WiredIpoeService


class OfflineClient:
    session = None
    base_url = None


class PriorityStub:
    @staticmethod
    def current_global_orders(_config: dict[str, Any]) -> dict[str, int]:
        return {"ISP": 0}


class CreationRollbackClient:
    def __init__(self) -> None:
        self.session = object()
        self.base_url = "http://192.0.2.1"
        self.priority_service = PriorityStub()
        self.rollback_calls: list[dict[str, Any]] = []

    @staticmethod
    def get_rci_data(*, endpoint: str, required: bool) -> dict[str, Any]:
        del required
        if endpoint == "show/sc/interface":
            return {"GigabitEthernet0/1": {"switchport": {}}}
        return {}

    @staticmethod
    def find_static_ipv4_network_conflict(**_kwargs: Any) -> None:
        return None

    @staticmethod
    def build_connection_creation_options(
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "ethernet.ipoe": {
                "ports": [{"port_id": "GigabitEthernet0/1"}]
            }
        }

    @staticmethod
    def choose_internal_vlan_id(_config: dict[str, Any]) -> int:
        return 101

    @staticmethod
    def extract_port_access_vid(_config: dict[str, Any]) -> int:
        return 1

    @staticmethod
    def extract_port_switch_mode(_config: dict[str, Any]) -> str:
        return "access"

    @staticmethod
    def extract_port_roles(_config: dict[str, Any]) -> list[str]:
        return ["home"]

    @staticmethod
    def quote_cli_argument(value: str) -> str:
        return value

    @staticmethod
    def execute_rci_batch(
        _commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        del operation_name
        return False, "команда каркаса отклонена"

    def rollback_wired_ipoe_creation(self, **kwargs: Any) -> tuple[bool, str]:
        self.rollback_calls.append(kwargs)
        return True, "порт восстановлен"


class WiredIpoeServiceTests(unittest.TestCase):
    def test_offline_operations_preserve_contract(self) -> None:
        service = WiredIpoeService(OfflineClient())

        self.assertEqual(
            service.create_wired_ipoe({}),
            (False, "Нет активной сессии с роутером.", {}),
        )
        self.assertEqual(
            service.delete_wired_ipoe("ISP"),
            (False, "Нет активной сессии с роутером.", {}),
        )

    def test_create_rolls_back_if_skeleton_command_fails(self) -> None:
        client = CreationRollbackClient()
        service = WiredIpoeService(client)

        success, message, details = service.create_wired_ipoe(
            {
                "port": {
                    "port_id": "GigabitEthernet0/1",
                    "port_label": "LAN1",
                    "parent_id": "GigabitEthernet0",
                }
            }
        )

        self.assertFalse(success)
        self.assertIn("команда каркаса отклонена", message)
        self.assertTrue(details["rollback_success"])
        self.assertEqual(
            client.rollback_calls,
            [
                {
                    "interface_id": "GigabitEthernet0/Vlan101",
                    "port_id": "GigabitEthernet0/1",
                    "old_access_vid": 1,
                    "old_switch_mode": "access",
                    "old_roles": ["home"],
                    "old_priorities": {"ISP": 0},
                }
            ],
        )


class WiredServiceFacadeTests(unittest.TestCase):
    def test_wired_service_delegates_ipoe_operations(self) -> None:
        service = WiredInternetService(OfflineClient())
        calls: list[tuple[str, Any]] = []

        class IpoeStub:
            @staticmethod
            def create_wired_ipoe(
                settings: dict[str, Any],
            ) -> tuple[bool, str, dict[str, Any]]:
                calls.append(("create", settings))
                return True, "created", {}

            @staticmethod
            def delete_wired_ipoe(
                interface_id: str,
            ) -> tuple[bool, str, dict[str, Any]]:
                calls.append(("delete", interface_id))
                return True, "deleted", {}

        service.wired_ipoe_service = IpoeStub()

        self.assertEqual(
            service.create_wired_ipoe({"description": "Provider"}),
            (True, "created", {}),
        )
        self.assertEqual(
            service.delete_wired_ipoe("IPoE0"),
            (True, "deleted", {}),
        )
        self.assertEqual(
            calls,
            [("create", {"description": "Provider"}), ("delete", "IPoE0")],
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

    def test_large_ipoe_operations_are_in_ipoe_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ipoe = root / "core" / "services" / "wired_ipoe_service.py"
        wired = root / "core" / "services" / "wired_internet_service.py"

        for method_name in ("create_wired_ipoe", "delete_wired_ipoe"):
            self.assertGreater(
                self.method_span(ipoe, "WiredIpoeService", method_name),
                300,
            )
            self.assertLess(
                self.method_span(wired, "WiredInternetService", method_name),
                15,
            )


if __name__ == "__main__":
    unittest.main()
