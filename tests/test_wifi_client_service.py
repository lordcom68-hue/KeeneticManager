from __future__ import annotations

import copy
import unittest
from typing import Any

from core.connection_worker import RouterMonitor
from core.services.wifi_client_service import WiFiClientService


class FakeClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = copy.deepcopy(config)
        self.commands: list[dict[str, Any]] = []

    @staticmethod
    def quote_cli_argument(value: str) -> str:
        return str(value)

    def get_rci_data(self, *, endpoint: str, required: bool) -> dict[str, Any]:
        del required
        if endpoint != "show/sc/interface":
            return {}
        return copy.deepcopy(self.config)

    def execute_rci_batch(
        self,
        commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        del operation_name
        self.commands = copy.deepcopy(commands)
        acl = self.config["Bridge0"].setdefault("mac", {}).setdefault(
            "access-list",
            {"type": "none"},
        )
        addresses = set(acl.get("address") or [])
        for item in commands:
            command = str(item.get("parse") or "")
            if " mac access-list type deny" in command:
                acl["type"] = "deny"
            elif " mac access-list type none" in command:
                acl["type"] = "none"
            elif " no mac access-list address " in command:
                addresses.discard(command.rsplit(" ", 1)[-1])
            elif " mac access-list address " in command:
                addresses.add(command.rsplit(" ", 1)[-1])
        acl["address"] = sorted(addresses)
        return True, ""


def interface_config(
    *,
    acl_type: str = "none",
    addresses: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "WifiMaster0/AccessPoint0": {"ssid": "Home"},
        "WifiMaster1/AccessPoint0": {
            "ssid": "Home",
            "follow": {"access-point": "WifiMaster0/AccessPoint0"},
        },
        "Bridge0": {
            "include": [
                {"interface": "WifiMaster0/AccessPoint0"},
                {"interface": "WifiMaster1/AccessPoint0"},
            ],
            "mac": {
                "access-list": {
                    "type": acl_type,
                    "address": list(addresses or []),
                }
            },
        },
    }


class WiFiClientServiceTests(unittest.TestCase):
    MAC = "00:11:22:33:44:55"

    def test_enrich_resolves_segment_and_deny_state(self) -> None:
        client = FakeClient(
            interface_config(acl_type="deny", addresses=[self.MAC])
        )
        service = WiFiClientService(client)
        clients = [
            {
                "connection_kind": "wifi",
                "mac": self.MAC,
                "ap": "WifiMaster1/AccessPoint0",
                "blocked": False,
            }
        ]

        service.enrich_clients(clients, interface_config=client.config)

        self.assertEqual(clients[0]["segment_id"], "Bridge0")
        self.assertTrue(clients[0]["wifi_blocked"])
        self.assertTrue(clients[0]["blocked"])

    def test_default_acl_blocks_only_requested_mac_on_segment(self) -> None:
        client = FakeClient(interface_config())
        service = WiFiClientService(client)

        success, message, details = service.set_access(
            {
                "mac": self.MAC,
                "ap": "WifiMaster1/AccessPoint0",
            },
            allowed=False,
        )

        self.assertTrue(success, message)
        self.assertEqual(details["segment_id"], "Bridge0")
        parse_commands = [
            item["parse"] for item in client.commands if "parse" in item
        ]
        self.assertEqual(
            parse_commands,
            [
                "interface Bridge0 mac access-list address 00:11:22:33:44:55",
                "interface Bridge0 mac access-list type deny",
            ],
        )
        self.assertIn(
            self.MAC,
            client.config["Bridge0"]["mac"]["access-list"]["address"],
        )

    def test_unregistered_client_is_registered_before_segment_acl(self) -> None:
        client = FakeClient(interface_config())
        service = WiFiClientService(client)

        success, message, details = service.set_access(
            {
                "mac": self.MAC,
                "name": "Phone",
                "registered": False,
                "ap": "WifiMaster0/AccessPoint0",
            },
            allowed=False,
        )

        self.assertTrue(success, message)
        self.assertTrue(details["registered_automatically"])
        parse_commands = [
            item["parse"] for item in client.commands if "parse" in item
        ]
        self.assertEqual(
            parse_commands,
            [
                "known host KM-001122334455 00:11:22:33:44:55",
                "interface Bridge0 mac access-list address 00:11:22:33:44:55",
                "interface Bridge0 mac access-list type deny",
            ],
        )

    def test_deny_acl_can_allow_client_again(self) -> None:
        client = FakeClient(
            interface_config(acl_type="deny", addresses=[self.MAC])
        )
        service = WiFiClientService(client)

        success, message, _details = service.set_access(
            {
                "mac": self.MAC,
                "segment_id": "Bridge0",
                "ap": "WifiMaster0/AccessPoint0",
            },
            allowed=True,
        )

        self.assertTrue(success, message)
        self.assertIn(
            "interface Bridge0 no mac access-list address 00:11:22:33:44:55",
            [item.get("parse") for item in client.commands],
        )

    def test_acl_mode_created_by_app_is_restored_after_last_unblock(self) -> None:
        client = FakeClient(interface_config())
        service = WiFiClientService(client)
        reference = {"mac": self.MAC, "ap": "WifiMaster0/AccessPoint0"}

        success, message, _details = service.set_access(
            reference,
            allowed=False,
        )
        self.assertTrue(success, message)
        success, message, _details = service.set_access(
            reference,
            allowed=True,
        )

        self.assertTrue(success, message)
        self.assertEqual(
            client.config["Bridge0"]["mac"]["access-list"]["type"],
            "none",
        )
        self.assertIn(
            "interface Bridge0 mac access-list type none",
            [item.get("parse") for item in client.commands],
        )

    def test_permit_acl_removes_client_to_block_and_adds_to_allow(self) -> None:
        client = FakeClient(
            interface_config(acl_type="permit", addresses=[self.MAC])
        )
        service = WiFiClientService(client)

        blocked, message, _details = service.set_access(
            {"mac": self.MAC, "ap": "WifiMaster0/AccessPoint0"},
            allowed=False,
        )
        self.assertTrue(blocked, message)
        self.assertNotIn(
            self.MAC,
            client.config["Bridge0"]["mac"]["access-list"]["address"],
        )

        allowed, message, _details = service.set_access(
            {"mac": self.MAC, "ap": "WifiMaster0/AccessPoint0"},
            allowed=True,
        )
        self.assertTrue(allowed, message)
        self.assertIn(
            self.MAC,
            client.config["Bridge0"]["mac"]["access-list"]["address"],
        )

    def test_permit_acl_registers_unknown_client_before_allowing(self) -> None:
        client = FakeClient(interface_config(acl_type="permit"))
        service = WiFiClientService(client)

        success, message, details = service.set_access(
            {
                "mac": self.MAC,
                "registered": False,
                "ap": "WifiMaster0/AccessPoint0",
            },
            allowed=True,
        )

        self.assertTrue(success, message)
        self.assertTrue(details["registered_automatically"])
        self.assertEqual(
            [item["parse"] for item in client.commands if "parse" in item],
            [
                "known host KM-001122334455 00:11:22:33:44:55",
                "interface Bridge0 mac access-list address 00:11:22:33:44:55",
            ],
        )

    def test_management_client_and_invalid_mac_are_rejected(self) -> None:
        service = WiFiClientService(FakeClient(interface_config()))

        success, _message, _details = service.set_access(
            {
                "mac": self.MAC,
                "ap": "WifiMaster0/AccessPoint0",
                "management_client": True,
            },
            allowed=False,
        )
        self.assertFalse(success)

        success, _message, _details = service.set_access(
            {
                "mac": "not-a-mac",
                "ap": "WifiMaster0/AccessPoint0",
            },
            allowed=False,
        )
        self.assertFalse(success)

    def test_worker_routes_action_and_emits_completion(self) -> None:
        fake_client = FakeClient(interface_config())
        fake_client.wifi_client_service = WiFiClientService(fake_client)
        monitor = RouterMonitor({"id": 77})
        emitted: list[tuple[Any, ...]] = []
        monitor.wifi_client_access_completed.connect(
            lambda *args: emitted.append(args)
        )

        refresh_required = monitor.process_action(
            client=fake_client,
            action_type="wifi_client_access",
            payload=(
                {"mac": self.MAC, "ap": "WifiMaster0/AccessPoint0"},
                False,
            ),
        )

        self.assertTrue(refresh_required)
        self.assertEqual(emitted[0][0], 77)
        self.assertEqual(emitted[0][1], self.MAC)
        self.assertTrue(emitted[0][2])


if __name__ == "__main__":
    unittest.main()
