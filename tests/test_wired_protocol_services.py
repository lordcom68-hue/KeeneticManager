from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.wired_internet_service import WiredInternetService
from core.services.wired_l2tp_service import WiredL2tpService
from core.services.wired_pppoe_service import WiredPppoeService
from core.services.wired_pptp_service import WiredPptpService


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
        self.rollback_calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def normalize_host(value: str) -> str:
        return value.strip().lower()

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
            "ethernet.cable": {
                "ports": [{"port_id": "GigabitEthernet0/1"}]
            }
        }

    @staticmethod
    def choose_internal_vlan_id(_config: dict[str, Any]) -> int:
        return 101

    @staticmethod
    def next_numbered_interface_id(
        _config: dict[str, Any],
        prefix: str,
    ) -> str:
        return f"{prefix}0"

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
        return False, "создание каркаса отклонено"

    def rollback_wired_pppoe_creation(
        self,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        self.rollback_calls.append(("pppoe", kwargs))
        return True, "порт восстановлен"

    def rollback_wired_pptp_creation(
        self,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        self.rollback_calls.append(("pptp", kwargs))
        return True, "порт восстановлен"

    def rollback_wired_l2tp_creation(
        self,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        self.rollback_calls.append(("l2tp", kwargs))
        return True, "порт восстановлен"


class WiredProtocolServiceTests(unittest.TestCase):
    SERVICES = (
        (WiredPppoeService, "pppoe"),
        (WiredPptpService, "pptp"),
        (WiredL2tpService, "l2tp"),
    )

    def test_offline_operations_preserve_contract(self) -> None:
        for service_type, protocol in self.SERVICES:
            with self.subTest(protocol=protocol):
                service = service_type(OfflineClient())
                create = getattr(service, f"create_wired_{protocol}")
                delete = getattr(service, f"delete_wired_{protocol}")
                self.assertEqual(
                    create({}),
                    (False, "Нет активной сессии с роутером.", {}),
                )
                self.assertEqual(
                    delete(f"{protocol.upper()}0"),
                    (False, "Нет активной сессии с роутером.", {}),
                )

    def test_each_creation_rolls_back_after_skeleton_failure(self) -> None:
        for service_type, protocol in self.SERVICES:
            with self.subTest(protocol=protocol):
                client = CreationRollbackClient()
                service = service_type(client)
                create = getattr(service, f"create_wired_{protocol}")

                success, message, details = create(
                    {
                        "port": {
                            "port_id": "GigabitEthernet0/1",
                            "port_label": "LAN1",
                            "parent_id": "GigabitEthernet0",
                        },
                        "protocol": {
                            "username": "user",
                            "password": "secret",
                            "server": "vpn.example",
                        },
                    }
                )

                self.assertFalse(success)
                self.assertIn("каркаса отклонено", message)
                self.assertTrue(details["rollback_success"])
                self.assertEqual(len(client.rollback_calls), 1)
                rollback_protocol, rollback = client.rollback_calls[0]
                self.assertEqual(rollback_protocol, protocol)
                self.assertEqual(rollback["port_id"], "GigabitEthernet0/1")
                self.assertEqual(
                    rollback["transport_id"],
                    "GigabitEthernet0/Vlan101",
                )
                self.assertEqual(rollback["old_priorities"], {"ISP": 0})


class WiredServiceFacadeTests(unittest.TestCase):
    def test_wired_service_delegates_all_protocol_operations(self) -> None:
        service = WiredInternetService(OfflineClient())
        calls: list[tuple[str, str, Any]] = []

        class ProtocolStub:
            def __init__(self, protocol: str) -> None:
                self.protocol = protocol

            def __getattr__(self, name: str) -> Any:
                action = "create" if name.startswith("create_") else "delete"

                def operation(value: Any) -> tuple[bool, str, dict[str, Any]]:
                    calls.append((self.protocol, action, value))
                    return True, action, {}

                return operation

        service.pppoe_service = ProtocolStub("pppoe")
        service.pptp_service = ProtocolStub("pptp")
        service.l2tp_service = ProtocolStub("l2tp")

        for protocol in ("pppoe", "pptp", "l2tp"):
            with self.subTest(protocol=protocol):
                create = getattr(service, f"create_wired_{protocol}")
                delete = getattr(service, f"delete_wired_{protocol}")
                self.assertEqual(create({"protocol": protocol}), (True, "create", {}))
                self.assertEqual(delete(f"{protocol.upper()}0"), (True, "delete", {}))

        self.assertEqual(len(calls), 6)


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

    def test_protocol_operations_are_physically_separated(self) -> None:
        root = Path(__file__).resolve().parents[1]
        wired = root / "core" / "services" / "wired_internet_service.py"
        services = (
            ("pppoe", "WiredPppoeService", "wired_pppoe_service.py"),
            ("pptp", "WiredPptpService", "wired_pptp_service.py"),
            ("l2tp", "WiredL2tpService", "wired_l2tp_service.py"),
        )

        for protocol, class_name, filename in services:
            path = root / "core" / "services" / filename
            for action, minimum in (("create", 500), ("delete", 250)):
                method_name = f"{action}_wired_{protocol}"
                with self.subTest(protocol=protocol, action=action):
                    self.assertGreater(
                        self.method_span(path, class_name, method_name),
                        minimum,
                    )
                    self.assertLess(
                        self.method_span(
                            wired,
                            "WiredInternetService",
                            method_name,
                        ),
                        15,
                    )


if __name__ == "__main__":
    unittest.main()
