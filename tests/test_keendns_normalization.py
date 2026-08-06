from __future__ import annotations

import unittest

from core.database import Database


class KeenDnsNormalizationTests(unittest.TestCase):
    def test_scheme_is_removed_but_port_is_preserved(self) -> None:
        self.assertEqual(
            Database.normalize_keendns(
                "https://Remote.Keenetic.Pro:8443/"
            ),
            "remote.keenetic.pro:8443",
        )

    def test_address_without_scheme_is_unchanged(self) -> None:
        self.assertEqual(
            Database.normalize_keendns(
                "home.keenetic.pro"
            ),
            "home.keenetic.pro",
        )

    def test_router_address_widget_contract_uses_https_after_normalization(
        self,
    ) -> None:
        normalized = Database.normalize_keendns(
            "https://remote.keenetic.pro:8443"
        )
        self.assertEqual(
            f"https://{normalized}",
            "https://remote.keenetic.pro:8443",
        )


if __name__ == "__main__":
    unittest.main()
