from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.services.cable_protocol_lifecycle_service import (
    CableProtocolLifecycleService,
)


class Inspector:
    @staticmethod
    def cable_protocol_from_config(_name: str, item: dict[str, object]) -> str:
        return str(item.get("protocol") or "none")

    @staticmethod
    def is_wired_transport_interface(name: str) -> bool:
        return name.startswith("GigabitEthernet")

    @staticmethod
    def extract_port_roles(item: dict[str, object]) -> list[dict[str, object]]:
        roles = item.get("roles")
        return roles if isinstance(roles, list) else []

    @staticmethod
    def runtime_ethernet_port_state(**_kwargs: object) -> dict[str, object]:
        return {"known": True, "active": True, "link": "up"}


class FakeCableClient:
    def __init__(self) -> None:
        self.base_url = "http://192.0.2.1"
        self.transport = SimpleNamespace(
            reauthenticate=lambda: True,
            last_error="",
        )
        self.batches: list[list[dict[str, object]]] = []
        self.commands: list[str] = []
        self.config: dict[str, object] = {}

    @staticmethod
    def quote_cli_argument(value: object) -> str:
        return str(value)

    @staticmethod
    def redact_secrets_from_text(value: str, _secret: str) -> str:
        return value

    def execute_rci_batch(
        self,
        commands: list[dict[str, object]],
        **_kwargs: object,
    ) -> tuple[bool, str]:
        self.batches.append(commands)
        return True, "ok"

    def execute_parse_command(self, command: str) -> tuple[bool, str]:
        self.commands.append(command)
        return True, "ok"

    @staticmethod
    def set_interface_global_enabled(
        _interface_id: str,
        _enabled: bool,
    ) -> tuple[bool, str]:
        return True, "ok"

    @staticmethod
    def set_interface_settings(**_kwargs: object) -> tuple[bool, str]:
        return True, "ok"

    @staticmethod
    def set_internet_priorities(_value: dict[str, int]) -> tuple[bool, str]:
        return True, "ok"

    def get_rci_data(self, **_kwargs: object) -> dict[str, object]:
        return self.config

    @staticmethod
    def cable_protocol_from_config(_name: str, item: dict[str, object]) -> str:
        return str(item.get("protocol") or "none")

    @staticmethod
    def assigned_ping_profile(item: dict[str, object]) -> str:
        return str(item.get("ping_profile") or "")


class CableProtocolLifecycleServiceTests(unittest.TestCase):
    def test_inspection_returns_safe_context_for_wired_transport(self) -> None:
        config = {
            "GigabitEthernet0": {
                "protocol": "none",
                "ip": {"global": {"order": 2}},
            },
            "GigabitEthernet0/0": {
                "roles": [
                    {"role": "inet", "ifor": "GigabitEthernet0"}
                ],
                "rename": "WAN",
            },
        }
        context, message = (
            CableProtocolLifecycleService.inspect_cable_protocol_change(
                Inspector,
                interface_id="GigabitEthernet0",
                interface_config=config,
                interface_runtime={},
            )
        )
        self.assertEqual(message, "")
        self.assertEqual(context["transport_id"], "GigabitEthernet0")
        self.assertEqual(context["global_order"], 2)
        self.assertTrue(context["port_link_active"])

    def test_create_rollback_reauthentication_and_verification(self) -> None:
        client = FakeCableClient()
        service = CableProtocolLifecycleService(client)
        success, _message = service.create_protocol_on_existing_transport(
            target_protocol="pppoe",
            target_id="PPPoE0",
            transport_id="GigabitEthernet0",
            description="Provider",
            username="user",
            password="secret",
            service="internet",
            server="",
        )
        self.assertTrue(success)
        self.assertTrue(
            any(
                "pppoe service" in str(command.get("parse") or "")
                for command in client.batches[0]
            )
        )
        self.assertTrue(service.renew_authenticated_rci_session()[0])

        success, _message = service.rollback_cable_protocol_change(
            source_id="ISP",
            target_id="PPPoE0",
            target_protocol="pppoe",
            transport_id="GigabitEthernet0",
            old_priorities={"ISP": 0},
            ping_profile="Profile0",
            transport_restore_settings=None,
        )
        self.assertTrue(success)
        self.assertIn("no interface PPPoE0", client.commands)

        client.config = {
            "PPPoE0": {
                "protocol": "pppoe",
                "connect": {"via": "GigabitEthernet0"},
                "authentication": {
                    "identity": "user",
                    "password": "stored",
                },
                "pppoe": {"service": "internet"},
                "ip": {"global": {"order": 0}, "mtu": 1492},
                "ping_profile": "Profile0",
            }
        }
        with patch("core.services.cable_protocol_lifecycle_service.time.sleep"):
            verified = service.verify_changed_cable_protocol(
                source_id="ISP",
                target_id="PPPoE0",
                target_protocol="pppoe",
                transport_id="GigabitEthernet0",
                expected_order=0,
                expected_username="user",
                expected_service="internet",
                expected_server="",
                expected_mtu=1492,
                expected_ping_profile="Profile0",
            )
        self.assertEqual(verified, (True, "Новый протокол подтверждён.", False))


if __name__ == "__main__":
    unittest.main()
