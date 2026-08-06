from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.credential_store import (
    CredentialMigrationError,
    CredentialStoreError,
    RouterCredentialStore,
)
from core.services.runtime_paths import (
    DATABASE_DIR,
    migrate_legacy_database,
)


class Database:
    VALID_CONNECTION_MODES = {
        "auto",
        "local",
        "keendns",
    }
    SCHEMA_VERSION = 280

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        credential_store: RouterCredentialStore | None = None,
    ) -> None:
        if database_path is None:
            self.database_path = DATABASE_DIR / "routers.db"
            migrate_legacy_database(target=self.database_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.database_path = Path(database_path)
            self.database_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self.credential_store = (
            credential_store or RouterCredentialStore()
        )

        self.create_tables()
        self.migrate_database()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """
        Открывает SQLite-соединение и гарантированно закрывает его.

        Контекстный менеджер sqlite3.Connection сам выполняет commit или
        rollback, но не закрывает файловый дескриптор. На Windows это
        оставляет routers.db занятым и мешает миграции, тестам и удалению
        временных баз. Здесь жизненный цикл соединения завершён явно.
        """
        connection = self.connect()

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_tables(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS routers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    local_ip TEXT,
                    keendns TEXT,
                    connection_mode TEXT NOT NULL DEFAULT 'auto',
                    username TEXT NOT NULL DEFAULT 'admin',
                    credential_ref TEXT,
                    group_name TEXT DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    status INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT
                )
                """
            )

    def migrate_database(self) -> None:
        """
        Обновляет старую базу без потери роутеров.

        Начиная с 0.27.0 пароль администратора больше не хранится в
        SQLite. Старое значение переносится в системный keyring, а в
        таблице остаётся только случайная ссылка credential_ref.
        """
        with self.connection() as connection:
            table_sql_row = connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'routers'
                """
            ).fetchone()

            table_sql = (
                str(table_sql_row["sql"])
                if table_sql_row and table_sql_row["sql"]
                else ""
            )
            normalized_sql = " ".join(
                table_sql.lower().split()
            )
            old_unique_keendns = (
                "keendns text not null unique"
                in normalized_sql
                or "keendns text unique"
                in normalized_sql
            )

            user_version_row = connection.execute(
                "PRAGMA user_version"
            ).fetchone()
            initial_user_version = (
                int(user_version_row[0])
                if user_version_row is not None
                else 0
            )

            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(routers)"
                ).fetchall()
            }

            sort_order_added = "sort_order" not in columns

            migrations = {
                "local_ip": (
                    "ALTER TABLE routers ADD COLUMN local_ip TEXT"
                ),
                "keendns": (
                    "ALTER TABLE routers ADD COLUMN keendns TEXT"
                ),
                "connection_mode": (
                    "ALTER TABLE routers ADD COLUMN connection_mode "
                    "TEXT NOT NULL DEFAULT 'auto'"
                ),
                "username": (
                    "ALTER TABLE routers ADD COLUMN username "
                    "TEXT NOT NULL DEFAULT 'admin'"
                ),
                "credential_ref": (
                    "ALTER TABLE routers ADD COLUMN credential_ref TEXT"
                ),
                "group_name": (
                    "ALTER TABLE routers ADD COLUMN group_name "
                    "TEXT DEFAULT ''"
                ),
                "sort_order": (
                    "ALTER TABLE routers ADD COLUMN sort_order "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "status": (
                    "ALTER TABLE routers ADD COLUMN status INTEGER "
                    "NOT NULL DEFAULT 0"
                ),
                "last_seen": (
                    "ALTER TABLE routers ADD COLUMN last_seen TEXT"
                ),
            }

            for column_name, statement in migrations.items():
                if column_name not in columns:
                    connection.execute(statement)
                    columns.add(column_name)

            if "password" in columns:
                self._migrate_plaintext_passwords(connection)

            connection.execute(
                """
                UPDATE routers
                SET keendns = NULL
                WHERE keendns IS NOT NULL
                  AND TRIM(keendns) = ''
                """
            )
            connection.execute(
                """
                UPDATE routers
                SET local_ip = NULL
                WHERE local_ip IS NOT NULL
                  AND TRIM(local_ip) = ''
                """
            )

        schema_rebuilt = bool(
            old_unique_keendns
            or "password" in columns
        )

        if schema_rebuilt:
            self.rebuild_routers_table()

        if sort_order_added or initial_user_version < 280:
            self.normalize_router_order(
                alphabetical_fallback=True,
            )
        else:
            self.normalize_router_order()

        if (
            schema_rebuilt
            or initial_user_version < self.SCHEMA_VERSION
        ):
            self.vacuum_database()

        with self.connection() as connection:
            index_rows = connection.execute(
                "PRAGMA index_list(routers)"
            ).fetchall()

            for index_row in index_rows:
                if (
                    str(index_row["name"])
                    == "idx_routers_keendns_unique"
                    and not bool(index_row["unique"])
                ):
                    connection.execute(
                        "DROP INDEX idx_routers_keendns_unique"
                    )
                    break

            try:
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_routers_keendns_unique
                    ON routers(keendns COLLATE NOCASE)
                    WHERE keendns IS NOT NULL
                      AND TRIM(keendns) <> ''
                    """
                )
            except sqlite3.IntegrityError:
                pass

            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_routers_credential_ref_unique
                ON routers(credential_ref)
                WHERE credential_ref IS NOT NULL
                  AND TRIM(credential_ref) <> ''
                """
            )

            connection.execute(
                f"PRAGMA user_version = {self.SCHEMA_VERSION}"
            )

        self.sanitize_legacy_backup_databases()

    def vacuum_database(self) -> None:
        with self.connection() as connection:
            connection.execute("VACUUM")

    def sanitize_legacy_backup_databases(self) -> None:
        """
        Удаляет открытые пароли из исторических SQLite-копий.

        Эти файлы не используются приложением напрямую, поэтому секреты
        в keyring для них не создаются. Метаданные роутеров сохраняются,
        а поле password очищается и файл переписывается через VACUUM.
        """
        for backup_path in sorted(
            self.database_path.parent.glob(
                "routers_backup_*.db"
            )
        ):
            if backup_path.resolve() == self.database_path.resolve():
                continue

            connection: sqlite3.Connection | None = None

            try:
                connection = sqlite3.connect(backup_path)
                table = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'routers'
                    """
                ).fetchone()

                if table is None:
                    continue

                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(routers)"
                    ).fetchall()
                }

                if "password" not in columns:
                    continue

                connection.execute(
                    """
                    UPDATE routers
                    SET password = ''
                    WHERE password IS NOT NULL
                      AND password <> ''
                    """
                )
                connection.commit()
                connection.execute("VACUUM")

            except sqlite3.Error:
                # Повреждённая или занятая старая копия не должна
                # блокировать запуск приложения и активную миграцию.
                continue
            finally:
                if connection is not None:
                    connection.close()

    def _migrate_plaintext_passwords(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """
            SELECT id, password, credential_ref
            FROM routers
            ORDER BY id
            """
        ).fetchall()

        changed_credentials: list[
            tuple[str, str | None]
        ] = []

        try:
            for row in rows:
                password = str(row["password"] or "")

                if not password:
                    continue

                reference = str(
                    row["credential_ref"] or ""
                ).strip()

                if not reference:
                    reference = (
                        self.credential_store.new_reference()
                    )

                old_secret = self.credential_store.get_password(
                    reference,
                    required=False,
                )
                self.credential_store.set_password(
                    reference,
                    password,
                )
                changed_credentials.append(
                    (reference, old_secret)
                )

                connection.execute(
                    """
                    UPDATE routers
                    SET credential_ref = ?, password = ''
                    WHERE id = ?
                    """,
                    (
                        reference,
                        int(row["id"]),
                    ),
                )

        except Exception as error:
            connection.rollback()

            for reference, old_secret in reversed(
                changed_credentials
            ):
                try:
                    if old_secret is None:
                        self.credential_store.delete_password(
                            reference,
                            missing_ok=True,
                        )
                    else:
                        self.credential_store.set_password(
                            reference,
                            old_secret,
                        )
                except CredentialStoreError:
                    pass

            if isinstance(error, CredentialStoreError):
                raise CredentialMigrationError(
                    "Не удалось перенести пароль роутера из SQLite "
                    "в Windows Credential Manager. Исходная база "
                    "оставлена без изменений."
                ) from error

            raise

    def rebuild_routers_table(self) -> None:
        with self.connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(routers)"
                ).fetchall()
            }

            connection.execute("BEGIN")

            try:
                connection.execute(
                    """
                    CREATE TABLE routers_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        local_ip TEXT,
                        keendns TEXT,
                        connection_mode TEXT
                            NOT NULL DEFAULT 'auto',
                        username TEXT
                            NOT NULL DEFAULT 'admin',
                        credential_ref TEXT,
                        group_name TEXT DEFAULT '',
                        sort_order INTEGER
                            NOT NULL DEFAULT 0,
                        status INTEGER
                            NOT NULL DEFAULT 0,
                        last_seen TEXT
                    )
                    """
                )

                def source_or_default(
                    column: str,
                    default_sql: str,
                ) -> str:
                    if column in columns:
                        return column
                    return default_sql

                select_fields = [
                    source_or_default("id", "NULL"),
                    source_or_default(
                        "name",
                        "'Без названия'",
                    ),
                    source_or_default(
                        "local_ip",
                        "NULL",
                    ),
                    (
                        "NULLIF(TRIM(keendns), '')"
                        if "keendns" in columns
                        else "NULL"
                    ),
                    source_or_default(
                        "connection_mode",
                        "'auto'",
                    ),
                    source_or_default(
                        "username",
                        "'admin'",
                    ),
                    source_or_default(
                        "credential_ref",
                        "NULL",
                    ),
                    source_or_default(
                        "group_name",
                        "''",
                    ),
                    source_or_default(
                        "sort_order",
                        "0",
                    ),
                    source_or_default(
                        "status",
                        "0",
                    ),
                    source_or_default(
                        "last_seen",
                        "NULL",
                    ),
                ]

                connection.execute(
                    f"""
                    INSERT INTO routers_new (
                        id,
                        name,
                        local_ip,
                        keendns,
                        connection_mode,
                        username,
                        credential_ref,
                        group_name,
                        sort_order,
                        status,
                        last_seen
                    )
                    SELECT
                        {", ".join(select_fields)}
                    FROM routers
                    """
                )
                connection.execute("DROP TABLE routers")
                connection.execute(
                    """
                    ALTER TABLE routers_new
                    RENAME TO routers
                    """
                )
                connection.commit()

            except Exception:
                connection.rollback()
                raise

    def normalize_router_order(
        self,
        *,
        alphabetical_fallback: bool = False,
    ) -> None:
        """Делает порядок роутеров непрерывным: 0, 1, 2, ..."""
        with self.connection() as connection:
            order_sql = (
                "name COLLATE NOCASE, id"
                if alphabetical_fallback
                else "sort_order, name COLLATE NOCASE, id"
            )
            rows = connection.execute(
                f"""
                SELECT id, sort_order
                FROM routers
                ORDER BY {order_sql}
                """
            ).fetchall()

            for sort_order, row in enumerate(rows):
                if int(row["sort_order"] or 0) == sort_order:
                    continue

                connection.execute(
                    """
                    UPDATE routers
                    SET sort_order = ?
                    WHERE id = ?
                    """,
                    (
                        sort_order,
                        int(row["id"]),
                    ),
                )

    def set_router_order(
        self,
        router_ids: list[int],
    ) -> None:
        """Атомарно сохраняет полный порядок списка роутеров."""
        normalized = [int(value) for value in router_ids]

        if len(normalized) != len(set(normalized)):
            raise ValueError(
                "Порядок роутеров содержит повторяющиеся ID."
            )

        with self.connection() as connection:
            existing = {
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM routers"
                ).fetchall()
            }

            if set(normalized) != existing:
                raise ValueError(
                    "Нельзя сохранить неполный порядок роутеров."
                )

            for sort_order, router_id in enumerate(normalized):
                connection.execute(
                    """
                    UPDATE routers
                    SET sort_order = ?
                    WHERE id = ?
                    """,
                    (
                        sort_order,
                        router_id,
                    ),
                )

    def normalize_connection_mode(
        self,
        connection_mode: str,
    ) -> str:
        value = (
            connection_mode
            or "auto"
        ).strip().lower()

        if value not in self.VALID_CONNECTION_MODES:
            return "auto"

        return value

    @staticmethod
    def normalize_keendns(
        keendns: str,
    ) -> str:
        value = (keendns or "").strip().lower()

        for prefix in (
            "https://",
            "http://",
        ):
            if value.startswith(prefix):
                value = value[len(prefix):]

        return value.rstrip("/")

    @staticmethod
    def ensure_keendns_available(
        connection: sqlite3.Connection,
        keendns: str,
        *,
        exclude_router_id: int | None = None,
    ) -> None:
        if not keendns:
            return

        parameters: list[Any] = [keendns]
        exclude_sql = ""

        if exclude_router_id is not None:
            exclude_sql = " AND id <> ?"
            parameters.append(int(exclude_router_id))

        row = connection.execute(
            (
                "SELECT id FROM routers "
                "WHERE keendns = ? COLLATE NOCASE"
                + exclude_sql
                + " LIMIT 1"
            ),
            tuple(parameters),
        ).fetchone()

        if row is not None:
            raise sqlite3.IntegrityError(
                "Роутер с таким KeenDNS-адресом уже существует."
            )

    def add_router(
        self,
        name: str,
        local_ip: str,
        keendns: str,
        connection_mode: str,
        username: str,
        password: str | None,
        group_name: str = "",
        clear_password: bool = False,
    ) -> int:
        del clear_password
        name = name.strip()

        if not name:
            raise ValueError(
                "Название роутера не может быть пустым."
            )

        local_ip = (local_ip or "").strip()
        keendns = self.normalize_keendns(
            keendns
        )
        username = (username or "").strip()
        group_name = (group_name or "").strip()
        secret = str(password or "")
        connection_mode = (
            self.normalize_connection_mode(
                connection_mode
            )
        )

        credential_ref = ""

        if secret:
            credential_ref = (
                self.credential_store.new_reference()
            )
            self.credential_store.set_password(
                credential_ref,
                secret,
            )

        try:
            with self.connection() as connection:
                self.ensure_keendns_available(
                    connection,
                    keendns,
                )
                next_order_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(sort_order), -1) + 1
                    FROM routers
                    """
                ).fetchone()
                next_order = int(next_order_row[0])
                cursor = connection.execute(
                    """
                    INSERT INTO routers (
                        name,
                        local_ip,
                        keendns,
                        connection_mode,
                        username,
                        credential_ref,
                        group_name,
                        sort_order,
                        status,
                        last_seen
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        name,
                        local_ip or None,
                        keendns or None,
                        connection_mode,
                        username,
                        credential_ref or None,
                        group_name,
                        next_order,
                    ),
                )
                return int(cursor.lastrowid)

        except Exception:
            if credential_ref:
                try:
                    self.credential_store.delete_password(
                        credential_ref,
                        missing_ok=True,
                    )
                except CredentialStoreError:
                    pass
            raise

    def get_routers(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    local_ip,
                    keendns,
                    connection_mode,
                    username,
                    credential_ref,
                    group_name,
                    sort_order,
                    status,
                    last_seen
                FROM routers
                ORDER BY sort_order, name COLLATE NOCASE, id
                """
            ).fetchall()

        return [
            self._public_router_record(dict(row))
            for row in rows
        ]

    def get_router(
        self,
        router_id: int,
        *,
        include_password: bool = False,
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    local_ip,
                    keendns,
                    connection_mode,
                    username,
                    credential_ref,
                    group_name,
                    sort_order,
                    status,
                    last_seen
                FROM routers
                WHERE id = ?
                """,
                (router_id,),
            ).fetchone()

        if row is None:
            return None

        raw_record = dict(row)
        credential_ref = str(
            raw_record.get("credential_ref") or ""
        ).strip()
        record = self._public_router_record(raw_record)

        if include_password:
            record["password"] = (
                self.credential_store.get_password(
                    credential_ref,
                    required=True,
                )
                if credential_ref
                else ""
            )

        return record

    @staticmethod
    def _public_router_record(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(record)
        credential_ref = str(
            result.pop("credential_ref", "") or ""
        ).strip()
        result.pop("password", None)
        result["password_configured"] = bool(
            credential_ref
        )
        return result

    def update_router(
        self,
        router_id: int,
        name: str,
        local_ip: str,
        keendns: str,
        connection_mode: str,
        username: str,
        password: str | None,
        group_name: str = "",
        clear_password: bool = False,
    ) -> None:
        name = name.strip()

        if not name:
            raise ValueError(
                "Название роутера не может быть пустым."
            )

        local_ip = (local_ip or "").strip()
        keendns = self.normalize_keendns(
            keendns
        )
        username = (username or "").strip()
        group_name = (group_name or "").strip()
        connection_mode = (
            self.normalize_connection_mode(
                connection_mode
            )
        )

        with self.connection() as connection:
            current = connection.execute(
                """
                SELECT credential_ref
                FROM routers
                WHERE id = ?
                """,
                (router_id,),
            ).fetchone()

            if current is None:
                raise ValueError(
                    "Роутер с указанным ID не найден."
                )

            self.ensure_keendns_available(
                connection,
                keendns,
                exclude_router_id=router_id,
            )

            old_reference = str(
                current["credential_ref"] or ""
            ).strip()
            new_reference = old_reference
            old_secret: str | None = None
            secret_changed = False
            secret_deleted = False

            if clear_password:
                new_reference = ""

                if old_reference:
                    old_secret = self.credential_store.get_password(
                        old_reference,
                        required=False,
                    )
                    self.credential_store.delete_password(
                        old_reference,
                        missing_ok=True,
                    )
                    secret_deleted = True

            elif password is not None:
                new_secret = str(password)

                if new_secret:
                    if not new_reference:
                        new_reference = (
                            self.credential_store.new_reference()
                        )
                    old_secret = self.credential_store.get_password(
                        new_reference,
                        required=False,
                    )
                    self.credential_store.set_password(
                        new_reference,
                        new_secret,
                    )
                    secret_changed = True
                elif old_reference:
                    old_secret = self.credential_store.get_password(
                        old_reference,
                        required=False,
                    )
                    self.credential_store.delete_password(
                        old_reference,
                        missing_ok=True,
                    )
                    new_reference = ""
                    secret_deleted = True

            try:
                cursor = connection.execute(
                    """
                    UPDATE routers
                    SET
                        name = ?,
                        local_ip = ?,
                        keendns = ?,
                        connection_mode = ?,
                        username = ?,
                        credential_ref = ?,
                        group_name = ?,
                        status = 0,
                        last_seen = NULL
                    WHERE id = ?
                    """,
                    (
                        name,
                        local_ip or None,
                        keendns or None,
                        connection_mode,
                        username,
                        new_reference or None,
                        group_name,
                        router_id,
                    ),
                )

                if cursor.rowcount == 0:
                    raise ValueError(
                        "Роутер с указанным ID не найден."
                    )

            except Exception:
                if secret_changed:
                    try:
                        if old_secret is None:
                            self.credential_store.delete_password(
                                new_reference,
                                missing_ok=True,
                            )
                        else:
                            self.credential_store.set_password(
                                new_reference,
                                old_secret,
                            )
                    except CredentialStoreError:
                        pass
                elif secret_deleted and old_reference:
                    if old_secret is not None:
                        try:
                            self.credential_store.set_password(
                                old_reference,
                                old_secret,
                            )
                        except CredentialStoreError:
                            pass
                raise

    def update_router_status(
        self,
        router_id: int,
        status: bool,
        *,
        touch_last_seen: bool = False,
    ) -> bool:
        status_value = int(bool(status))
        touch_value = int(
            bool(status)
            and bool(touch_last_seen)
        )

        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE routers
                SET
                    status = ?,
                    last_seen = CASE
                        WHEN ? = 1
                        THEN datetime(
                            'now',
                            'localtime'
                        )
                        ELSE last_seen
                    END
                WHERE id = ?
                  AND (
                      status <> ?
                      OR ? = 1
                  )
                """,
                (
                    status_value,
                    touch_value,
                    router_id,
                    status_value,
                    touch_value,
                ),
            )
            return cursor.rowcount > 0

    def get_schema_snapshot(self) -> dict[str, Any]:
        with self.connection() as connection:
            columns = [
                dict(row)
                for row in connection.execute(
                    "PRAGMA table_info(routers)"
                ).fetchall()
            ]
            indexes = [
                dict(row)
                for row in connection.execute(
                    "PRAGMA index_list(routers)"
                ).fetchall()
            ]
            user_version_row = connection.execute(
                "PRAGMA user_version"
            ).fetchone()

        return {
            "database_path": str(self.database_path),
            "routers_columns": columns,
            "routers_indexes": indexes,
            "user_version": (
                int(user_version_row[0])
                if user_version_row is not None
                else 0
            ),
        }

    def delete_router(
        self,
        router_id: int,
    ) -> None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT credential_ref
                FROM routers
                WHERE id = ?
                """,
                (router_id,),
            ).fetchone()

            if row is None:
                return

            reference = str(
                row["credential_ref"] or ""
            ).strip()
            old_secret: str | None = None

            if reference:
                old_secret = self.credential_store.get_password(
                    reference,
                    required=False,
                )
                self.credential_store.delete_password(
                    reference,
                    missing_ok=True,
                )

            try:
                connection.execute(
                    """
                    DELETE FROM routers
                    WHERE id = ?
                    """,
                    (router_id,),
                )
                rows = connection.execute(
                    """
                    SELECT id
                    FROM routers
                    ORDER BY sort_order, name COLLATE NOCASE, id
                    """
                ).fetchall()

                for sort_order, remaining in enumerate(rows):
                    connection.execute(
                        """
                        UPDATE routers
                        SET sort_order = ?
                        WHERE id = ?
                        """,
                        (
                            sort_order,
                            int(remaining["id"]),
                        ),
                    )
            except Exception:
                if reference and old_secret is not None:
                    try:
                        self.credential_store.set_password(
                            reference,
                            old_secret,
                        )
                    except CredentialStoreError:
                        pass
                raise
