from __future__ import annotations

import unittest

from core.keenetic_client import KeeneticClient
from core.services.interface_service import InterfaceService


class FakeClient:
    @staticmethod
    def get_interface_cli_name(interface_id: str) -> str:
        return f"cli-{interface_id}"

    @staticmethod
    def quote_cli_argument(value: str) -> str:
        return f"<{value}>"


class InterfaceAddressServiceTests(unittest.TestCase):
    def test_ipv4_prefix_and_config_normalization(self) -> None:
        self.assertEqual(InterfaceService.prefix_from_ipv4_mask("/24"), 24)
        self.assertEqual(
            InterfaceService.prefix_from_ipv4_mask("255.255.255.0"),
            24,
        )
        self.assertIsNone(InterfaceService.prefix_from_ipv4_mask("33"))
        self.assertEqual(
            InterfaceService.extract_configured_ipv4({"dhcp": "yes"}),
            (True, "", None),
        )
        self.assertEqual(
            InterfaceService.extract_configured_ipv4("192.0.2.10/27"),
            (False, "192.0.2.10", 27),
        )
        self.assertEqual(
            InterfaceService.extract_configured_ipv4(
                {"address": "192.0.2.20", "mask": "255.255.255.0"}
            ),
            (False, "192.0.2.20", 24),
        )
        self.assertEqual(
            InterfaceService.normalize_ipv4_text("192.0.2.30/24"),
            "192.0.2.30",
        )
        self.assertEqual(InterfaceService.normalize_ipv4_text("2001:db8::1"), "")

    def test_mac_settings_normalization_and_validation(self) -> None:
        self.assertEqual(
            InterfaceService.normalize_mac_address("02-aa-bb-cc-dd-ee"),
            "02:AA:BB:CC:DD:EE",
        )
        self.assertEqual(
            InterfaceService.extract_configured_mac_settings(
                {"mac": {"address": {"factory": {"wan": "yes"}}}}
            ),
            (True, "factory", ""),
        )
        self.assertEqual(
            InterfaceService.extract_configured_mac_settings(
                {"mac": {"address": {"value": "02aabbccddee"}}}
            ),
            (True, "manual", "02:AA:BB:CC:DD:EE"),
        )
        self.assertEqual(
            InterfaceService.validate_manual_mac("02:AA:BB:CC:DD:EE"),
            (True, "02:AA:BB:CC:DD:EE"),
        )
        self.assertEqual(
            InterfaceService.validate_manual_mac("01:AA:BB:CC:DD:EE"),
            (False, "01:AA:BB:CC:DD:EE"),
        )

    def test_mac_command_and_client_facades_are_preserved(self) -> None:
        service = InterfaceService(FakeClient())
        self.assertEqual(
            service.build_interface_mac_command("ISP", {"mode": "factory"}),
            "interface <cli-ISP> mac address factory wan",
        )
        self.assertEqual(
            service.build_interface_mac_command(
                "ISP",
                {"mode": "manual", "address": "02:AA:BB:CC:DD:EE"},
            ),
            "interface <cli-ISP> mac address 02:AA:BB:CC:DD:EE",
        )
        self.assertEqual(
            KeeneticClient.extract_configured_ipv4("198.51.100.8/30"),
            (False, "198.51.100.8", 30),
        )
        self.assertEqual(
            KeeneticClient.normalize_mac_address("02aabbccddee"),
            "02:AA:BB:CC:DD:EE",
        )
