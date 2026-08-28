from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.crash_report_service import CrashReportService


class CrashReportServiceTests(unittest.TestCase):
    def create_service(self, directory: str) -> CrashReportService:
        root = Path(directory)
        return CrashReportService(
            path=root / "crash_report.jsonl",
            fatal_path=root / "fatal_python.log",
        )

    def test_exception_is_sanitized_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.create_service(directory)
            error = RuntimeError(
                "request failed for https://admin:secret@example.test/"
            )

            path = service.record_exception(
                error,
                context="unit_test",
                details={"password": "secret"},
            )

            self.assertIsNotNone(path)
            payload = json.loads(
                Path(path).read_text(encoding="utf-8").splitlines()[0]
            )
            rendered = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("admin:secret@", rendered)
            self.assertNotIn('"password": "secret"', rendered)
            self.assertEqual(payload["context"], "unit_test")
            self.assertEqual(payload["exception_type"], "RuntimeError")

    def test_log_is_rotated_before_limit_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.create_service(directory)
            service.MAX_LOG_BYTES = 250
            service.path.parent.mkdir(parents=True, exist_ok=True)
            service.path.write_text("x" * 240, encoding="utf-8")

            service.record_exception(
                RuntimeError("rotated"),
                context="rotation_test",
            )

            self.assertTrue(
                service.path.with_suffix(".jsonl.1").is_file()
            )
            self.assertIn(
                "rotation_test",
                service.path.read_text(encoding="utf-8"),
            )

    def test_install_captures_sys_hook_and_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.create_service(directory)
            notifications: list[str] = []

            with patch.object(
                sys,
                "excepthook",
                lambda *_arguments: None,
            ):
                previous = sys.excepthook
                service.install(
                    notify=notifications.append,
                    enable_faulthandler=False,
                )
                try:
                    error = RuntimeError("captured")
                    sys.excepthook(RuntimeError, error, None)
                finally:
                    service.uninstall()

                self.assertIs(sys.excepthook, previous)
            self.assertEqual(len(notifications), 1)
            self.assertIn("captured", service.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
