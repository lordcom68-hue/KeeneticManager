from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.refresh_button import (  # noqa: E402
    AddToolButton,
    RefreshToolButton,
)


class RefreshToolButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_glyph_uses_logical_button_coordinates(self) -> None:
        button = RefreshToolButton(glyph_size=20)
        button.setFixedSize(34, 34)

        self.assertEqual(button.glyph_size, 20)
        self.assertEqual(button.glyph_rect(), QRectF(7, 7, 20, 20))
        self.assertEqual(button.text(), "")

    def test_add_glyph_size_is_dpi_independent(self) -> None:
        button = AddToolButton(glyph_size=18)
        button.setFixedSize(34, 34)

        self.assertEqual(button.glyph_size, 18)
        self.assertEqual(button.text(), "")


if __name__ == "__main__":
    unittest.main()
