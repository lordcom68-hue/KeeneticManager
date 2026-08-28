from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import main


class MainStartupTests(unittest.TestCase):
    @patch("main.apply_application_theme")
    @patch("main.install_window_icon_manager")
    @patch("main.MainWindow")
    @patch("main.QIcon")
    @patch("main.QTimer.singleShot")
    @patch("main.QApplication")
    def test_main_window_always_starts_maximized(
        self,
        application_class,
        single_shot,
        icon_class,
        main_window_class,
        install_icon_manager,
        apply_theme,
    ) -> None:
        application = application_class.return_value
        application.exec.return_value = 0
        window = main_window_class.return_value

        with patch("main.get_crash_report_service") as service_factory:
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
        single_shot.assert_not_called()
        service_factory.return_value.install.assert_called_once()
        service_factory.return_value.uninstall.assert_called_once_with()

    @patch("main.apply_application_theme")
    @patch("main.install_window_icon_manager")
    @patch("main.MainWindow")
    @patch("main.QIcon")
    @patch("main.QTimer.singleShot")
    @patch("main.QApplication")
    def test_release_smoke_mode_closes_through_normal_window_path(
        self,
        application_class,
        single_shot,
        _icon_class,
        main_window_class,
        _install_icon_manager,
        _apply_theme,
    ) -> None:
        application_class.return_value.exec.return_value = 0
        window = main_window_class.return_value

        with (
            patch("main.get_crash_report_service") as service_factory,
            patch.dict(
                os.environ,
                {"KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS": "750"},
            ),
        ):
            self.assertEqual(main.main(), 0)

        single_shot.assert_called_once_with(750, window.close)
        service_factory.return_value.uninstall.assert_called_once_with()

    def test_release_smoke_timeout_is_bounded_and_optional(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(main.release_smoke_test_timeout_ms())
        with patch.dict(
            os.environ,
            {"KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS": "10"},
        ):
            self.assertEqual(main.release_smoke_test_timeout_ms(), 250)
        with patch.dict(
            os.environ,
            {"KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS": "50000"},
        ):
            self.assertEqual(main.release_smoke_test_timeout_ms(), 15_000)


if __name__ == "__main__":
    unittest.main()
