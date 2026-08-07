from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from core.rci import DiagnosticCollector
from core.services.diagnostics_service import DiagnosticsService


class FakeDatabase:
    def __init__(self, status: bool = False) -> None:
        self.status = bool(status)

    def get_router(self, router_id: int) -> dict[str, Any]:
        return {
            "id": router_id,
            "name": "Test Router",
            "local_ip": "192.0.2.1",
            "keendns": "test.keenetic.pro",
            "username": "admin",
            "password": "must-not-leak",
            "status": int(self.status),
        }

    def get_schema_snapshot(self) -> dict[str, Any]:
        return {
            "routers_columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "password", "type": "TEXT"},
            ]
        }


class DiagnosticsServiceTests(unittest.TestCase):
    def test_archive_member_names_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "current" / "application.log"
            second = root / "legacy" / "application.log"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("current", encoding="utf-8")
            second.write_text("legacy", encoding="utf-8")

            archive_path = DiagnosticCollector().create_archive(
                root / "archive",
                application_logs=[first, second],
            )

            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()

            self.assertEqual(len(names), len(set(names)))
            self.assertIn("logs/application.log", names)
            self.assertIn("logs/application_2.log", names)

    def test_offline_archive_contains_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=root / "history",
                logs_root=root / "logs",
            )
            operation_history = (
                root / "logs" / "operation_history_router_12.jsonl"
            )
            operation_history.parent.mkdir(parents=True, exist_ok=True)
            operation_history.write_text(
                '{"type":"ethernet_port_state","message":"ok"}\n',
                encoding="utf-8",
            )
            archive_path = service.create_offline_archive(
                destination=root,
                router_id=12,
                application_settings={"auto_refresh": False},
            )

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                required = {
                    "manifest.json",
                    "events.json",
                    "statistics.json",
                    "connection_attempts.json",
                    "router_record.json",
                    "application_settings.json",
                    "database_schema.json",
                    "connection_state.json",
                }
                self.assertFalse(required - names)
                self.assertIn(
                    "logs/operation_history_router_12.jsonl",
                    names,
                )

                router_record = archive.read(
                    "router_record.json"
                ).decode("utf-8")
                self.assertNotIn("must-not-leak", router_record)
                self.assertIn("[REDACTED]", router_record)

    def test_offline_archive_loads_persisted_history_cache_and_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_root = root / "history"
            service = DiagnosticsService(
                database=FakeDatabase(status=True),  # type: ignore[arg-type]
                history_root=history_root,
                logs_root=root / "logs",
            )
            history_root.mkdir(parents=True, exist_ok=True)
            history_path = history_root / "connection_attempts_router_12.jsonl"
            attempts = [
                {
                    "timestamp": "2026-08-04T10:00:00+03:00",
                    "phase": "rci",
                    "endpoint": "show/system",
                    "attempt": 1,
                    "success": False,
                    "failure_kind": "timeout",
                    "message": "Истекло время ожидания RCI.",
                    "url": "https://test.keenetic.pro/rci/show/system",
                    "connection_state": "unstable",
                },
                {
                    "timestamp": "2026-08-04T10:00:02+03:00",
                    "phase": "rci",
                    "endpoint": "show/system",
                    "attempt": 2,
                    "success": False,
                    "status_code": 503,
                    "failure_kind": "keendns_http_503",
                    "message": "HTTP 503 при RCI-запросе.",
                    "url": "https://test.keenetic.pro/rci/show/system",
                    "connection_state": "retrying",
                },
                {
                    "timestamp": "2026-08-04T10:00:04+03:00",
                    "phase": "state_transition",
                    "endpoint": "connection",
                    "attempt": 0,
                    "success": None,
                    "message": "Роутер подтверждённо недоступен.",
                    "url": "https://test.keenetic.pro",
                    "previous_state": "retrying",
                    "new_state": "offline",
                    "connection_state": "offline",
                },
            ]
            history_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in attempts
                ),
                encoding="utf-8",
            )

            collector = DiagnosticCollector()
            collector.record_snapshot(
                "show/system",
                {"hostname": "cached"},
            )
            service.save_snapshot_cache(
                router_id=12,
                collector=collector,
                metadata={"source": "unit-test"},
            )

            archive_path = service.create_offline_archive(
                destination=root,
                router_id=12,
            )
            with zipfile.ZipFile(archive_path) as archive:
                archived_attempts = json.loads(
                    archive.read("connection_attempts.json")
                )
                state = json.loads(
                    archive.read("connection_state.json")
                )
                names = set(archive.namelist())

            failure_kinds = {
                item.get("failure_kind")
                for item in archived_attempts
            }
            self.assertIn("timeout", failure_kinds)
            self.assertIn("keendns_http_503", failure_kinds)
            self.assertIs(state["database_status"], True)
            self.assertEqual(
                state["current_connection_status"],
                "offline",
            )
            self.assertTrue(state["status_mismatch"])
            self.assertEqual(state["snapshot_source"], "cached")
            self.assertIn("snapshots/show_system.json", names)
            self.assertEqual(
                state["last_result_message"],
                "HTTP 503 при RCI-запросе.",
            )
            self.assertEqual(
                state["last_failure_kind"],
                "keendns_http_503",
            )
            self.assertEqual(
                state["last_failure_status_code"],
                503,
            )
            self.assertEqual(
                state["last_failure_url"],
                "https://test.keenetic.pro/rci/show/system",
            )
            self.assertEqual(
                state["last_connection_state"],
                "offline",
            )


    def test_empty_explicit_summary_is_recovered_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_root = root / "history"
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=history_root,
                logs_root=root / "logs",
            )
            history_root.mkdir(parents=True, exist_ok=True)
            history_path = (
                history_root
                / "connection_attempts_router_12.jsonl"
            )
            rows = [
                {
                    "timestamp": "2026-08-04T10:00:02+03:00",
                    "phase": "state_transition",
                    "endpoint": "connection",
                    "attempt": 0,
                    "success": None,
                    "message": "Роутер подтверждённо недоступен.",
                    "url": "https://test.keenetic.pro",
                    "connection_state": "offline",
                    "previous_state": "retrying",
                    "new_state": "offline",
                },
                {
                    "timestamp": "2026-08-04T10:00:01+03:00",
                    "phase": "rci",
                    "endpoint": "show/system",
                    "attempt": 2,
                    "success": False,
                    "status_code": 503,
                    "failure_kind": "keendns_http_503",
                    "message": "HTTP 503 при RCI-запросе.",
                    "url": (
                        "https://test.keenetic.pro/"
                        "rci/show/system"
                    ),
                    "connection_state": "retrying",
                },
            ]
            history_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in rows
                ),
                encoding="utf-8",
            )

            archive_path = service.create_archive(
                destination=root,
                router_id=12,
                connection_state={
                    "router_connected": False,
                    "last_result_message": "",
                    "last_failure_kind": None,
                },
            )
            with zipfile.ZipFile(archive_path) as archive:
                state = json.loads(
                    archive.read("connection_state.json")
                )
                attempts = json.loads(
                    archive.read("connection_attempts.json")
                )

            self.assertEqual(
                [item["timestamp"] for item in attempts],
                sorted(item["timestamp"] for item in attempts),
            )
            self.assertEqual(
                state["last_result_message"],
                "HTTP 503 при RCI-запросе.",
            )
            self.assertEqual(
                state["last_failure_kind"],
                "keendns_http_503",
            )
            self.assertEqual(
                state["last_connection_state"],
                "offline",
            )
            self.assertEqual(
                state["last_attempt_url"],
                "https://test.keenetic.pro",
            )



if __name__ == "__main__":
    unittest.main()
