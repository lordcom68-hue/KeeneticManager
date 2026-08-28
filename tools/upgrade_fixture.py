from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class UpgradeRuntime:
    database_type: Any
    credential_store_type: Any
    qsettings_type: Any


@dataclass(frozen=True)
class FixturePaths:
    data_dir: Path
    settings_file: Path
    state_file: Path

    @property
    def database_file(self) -> Path:
        return self.data_dir / "database" / "routers.db"


def load_runtime(
    source_root: Path,
    profile_path: Path,
    paths: FixturePaths,
) -> UpgradeRuntime:
    source = source_root.resolve()
    profile = profile_path.resolve()
    if not (source / "core" / "database.py").is_file():
        raise FileNotFoundError(
            f"Database module was not found in release source: {source}"
        )
    if not profile.is_file():
        raise FileNotFoundError(f"Release profile was not found: {profile}")

    os.environ["KEENETIC_MANAGER_PROFILE"] = str(profile)
    os.environ["KEENETIC_MANAGER_DATA_DIR"] = str(paths.data_dir.resolve())
    os.environ["KEENETIC_MANAGER_SETTINGS_FILE"] = str(
        paths.settings_file.resolve()
    )
    sys.path.insert(0, str(source))

    from PySide6.QtCore import QSettings
    from core.credential_store import RouterCredentialStore
    from core.database import Database

    return UpgradeRuntime(
        database_type=Database,
        credential_store_type=RouterCredentialStore,
        qsettings_type=QSettings,
    )


def expected_secret(fixture_id: str) -> str:
    return f"upgrade-secret-{fixture_id}"


def expected_routers(fixture_id: str) -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "Маршрутизатор обновления",
            "local_ip": "192.0.2.55",
            "keendns": f"upgrade-{fixture_id}.example.invalid",
            "connection_mode": "local",
            "username": "upgrade-admin",
            "password": expected_secret(fixture_id),
            "group_name": "Тест обновления",
        },
        {
            "name": "Router without password",
            "local_ip": "198.51.100.55",
            "keendns": "",
            "connection_mode": "auto",
            "username": "admin",
            "password": None,
            "group_name": "Upgrade test",
        },
    )


def _read_credential_references(database_file: Path) -> list[str]:
    with closing(sqlite3.connect(database_file)) as connection:
        rows = connection.execute(
            "SELECT credential_ref FROM routers ORDER BY sort_order, id"
        ).fetchall()
    return [str(row[0] or "").strip() for row in rows]


def _write_settings(runtime: UpgradeRuntime, paths: FixturePaths, fixture_id: str) -> None:
    paths.settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings = runtime.qsettings_type(
        str(paths.settings_file),
        runtime.qsettings_type.Format.IniFormat,
    )
    settings.setValue("monitor/interval_ms", 5_000)
    settings.setValue("monitor/auto_refresh_enabled", False)
    settings.setValue("monitor/reachability_enabled", False)
    settings.setValue("developer/mode_enabled", True)
    settings.setValue("upgrade/sentinel", fixture_id)
    settings.sync()
    if settings.status() != runtime.qsettings_type.Status.NoError:
        raise RuntimeError("Unable to write the isolated settings file.")


def seed_fixture(
    runtime: UpgradeRuntime,
    paths: FixturePaths,
    *,
    fixture_id: str | None = None,
    credential_store: Any = None,
) -> dict[str, Any]:
    token = fixture_id or uuid4().hex
    store = credential_store or runtime.credential_store_type()
    database = runtime.database_type(
        database_path=paths.database_file,
        credential_store=store,
    )
    if database.get_routers():
        raise RuntimeError("The isolated upgrade database is not empty.")

    router_ids = [
        database.add_router(**router)
        for router in expected_routers(token)
    ]
    references = _read_credential_references(paths.database_file)
    _write_settings(runtime, paths, token)

    state = {
        "fixture_id": token,
        "router_ids": router_ids,
        "credential_references": [
            reference for reference in references if reference
        ],
    }
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert_fixture(
        runtime,
        paths,
        credential_store=store,
    )
    return state


def _load_state(paths: FixturePaths) -> dict[str, Any]:
    raw = json.loads(paths.state_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not str(raw.get("fixture_id") or "").strip():
        raise RuntimeError("The upgrade fixture state is invalid.")
    return raw


def _assert_settings(runtime: UpgradeRuntime, paths: FixturePaths, fixture_id: str) -> None:
    settings = runtime.qsettings_type(
        str(paths.settings_file),
        runtime.qsettings_type.Format.IniFormat,
    )
    checks = {
        "monitor/interval_ms": (
            settings.value("monitor/interval_ms", 0, type=int),
            5_000,
        ),
        "monitor/auto_refresh_enabled": (
            settings.value("monitor/auto_refresh_enabled", True, type=bool),
            False,
        ),
        "monitor/reachability_enabled": (
            settings.value("monitor/reachability_enabled", True, type=bool),
            False,
        ),
        "developer/mode_enabled": (
            settings.value("developer/mode_enabled", False, type=bool),
            True,
        ),
        "upgrade/sentinel": (
            str(settings.value("upgrade/sentinel", "")),
            fixture_id,
        ),
    }
    failed = [key for key, values in checks.items() if values[0] != values[1]]
    if failed:
        raise AssertionError(f"Settings changed during upgrade: {failed}")


def assert_fixture(
    runtime: UpgradeRuntime,
    paths: FixturePaths,
    *,
    credential_store: Any = None,
) -> dict[str, Any]:
    state = _load_state(paths)
    fixture_id = str(state["fixture_id"])
    store = credential_store or runtime.credential_store_type()
    database = runtime.database_type(
        database_path=paths.database_file,
        credential_store=store,
    )
    actual_routers = database.get_routers()
    expected = expected_routers(fixture_id)
    if len(actual_routers) != len(expected):
        raise AssertionError("Router count changed during upgrade.")

    for actual, wanted in zip(actual_routers, expected, strict=True):
        for key in (
            "name",
            "local_ip",
            "keendns",
            "connection_mode",
            "username",
            "group_name",
        ):
            actual_value = str(actual.get(key) or "")
            wanted_value = str(wanted.get(key) or "")
            if actual_value != wanted_value:
                raise AssertionError(f"Router field changed during upgrade: {key}")

        password_expected = bool(wanted.get("password"))
        if bool(actual.get("password_configured")) != password_expected:
            raise AssertionError("Router password flag changed during upgrade.")
        if "password" in actual or "credential_ref" in actual:
            raise AssertionError("Public router record exposed credential data.")

    protected = database.get_router(
        int(state["router_ids"][0]),
        include_password=True,
    )
    if protected is None or protected.get("password") != expected_secret(fixture_id):
        raise AssertionError("Credential Manager secret was not preserved.")

    schema = database.get_schema_snapshot()
    columns = {
        str(item.get("name") or "")
        for item in schema["routers_columns"]
    }
    if "password" in columns or "credential_ref" not in columns:
        raise AssertionError("Router database credential schema is unsafe.")
    if expected_secret(fixture_id).encode("utf-8") in paths.database_file.read_bytes():
        raise AssertionError("The synthetic secret leaked into SQLite.")

    current_references = [
        value
        for value in _read_credential_references(paths.database_file)
        if value
    ]
    if current_references != list(state["credential_references"]):
        raise AssertionError("Credential reference changed during upgrade.")
    _assert_settings(runtime, paths, fixture_id)
    return {
        "router_count": len(actual_routers),
        "settings_preserved": True,
        "credential_preserved": True,
        "plaintext_secret_in_database": False,
    }


def cleanup_fixture(
    runtime: UpgradeRuntime,
    paths: FixturePaths,
    *,
    credential_store: Any = None,
) -> None:
    if not paths.state_file.is_file():
        return
    state = _load_state(paths)
    store = credential_store or runtime.credential_store_type()
    for reference in state.get("credential_references", []):
        value = str(reference or "").strip()
        if value:
            store.delete_password(value, missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and validate an isolated installer-upgrade fixture."
    )
    parser.add_argument("action", choices=("seed", "assert", "cleanup"))
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--settings-file", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = FixturePaths(
        data_dir=args.data_dir.resolve(),
        settings_file=args.settings_file.resolve(),
        state_file=args.state_file.resolve(),
    )
    runtime = load_runtime(
        args.source_root,
        args.profile,
        paths,
    )
    if args.action == "seed":
        state = seed_fixture(runtime, paths)
        result = {
            "fixture_created": True,
            "router_count": len(state["router_ids"]),
            "credential_reference_count": len(
                state["credential_references"]
            ),
        }
    elif args.action == "assert":
        result = assert_fixture(runtime, paths)
    else:
        cleanup_fixture(runtime, paths)
        result = {"credential_cleanup": True}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
