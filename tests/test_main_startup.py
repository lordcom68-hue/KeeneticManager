from __future__ import annotations

import unittest
from unittest.mock import patch

import main


class MainStartupTests(unittest.TestCase):
    @patch("main.apply_application_theme")
    @patch("main.install_window_icon_manager")
    @patch("main.MainWindow")
    @patch("main.QIcon")
    @patch("main.QApplication")
    def test_main_window_always_starts_maximized(
        self,
        application_class,
        icon_class,
        main_window_class,
        install_icon_manager,
        apply_theme,
    ) -> None:
        application = application_class.return_value
        application.exec.return_value = 0
        window = main_window_class.return_value

        result = main.main()

        self.assertEqual(result, 0)
        apply_theme.assert_called_once_with(application)
        icon_class.assert_called_once_with(str(main.APP_ICON_PATH))
        application.setWindowIcon.assert_called_once_with(
            icon_class.return_value
        )
        install_icon_manager.assert_called_once_with(
            application,
            icon_class.return_value,
        )
        window.setWindowIcon.assert_called_once_with(
            icon_class.return_value
        )
        window.showMaximized.assert_called_once_with()
        window.show.assert_not_called()


if __name__ == "__main__":
    unittest.main()
