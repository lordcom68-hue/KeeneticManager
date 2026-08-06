from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.keenetic_client import KeeneticClient
from core.services.runtime_paths import (
    APP_DATA_DIR,
    DIAGNOSTICS_EXPORT_DIR,
    DIAGNOSTICS_HISTORY_DIR,
    LOGS_DIR,
    PROJECT_ROOT,
    migrate_legacy_database,
)


class RuntimeStorageTests(unittest.TestCase):
    def test_runtime_files_are_not_stored_in_source_tree(self) -> None:
        self.assertNotEqual(APP_DATA_DIR, PROJECT_ROOT)
        self.assertTrue(APP_DATA_DIR in DIAGNOSTICS_HISTORY_DIR.parents)
        self.assertTrue(APP_DATA_DIR in DIAGNOSTICS_EXPORT_DIR.parents)
        self.assertTrue(APP_DATA_DIR in LOGS_DIR.parents)

    def test_legacy_database_is_copied_once_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy" / "routers.db"
            target = root / "data" / "routers.db"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"first database")

            self.assertTrue(
                migrate_legacy_database(target=target, legacy=legacy)
            )
            self.assertEqual(target.read_bytes(), b"first database")

            legacy.write_bytes(b"changed legacy database")
            self.assertFalse(
                migrate_legacy_database(target=target, legacy=legacy)
            )
            self.assertEqual(target.read_bytes(), b"first database")

    def test_newer_legacy_database_replaces_unmarked_initial_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy" / "routers.db"
            target = root / "data" / "routers.db"
            legacy.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"initial copy")
            legacy.write_bytes(b"latest legacy database")

            os.utime(target, ns=(1_000_000_000, 1_000_000_000))
            os.utime(legacy, ns=(2_000_000_000, 2_000_000_000))

            self.assertTrue(
                migrate_legacy_database(target=target, legacy=legacy)
            )
            self.assertEqual(
                target.read_bytes(),
                b"latest legacy database",
            )
            self.assertEqual(
                target.with_name("routers.pre_legacy_migration.db").read_bytes(),
                b"initial copy",
            )
            self.assertFalse(
                migrate_legacy_database(target=target, legacy=legacy)
            )

    def test_hardware_guard_log_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "show": {"hardware": {}},
                "password": "router-secret",
                "nested": {
                    "access_token": "session-secret",
                    "url": "https://admin:uri-secret@example.invalid/rci/",
                },
            }

            with patch("core.keenetic_client.LOGS_DIR", root):
                KeeneticClient.log_hardware_guard_event(
                    "POST",
                    "https://router.invalid/rci/",
                    payload,
                )

            text = (root / "hardware_guard.log").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("router-secret", text)
            self.assertNotIn("session-secret", text)
            self.assertNotIn("uri-secret", text)
            self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()
