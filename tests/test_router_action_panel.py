from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.widgets.router_action_panel import (  # noqa: E402
    RouterMonitorActionPanel,
)


class RouterMonitorActionPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = RouterMonitorActionPanel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_disconnected_state_offers_only_connect(self) -> None:
        self.panel.set_state("disconnected")

        self.assertEqual(
            self.panel.status_label.text(),
            "Мониторинг не запущен",
        )
        self.assertFalse(self.panel.connect_button.isHidden())
        self.assertTrue(self.panel.disconnect_button.isHidden())
        self.assertEqual(
            self.panel.connect_button.text(),
            "Подключиться",
        )

    def test_connected_state_is_status_not_disabled_button(self) -> None:
        self.panel.set_state("connected")

        self.assertEqual(
            self.panel.status_label.text(),
            "Мониторинг активен",
        )
        self.assertTrue(self.panel.connect_button.isHidden())
        self.assertFalse(self.panel.disconnect_button.isHidden())
        self.assertEqual(
            self.panel.disconnect_button.text(),
            "Отключить",
        )
        self.assertIn(
            "#34D399".lower(),
            self.panel.status_dot.styleSheet().lower(),
        )

    def test_connection_progress_offers_stop(self) -> None:
        for state, title in (
            ("connecting", "Подключение…"),
            ("retrying", "Повторная попытка…"),
            ("unstable", "Связь нестабильна"),
            ("reconnecting", "Переподключение…"),
            (
                "authentication_required",
                "Требуется авторизация",
            ),
        ):
            with self.subTest(state=state):
                self.panel.set_state(state)
                self.assertEqual(
                    self.panel.status_label.text(),
                    title,
                )
                self.assertTrue(
                    self.panel.connect_button.isHidden()
                )
                self.assertFalse(
                    self.panel.disconnect_button.isHidden()
                )
                self.assertEqual(
                    self.panel.disconnect_button.text(),
                    "Остановить",
                )

    def test_stopping_state_has_no_competing_action(self) -> None:
        self.panel.set_state("stopping")

        self.assertEqual(
            self.panel.status_label.text(),
            "Остановка мониторинга…",
        )
        self.assertTrue(self.panel.connect_button.isHidden())
        self.assertTrue(self.panel.disconnect_button.isHidden())

    def test_active_and_stopping_monitors_both_block_management(self) -> None:
        fake_window = SimpleNamespace(
            router_monitors={1: object()},
            stopping_router_monitors={2: object()},
        )

        self.assertTrue(
            MainWindow._router_is_busy(fake_window, 1)
        )
        self.assertTrue(
            MainWindow._router_is_busy(fake_window, 2)
        )
        self.assertFalse(
            MainWindow._router_is_busy(fake_window, 3)
        )
        self.assertFalse(
            MainWindow._router_is_busy(fake_window, None)
        )

    def test_monitor_state_survives_switching_between_routers(self) -> None:
        fake_window = SimpleNamespace(
            router_monitors={
                1: object(),
                2: object(),
                3: object(),
                6: object(),
            },
            stopping_router_monitors={4: object()},
            last_monitor_results={
                1: SimpleNamespace(success=True),
                2: SimpleNamespace(
                    success=False,
                    transient_failure_suppressed=True,
                    connection_state="retrying",
                ),
                3: SimpleNamespace(
                    success=False,
                    transient_failure_suppressed=False,
                    connection_state="offline",
                ),
                6: SimpleNamespace(
                    success=False,
                    transient_failure_suppressed=False,
                    connection_state="authentication_required",
                ),
            },
        )

        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 1),
            "connected",
        )
        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 2),
            "retrying",
        )
        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 3),
            "reconnecting",
        )
        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 4),
            "stopping",
        )
        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 5),
            "disconnected",
        )
        self.assertEqual(
            MainWindow.router_monitor_ui_state(fake_window, 6),
            "authentication_required",
        )


if __name__ == "__main__":
    unittest.main()
