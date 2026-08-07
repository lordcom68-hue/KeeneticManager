from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
