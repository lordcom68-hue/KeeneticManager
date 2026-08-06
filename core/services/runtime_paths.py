from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from core.app_profile import APP_STORAGE_NAMESPACE


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_app_data_dir() -> Path:
    override = os.environ.get("KEENETIC_MANAGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / APP_STORAGE_NAMESPACE
        return (
            Path.home()
            / "AppData"
            / "Local"
            / APP_STORAGE_NAMESPACE
        )

    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home) / APP_STORAGE_NAMESPACE
    return Path.home() / ".local" / "share" / APP_STORAGE_NAMESPACE


APP_DATA_DIR = _resolve_app_data_dir()
DATABASE_DIR = APP_DATA_DIR / "database"
DIAGNOSTICS_HISTORY_DIR = APP_DATA_DIR / "diagnostics_history"
DIAGNOSTICS_EXPORT_DIR = APP_DATA_DIR / "diagnostics"
LOGS_DIR = APP_DATA_DIR / "logs"
LEGACY_DATABASE_DIR = PROJECT_ROOT / "database"


def migrate_legacy_database(
    *,
    target: Path | None = None,
    legacy: Path | None = None,
) -> bool:
    """Copy the freshest legacy database once, preserving the prior target."""
    target_path = (
        Path(target)
        if target is not None
        else DATABASE_DIR / "routers.db"
    )
    legacy_path = (
        Path(legacy)
        if legacy is not None
        else LEGACY_DATABASE_DIR / "routers.db"
    )

    marker_path = target_path.parent / ".legacy_migration_complete"
    if marker_path.is_file() or not legacy_path.is_file():
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if (
        target_path.is_file()
        and target_path.stat().st_mtime_ns >= legacy_path.stat().st_mtime_ns
    ):
        marker_path.write_text(
            "Existing user database is newer than the legacy copy.\n",
            encoding="utf-8",
        )
        return False

    if target_path.is_file():
        backup_path = target_path.with_name(
            f"{target_path.stem}.pre_legacy_migration{target_path.suffix}"
        )
        if not backup_path.exists():
            shutil.copy2(target_path, backup_path)

    temporary = target_path.with_suffix(
        target_path.suffix + ".migrating"
    )
    try:
        shutil.copy2(legacy_path, temporary)
        temporary.replace(target_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    marker_path.write_text(
        (
            "Legacy database migration completed at "
            f"{datetime.now().astimezone().isoformat()}.\n"
        ),
        encoding="utf-8",
    )
    return True


def router_history_path(
    router_id: int | None,
    *,
    root: Path | None = None,
) -> Path:
    suffix = str(router_id) if router_id is not None else "unknown"
    directory = Path(root) if root is not None else DIAGNOSTICS_HISTORY_DIR
    return directory / f"connection_attempts_router_{suffix}.jsonl"


def router_snapshot_cache_path(
    router_id: int | None,
    *,
    root: Path | None = None,
) -> Path:
    suffix = str(router_id) if router_id is not None else "unknown"
    directory = Path(root) if root is not None else DIAGNOSTICS_HISTORY_DIR
    return directory / f"last_snapshot_router_{suffix}.json"


def legacy_router_history_path(router_id: int | None) -> Path:
    """Return the pre-0.35.11 history path inside the source tree."""
    suffix = str(router_id) if router_id is not None else "unknown"
    return (
        PROJECT_ROOT
        / "diagnostics_history"
        / f"connection_attempts_router_{suffix}.jsonl"
    )


def legacy_router_snapshot_cache_path(router_id: int | None) -> Path:
    suffix = str(router_id) if router_id is not None else "unknown"
    return (
        PROJECT_ROOT
        / "diagnostics_history"
        / f"last_snapshot_router_{suffix}.json"
    )
