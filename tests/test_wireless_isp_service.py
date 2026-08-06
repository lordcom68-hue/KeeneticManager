from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.wireless_isp_service import (
    WirelessIspService,
)


class FakeResponse:
    def __init__(
        self,
        *,
        data: Any,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self) -> Any:
        return self._data


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        **kwargs: Any,
    ) -> FakeResponse:
        del kwargs
        self.calls.append(url)

        return FakeResponse(
            data=[
                {
                    "ssid": "Home Wi-Fi",
                    "bssid": "AA:BB:CC:DD:EE:FF",
                    "channel": 6,
                    "rssi": -48,
                    "security": "WPA2",
                },
                {
                    "ssid": "Guest",
                    "bssid": "11:22:33:44:55:66",
                    "channel": 11,
                    "rssi": -70,
                    "security": "open",
                },
            ]
        )


class FakeScanClient:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.base_url = "https://router.invalid"
        self.timeout = 2.0
        self.verify_ssl = False

    def post_rci_query(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> tuple[bool, Any, str]:
        del payload, timeout
        return False, None, "not used"


class OfflineClient:
    def __init__(self) -> None:
        self.session = None
        self.base_url = None


class WirelessIspServiceTests(unittest.TestCase):
    def test_band_normalization_accepts_user_formats(self) -> None:
        cases = (
            ("2,4 ГГц", "2.4"),
            ("2.4ghz", "2.4"),
            ("WifiMaster0", "2.4"),
            ("5 ГГц", "5"),
            ("WifiMaster1", "5"),
            ("unknown", ""),
        )

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    WirelessIspService
                    .normalize_wireless_isp_band(
                        value
                    ),
                    expected,
                )

    def test_interface_mapping_for_both_bands(self) -> None:
        self.assertEqual(
            WirelessIspService
            .wireless_isp_interface_for_band(
                "2.4"
            ),
            (
                "WifiMaster0",
                "WifiMaster0/WifiStation0",
            ),
        )
        self.assertEqual(
            WirelessIspService
            .wireless_isp_interface_for_band(
                "5"
            ),
            (
                "WifiMaster1",
                "WifiMaster1/WifiStation0",
            ),
        )

    def test_band_is_detected_from_station_interface(self) -> None:
        self.assertEqual(
            WirelessIspService
            .wireless_isp_band_from_interface(
                "WifiMaster0/WifiStation0"
            ),
            "2.4",
        )
        self.assertEqual(
            WirelessIspService
            .wireless_isp_band_from_interface(
                "WifiMaster1/WifiStation0"
            ),
            "5",
        )
        self.assertEqual(
            WirelessIspService
            .wireless_isp_band_from_interface(
                "Ethernet0"
            ),
            "",
        )

    def test_ssid_and_password_validation(self) -> None:
        self.assertTrue(
            WirelessIspService
            .valid_wireless_isp_ssid(
                "Home"
            )
        )
        self.assertFalse(
            WirelessIspService
            .valid_wireless_isp_ssid("")
        )
        self.assertTrue(
            WirelessIspService
            .valid_wireless_isp_password(
                "12345678"
            )
        )
        self.assertFalse(
            WirelessIspService
            .valid_wireless_isp_password(
                "short"
            )
        )

    def test_configured_item_requires_real_profile_data(self) -> None:
        self.assertFalse(
            WirelessIspService
            .wireless_isp_configured_item(
                {
                    "state": "up",
                }
            )
        )
        self.assertTrue(
            WirelessIspService
            .wireless_isp_configured_item(
                {
                    "ssid": "Provider",
                }
            )
        )
        self.assertTrue(
            WirelessIspService
            .wireless_isp_configured_item(
                {
                    "ip": {
                        "global": {
                            "order": 1,
                        }
                    }
                }
            )
        )

    def test_wireless_interface_detection(self) -> None:
        self.assertTrue(
            WirelessIspService
            .is_wireless_isp_interface(
                "WifiStation",
                "WifiMaster0/WifiStation0",
                "",
            )
        )
        self.assertFalse(
            WirelessIspService
            .is_wireless_isp_interface(
                "GigabitEthernet",
                "Ethernet0",
                "WAN",
            )
        )

    def test_wireless_display_name_contains_band(self) -> None:
        self.assertEqual(
            WirelessIspService
            .wireless_isp_display_name(
                "WifiMaster0/WifiStation0",
                "",
                {},
            ),
            "Wireless ISP 2.4 ГГц",
        )
        self.assertEqual(
            WirelessIspService
            .wireless_isp_display_name(
                "WifiMaster1/WifiStation0",
                "",
                {},
            ),
            "Wireless ISP 5 ГГц",
        )

    def test_site_survey_is_normalized_and_sorted(self) -> None:
        networks = (
            WirelessIspService
            .parse_wireless_site_survey(
                [
                    {
                        "ssid": "Weak",
                        "bssid": "11-22-33-44-55-66",
                        "rssi": -80,
                        "channel": 11,
                        "security": "open",
                    },
                    {
                        "ssid": "Strong",
                        "bssid": "AA:BB:CC:DD:EE:FF",
                        "rssi": -42,
                        "channel": 6,
                        "security": "WPA2",
                    },
                ]
            )
        )

        self.assertEqual(
            [item["ssid"] for item in networks],
            ["Strong", "Weak"],
        )
        self.assertEqual(
            networks[0]["bssid"],
            "AA:BB:CC:DD:EE:FF",
        )
        self.assertFalse(
            networks[0]["open"]
        )
        self.assertTrue(
            networks[1]["open"]
        )

    def test_scan_uses_band_specific_master(self) -> None:
        client = FakeScanClient()
        service = WirelessIspService(client)

        success, message, networks = (
            service.scan_wireless_isp(
                "2.4"
            )
        )

        self.assertTrue(success)
        self.assertEqual(
            message,
            "Найдено сетей: 2.",
        )
        self.assertEqual(
            len(networks),
            2,
        )
        self.assertIn(
            "WifiMaster0",
            client.session.calls[0],
        )

    def test_scan_without_session_preserves_contract(self) -> None:
        result = WirelessIspService(
            OfflineClient()
        ).scan_wireless_isp(
            "2.4"
        )

        self.assertEqual(
            result,
            (
                False,
                "Нет активной сессии с роутером.",
                [],
            ),
        )

    def test_create_preflight_rejects_missing_band(self) -> None:
        service = WirelessIspService(
            OfflineClient()
        )

        success, message, details = (
            service.create_wireless_isp(
                {
                    "band": "",
                    "ssid": "Home",
                    "open_network": True,
                }
            )
        )

        self.assertFalse(success)
        self.assertIn(
            "Не выбран диапазон",
            message,
        )
        self.assertTrue(
            details["preflight_rejected"]
        )

    def test_delete_preflight_rejects_non_wireless_interface(
        self,
    ) -> None:
        service = WirelessIspService(
            OfflineClient()
        )

        success, message, details = (
            service.delete_wireless_isp(
                interface_id="Ethernet0"
            )
        )

        self.assertFalse(success)
        self.assertIn(
            "неподдерживаемый интерфейс",
            message,
        )
        self.assertTrue(
            details["preflight_rejected"]
        )

    def test_enrich_adds_safe_wireless_details(self) -> None:
        from core.keenetic_client import (
            KeeneticClient,
        )

        client = KeeneticClient(
            username="admin",
            password="secret",
        )
        connections = [
            {
                "interface_id": (
                    "WifiMaster0/WifiStation0"
                ),
                "interface_name": "WifiStation0",
                "type": "WifiStation",
            }
        ]
        runtime = {
            "WifiMaster0/WifiStation0": {
                "type": "WifiStation",
                "interface-name": "WifiStation0",
                "ssid": "Provider",
                "ap": "AA:BB:CC:DD:EE:FF",
                "rssi": -55,
                "channel": 6,
                "link": "up",
                "global": True,
            }
        }
        config = {
            "WifiMaster0/WifiStation0": {
                "ssid": "Provider",
                "authentication": {
                    "wpa-psk": {},
                },
                "ip": {
                    "global": {
                        "order": 1,
                    }
                },
            }
        }

        client.wireless_isp_service.enrich_wireless_isp_details(
            connections=connections,
            interface_data=runtime,
            interface_config_data=config,
        )

        connection = connections[0]
        self.assertEqual(
            connection["wireless_isp_band"],
            "2.4",
        )
        self.assertEqual(
            connection["wireless_isp_ssid"],
            "Provider",
        )
        self.assertTrue(
            connection["delete_supported"]
        )
        self.assertFalse(
            connection["wireless_isp_open_network"]
        )


class ClientFacadeTests(unittest.TestCase):
    def test_client_constructs_wireless_service(self) -> None:
        from core.keenetic_client import (
            KeeneticClient,
        )

        client = KeeneticClient(
            username="admin",
            password="secret",
        )

        self.assertIsInstance(
            client.wireless_isp_service,
            WirelessIspService,
        )

    def test_scan_facade_delegates_to_service(self) -> None:
        from core.keenetic_client import (
            KeeneticClient,
        )

        client = KeeneticClient(
            username="admin",
            password="secret",
        )
        captured: list[str] = []

        def scan(
            band: Any,
        ) -> tuple[
            bool,
            str,
            list[dict[str, Any]],
        ]:
            captured.append(str(band))
            return True, "delegated", []

        client.wireless_isp_service.scan_wireless_isp = scan  # type: ignore[method-assign]

        self.assertEqual(
            client.scan_wireless_isp("5"),
            (True, "delegated", []),
        )
        self.assertEqual(
            captured,
            ["5"],
        )

    def test_create_and_delete_facades_keep_arguments(self) -> None:
        from core.keenetic_client import (
            KeeneticClient,
        )

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
            *,
            interface_id: str,
        ) -> tuple[bool, str, dict[str, Any]]:
            calls.append(("delete", interface_id))
            return True, "deleted", {}

        client.wireless_isp_service.create_wireless_isp = create  # type: ignore[method-assign]
        client.wireless_isp_service.delete_wireless_isp = delete  # type: ignore[method-assign]

        self.assertEqual(
            client.create_wireless_isp(
                {
                    "band": "5",
                    "ssid": "Provider",
                }
            ),
            (True, "created", {}),
        )
        self.assertEqual(
            client.delete_wireless_isp(
                interface_id=(
                    "WifiMaster1/WifiStation0"
                )
            ),
            (True, "deleted", {}),
        )
        self.assertEqual(
            calls[0][0],
            "create",
        )
        self.assertEqual(
            calls[1],
            (
                "delete",
                "WifiMaster1/WifiStation0",
            ),
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

    def test_large_create_and_delete_are_physically_moved(
        self,
    ) -> None:
        service_path = (
            self.project_root
            / "core"
            / "services"
            / "wireless_isp_service.py"
        )
        client_path = (
            self.project_root
            / "core"
            / "keenetic_client.py"
        )

        self.assertGreater(
            self.method_span(
                service_path,
                "WirelessIspService",
                "create_wireless_isp",
            ),
            200,
        )
        self.assertGreater(
            self.method_span(
                service_path,
                "WirelessIspService",
                "delete_wireless_isp",
            ),
            200,
        )
        self.assertLess(
            self.method_span(
                client_path,
                "KeeneticClient",
                "create_wireless_isp",
            ),
            12,
        )
        self.assertLess(
            self.method_span(
                client_path,
                "KeeneticClient",
                "delete_wireless_isp",
            ),
            12,
        )

    def test_enrichment_is_physically_in_service(
        self,
    ) -> None:
        service_path = (
            self.project_root
            / "core"
            / "services"
            / "wireless_isp_service.py"
        )
        client_path = (
            self.project_root
            / "core"
            / "keenetic_client.py"
        )

        self.assertGreater(
            self.method_span(
                service_path,
                "WirelessIspService",
                "enrich_wireless_isp_details",
            ),
            130,
        )
        self.assertLess(
            self.method_span(
                client_path,
                "KeeneticClient",
                "enrich_wireless_isp_details",
            ),
            15,
        )

    def test_connection_worker_calls_service_directly(
        self,
    ) -> None:
        source = (
            self.project_root
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")

        required = (
            (
                "client.wireless_isp_service"
                ".scan_wireless_isp"
            ),
            (
                "client.wireless_isp_service"
                ".create_wireless_isp"
            ),
            (
                "client.wireless_isp_service"
                ".delete_wireless_isp"
            ),
        )

        for fragment in required:
            self.assertIn(
                fragment,
                source,
            )

        self.assertNotIn(
            "client.scan_wireless_isp(band)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
