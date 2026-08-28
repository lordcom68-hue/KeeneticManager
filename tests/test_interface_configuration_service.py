from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.keenetic_client import KeeneticClient
from core.services.interface_configuration_service import (
    InterfaceConfigurationService,
)


CONFIGURATION_METHODS = (
    "remove_interface_dns_record",
    "add_interface_dns_record",
    "replace_interface_dns_records",
    "route_missing_message",
    "get_configured_interface_ipv4",
    "remove_interface_default_route",
    "apply_interface_ipv4_settings",
    "find_interface_default_gateway",
    "set_interface_settings",
    "verify_interface_settings",
)


class InterfaceConfigurationServiceTests(unittest.TestCase):
    def make_client(self) -> KeeneticClient:
        client = KeeneticClient("admin", "secret")
        client.session = MagicMock()
        client.base_url = "http://192.0.2.1"
        return client

    def test_static_ipv4_replaces_dhcp_in_safe_order(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="ISP")
        client.get_rci_data = MagicMock(
            return_value={
                "ISP": {
                    "ip": {
                        "address": {"dhcp": True},
                    }
                }
            }
        )
        client.execute_parse_command = MagicMock(return_value=(True, ""))

        success, message = client.apply_interface_ipv4_settings(
            "ISP",
            {
                "dhcp": False,
                "address": "192.0.2.10",
                "prefix": 24,
                "gateway": "192.0.2.1",
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(
            [call.args[0] for call in client.execute_parse_command.call_args_list],
            [
                "interface ISP no ip address dhcp",
                "interface ISP ip address 192.0.2.10/24",
                "ip route default 192.0.2.1 ISP",
            ],
        )

    def test_dhcp_removes_static_route_before_enabling_client(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="ISP")
        client.get_rci_data = MagicMock(
            return_value={
                "ISP": {
                    "ip": {
                        "address": "192.0.2.10/24",
                    }
                }
            }
        )
        client.execute_parse_command = MagicMock(return_value=(True, ""))

        success, message = client.apply_interface_ipv4_settings(
            "ISP",
            {"dhcp": True},
            current_gateway="192.0.2.1",
        )

        self.assertTrue(success, message)
        self.assertEqual(
            [call.args[0] for call in client.execute_parse_command.call_args_list],
            [
                "no ip route default ISP",
                "interface ISP ip address dhcp",
            ],
        )

    def test_dns_replacement_removes_old_records_then_adds_new(self) -> None:
        client = self.make_client()
        client.get_configured_interface_dns = MagicMock(
            return_value=["8.8.8.8", "2001:4860:4860::8888"]
        )
        client.get_interface_cli_name = MagicMock(return_value="ISP")
        client.execute_parse_command = MagicMock(return_value=(True, ""))

        success, message = client.replace_interface_dns_records(
            "ISP",
            ["1.1.1.1", "2606:4700:4700::1111"],
        )

        self.assertTrue(success, message)
        self.assertEqual(
            [call.args[0] for call in client.execute_parse_command.call_args_list],
            [
                'no ip name-server 8.8.8.8 "" on ISP',
                'no ipv6 name-server 2001:4860:4860::8888 "" on ISP',
                'ip name-server 1.1.1.1 "" on ISP',
                'ipv6 name-server 2606:4700:4700::1111 "" on ISP',
            ],
        )

    def test_gateway_is_found_in_nested_route_payload(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="Provider")

        def get_rci_data(*, endpoint: str, required: bool = False) -> object:
            del required
            if endpoint == "show/ip/route":
                return {
                    "routes": [
                        {
                            "destination": "0.0.0.0/0",
                            "interface": "Provider",
                            "gateway": "192.0.2.1",
                        }
                    ]
                }
            return {}

        client.get_rci_data = MagicMock(side_effect=get_rci_data)

        self.assertEqual(
            client.find_interface_default_gateway("ISP"),
            "192.0.2.1",
        )

    def test_manual_mac_is_batched_and_verified(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="ISP")
        client.execute_rci_batch = MagicMock(return_value=(True, ""))
        service = client.interface_configuration_service
        service.verify_interface_settings = MagicMock(
            return_value=(True, "Настройки подтверждены роутером.")
        )

        success, message = client.set_interface_settings(
            "ISP",
            {
                "mac": {
                    "mode": "manual",
                    "address": "02:00:5E:10:00:01",
                }
            },
        )

        self.assertTrue(success, message)
        commands = client.execute_rci_batch.call_args.args[0]
        self.assertEqual(
            commands,
            [
                {
                    "parse": (
                        "interface ISP mac address "
                        "02:00:5E:10:00:01"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
        )
        service.verify_interface_settings.assert_called_once()

    def test_apply_failure_stops_before_verification(self) -> None:
        client = self.make_client()
        client.execute_rci_batch = MagicMock(
            return_value=(False, "синтетическая ошибка применения")
        )
        service = client.interface_configuration_service
        service.verify_interface_settings = MagicMock()

        success, message = client.set_interface_settings(
            "ISP",
            {"description": "Provider"},
        )

        self.assertFalse(success)
        self.assertEqual(message, "синтетическая ошибка применения")
        service.verify_interface_settings.assert_not_called()

    def test_verification_covers_ipv4_dns_gateway_mac_and_dhcp(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="ISP")

        config = {
            "ISP": {
                "description": "Provider",
                "ip": {
                    "mtu": 1500,
                    "name-servers": True,
                    "name-server": [
                        {"name-server": "1.1.1.1"},
                        {"address": "2606:4700:4700::1111"},
                    ],
                    "address": "192.0.2.10/24",
                    "dhcp": {
                        "client": {
                            "hostname": "router-edge",
                            "routes": True,
                            "dns-routes": False,
                        }
                    },
                },
                "mac": {
                    "address": "02:00:5E:10:00:01",
                },
            }
        }

        def get_rci_data(*, endpoint: str, required: bool = False) -> object:
            del required
            if endpoint == "show/sc/interface":
                return config
            if endpoint == "show/ip/route":
                return {
                    "routes": [
                        {
                            "destination": "default",
                            "interface": "ISP",
                            "gateway": "192.0.2.1",
                        }
                    ]
                }
            return {}

        client.get_rci_data = MagicMock(side_effect=get_rci_data)
        expected = {
            "description": "Provider",
            "mtu": 1500,
            "use_provider_dns": True,
            "manual_dns": ["1.1.1.1", "2606:4700:4700::1111"],
            "mac": {
                "mode": "manual",
                "address": "02:00:5E:10:00:01",
            },
            "dhcp_client": {
                "hostname": "router-edge",
                "routes": True,
                "dns_routes": False,
            },
            "ipv4": {
                "dhcp": False,
                "address": "192.0.2.10",
                "prefix": 24,
                "gateway": "192.0.2.1",
            },
        }

        with patch(
            "core.services.interface_configuration_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_interface_settings(
                "ISP",
                expected,
            )

        self.assertTrue(success, message)

    def test_full_settings_are_normalized_before_single_saved_batch(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={
                "ISP": {
                    "ping-check": {"profile": "old-profile"},
                }
            }
        )
        client.execute_rci_batch = MagicMock(return_value=(True, ""))
        client.ping_check_service.build_ping_check_commands = MagicMock(
            return_value=(
                ["ping-check profile _KM_ISP mode tls"],
                "_KM_ISP",
            )
        )
        service = client.interface_configuration_service
        service.replace_interface_dns_records = MagicMock(
            return_value=(True, "")
        )
        service.find_interface_default_gateway = MagicMock(
            return_value="192.0.2.254"
        )
        service.apply_interface_ipv4_settings = MagicMock(
            return_value=(True, "")
        )
        service.verify_interface_settings = MagicMock(
            return_value=(True, "Настройки подтверждены роутером.")
        )

        success, message = client.set_interface_settings(
            "ISP",
            {
                "description": "Provider uplink",
                "mtu": "1492",
                "use_provider_dns": False,
                "manual_dns": ["1.1.1.1", "2606:4700:4700::1111"],
                "ipv4": {
                    "dhcp": False,
                    "address": "192.0.2.10/24",
                    "prefix": "24",
                    "gateway": "192.0.2.1",
                },
                "dhcp_client": {
                    "hostname": "router-edge",
                    "routes": True,
                    "dns_routes": False,
                },
                "ping_check": {
                    "mode": "tls",
                    "host": "one.one.one.one",
                    "interval": "10",
                    "max_fails": "3",
                    "port": "443",
                },
                "mac": {"mode": "factory"},
            },
        )

        self.assertTrue(success, message)
        service.replace_interface_dns_records.assert_called_once_with(
            interface_id="ISP",
            addresses=["1.1.1.1", "2606:4700:4700::1111"],
        )
        service.apply_interface_ipv4_settings.assert_called_once_with(
            interface_id="ISP",
            ipv4_settings={
                "dhcp": False,
                "address": "192.0.2.10",
                "prefix": 24,
                "gateway": "192.0.2.1",
            },
            current_gateway="192.0.2.254",
        )
        expected = service.verify_interface_settings.call_args.kwargs[
            "expected"
        ]
        self.assertEqual(expected["mtu"], 1492)
        self.assertEqual(expected["ping_check_profile"], "_KM_ISP")
        self.assertEqual(expected["ping_check"]["port"], 443)
        commands = client.execute_rci_batch.call_args.args[0]
        self.assertIn(
            {"parse": "ping-check profile _KM_ISP mode tls"},
            commands,
        )
        self.assertEqual(
            commands[-1],
            {"system": {"configuration": {"save": {}}}},
        )

    def test_invalid_settings_are_rejected_before_router_write(self) -> None:
        client = self.make_client()
        client.execute_rci_batch = MagicMock(return_value=(True, ""))
        cases = (
            ({"description": ""}, "не может быть пустым"),
            ({"description": "x" * 65}, "слишком длинное"),
            ({"mtu": None}, "Пустое значение MTU"),
            ({"mtu": "bad"}, "целым числом"),
            ({"mtu": 500}, "от 576 до 9000"),
            ({"manual_dns": "1.1.1.1"}, "переданы списком"),
            ({"manual_dns": ["invalid"]}, "Некорректный DNS"),
            ({"manual_dns": ["1.1.1.1", "1.1.1.1"]}, "не должны совпадать"),
            ({"ipv4": "dhcp"}, "неверном формате"),
            ({"ipv4": {"dhcp": False, "prefix": "bad"}}, "должна быть числом"),
            (
                {
                    "ipv4": {
                        "dhcp": False,
                        "address": "invalid",
                        "prefix": 24,
                        "gateway": "192.0.2.1",
                    }
                },
                "ручной IPv4",
            ),
            (
                {
                    "ipv4": {
                        "dhcp": False,
                        "address": "192.0.2.10",
                        "prefix": 24,
                        "gateway": "invalid",
                    }
                },
                "адрес шлюза",
            ),
            ({"dhcp_client": "router"}, "неверном формате"),
            ({"dhcp_client": {"hostname": ""}}, "не может быть пустым"),
            (
                {"dhcp_client": {"hostname": "bad name"}},
                "недопустимые символы",
            ),
            ({"ping_check": "icmp"}, "неверном формате"),
            (
                {"ping_check": {"mode": "icmp", "host": "bad/host"}},
                "Некорректный узел",
            ),
            (
                {
                    "ping_check": {
                        "mode": "icmp",
                        "host": "1.1.1.1",
                        "interval": "bad",
                        "max_fails": 3,
                    }
                },
                "целыми числами",
            ),
            ({"ping_check": {"mode": "unsupported"}}, "Неподдерживаемый"),
            ({"mac": "factory"}, "неверном формате"),
            ({"mac": {"mode": "manual", "address": "invalid"}}, "MAC-адрес"),
            ({"mac": {"mode": "random"}}, "Неизвестный режим"),
            ({}, "Нет изменённых параметров"),
        )

        for settings, expected_message in cases:
            with self.subTest(settings=settings):
                success, message = client.set_interface_settings(
                    "ISP",
                    settings,
                )
                self.assertFalse(success)
                self.assertIn(expected_message, message)

        client.execute_rci_batch.assert_not_called()

    def test_wireless_isp_only_path_propagates_result(self) -> None:
        client = self.make_client()
        client.wireless_isp_service.update_wireless_isp_settings = MagicMock(
            return_value=(True, "Wireless ISP обновлён.")
        )

        success, message = client.set_interface_settings(
            "WifiStation0",
            {"wireless_isp": {"ssid": "Provider"}},
        )

        self.assertTrue(success)
        self.assertEqual(message, "Wireless ISP обновлён.")
        client.wireless_isp_service.update_wireless_isp_settings.assert_called_once()

        client.wireless_isp_service.update_wireless_isp_settings.return_value = (
            False,
            "Ошибка Wireless ISP.",
        )
        success, message = client.set_interface_settings(
            "WifiStation0",
            {"wireless_isp": {"ssid": "Provider 2"}},
        )
        self.assertFalse(success)
        self.assertEqual(message, "Ошибка Wireless ISP.")

    def test_verification_failure_reports_last_mismatch(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={"ISP": {"description": "Old name"}}
        )

        with patch(
            "core.services.interface_configuration_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_interface_settings(
                "ISP",
                {"description": "New name"},
            )

        self.assertFalse(success)
        self.assertIn("имя не подтверждено", message)

    def test_route_removal_accepts_missing_route_and_gateway_fallback(self) -> None:
        client = self.make_client()
        client.get_interface_cli_name = MagicMock(return_value="ISP")
        client.execute_parse_command = MagicMock(
            return_value=(False, "route not found")
        )

        success, message = client.remove_interface_default_route(
            "ISP",
            "192.0.2.1",
        )
        self.assertTrue(success, message)
        self.assertEqual(client.execute_parse_command.call_count, 1)

        client.execute_parse_command.reset_mock()
        client.execute_parse_command.side_effect = [
            (False, "interface form rejected"),
            (True, ""),
        ]
        success, message = client.remove_interface_default_route(
            "ISP",
            "192.0.2.1",
        )
        self.assertTrue(success, message)
        self.assertEqual(
            [call.args[0] for call in client.execute_parse_command.call_args_list],
            [
                "no ip route default ISP",
                "no ip route default 192.0.2.1",
            ],
        )


class InterfaceConfigurationSourceBoundaryTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    @staticmethod
    def method_spans(path: Path, class_name: str) -> dict[str, int]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        return {
            node.name: int(node.end_lineno) - int(node.lineno) + 1
            for node in class_node.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_service_owns_large_configuration_methods(self) -> None:
        service_path = (
            self.root
            / "core"
            / "services"
            / "interface_configuration_service.py"
        )
        client_path = self.root / "core" / "keenetic_client.py"
        service_spans = self.method_spans(
            service_path,
            "InterfaceConfigurationService",
        )
        client_spans = self.method_spans(client_path, "KeeneticClient")

        for name in CONFIGURATION_METHODS:
            self.assertIn(name, service_spans)
            self.assertIn(name, client_spans)
            self.assertLess(client_spans[name], 25, name)

        self.assertGreater(service_spans["set_interface_settings"], 500)
        self.assertGreater(service_spans["verify_interface_settings"], 500)

    def test_client_constructs_and_routes_to_configuration_service(self) -> None:
        client_source = (
            self.root / "core" / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "self.interface_configuration_service = (",
            client_source,
        )
        self.assertIn(
            "self.interface_configuration_service.set_interface_settings(",
            client_source,
        )
        self.assertNotIn("ip address dhcp", client_source)
        self.assertNotIn("no ip route default", client_source)

    def test_exported_service_contract(self) -> None:
        self.assertTrue(callable(InterfaceConfigurationService))
        for name in CONFIGURATION_METHODS:
            self.assertTrue(
                callable(getattr(InterfaceConfigurationService, name, None)),
                name,
            )


if __name__ == "__main__":
    unittest.main()
