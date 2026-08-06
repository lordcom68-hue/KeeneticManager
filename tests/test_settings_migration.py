from __future__ import annotations

import unittest
from unittest.mock import patch

from core.settings_migration import migrate_legacy_settings


class FakeSettings:
    stores: dict[tuple[str, str], dict[str, object]] = {}

    def __init__(self, organization: str, application: str) -> None:
        self.key = (organization, application)
        self.store = self.stores.setdefault(self.key, {})

    def value(self, key: str, default=None, type=None):
        value = self.store.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key: str, value: object) -> None:
        self.store[key] = value

    def allKeys(self) -> list[str]:
        return list(self.store)

    def contains(self, key: str) -> bool:
        return key in self.store

    def sync(self) -> None:
        return None


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSettings.stores.clear()

    def test_missing_values_are_copied_without_overwriting_new_values(self) -> None:
        legacy = FakeSettings(
            "KeeneticManagerBeta",
            "KeeneticManager-1.0.1-beta",
        )
        legacy.setValue("monitor/interval_ms", 5000)
        legacy.setValue("monitor/reachability_enabled", False)
        target = FakeSettings(
            "KeeneticManagerBeta",
            "KeeneticManager-Beta",
        )
        target.setValue("monitor/interval_ms", 2000)

        with (
            patch(
                "core.settings_migration.SETTINGS_ORGANIZATION",
                "KeeneticManagerBeta",
            ),
            patch(
                "core.settings_migration.SETTINGS_APPLICATION",
                "KeeneticManager-Beta",
            ),
            patch(
                "core.settings_migration.LEGACY_SETTINGS_APPLICATIONS",
                ("KeeneticManager-1.0.1-beta",),
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            self.assertTrue(migrate_legacy_settings(FakeSettings))

        self.assertEqual(target.value("monitor/interval_ms"), 2000)
        self.assertFalse(target.value("monitor/reachability_enabled"))

    def test_portable_settings_are_not_migrated_from_registry(self) -> None:
        with patch.dict(
            "os.environ",
            {"KEENETIC_MANAGER_SETTINGS_FILE": "portable.ini"},
            clear=True,
        ):
            self.assertFalse(migrate_legacy_settings(FakeSettings))
        self.assertEqual(FakeSettings.stores, {})


if __name__ == "__main__":
    unittest.main()
