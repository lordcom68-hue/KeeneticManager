from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


class Kn3711ReadOnlyFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "kn3711_readonly_profile.json"
        )
        cls.text = path.read_text(encoding="utf-8")
        cls.fixture = json.loads(cls.text)

    def test_fixture_records_real_read_only_contract(self) -> None:
        fixture = self.fixture
        privacy = fixture["privacy"]

        self.assertEqual(
            fixture["capture_source"],
            "read_only_real_router",
        )
        self.assertEqual(
            fixture["router"]["model"],
            "Sprinter (KN-3711)",
        )
        self.assertEqual(fixture["router"]["connection_scheme"], "http")
        self.assertTrue(privacy["anonymized"])
        self.assertFalse(privacy["configuration_changed"])
        self.assertFalse(privacy["contains_credentials"])
        self.assertFalse(privacy["contains_addresses"])
        self.assertFalse(privacy["contains_ssids"])
        self.assertFalse(privacy["contains_client_identifiers"])

    def test_fixture_preserves_supported_feature_matrix(self) -> None:
        features = self.fixture["features"]
        required = {
            "monitoring",
            "internet_management",
            "wifi_monitoring",
            "wifi_management",
            "client_access",
            "ethernet_ports",
            "rescue_mode",
            "system_log",
            "diagnostics",
        }

        self.assertTrue(required.issubset(features))
        self.assertTrue(all(features[key] == "supported" for key in required))
        self.assertGreaterEqual(self.fixture["observed_counts"]["ports"], 1)

    def test_fixture_contains_no_network_or_client_identity(self) -> None:
        lowered = self.text.casefold()

        self.assertNotIn("password", lowered)
        self.assertNotIn("ssid", lowered.replace("contains_ssids", ""))
        self.assertNotRegex(self.text, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        self.assertIsNone(
            re.search(
                r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b",
                self.text,
            )
        )


if __name__ == "__main__":
    unittest.main()
