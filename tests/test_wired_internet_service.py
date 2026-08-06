from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.wired_internet_service import (
    WiredInternetService,
)


class DispatchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def create_dedicated_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        self.calls.append(("dedicated", settings))
        return True, "dedicated", {}

    def create_wired_ipoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        self.calls.append(("ipoe", settings))
        return True, "ipoe", {}

    def create_wired_pppoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        self.calls.append(("pppoe", settings))
        return True, "pppoe", {}

    def create_wired_pptp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        self.calls.append(("pptp", settings))
        return True, "pptp", {}

    def create_wired_l2tp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        self.calls.append(("l2tp", settings))
        return True, "l2tp", {}


class OfflineClient:
    def __init__(self) -> None:
        self.session = None
        self.base_url = None


class WiredInternetServiceTests(unittest.TestCase):
    def test_protocol_type_and_connection_type(self) -> None:
        self.assertEqual(
            WiredInternetService.protocol_type({}),
            "none",
        )
        self.assertEqual(
            WiredInternetService.protocol_type(
                {"protocol": {"type": "PPPoE"}}
            ),
            "pppoe",
        )
        self.assertEqual(
            WiredInternetService.connection_type(
                {"protocol": {"type": "pptp"}}
            ),
            "ethernet.pptp",
        )
        self.assertEqual(
            WiredInternetService.connection_type({}),
            "ethernet.ipoe",
        )

    def test_create_cable_dispatches_all_protocols(self) -> None:
        client = DispatchClient()
        service = WiredInternetService(client)

        cases = (
            (
                {
                    "port": {
                        "mode": "dedicated_wan",
                    }
                },
                "dedicated",
            ),
            ({}, "ipoe"),
            (
                {
                    "protocol": {
                        "type": "pppoe",
                    }
                },
                "pppoe",
            ),
            (
                {
                    "protocol": {
                        "type": "pptp",
                    }
                },
                "pptp",
            ),
            (
                {
                    "protocol": {
                        "type": "l2tp",
                    }
                },
                "l2tp",
            ),
        )

        for settings, expected in cases:
            with self.subTest(expected=expected):
                success, message, _details = (
                    service.create_cable_internet(
                        settings
                    )
                )
                self.assertTrue(success)
                self.assertEqual(message, expected)
                self.assertEqual(
                    client.calls[-1][0],
                    expected,
                )

    def test_unsupported_protocol_is_rejected(self) -> None:
        service = WiredInternetService(
            DispatchClient()
        )

        success, message, details = (
            service.create_cable_internet(
                {
                    "protocol": {
                        "type": "sstp",
                    }
                }
            )
        )

        self.assertFalse(success)
        self.assertIn(
            "не поддерживается",
            message,
        )
        self.assertTrue(
            details["preflight_rejected"]
        )

    def test_cable_settings_normalization_is_in_service(self) -> None:
        result = (
            WiredInternetService
            .cable_settings_for_interface(
                {
                    "mtu": 1492,
                    "manual_dns": [
                        "1.1.1.1",
                    ],
                    "mac": {
                        "mode": "manual",
                        "value": "00:11:22:33:44:55",
                    },
                },
                description="Provider",
            )
        )

        self.assertEqual(
            result["description"],
            "Provider",
        )
        self.assertEqual(
            result["mtu"],
            1492,
        )
        self.assertEqual(
            result["manual_dns"],
            ["1.1.1.1"],
        )

    def test_operations_without_session_preserve_old_contract(self) -> None:
        service = WiredInternetService(
            OfflineClient()
        )

        operations = (
            lambda: service.create_wired_ipoe({}),
            lambda: service.create_wired_pppoe({}),
            lambda: service.create_wired_pptp({}),
            lambda: service.create_wired_l2tp({}),
            lambda: service.create_dedicated_cable_internet({}),
            lambda: service.change_cable_internet_protocol(
                interface_id="Ethernet0",
                settings={},
            ),
            lambda: service.delete_dedicated_wan(
                interface_id="Ethernet0",
            ),
            lambda: service.delete_wired_ipoe("IPoE0"),
            lambda: service.delete_wired_pppoe("PPPoE0"),
            lambda: service.delete_wired_pptp("PPTP0"),
            lambda: service.delete_wired_l2tp("L2TP0"),
        )

        for operation in operations:
            with self.subTest(operation=operation):
                success, message, details = operation()
                self.assertFalse(success)
                self.assertEqual(
                    message,
                    "Нет активной сессии с роутером.",
                )
                self.assertEqual(details, {})


class ClientFacadeTests(unittest.TestCase):
    def test_client_constructs_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(
            username="admin",
            password="secret",
        )

        self.assertIsInstance(
            client.wired_internet_service,
            WiredInternetService,
        )

    def test_client_facade_delegates_create_and_delete(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(
            username="admin",
            password="secret",
        )
        calls: list[tuple[str, Any]] = []

        def create(
            settings: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any]]:
            calls.append(("create", settings))
            return True, "created", {}

        def delete(
            interface_id: str,
        ) -> tuple[bool, str, dict[str, Any]]:
            calls.append(("delete", interface_id))
            return True, "deleted", {}

        client.wired_internet_service.create_wired_ipoe = create  # type: ignore[method-assign]
        client.wired_internet_service.delete_wired_pppoe = delete  # type: ignore[method-assign]

        self.assertEqual(
            client.create_wired_ipoe(
                {"description": "Test"}
            ),
            (True, "created", {}),
        )
        self.assertEqual(
            client.delete_wired_pppoe(
                "PPPoE0"
            ),
            (True, "deleted", {}),
        )
        self.assertEqual(
            calls,
            [
                (
                    "create",
                    {"description": "Test"},
                ),
                ("delete", "PPPoE0"),
            ],
        )

    def test_protocol_change_facade_delegates_keywords(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(
            username="admin",
            password="secret",
        )
        captured: dict[str, Any] = {}

        def change(
            *,
            interface_id: str,
            settings: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any]]:
            captured["interface_id"] = interface_id
            captured["settings"] = settings
            return True, "changed", {}

        client.wired_internet_service.change_cable_internet_protocol = change  # type: ignore[method-assign]

        result = client.change_cable_internet_protocol(
            interface_id="IPoE0",
            settings={"target_protocol": "pppoe"},
        )

        self.assertEqual(
            result,
            (True, "changed", {}),
        )
        self.assertEqual(
            captured,
            {
                "interface_id": "IPoE0",
                "settings": {
                    "target_protocol": "pppoe",
                },
            },
        )


class SourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = (
            Path(__file__).resolve().parents[1]
        )

    def method_span(
        self,
        path: Path,
        class_name: str,
        method_name: str,
    ) -> int:
        module = ast.parse(
            path.read_text(encoding="utf-8")
        )
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef)
            and node.name == method_name
        )
        return (
            int(method.end_lineno)
            - int(method.lineno)
            + 1
        )

    def test_large_operations_are_physically_in_service(self) -> None:
        service_path = (
            self.project_root
            / "core"
            / "services"
            / "wired_internet_service.py"
        )
        client_path = (
            self.project_root
            / "core"
            / "keenetic_client.py"
        )

        self.assertGreater(
            self.method_span(
                service_path,
                "WiredInternetService",
                "create_wired_ipoe",
            ),
            500,
        )
        self.assertLess(
            self.method_span(
                client_path,
                "KeeneticClient",
                "create_wired_ipoe",
            ),
            15,
        )
        self.assertGreater(
            self.method_span(
                service_path,
                "WiredInternetService",
                "change_cable_internet_protocol",
            ),
            450,
        )
        self.assertLess(
            self.method_span(
                client_path,
                "KeeneticClient",
                "change_cable_internet_protocol",
            ),
            20,
        )

    def test_connection_worker_calls_service_directly(self) -> None:
        source = (
            self.project_root
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")

        required = (
            (
                "client.wired_internet_service"
                ".create_cable_internet"
            ),
            (
                "client.wired_internet_service"
                ".create_wired_ipoe"
            ),
            (
                "client.wired_internet_service"
                ".change_cable_internet_protocol"
            ),
            (
                "client.wired_internet_service"
                ".delete_wired_pppoe"
            ),
        )

        for fragment in required:
            self.assertIn(
                fragment,
                source,
            )

        self.assertNotIn(
            "client.create_cable_internet(settings)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
