from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.dedicated_wan_service import DedicatedWanService
from core.services.wired_internet_service import WiredInternetService


class PriorityStub:
    @staticmethod
    def current_global_orders(_config: dict[str, Any]) -> dict[str, int]:
        return {"ISP": 0, "Backup": 1}


class OfflineClient:
    session = None
    base_url = None


class CreationRollbackClient:
    def __init__(self) -> None:
        self.session = object()
        self.base_url = "http://192.0.2.1"
        self.priority_service = PriorityStub()
        self.rollback_calls: list[dict[str, Any]] = []

    @staticmethod
    def get_rci_data(*, endpoint: str, required: bool) -> dict[str, Any]:
        del endpoint, required
        return {}

    @staticmethod
    def build_connection_creation_options(
        *,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> dict[str, Any]:
        del interface_data, interface_config_data
        return {
            "ethernet.cable": {
                "ports": [
                    {
                        "port_id": "GigabitEthernet0/0",
                        "mode": "dedicated_wan",
                    }
                ]
            }
        }

    @staticmethod
    def find_static_ipv4_network_conflict(**_kwargs: Any) -> None:
        return None

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
        return True, "WAN исключён из Bridge"

    @staticmethod
    def set_interface_settings(
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        del interface_id, settings
        return False, "настройки отклонены"

    def rollback_dedicated_wan_creation(self, **kwargs: Any) -> tuple[bool, str]:
        self.rollback_calls.append(kwargs)
        return True, "исходная конфигурация восстановлена"


class DeletionRollbackClient:
    def __init__(self) -> None:
        self.session = object()
        self.base_url = "http://192.0.2.1"
        self.priority_service = PriorityStub()
        self.rollback_calls: list[dict[str, Any]] = []

    @staticmethod
    def get_rci_data(*, endpoint: str, required: bool) -> dict[str, Any]:
        del endpoint, required
        return {}

    @staticmethod
    def inspect_dedicated_wan_deletion(
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], str]:
        return (
            {
                "interface_id": "ISP",
                "transport_id": "GigabitEthernet0",
                "bridge_id": "Bridge0",
                "port_id": "GigabitEthernet0/0",
                "port_number": "0",
                "ping_profile": "",
            },
            "",
        )

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
        return True, "WAN подготовлен"

    @staticmethod
    def set_interface_global_enabled(
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        del interface_id, enabled
        return False, "не удалось изменить глобальный приоритет"

    def rollback_dedicated_wan_deletion(self, **kwargs: Any) -> tuple[bool, str]:
        self.rollback_calls.append(kwargs)
        return True, "исходная конфигурация восстановлена"


class DedicatedWanServiceTests(unittest.TestCase):
    def test_offline_operations_preserve_contract(self) -> None:
        service = DedicatedWanService(OfflineClient())

        for operation in (
            lambda: service.create_dedicated_cable_internet({}),
            lambda: service.delete_dedicated_wan(interface_id="ISP"),
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    operation(),
                    (False, "Нет активной сессии с роутером.", {}),
                )

    def test_create_rejects_missing_and_no_longer_free_port(self) -> None:
        client = CreationRollbackClient()
        service = DedicatedWanService(client)

        success, message, details = service.create_dedicated_cable_internet({})
        self.assertFalse(success)
        self.assertIn("Не выбран", message)
        self.assertEqual(details, {})

        client.build_connection_creation_options = (  # type: ignore[method-assign]
            lambda **_kwargs: {"ethernet.cable": {"ports": []}}
        )
        success, message, details = service.create_dedicated_cable_internet(
            self._settings()
        )
        self.assertFalse(success)
        self.assertIn("больше не свободен", message)
        self.assertTrue(details["preflight_rejected"])

    def test_create_rolls_back_after_interface_settings_failure(self) -> None:
        client = CreationRollbackClient()
        service = DedicatedWanService(client)

        success, message, details = service.create_dedicated_cable_internet(
            self._settings()
        )

        self.assertFalse(success)
        self.assertIn("настройки отклонены", message)
        self.assertTrue(details["rollback_success"])
        self.assertEqual(
            client.rollback_calls,
            [
                {
                    "bridge_id": "Bridge0",
                    "transport_id": "GigabitEthernet0",
                    "old_priorities": {"ISP": 0, "Backup": 1},
                }
            ],
        )

    def test_delete_rolls_back_after_global_disable_failure(self) -> None:
        client = DeletionRollbackClient()
        service = DedicatedWanService(client)

        success, message, details = service.delete_dedicated_wan(
            interface_id="ISP"
        )

        self.assertFalse(success)
        self.assertIn("глобальный приоритет", message)
        self.assertTrue(details["rollback_success"])
        self.assertEqual(len(client.rollback_calls), 1)
        self.assertEqual(
            client.rollback_calls[0]["old_priorities"],
            {"ISP": 0, "Backup": 1},
        )
        self.assertEqual(
            client.rollback_calls[0]["context"]["bridge_id"],
            "Bridge0",
        )

    @staticmethod
    def _settings() -> dict[str, Any]:
        return {
            "port": {
                "port_id": "GigabitEthernet0/0",
                "port_label": "WAN",
                "transport_id": "GigabitEthernet0",
                "bridge_id": "Bridge0",
            }
        }


class WiredServiceFacadeTests(unittest.TestCase):
    def test_wired_service_delegates_dedicated_wan_operations(self) -> None:
        service = WiredInternetService(OfflineClient())
        calls: list[tuple[str, Any]] = []

        class DedicatedStub:
            @staticmethod
            def create_dedicated_cable_internet(
                settings: dict[str, Any],
            ) -> tuple[bool, str, dict[str, Any]]:
                calls.append(("create", settings))
                return True, "created", {}

            @staticmethod
            def delete_dedicated_wan(
                *,
                interface_id: str,
            ) -> tuple[bool, str, dict[str, Any]]:
                calls.append(("delete", interface_id))
                return True, "deleted", {}

        service.dedicated_wan_service = DedicatedStub()

        self.assertEqual(
            service.create_dedicated_cable_internet({"port": {}}),
            (True, "created", {}),
        )
        self.assertEqual(
            service.delete_dedicated_wan(interface_id="ISP"),
            (True, "deleted", {}),
        )
        self.assertEqual(
            calls,
            [("create", {"port": {}}), ("delete", "ISP")],
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

    def test_large_operations_are_physically_in_dedicated_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dedicated = root / "core" / "services" / "dedicated_wan_service.py"
        wired = root / "core" / "services" / "wired_internet_service.py"

        for method_name in (
            "create_dedicated_cable_internet",
            "delete_dedicated_wan",
        ):
            self.assertGreater(
                self.method_span(dedicated, "DedicatedWanService", method_name),
                200,
            )
            self.assertLess(
                self.method_span(wired, "WiredInternetService", method_name),
                15,
            )


if __name__ == "__main__":
    unittest.main()
