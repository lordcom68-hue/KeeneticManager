from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.rci.capabilities import RouterCapabilities
from core.services.router_compatibility_service import (
    RouterCompatibilityService,
)


class RouterCompatibilityServiceTests(unittest.TestCase):
    @staticmethod
    def fixtures() -> dict[str, dict[str, object]]:
        path = (
            Path(__file__).parent
            / "fixtures"
            / "compatibility_profiles.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def now() -> datetime:
        return datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)

    def test_three_router_profiles_have_expected_support_levels(self) -> None:
        fixtures = self.fixtures()
        expected = {
            "sprinter_kos_5_1": ("full", "mws"),
            "n6_ax1800_kos_5_0": ("limited", "monitoring"),
            "legacy_kos_4": ("limited", "legacy"),
        }

        for router_id, (fixture_name, expected_values) in enumerate(
            expected.items(),
            1,
        ):
            version_data = fixtures[fixture_name]
            capabilities = RouterCapabilities.from_version_data(version_data)
            profile = RouterCompatibilityService.build_profile(
                router_id=router_id,
                capabilities=capabilities,
                version_data=version_data,
                detected_at=self.now().isoformat(),
            )

            self.assertEqual(profile.level, expected_values[0])
            self.assertEqual(
                profile.wifi_management_mode,
                expected_values[1],
            )
            self.assertEqual(profile.feature_state("system_log"), "unknown")

    def test_cached_runtime_result_is_reused_for_same_firmware(self) -> None:
        version_data = self.fixtures()["n6_ax1800_kos_5_0"]
        capabilities = RouterCapabilities.from_version_data(version_data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatibility.json"
            first = RouterCompatibilityService(
                2,
                path=path,
                now=self.now,
            )
            first.assess(capabilities, version_data)
            first.update_runtime_feature("system_log", False)

            second = RouterCompatibilityService(
                2,
                path=path,
                now=self.now,
            )
            profile = second.assess(capabilities, version_data)

        self.assertTrue(profile.cache_hit)
        self.assertEqual(profile.feature_state("system_log"), "unavailable")

    def test_firmware_change_invalidates_runtime_capability_cache(self) -> None:
        fixtures = self.fixtures()
        old_version = fixtures["n6_ax1800_kos_5_0"]
        new_version = fixtures["sprinter_kos_5_1"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatibility.json"
            service = RouterCompatibilityService(2, path=path, now=self.now)
            service.assess(
                RouterCapabilities.from_version_data(old_version),
                old_version,
            )
            service.update_runtime_feature("system_log", False)

            profile = service.assess(
                RouterCapabilities.from_version_data(new_version),
                new_version,
            )

        self.assertFalse(profile.cache_hit)
        self.assertEqual(profile.feature_state("system_log"), "unknown")

    def test_corrupted_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatibility.json"
            path.write_text("not-json", encoding="utf-8")

            service = RouterCompatibilityService(7, path=path)

        self.assertIsNone(service.profile)


if __name__ == "__main__":
    unittest.main()
