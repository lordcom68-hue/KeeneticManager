from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from core.services.journal_event_analyzer import (  # noqa: E402
    JournalEventAnalyzer,
)
from ui.widgets.journal_tab import JournalTab  # noqa: E402


class JournalTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def entries() -> list[dict[str, str]]:
        return [
            {
                "id": "1",
                "timestamp": "Aug 6 10:00:00",
                "level": "info",
                "level_label": "Информация",
                "source": "ndm",
                "message": "Router started",
            },
            {
                "id": "2",
                "timestamp": "Aug 6 10:01:00",
                "level": "error",
                "level_label": "Ошибка",
                "source": "kernel",
                "message": "Link failed",
            },
            {
                "id": "3",
                "timestamp": "Aug 6 10:02:00",
                "level": "warning",
                "level_label": "Предупреждение",
                "source": "ndm",
                "message": "Signal is weak",
            },
        ]

    def test_initial_refresh_is_requested_once_per_router(self) -> None:
        tab = JournalTab()
        requested: list[int] = []
        tab.refresh_requested.connect(requested.append)

        tab.set_router(7)
        self.assertTrue(tab.needs_initial_load())
        tab.request_refresh()

        self.assertEqual(requested, [500])
        self.assertFalse(tab.needs_initial_load())

        tab.set_entries(self.entries())
        self.assertFalse(tab.needs_initial_load())
        tab.set_router(8)
        self.assertTrue(tab.needs_initial_load())

    def test_filters_by_level_source_and_text(self) -> None:
        tab = JournalTab()
        tab.set_router(7)
        tab.set_entries(self.entries())

        tab.level_combo.setCurrentIndex(
            tab.level_combo.findData("errors")
        )
        self.assertEqual(len(tab.visible_entries), 1)
        self.assertEqual(tab.visible_entries[0]["source"], "kernel")

        tab.level_combo.setCurrentIndex(0)
        tab.source_combo.setCurrentIndex(
            tab.source_combo.findData("ndm")
        )
        tab.search_input.setText("signal")
        self.assertEqual(len(tab.visible_entries), 1)
        self.assertEqual(tab.visible_entries[0]["level"], "warning")

    def test_summary_and_table_keep_all_entries(self) -> None:
        tab = JournalTab()
        tab.set_entries(self.entries())

        self.assertEqual(tab.table.rowCount(), 3)
        self.assertEqual(
            tab.summary_chips["errors"].value_label.text(),
            "1",
        )
        self.assertEqual(
            tab.summary_chips["warnings"].value_label.text(),
            "1",
        )

    def test_new_application_event_marks_loaded_journal_stale(self) -> None:
        tab = JournalTab()
        tab.set_router(7)
        tab.set_entries(self.entries())

        tab.mark_stale()

        self.assertTrue(tab.needs_initial_load())
        self.assertIn("новые события", tab.status_label.text())

    def test_category_and_period_filters_are_applied(self) -> None:
        now = datetime.now().astimezone()
        tab = JournalTab()
        tab.set_entries(
            [
                {
                    "id": "recent",
                    "timestamp": (now - timedelta(minutes=5)).isoformat(),
                    "level": "error",
                    "source": "coalagent",
                    "message": "Network unreachable.",
                },
                {
                    "id": "old",
                    "timestamp": (now - timedelta(days=2)).isoformat(),
                    "level": "warning",
                    "source": "kernel",
                    "message": "Ethernet link is down.",
                },
            ]
        )

        tab.category_combo.setCurrentIndex(
            tab.category_combo.findData("connectivity")
        )
        tab.period_combo.setCurrentIndex(
            tab.period_combo.findData(60 * 60)
        )

        self.assertEqual(len(tab.visible_entries), 1)
        self.assertEqual(tab.visible_entries[0]["id"], "recent")

    def test_diagnostic_archive_contains_only_visible_explained_entries(self) -> None:
        tab = JournalTab()
        tab.set_router(12)
        tab.set_entries(self.entries())
        tab.level_combo.setCurrentIndex(
            tab.level_combo.findData("errors")
        )

        with tempfile.TemporaryDirectory() as directory:
            path = tab.write_diagnostic_archive(
                Path(directory) / "journal.zip"
            )
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"journal.csv", "journal.json", "summary.json"},
                )
                entries = json.loads(
                    archive.read("journal.json").decode("utf-8")
                )
                summary = json.loads(
                    archive.read("summary.json").decode("utf-8")
                )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "2")
        self.assertEqual(summary["router_id"], 12)
        self.assertEqual(summary["filters"]["level"], "errors")

    def test_unsupported_router_log_disables_router_limit_only(self) -> None:
        tab = JournalTab()
        tab.set_entries(
            [
                {
                    "timestamp": "2026-08-07T20:00:00+03:00",
                    "level": "info",
                    "source": "Keenetic Manager",
                    "message": "Операция завершена.",
                    "application_event": True,
                    "router_log_supported": False,
                }
            ]
        )

        self.assertFalse(tab.limit_combo.isEnabled())
        self.assertTrue(tab.refresh_button.isEnabled())
        self.assertIn("недоступен", tab.subtitle_label.text())

    def test_large_journal_is_rendered_in_event_loop_chunks(self) -> None:
        tab = JournalTab()
        tab.set_router(7)
        tab.set_loading(True)
        entries = [
            {
                "id": str(index),
                "timestamp": f"2026-08-16T10:{index // 60:02d}:{index % 60:02d}+03:00",
                "level": "info",
                "level_label": "Информация",
                "source": "ndm",
                "message": f"Journal event {index}",
            }
            for index in range(500)
        ]
        callbacks: list[object] = []

        with patch(
            "ui.widgets.journal_tab.QTimer.singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            tab.set_entries(entries, message="Журнал обновлён.")

            self.assertEqual(tab.table.rowCount(), 500)
            self.assertIsNone(tab.table.item(499, 0))
            self.assertFalse(tab.refresh_button.isEnabled())
            self.assertTrue(callbacks)

            callback_index = 0
            while callback_index < len(callbacks):
                callback = callbacks[callback_index]
                callback_index += 1
                self.assertTrue(callable(callback))
                callback()

        self.assertIsNotNone(tab.table.item(499, 0))
        self.assertTrue(tab.refresh_button.isEnabled())
        self.assertEqual(tab.status_label.text(), "Журнал обновлён.")
        self.assertEqual(
            tab.table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Interactive,
        )

    def test_worker_analyzed_entries_are_not_analyzed_twice(self) -> None:
        tab = JournalTab()
        analyzed = JournalEventAnalyzer.analyze(self.entries())

        with patch.object(
            JournalEventAnalyzer,
            "analyze",
            side_effect=AssertionError("unexpected repeated analysis"),
        ):
            tab.set_entries(analyzed)

        self.assertEqual(tab.table.rowCount(), 3)

    def test_large_journal_search_is_debounced(self) -> None:
        tab = JournalTab()
        tab._entries = [
            {"message": f"event {index}"}
            for index in range(tab.ASYNC_RENDER_THRESHOLD + 1)
        ]

        with (
            patch.object(tab._filter_timer, "start") as start,
            patch.object(tab, "apply_filter") as apply_filter,
        ):
            tab.search_input.setText("event")
            tab.search_input.setText("event 1")

        self.assertEqual(start.call_count, 2)
        apply_filter.assert_not_called()

    def test_search_text_is_cached_when_entries_are_loaded(self) -> None:
        tab = JournalTab()
        entries = JournalEventAnalyzer.analyze(self.entries())

        with patch.object(
            tab,
            "_entry_search_text",
            wraps=tab._entry_search_text,
        ) as build_search_text:
            tab.set_entries(entries)
            initial_calls = build_search_text.call_count
            tab.apply_filter()
            tab.apply_filter()

        self.assertEqual(initial_calls, len(entries))
        self.assertEqual(build_search_text.call_count, initial_calls)


if __name__ == "__main__":
    unittest.main()
