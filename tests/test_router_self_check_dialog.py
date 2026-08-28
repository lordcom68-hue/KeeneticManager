from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.services.router_self_check_service import (  # noqa: E402
    RouterSelfCheckItem,
    RouterSelfCheckResult,
)
from ui.dialogs.router_self_check_dialog import (  # noqa: E402
    RouterSelfCheckDialog,
)


def sample_result() -> RouterSelfCheckResult:
    return RouterSelfCheckResult(
        schema_version=1,
        router_id=7,
        started_at="2026-08-16T12:00:00+03:00",
        finished_at="2026-08-16T12:00:02+03:00",
        overall_status="warning",
        overall_label="Есть предупреждения",
        duration_ms=2_000,
        model="KN-2311",
        version="5.01.C.3.0-1",
        connection_type="Локальная сеть",
        checks=(
            RouterSelfCheckItem(
                key="local_http",
                category="Подключение",
                title="Локальный HTTP",
                status="passed",
                summary="Авторизация и чтение RCI подтверждены.",
                duration_ms=250,
            ),
            RouterSelfCheckItem(
                key="keendns_https",
                category="Подключение",
                title="KeenDNS HTTPS",
                status="warning",
                summary="Адрес не отвечает.",
                duration_ms=1_500,
            ),
        ),
    )


class RouterSelfCheckDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_renders_summary_and_all_checks(self) -> None:
        dialog = RouterSelfCheckDialog(sample_result())
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertIn("предупреждений: 1", dialog.summary_label.text())
        self.assertEqual(dialog.table.item(0, 3).text(), "Локальный HTTP")
        self.assertEqual(dialog.table.item(1, 0).text(), "● Предупреждение")

    def test_dialog_can_switch_saved_history(self) -> None:
        older = sample_result()
        latest = RouterSelfCheckResult(
            schema_version=2,
            router_id=7,
            started_at="2026-08-16T13:00:00+03:00",
            finished_at="2026-08-16T13:00:01+03:00",
            overall_status="passed",
            overall_label="Проверка пройдена",
            duration_ms=1_000,
            model="KN-2311",
            version="5.01.C.3.0-1",
            connection_type="Локальная сеть",
            checks=(
                RouterSelfCheckItem(
                    key="keendns_https",
                    category="Подключение",
                    title="KeenDNS HTTPS",
                    status="passed",
                    summary="Канал отвечает.",
                    change="resolved",
                    previous_status="warning",
                ),
            ),
        )
        dialog = RouterSelfCheckDialog(
            latest,
            history=(older, latest),
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.history_combo.count(), 2)
        self.assertIn("Устранено: 1", dialog.comparison_label.text())
        self.assertEqual(dialog.table.item(0, 1).text(), "Устранено")
        dialog.history_combo.setCurrentIndex(1)
        self.assertIn("предупреждений: 1", dialog.summary_label.text())

    def test_copy_report_uses_anonymized_text(self) -> None:
        dialog = RouterSelfCheckDialog(sample_result())
        self.addCleanup(dialog.deleteLater)

        dialog.copy_report()
        report = QApplication.clipboard().text()

        self.assertIn("Keenetic Manager — самопроверка", report)
        self.assertIn("KN-2311", report)
        self.assertNotIn("192.168.", report)
        self.assertNotIn("пароль", report.casefold())

    def test_save_json_appends_extension_and_uses_portable_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report"
            dialog = RouterSelfCheckDialog(sample_result())
            self.addCleanup(dialog.deleteLater)

            with (
                patch(
                    "ui.dialogs.router_self_check_dialog.QFileDialog.getSaveFileName",
                    return_value=(str(target), ""),
                ),
                patch(
                    "ui.dialogs.router_self_check_dialog.QMessageBox.information"
                ),
            ):
                dialog.save_json()

            payload = json.loads(
                target.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["overall_status"], "warning")
            self.assertFalse(payload["privacy"]["contains_addresses"])


if __name__ == "__main__":
    unittest.main()
