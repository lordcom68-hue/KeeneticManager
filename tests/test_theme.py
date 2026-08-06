import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.theme import (  # noqa: E402
    APPLICATION_STYLESHEET,
    COLORS,
    apply_application_theme,
)
from ui.widgets.status_badge import StatusBadge  # noqa: E402


class ApplicationThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        apply_application_theme(cls.app)

    def test_palette_uses_project_colors(self) -> None:
        palette = self.app.palette()

        self.assertEqual(
            palette.color(QPalette.ColorRole.Window).name(),
            COLORS["window"],
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Text).name(),
            COLORS["text"],
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Link).name(),
            COLORS["accent"],
        )

    def test_global_styles_cover_core_control_families(self) -> None:
        for selector in (
            "QPushButton",
            "QLineEdit",
            "QTabWidget::pane",
            "QScrollBar:vertical",
            "QTableWidget",
            "QMenu",
            "QStatusBar",
            "QPushButton#dangerButton",
        ):
            self.assertIn(selector, APPLICATION_STYLESHEET)

    def test_status_badge_uses_semantic_flat_colors(self) -> None:
        badge = StatusBadge("Подключено", "connected")
        self.assertIn("#34d399", badge.dot.styleSheet())

        badge.set_state("warning")
        self.assertIn("#fbbf24", badge.dot.styleSheet())

        badge.set_state("disconnected")
        self.assertIn("#fb7185", badge.dot.styleSheet())

        badge.set_state("unknown")
        self.assertIn("#64748b", badge.dot.styleSheet())


if __name__ == "__main__":
    unittest.main()
