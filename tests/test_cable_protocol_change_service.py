from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.cable_protocol_change_service import (
    CableProtocolChangeService,
)
from core.services.wired_internet_service import WiredInternetService


class OfflineClient:
    session = None
    base_url = None


class PriorityStub:
    @staticmethod
    def current_global_orders(_config: dict[str, Any]) -> dict[str, int]:
        return {"ISP": 0}


class PingCheckStub:
    @staticmethod
    def assigned_ping_profile(_config: dict[str, Any]) -> str:
        return ""


class ProtocolRollbackClient:
    def __init__(self) -> None:
        self.session = object()
        self.base_url = "http://192.0.2.1"
        self.priority_service = PriorityStub()
        self.ping_check_service = PingCheckStub()
        self.rollback_calls: list[dict[str, Any]] = []

    @staticmethod
    def normalize_host(value: str) -> str:
        return value.strip().lower()

    @staticmethod
    def redact_secrets_from_text(value: str, *_secrets: str) -> str:
        return value

    @staticmethod
    def get_rci_data(*, endpoint: str, required: bool) -> dict[str, Any]:
        del endpoint, required
        return {"ISP": {"ip": {"mtu": 1500}}}

    @staticmethod
    def inspect_cable_protocol_change(
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], str]:
        return (
            {
                "current_protocol": "none",
                "transport_id": "ISP",
                "global_order": 0,
            },
            "",
        )

    @staticmethod
    def configured_name_server_settings(
        _config: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        return True, []

    @staticmethod
    def next_numbered_interface_id(
        _config: dict[str, Any],
        prefix: str,
    ) -> str:
        return f"{prefix}0"

    @staticmethod
    def create_protocol_on_existing_transport(**_kwargs: Any) -> tuple[bool, str]:
        return True, "протокол создан"

    @staticmethod
    def set_interface_settings(
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        del interface_id, settings
        return False, "настройки протокола отклонены"

    def rollback_cable_protocol_change(self, **kwargs: Any) -> tuple[bool, str]:
        self.rollback_calls.append(kwargs)
        return True, "исходный протокол восстановлен"


class ProtocolSuccessClient(ProtocolRollbackClient):
    def __init__(self) -> None:
        super().__init__()
        self.traffic_service = type(
            "TrafficStub",
            (),
            {"clear": staticmethod(lambda: None)},
        )()
        self.ping_check_service.runtime_cache = {}

    @staticmethod
    def set_interface_settings(**_kwargs: Any) -> tuple[bool, str]:
        return True, "настройки сохранены"

    @staticmethod
    def set_interface_global_enabled(
        _interface_id: str,
        _enabled: bool,
    ) -> tuple[bool, str]:
        return True, "состояние изменено"

    @staticmethod
    def quote_cli_argument(value: Any) -> str:
        return str(value)

    @staticmethod
    def execute_rci_batch(
        _commands: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> tuple[bool, str]:
        return True, "команда выполнена"

    @staticmethod
    def is_transient_rci_failure(_message: str) -> bool:
        return False

    @staticmethod
    def verify_changed_cable_protocol(
        **_kwargs: Any,
    ) -> tuple[bool, str, bool]:
        return True, "подтверждено", False

    @staticmethod
    def set_priorities(_priorities: dict[str, int]) -> tuple[bool, str]:
        return True, "приоритеты сохранены"

    def prepare(self) -> None:
        self.priority_service.set_internet_priorities = self.set_priorities


class CableProtocolChangeServiceTests(unittest.TestCase):
    def test_offline_operation_preserves_contract(self) -> None:
        service = CableProtocolChangeService(OfflineClient())
        self.assertEqual(
            service.change_cable_internet_protocol(
                interface_id="ISP",
                settings={"target_protocol": "pppoe"},
            ),
            (False, "Нет активной сессии с роутером.", {}),
        )

    def test_change_rolls_back_after_target_settings_failure(self) -> None:
        client = ProtocolRollbackClient()
        service = CableProtocolChangeService(client)

        success, message, details = service.change_cable_internet_protocol(
            interface_id="ISP",
            settings={
                "target_protocol": "pppoe",
                "description": "Provider",
                "username": "user",
                "password": "secret",
            },
        )

        self.assertFalse(success)
        self.assertIn("настройки протокола отклонены", message)
        self.assertTrue(details["rollback_success"])
        self.assertEqual(len(client.rollback_calls), 1)
        self.assertEqual(
            client.rollback_calls[0],
            {
                "source_id": "ISP",
                "target_id": "PPPoE0",
                "target_protocol": "pppoe",
                "transport_id": "ISP",
                "old_priorities": {"ISP": 0},
                "ping_profile": "",
                "transport_restore_settings": None,
            },
        )

    def test_successful_change_reaches_verified_result(self) -> None:
        client = ProtocolSuccessClient()
        client.prepare()
        service = CableProtocolChangeService(client)

        success, message, details = service.change_cable_internet_protocol(
            interface_id="ISP",
            settings={
                "target_protocol": "pppoe",
                "description": "Provider",
                "username": "user",
                "password": "secret",
                "service": "internet",
                "mtu": 1492,
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(details["interface_id"], "PPPoE0")
        self.assertEqual(details["protocol"], "pppoe")
        self.assertFalse(details["transient_recovered"])


class WiredServiceFacadeTests(unittest.TestCase):
    def test_wired_service_delegates_protocol_change(self) -> None:
        service = WiredInternetService(OfflineClient())
        captured: dict[str, Any] = {}

        class ProtocolStub:
            @staticmethod
            def change_cable_internet_protocol(
                *,
                interface_id: str,
                settings: dict[str, Any],
            ) -> tuple[bool, str, dict[str, Any]]:
                captured["interface_id"] = interface_id
                captured["settings"] = settings
                return True, "changed", {}

        service.cable_protocol_change_service = ProtocolStub()
        settings = {"target_protocol": "pppoe"}

        self.assertEqual(
            service.change_cable_internet_protocol(
                interface_id="ISP",
                settings=settings,
            ),
            (True, "changed", {}),
        )
        self.assertEqual(
            captured,
            {"interface_id": "ISP", "settings": settings},
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

    def test_large_operation_is_in_protocol_change_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        protocol = (
            root / "core" / "services" / "cable_protocol_change_service.py"
        )
        wired = root / "core" / "services" / "wired_internet_service.py"
        method_name = "change_cable_internet_protocol"

        self.assertGreater(
            self.method_span(
                protocol,
                "CableProtocolChangeService",
                method_name,
            ),
            450,
        )
        self.assertLess(
            self.method_span(wired, "WiredInternetService", method_name),
            20,
        )


if __name__ == "__main__":
    unittest.main()
