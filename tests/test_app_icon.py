from __future__ import annotations

import unittest
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImageReader
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ui.window_icon_manager import install_window_icon_manager


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_vector_and_windows_icons_are_valid(self) -> None:
        assets = Path(__file__).resolve().parents[1] / "assets"
        svg_path = assets / "keenetic_manager.svg"
        ico_path = assets / "keenetic_manager.ico"

        self.assertTrue(QSvgRenderer(str(svg_path)).isValid())
        self.assertTrue(ico_path.is_file())

        reader = QImageReader(str(ico_path))
        self.assertTrue(reader.canRead(), reader.errorString())
        self.assertFalse(reader.read().isNull())

    def test_all_dialog_windows_receive_application_icon(self) -> None:
        assets = Path(__file__).resolve().parents[1] / "assets"
        from PySide6.QtGui import QIcon

        icon = QIcon(str(assets / "keenetic_manager.ico"))
        manager = install_window_icon_manager(self.app, icon)
        try:
            for window in (QDialog(), QMessageBox()):
                window.setWindowIcon(QIcon())
                window.show()
                self.app.processEvents()
                self.assertFalse(window.windowIcon().isNull())
                window.close()
        finally:
            self.app.removeEventFilter(manager)
            manager.deleteLater()


if __name__ == "__main__":
    unittest.main()
