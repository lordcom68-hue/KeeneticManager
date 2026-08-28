from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.rci import DiagnosticCollector
from core.services.diagnostics_service import DiagnosticsService
from core.services.firmware_inspection_service import FirmwareInspectionService
from tests.test_firmware_inspection_service import build_uimage


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
            (root / "logs" / "crash_report.jsonl").write_text(
                '{"context":"background_thread"}\n',
                encoding="utf-8",
            )
            (root / "logs" / "fatal_python.log").write_text(
                "fatal handler enabled\n",
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
                    "compatibility_profile.json",
                    "firmware_catalog_summary.json",
                    "router_self_check_summary.json",
                    "router_self_check_history.json",
                }
                self.assertFalse(required - names)
                self.assertIn(
                    "logs/operation_history_router_12.jsonl",
                    names,
                )
                self.assertIn("logs/crash_report.jsonl", names)
                self.assertIn("logs/fatal_python.log", names)

                router_record = archive.read(
                    "router_record.json"
                ).decode("utf-8")
                self.assertNotIn("must-not-leak", router_record)
                self.assertIn("[REDACTED]", router_record)
                firmware_summary = json.loads(
                    archive.read("firmware_catalog_summary.json")
                )
                self.assertFalse(
                    firmware_summary["privacy"]["contains_local_paths"]
                )

    def test_archive_contains_anonymized_router_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history"
            history.mkdir()
            (history / "self_check_router_12.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "overall_status": "warning",
                        "checks": [
                            {
                                "key": "keendns_https",
                                "status": "warning",
                                "summary": "Адрес не отвечает.",
                            }
                        ],
                        "privacy": {
                            "contains_addresses": False,
                            "contains_credentials": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (history / "self_check_history_router_12.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "results": [{"overall_status": "warning"}],
                        "privacy": {"contains_addresses": False},
                    }
                ),
                encoding="utf-8",
            )
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=history,
                logs_root=root / "logs",
            )

            archive_path = service.create_offline_archive(
                destination=root,
                router_id=12,
            )
            with zipfile.ZipFile(archive_path) as archive:
                summary = json.loads(
                    archive.read("router_self_check_summary.json")
                )
                saved_history = json.loads(
                    archive.read("router_self_check_history.json")
                )

            self.assertEqual(summary["overall_status"], "warning")
            self.assertFalse(summary["privacy"]["contains_addresses"])
            self.assertEqual(len(saved_history["results"]), 1)
            self.assertFalse(
                saved_history["privacy"]["contains_addresses"]
            )

    def test_clear_local_data_removes_saved_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history"
            history.mkdir()
            self_check = history / "self_check_router_12.json"
            self_check.write_text("{}", encoding="utf-8")
            self_check_history = history / "self_check_history_router_12.json"
            self_check_history.write_text("{}", encoding="utf-8")
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=history,
                logs_root=root / "logs",
            )

            service.clear_local_data(12)

            self.assertFalse(self_check.exists())
            self.assertFalse(self_check_history.exists())

    def test_offline_archive_contains_last_compatibility_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=root / "history",
                logs_root=root / "logs",
            )
            archive_path = service.create_offline_archive(
                destination=root,
                router_id=12,
                last_result=SimpleNamespace(
                    success=False,
                    compatibility_profile={
                        "level": "limited",
                        "level_label": "Ограниченная поддержка",
                        "features": [
                            {
                                "key": "wifi_management",
                                "state": "unavailable",
                            }
                        ],
                    },
                ),
            )

            with zipfile.ZipFile(archive_path) as archive:
                profile = json.loads(
                    archive.read("compatibility_profile.json")
                )

        self.assertEqual(profile["level"], "limited")
        self.assertEqual(
            profile["features"][0]["key"],
            "wifi_management",
        )

    def test_archive_firmware_summary_omits_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_path = root / "private" / "candidate.bin"
            firmware_path.parent.mkdir()
            firmware_path.write_bytes(build_uimage(ported=False))
            service = DiagnosticsService(
                database=FakeDatabase(),  # type: ignore[arg-type]
                history_root=root / "history",
                logs_root=root / "logs",
            )
            service.firmware_catalog_service.upsert_inspection(
                FirmwareInspectionService.inspect(firmware_path)
            )

            archive_path = service.create_offline_archive(
                destination=root,
                router_id=12,
            )

            with zipfile.ZipFile(archive_path) as archive:
                summary = json.loads(
                    archive.read("firmware_catalog_summary.json")
                )
            encoded = json.dumps(summary, ensure_ascii=False)

            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["records"][0]["file_name"], "candidate.bin")
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("file_path", encoded)

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
