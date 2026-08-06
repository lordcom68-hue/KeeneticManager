from __future__ import annotations

import sqlite3
import sys
import tempfile
from contextlib import contextmanager
import traceback
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.credential_store import RouterCredentialStore
from core.database import Database


class TrackingCredentialStore(RouterCredentialStore):
    def __init__(self) -> None:
        super().__init__(
            service_name=(
                "KeeneticManager.Router.Admin.SmokeTest."
                + uuid4().hex
            )
        )
        self.references: set[str] = set()

    def new_reference(self) -> str:
        reference = super().new_reference()
        self.references.add(reference)
        return reference

    def cleanup(self) -> None:
        for reference in list(self.references):
            try:
                self.delete_password(
                    reference,
                    missing_ok=True,
                )
            except Exception:
                pass


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


def create_legacy_database(
    path: Path,
    password: str,
) -> None:
    with closing_sqlite_connection(path) as connection:
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
                "Credential smoke router",
                "192.0.2.1",
                "credential-smoke.example.invalid",
                "auto",
                "admin",
                password,
                "Smoke test",
            ),
        )


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    store = TrackingCredentialStore()
    temporary_secret = "smoke-" + uuid4().hex

    try:
        store.ensure_available()
        print("[OK] Windows Credential Manager доступен")

        direct_reference = store.new_reference()
        store.set_password(
            direct_reference,
            temporary_secret,
        )
        ensure(
            store.get_password(
                direct_reference,
                required=True,
            )
            == temporary_secret,
            "Временный секрет не прочитан обратно.",
        )
        store.delete_password(direct_reference)
        print("[OK] Временная запись set/get/delete")

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "routers.db"
            legacy_secret = "legacy-" + uuid4().hex
            create_legacy_database(
                database_path,
                legacy_secret,
            )

            database = Database(
                database_path=database_path,
                credential_store=store,
            )

            columns = {
                str(item["name"])
                for item in database.get_schema_snapshot()[
                    "routers_columns"
                ]
            }
            ensure(
                "password" not in columns,
                "Колонка password осталась после миграции.",
            )
            ensure(
                "credential_ref" in columns,
                "Колонка credential_ref не создана.",
            )
            ensure(
                legacy_secret.encode("utf-8")
                not in database_path.read_bytes(),
                "Старый пароль остался в файле SQLite.",
            )

            public_router = database.get_router(1)
            ensure(
                public_router is not None,
                "Мигрированный роутер не найден.",
            )
            ensure(
                "password" not in public_router,
                "Обычный get_router раскрыл пароль.",
            )
            ensure(
                "credential_ref" not in public_router,
                "Обычный get_router раскрыл credential_ref.",
            )
            ensure(
                bool(public_router.get("password_configured")),
                "Флаг password_configured не установлен.",
            )

            connected_router = database.get_router(
                1,
                include_password=True,
            )
            ensure(
                connected_router is not None
                and connected_router.get("password")
                == legacy_secret,
                "Секрет не получен перед подключением.",
            )

            database.update_router(
                router_id=1,
                name="Credential smoke router edited",
                local_ip="192.0.2.2",
                keendns="credential-smoke.example.invalid",
                connection_mode="local",
                username="admin",
                password=None,
                group_name="Smoke test",
            )
            preserved = database.get_router(
                1,
                include_password=True,
            )
            ensure(
                preserved is not None
                and preserved.get("password") == legacy_secret,
                "Пустое поле редактирования не сохранило пароль.",
            )

            replacement = "replacement-" + uuid4().hex
            database.update_router(
                router_id=1,
                name="Credential smoke router edited",
                local_ip="192.0.2.2",
                keendns="credential-smoke.example.invalid",
                connection_mode="local",
                username="admin",
                password=replacement,
                group_name="Smoke test",
            )
            replaced = database.get_router(
                1,
                include_password=True,
            )
            ensure(
                replaced is not None
                and replaced.get("password") == replacement,
                "Новый пароль не заменил старый.",
            )

            database.delete_router(1)
            ensure(
                database.get_router(1) is None,
                "Тестовый роутер не удалён.",
            )

        print("[OK] Миграция SQLite → Credential Manager")
        print("[OK] Сохранение, замена и удаление секрета")
        print("Проверка Windows Credential Manager завершена успешно.")

    finally:
        store.cleanup()


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(
            "Проверка Windows Credential Manager завершилась ошибкой:",
            file=sys.stderr,
        )
        print(str(error), file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
