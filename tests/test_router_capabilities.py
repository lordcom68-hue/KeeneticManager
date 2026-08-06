from __future__ import annotations

import unittest

from core.rci.capabilities import RouterCapabilities


class RouterCapabilitiesTests(unittest.TestCase):
    @staticmethod
    def _version(release: str, components: str) -> dict[str, object]:
        return {
            "release": release,
            "ndw": {"components": components},
        }

    def test_keeneticos_5_01_supports_logical_mws(self) -> None:
        result = RouterCapabilities.from_version_data(
            self._version("5.01.C.1.0-0", "corewireless,mws")
        )

        self.assertTrue(result.wifi)
        self.assertTrue(result.logical_mws)
        self.assertFalse(result.legacy_wifi)
        self.assertEqual(result.wifi_management_mode, "mws")

    def test_keeneticos_5_00_is_monitoring_only(self) -> None:
        result = RouterCapabilities.from_version_data(
            self._version("5.00.C.12.0-0", "corewireless,mws")
        )

        self.assertTrue(result.mws_component)
        self.assertFalse(result.logical_mws)
        self.assertFalse(result.legacy_wifi)
        self.assertEqual(result.wifi_management_mode, "monitoring")

    def test_keeneticos_4_uses_legacy_wifi_management(self) -> None:
        result = RouterCapabilities.from_version_data(
            self._version("4.03.C.8.0-0", "corewireless,mws")
        )

        self.assertFalse(result.logical_mws)
        self.assertTrue(result.legacy_wifi)
        self.assertEqual(result.wifi_management_mode, "legacy")

    def test_router_without_wifi_component_is_monitoring_only(self) -> None:
        result = RouterCapabilities.from_version_data(
            self._version("5.01.C.1.0-0", "base,wireguard")
        )

        self.assertFalse(result.wifi)
        self.assertFalse(result.logical_mws)
        self.assertEqual(result.wifi_management_mode, "monitoring")


if __name__ == "__main__":
    unittest.main()
