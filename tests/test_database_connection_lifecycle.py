from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.database import Database
from tests.test_router_credentials import MemoryCredentialStore


class TrackingConnection(sqlite3.Connection):
    opened = 0
    closed = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self).opened += 1
        self._closed_once = False
        super().__init__(*args, **kwargs)

    def close(self) -> None:
        if not self._closed_once:
            self._closed_once = True
            type(self).closed += 1
        super().close()


class DatabaseConnectionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        TrackingConnection.opened = 0
        TrackingConnection.closed = 0
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name)
            / "routers.db"
        )
        self.store = MemoryCredentialStore()
        self.original_connect = sqlite3.connect

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def tracking_connect(self, *args, **kwargs):
        kwargs["factory"] = TrackingConnection
        return self.original_connect(*args, **kwargs)

    def test_database_closes_every_sqlite_connection(self) -> None:
        with patch(
            "core.database.sqlite3.connect",
            side_effect=self.tracking_connect,
        ):
            database = Database(
                database_path=self.database_path,
                credential_store=self.store,
            )
            router_id = database.add_router(
                name="Lifecycle",
                local_ip="192.0.2.50",
                keendns="lifecycle.example.invalid",
                connection_mode="auto",
                username="admin",
                password="secret",
                group_name="Tests",
            )
            database.get_routers()
            database.get_router(router_id)
            database.get_router(
                router_id,
                include_password=True,
            )
            database.update_router(
                router_id=router_id,
                name="Lifecycle edited",
                local_ip="192.0.2.51",
                keendns="lifecycle.example.invalid",
                connection_mode="local",
                username="admin",
                password=None,
                group_name="Tests",
            )
            database.update_router_status(
                router_id,
                True,
                touch_last_seen=True,
            )
            database.get_schema_snapshot()
            database.delete_router(router_id)

        self.assertGreater(
            TrackingConnection.opened,
            0,
        )
        self.assertEqual(
            TrackingConnection.opened,
            TrackingConnection.closed,
            (
                "Не все SQLite-соединения закрыты: "
                f"opened={TrackingConnection.opened}, "
                f"closed={TrackingConnection.closed}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
