from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.app_version import APP_NAME, APP_VERSION
from core.database import Database
from core.rci import DiagnosticCollector, RouterCapabilities
from core.rci.diagnostics import sanitize_value
from core.services.runtime_paths import (
    DIAGNOSTICS_HISTORY_DIR,
    LOGS_DIR,
    PROJECT_ROOT,
    legacy_router_history_path,
    legacy_router_snapshot_cache_path,
    router_history_path,
    router_operation_history_path,
    router_snapshot_cache_path,
)


class DiagnosticsService:
    """Создание подключённой и офлайн-диагностики приложения."""

    MAX_PERSISTED_ATTEMPTS = 1000
    SNAPSHOT_CACHE_VERSION = 1

    def __init__(
        self,
        *,
        database: Database | None = None,
        history_root: Path | None = None,
        logs_root: Path | None = None,
    ) -> None:
        # База открывается лениво: сохранение фонового snapshot-cache
        # не должно запускать миграции SQLite каждые 30 секунд.
        self.database = database
        self.history_root = (
            Path(history_root)
            if history_root is not None
            else DIAGNOSTICS_HISTORY_DIR
        )
        self.logs_root = (
            Path(logs_root)
            if logs_root is not None
            else LOGS_DIR
        )

    def get_database(self) -> Database:
        if self.database is None:
            self.database = Database()
        return self.database

    @staticmethod
    def _unique_paths(paths: list[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()

        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path.absolute())
            if key in seen:
                continue
            seen.add(key)
            result.append(path)

        return result

    @staticmethod
    def history_paths(
        router_id: int | None,
        *,
        history_root: Path | None = None,
        include_legacy: bool = True,
    ) -> list[Path]:
        base = router_history_path(
            router_id,
            root=history_root,
        )
        paths = [base.with_suffix(".jsonl.1"), base]

        if include_legacy:
            legacy = legacy_router_history_path(router_id)
            paths.extend(
                [legacy.with_suffix(".jsonl.1"), legacy]
            )

        return DiagnosticsService._unique_paths(paths)

    @staticmethod
    def snapshot_cache_paths(
        router_id: int | None,
        *,
        history_root: Path | None = None,
        include_legacy: bool = True,
    ) -> list[Path]:
        paths = [
            router_snapshot_cache_path(
                router_id,
                root=history_root,
            )
        ]

        if include_legacy:
            paths.append(
                legacy_router_snapshot_cache_path(router_id)
            )

        return DiagnosticsService._unique_paths(paths)

    @staticmethod
    def local_data_paths(
        router_id: int | None,
        *,
        history_root: Path | None = None,
        logs_root: Path | None = None,
    ) -> list[Path]:
        include_legacy = (
            history_root is None
            or Path(history_root) == DIAGNOSTICS_HISTORY_DIR
        )
        operation_path = router_operation_history_path(
            router_id,
            root=(LOGS_DIR if logs_root is None else logs_root),
        )
        return DiagnosticsService._unique_paths(
            [
                *DiagnosticsService.history_paths(
                    router_id,
                    history_root=history_root,
                    include_legacy=include_legacy,
                ),
                *DiagnosticsService.snapshot_cache_paths(
                    router_id,
                    history_root=history_root,
                    include_legacy=include_legacy,
                ),
                operation_path.with_suffix(".jsonl.1"),
                operation_path,
            ]
        )

    def load_persisted_attempts(
        self,
        router_id: int | None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        include_legacy = self.history_root == DIAGNOSTICS_HISTORY_DIR

        for path in self.history_paths(
            router_id,
            history_root=self.history_root,
            include_legacy=include_legacy,
        ):
            if not path.is_file():
                continue

            try:
                lines = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except OSError:
                continue

            for line in lines:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    result.append(value)

        result.sort(
            key=lambda item: str(item.get("timestamp") or "")
        )
        return result[-self.MAX_PERSISTED_ATTEMPTS :]

    def snapshot_cache_path(self, router_id: int | None) -> Path:
        return router_snapshot_cache_path(
            router_id,
            root=self.history_root,
        )

    def save_snapshot_cache(
        self,
        *,
        router_id: int | None,
        collector: DiagnosticCollector,
        metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        if router_id is None or not collector.snapshots:
            return None

        path = self.snapshot_cache_path(router_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = sanitize_value(
            {
                "cache_version": self.SNAPSHOT_CACHE_VERSION,
                "created_at": datetime.now().astimezone().isoformat(),
                "router_id": router_id,
                "metadata": dict(metadata or {}),
                "statistics": collector.statistics_dict(),
                "snapshots": collector.snapshots,
            }
        )

        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def load_snapshot_cache(
        self,
        router_id: int | None,
    ) -> dict[str, Any] | None:
        include_legacy = self.history_root == DIAGNOSTICS_HISTORY_DIR

        for path in self.snapshot_cache_paths(
            router_id,
            history_root=self.history_root,
            include_legacy=include_legacy,
        ):
            if not path.is_file():
                continue

            try:
                value = json.loads(
                    path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(value, dict):
                continue
            snapshots = value.get("snapshots")
            if not isinstance(snapshots, dict) or not snapshots:
                continue
            value["_source_path"] = str(path)
            return value

        return None

    def application_logs(
        self,
        router_id: int | None,
    ) -> list[Path]:
        suffix = str(router_id) if router_id is not None else "unknown"
        include_legacy = self.history_root == DIAGNOSTICS_HISTORY_DIR

        return self._unique_paths(
            [
                self.logs_root / f"priority_debug_router_{suffix}.log",
                self.logs_root / "priority_debug.log",
                self.logs_root / "hardware_guard.log",
                router_operation_history_path(
                    router_id,
                    root=self.logs_root,
                ),
                router_operation_history_path(
                    router_id,
                    root=self.logs_root,
                ).with_suffix(".jsonl.1"),
                # Пути до 0.26.1 оставлены для совместимости.
                PROJECT_ROOT / f"priority_debug_router_{suffix}.log",
                PROJECT_ROOT / "priority_debug.log",
                PROJECT_ROOT / "hardware_guard.log",
                *self.history_paths(
                    router_id,
                    history_root=self.history_root,
                    include_legacy=include_legacy,
                ),
            ]
        )

    @staticmethod
    def _attempt_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item.get("timestamp"),
            item.get("phase"),
            item.get("endpoint"),
            item.get("url"),
            item.get("attempt"),
            item.get("success"),
        )

    @staticmethod
    def _is_blank(value: Any) -> bool:
        return value is None or (
            isinstance(value, str) and not value.strip()
        )

    @classmethod
    def summarize_attempts(
        cls,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Строит сводку по истории без активного транспорта."""
        ordered = sorted(
            (
                dict(item)
                for item in attempts
                if isinstance(item, dict)
            ),
            key=lambda item: str(item.get("timestamp") or ""),
        )
        if not ordered:
            return {}

        latest = ordered[-1]
        latest_with_url = next(
            (
                item
                for item in reversed(ordered)
                if str(item.get("url") or "").strip()
            ),
            latest,
        )
        latest_transition = next(
            (
                item
                for item in reversed(ordered)
                if item.get("phase") == "state_transition"
            ),
            None,
        )
        latest_failure = next(
            (
                item
                for item in reversed(ordered)
                if item.get("success") is False
                or not cls._is_blank(item.get("failure_kind"))
            ),
            None,
        )

        summary: dict[str, Any] = {
            "last_attempt_url": latest_with_url.get("url"),
            "last_connection_state": (
                (latest_transition or {}).get("new_state")
                or latest.get("connection_state")
            ),
        }

        if latest_failure is not None:
            summary.update(
                {
                    "last_result_message": str(
                        latest_failure.get("message")
                        or latest_failure.get("error")
                        or ""
                    ),
                    "last_failure_kind": (
                        latest_failure.get("failure_kind")
                    ),
                    "last_failure_at": latest_failure.get(
                        "timestamp"
                    ),
                    "last_failure_url": latest_failure.get("url"),
                    "last_failure_endpoint": latest_failure.get(
                        "endpoint"
                    ),
                    "last_failure_status_code": latest_failure.get(
                        "status_code"
                    ),
                }
            )
        else:
            summary["last_result_message"] = str(
                latest.get("message") or ""
            )
            summary["last_failure_kind"] = None

        return summary

    def create_archive(
        self,
        *,
        destination: str | Path,
        router_id: int | None,
        collector: DiagnosticCollector | None = None,
        metadata: dict[str, Any] | None = None,
        capabilities: RouterCapabilities | None = None,
        application_settings: dict[str, Any] | None = None,
        connection_state: dict[str, Any] | None = None,
    ) -> Path:
        diagnostics = collector or DiagnosticCollector()
        database = self.get_database()
        router_record = (
            database.get_router(int(router_id))
            if router_id is not None
            else None
        )

        combined_attempts = [
            dict(item)
            for item in diagnostics.connection_attempts
            if isinstance(item, dict)
        ]
        existing_keys = {
            self._attempt_key(item)
            for item in combined_attempts
        }
        persisted_attempts = self.load_persisted_attempts(router_id)

        for item in persisted_attempts:
            key = self._attempt_key(item)
            if key in existing_keys:
                continue
            combined_attempts.append(dict(item))
            existing_keys.add(key)

        combined_attempts.sort(
            key=lambda item: str(item.get("timestamp") or "")
        )
        diagnostics.connection_attempts = combined_attempts[
            -diagnostics.MAX_CONNECTION_ATTEMPTS :
        ]

        state = dict(connection_state or {})
        router_connected = bool(state.get("router_connected", False))

        cached_snapshot: dict[str, Any] | None = None
        if diagnostics.snapshots:
            snapshot_source = "live"
            try:
                self.save_snapshot_cache(
                    router_id=router_id,
                    collector=diagnostics,
                    metadata=metadata,
                )
            except OSError:
                pass
        else:
            cached_snapshot = self.load_snapshot_cache(router_id)
            if cached_snapshot is not None:
                for endpoint, data in dict(
                    cached_snapshot.get("snapshots") or {}
                ).items():
                    diagnostics.record_snapshot(str(endpoint), data)
                snapshot_source = "cached"
            else:
                snapshot_source = "unavailable"

        database_status = bool(
            (router_record or {}).get("status", False)
        )
        current_status = str(
            state.get("current_connection_status")
            or state.get("transport_state")
            or ("connected" if router_connected else "offline")
        ).strip().lower()

        state["router_connected"] = router_connected
        state["database_status"] = database_status
        state["current_connection_status"] = current_status
        state["status_mismatch"] = (
            database_status != (current_status == "connected")
        )
        state["snapshot_source"] = snapshot_source
        state["persisted_connection_attempt_count"] = len(
            persisted_attempts
        )

        attempt_summary = self.summarize_attempts(
            diagnostics.connection_attempts
        )
        for key, value in attempt_summary.items():
            if self._is_blank(state.get(key)):
                state[key] = value

        if diagnostics.connection_attempts:
            state["last_connection_attempt"] = (
                diagnostics.connection_attempts[-1]
            )

        if cached_snapshot is not None:
            state["cached_snapshot_created_at"] = (
                cached_snapshot.get("created_at")
            )
            state["cached_snapshot_path"] = (
                cached_snapshot.get("_source_path")
            )
            state["cached_snapshot_metadata"] = (
                cached_snapshot.get("metadata") or {}
            )

        archive_metadata = {
            "application": APP_NAME,
            "application_version": APP_VERSION,
            "router_id": router_id,
            **dict(metadata or {}),
        }

        return diagnostics.create_archive(
            destination=destination,
            metadata=archive_metadata,
            capabilities=capabilities or RouterCapabilities(),
            application_logs=self.application_logs(router_id),
            extra_json_files={
                "router_record.json": router_record or {},
                "application_settings.json": dict(
                    application_settings or {}
                ),
                "database_schema.json": (
                    database.get_schema_snapshot()
                ),
                "connection_state.json": state,
            },
        )

    def create_offline_archive(
        self,
        *,
        destination: str | Path,
        router_id: int,
        application_settings: dict[str, Any] | None = None,
        last_result: Any = None,
    ) -> Path:
        collector = DiagnosticCollector()
        last_result_success = bool(
            getattr(last_result, "success", False)
        )
        last_result_message = str(
            getattr(last_result, "message", "") or ""
        )
        result_state = str(
            getattr(last_result, "connection_state", "") or ""
        ).strip().lower()

        collector.record_event(
            "offline_diagnostic",
            (
                "Диагностический архив создан без активной "
                "HTTP-сессии с роутером."
            ),
            details={
                "router_id": router_id,
                "last_result_success": last_result_success,
                "last_result_message": last_result_message,
            },
        )

        return self.create_archive(
            destination=destination,
            router_id=router_id,
            collector=collector,
            application_settings=application_settings,
            connection_state={
                "router_connected": False,
                "current_connection_status": result_state or "offline",
                "last_result_success": last_result_success,
                "last_result_message": last_result_message,
                "confirmed_offline": bool(
                    getattr(last_result, "confirmed_offline", True)
                ),
                "consecutive_failures": int(
                    getattr(last_result, "consecutive_failures", 0)
                    or 0
                ),
            },
        )

    def clear_local_data(self, router_id: int | None) -> None:
        for path in self.local_data_paths(
            router_id,
            history_root=self.history_root,
            logs_root=self.logs_root,
        ):
            path.unlink(missing_ok=True)
