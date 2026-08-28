from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings

from core.credential_store import RouterCredentialStore
from core.database import Database
from tools.upgrade_fixture import (
    FixturePaths,
    UpgradeRuntime,
    assert_fixture,
    cleanup_fixture,
    seed_fixture,
)


class MemoryCredentialBackend:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.passwords.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.passwords[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        del self.passwords[(service, account)]


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = FixturePaths(
            data_dir=root / "profile",
            settings_file=root / "profile" / "settings.ini",
            state_file=root / "fixture.json",
        )
        self.backend = MemoryCredentialBackend()
        self.store = RouterCredentialStore(
            backend=self.backend,
            service_name="KeeneticManager.UpgradeFixture.Test",
            fallback_services=(),
        )
        self.runtime = UpgradeRuntime(
            database_type=Database,
            credential_store_type=RouterCredentialStore,
            qsettings_type=QSettings,
        )

    def test_fixture_preserves_routers_settings_and_credential(self) -> None:
        state = seed_fixture(
            self.runtime,
            self.paths,
            fixture_id="fixture123",
            credential_store=self.store,
        )

        result = assert_fixture(
            self.runtime,
            self.paths,
            credential_store=self.store,
        )

        self.assertEqual(result["router_count"], 2)
        self.assertTrue(result["settings_preserved"])
        self.assertTrue(result["credential_preserved"])
        self.assertEqual(len(state["credential_references"]), 1)
        self.assertEqual(len(self.backend.passwords), 1)

        cleanup_fixture(
            self.runtime,
            self.paths,
            credential_store=self.store,
        )
        self.assertEqual(self.backend.passwords, {})

    def test_fixture_detects_changed_setting(self) -> None:
        seed_fixture(
            self.runtime,
            self.paths,
            fixture_id="fixture456",
            credential_store=self.store,
        )
        settings = QSettings(
            str(self.paths.settings_file),
            QSettings.Format.IniFormat,
        )
        settings.setValue("monitor/interval_ms", 10_000)
        settings.sync()

        with self.assertRaisesRegex(AssertionError, "Settings changed"):
            assert_fixture(
                self.runtime,
                self.paths,
                credential_store=self.store,
            )

        cleanup_fixture(
            self.runtime,
            self.paths,
            credential_store=self.store,
        )
