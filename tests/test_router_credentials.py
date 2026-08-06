from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from core.credential_store import (
    CredentialMigrationError,
    CredentialNotFoundError,
    CredentialStoreError,
    RouterCredentialStore,
)
from core.database import Database



@contextmanager
def closing_sqlite_connection(
    path: Path,
):
    connection = sqlite3.connect(path)

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class MemoryKeyringBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.fail_set = False
        self.fail_after: int | None = None
        self.set_calls = 0

    def get_password(
        self,
        service: str,
        account: str,
    ) -> str | None:
        return self.values.get((service, account))

    def set_password(
        self,
        service: str,
        account: str,
        password: str,
    ) -> None:
        self.set_calls += 1
        if self.fail_set:
            raise RuntimeError("backend failure")
        if (
            self.fail_after is not None
            and self.set_calls > self.fail_after
        ):
            raise RuntimeError("backend failure after partial migration")
        self.values[(service, account)] = password

    def delete_password(
        self,
        service: str,
        account: str,
    ) -> None:
        key = (service, account)
        if key not in self.values:
            raise RuntimeError("missing")
        del self.values[key]


class MemoryCredentialStore(RouterCredentialStore):
    def __init__(self) -> None:
        self.memory_backend = MemoryKeyringBackend()
        super().__init__(backend=self.memory_backend)

    def delete_password(
        self,
        reference: str,
        *,
        missing_ok: bool = True,
    ) -> None:
        location = self.location(reference)
        key = (location.service, location.account)

        if key not in self.memory_backend.values:
            if missing_ok:
                return
            raise CredentialStoreError("missing")

        del self.memory_backend.values[key]


class RouterCredentialDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "routers.db"
        self.store = MemoryCredentialStore()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_database(self) -> Database:
        return Database(
            database_path=self.database_path,
            credential_store=self.store,
        )

    def add_router(
        self,
        database: Database,
        *,
        password: str | None = "router-secret",
    ) -> int:
        return database.add_router(
            name="Тестовый роутер",
            local_ip="192.0.2.1",
            keendns="virtual.example.invalid",
            connection_mode="auto",
            username="admin",
            password=password,
            group_name="Тесты",
        )

    def schema_columns(self) -> set[str]:
        with closing_sqlite_connection(self.database_path) as connection:
            return {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(routers)"
                ).fetchall()
            }

    def raw_router_row(self) -> sqlite3.Row:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM routers LIMIT 1"
            ).fetchone()
            assert row is not None
            return row
        finally:
            connection.close()

    def test_new_schema_has_reference_but_no_password_column(self) -> None:
        database = self.create_database()
        snapshot = database.get_schema_snapshot()

        self.assertIn("credential_ref", self.schema_columns())
        self.assertNotIn("password", self.schema_columns())
        self.assertEqual(snapshot["user_version"], 280)

    def test_add_router_stores_secret_only_in_credential_store(self) -> None:
        database = self.create_database()
        router_id = self.add_router(database)
        row = self.raw_router_row()

        self.assertTrue(row["credential_ref"])
        self.assertNotIn("router-secret", repr(dict(row)))

        public_router = database.get_router(router_id)
        assert public_router is not None
        self.assertNotIn("password", public_router)
        self.assertNotIn("credential_ref", public_router)
        self.assertTrue(public_router["password_configured"])

        connected_router = database.get_router(
            router_id,
            include_password=True,
        )
        assert connected_router is not None
        self.assertEqual(
            connected_router["password"],
            "router-secret",
        )

    def test_edit_without_password_preserves_existing_secret(self) -> None:
        database = self.create_database()
        router_id = self.add_router(database)

        database.update_router(
            router_id=router_id,
            name="Изменённый роутер",
            local_ip="192.0.2.2",
            keendns="changed.example.invalid",
            connection_mode="local",
            username="admin2",
            password=None,
            group_name="Новая группа",
        )

        router = database.get_router(
            router_id,
            include_password=True,
        )
        assert router is not None
        self.assertEqual(router["password"], "router-secret")
        self.assertEqual(router["name"], "Изменённый роутер")

    def test_edit_can_replace_and_clear_secret(self) -> None:
        database = self.create_database()
        router_id = self.add_router(database)

        database.update_router(
            router_id=router_id,
            name="Тестовый роутер",
            local_ip="192.0.2.1",
            keendns="virtual.example.invalid",
            connection_mode="auto",
            username="admin",
            password="new-secret",
            group_name="Тесты",
        )
        changed = database.get_router(
            router_id,
            include_password=True,
        )
        assert changed is not None
        self.assertEqual(changed["password"], "new-secret")

        database.update_router(
            router_id=router_id,
            name="Тестовый роутер",
            local_ip="192.0.2.1",
            keendns="virtual.example.invalid",
            connection_mode="auto",
            username="admin",
            password=None,
            clear_password=True,
            group_name="Тесты",
        )
        cleared = database.get_router(router_id)
        assert cleared is not None
        self.assertFalse(cleared["password_configured"])
        self.assertNotIn("password", cleared)

    def test_delete_router_removes_secret(self) -> None:
        database = self.create_database()
        router_id = self.add_router(database)
        reference = str(self.raw_router_row()["credential_ref"])

        database.delete_router(router_id)

        self.assertIsNone(database.get_router(router_id))
        self.assertIsNone(
            self.store.get_password(
                reference,
                required=False,
            )
        )

    def test_missing_referenced_secret_is_reported_only_on_connection(self) -> None:
        database = self.create_database()
        router_id = self.add_router(database)
        reference = str(self.raw_router_row()["credential_ref"])
        self.store.delete_password(reference)

        public_router = database.get_router(router_id)
        assert public_router is not None
        self.assertTrue(public_router["password_configured"])

        with self.assertRaises(CredentialNotFoundError):
            database.get_router(
                router_id,
                include_password=True,
            )

    def create_legacy_database(self, password: str) -> None:
        with closing_sqlite_connection(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE routers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    local_ip TEXT,
                    keendns TEXT,
                    connection_mode TEXT NOT NULL DEFAULT 'auto',
                    username TEXT NOT NULL DEFAULT 'admin',
                    password TEXT NOT NULL DEFAULT '',
                    group_name TEXT DEFAULT '',
                    status INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO routers (
                    name,
                    local_ip,
                    keendns,
                    connection_mode,
                    username,
                    password,
                    group_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Старый роутер",
                    "192.0.2.10",
                    "legacy.example.invalid",
                    "auto",
                    "admin",
                    password,
                    "Миграция",
                ),
            )

    def test_legacy_plaintext_password_is_migrated_and_column_removed(self) -> None:
        self.create_legacy_database("legacy-secret")
        database = self.create_database()

        self.assertNotIn("password", self.schema_columns())
        self.assertIn("credential_ref", self.schema_columns())

        router = database.get_router(
            1,
            include_password=True,
        )
        assert router is not None
        self.assertEqual(router["password"], "legacy-secret")

        database_bytes = self.database_path.read_bytes()
        self.assertNotIn(b"legacy-secret", database_bytes)

    def test_legacy_backup_passwords_are_sanitized(self) -> None:
        backup_path = self.root / "routers_backup_20260804_000000.db"

        with closing_sqlite_connection(backup_path) as connection:
            connection.execute(
                """
                CREATE TABLE routers (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    password TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO routers (id, name, password)
                VALUES (1, 'Backup router', 'backup-secret')
                """
            )

        self.create_database()

        with closing_sqlite_connection(backup_path) as connection:
            value = connection.execute(
                "SELECT password FROM routers WHERE id = 1"
            ).fetchone()[0]

        self.assertEqual(value, "")
        self.assertNotIn(b"backup-secret", backup_path.read_bytes())

    def test_partial_legacy_migration_rolls_back_created_credentials(self) -> None:
        self.create_legacy_database("first-secret")
        with closing_sqlite_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO routers (
                    name,
                    local_ip,
                    keendns,
                    connection_mode,
                    username,
                    password,
                    group_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Второй старый роутер",
                    "192.0.2.11",
                    "legacy-two.example.invalid",
                    "auto",
                    "admin",
                    "second-secret",
                    "Миграция",
                ),
            )

        self.store.memory_backend.fail_after = 1

        with self.assertRaises(CredentialMigrationError):
            self.create_database()

        self.assertEqual(self.store.memory_backend.values, {})

        with closing_sqlite_connection(self.database_path) as connection:
            passwords = [
                str(row[0])
                for row in connection.execute(
                    "SELECT password FROM routers ORDER BY id"
                ).fetchall()
            ]

        self.assertEqual(
            passwords,
            ["first-secret", "second-secret"],
        )

    def test_failed_legacy_migration_keeps_plaintext_database_intact(self) -> None:
        self.create_legacy_database("legacy-secret")
        self.store.memory_backend.fail_set = True

        with self.assertRaises(CredentialMigrationError):
            self.create_database()

        with closing_sqlite_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT password FROM routers WHERE id = 1"
            ).fetchone()

        self.assertEqual(row[0], "legacy-secret")


if __name__ == "__main__":
    unittest.main()
