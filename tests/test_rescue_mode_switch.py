from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.services.rescue_mode_service import (
    RESCUED_MODE,
    RESCUER_MODE,
    RescueModeService,
)
from ui.main_window import MainWindow
from ui.widgets.rescue_mode_switch import RescueModeSwitch


class RescueModeSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = RescueModeSwitch()

    def tearDown(self) -> None:
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    @staticmethod
    def context(
        *,
        wisp_enabled: bool,
        wifi_enabled: bool,
    ):
        return RescueModeService.inspect(
            [
                {
                    "interface_id": "WifiMaster0/WifiStation0",
                    "name": "WISP",
                    "enabled": wisp_enabled,
                    "wireless_isp_settings_supported": True,
                }
            ],
            [
                {
                    "network_type": "wifi",
                    "name": "1234",
                    "enabled": wifi_enabled,
                    "bands": ["2.4 ГГц"],
                    "interfaces": [
                        "WifiMaster0/AccessPoint0"
                    ],
                    "mws_profile_id": "wlan0",
                }
            ],
        )

    def test_active_rescuer_is_visually_marked(self) -> None:
        self.widget.set_context(
            self.context(
                wisp_enabled=False,
                wifi_enabled=True,
            )
        )

        self.assertEqual(
            self.widget.rescuer_button.property("modeActive"),
            True,
        )
        self.assertEqual(
            self.widget.rescued_button.property("modeActive"),
            False,
        )
        self.assertIn("Спасатель", self.widget.status_label.text())

    def test_mixed_state_leaves_both_actions_available(self) -> None:
        self.widget.set_context(
            self.context(
                wisp_enabled=True,
                wifi_enabled=True,
            )
        )

        self.assertTrue(self.widget.rescuer_button.isEnabled())
        self.assertTrue(self.widget.rescued_button.isEnabled())
        self.assertIn("Смешанный", self.widget.status_label.text())

    def test_buttons_emit_expected_mode(self) -> None:
        modes: list[str] = []
        self.widget.mode_requested.connect(modes.append)
        self.widget.set_context(
            self.context(
                wisp_enabled=True,
                wifi_enabled=True,
            )
        )

        self.widget.rescuer_button.click()
        self.widget.rescued_button.click()

        self.assertEqual(modes, [RESCUER_MODE, RESCUED_MODE])

    def test_busy_state_disables_both_buttons(self) -> None:
        self.widget.set_context(
            self.context(
                wisp_enabled=True,
                wifi_enabled=True,
            )
        )
        self.widget.set_busy(RESCUED_MODE)

        self.assertFalse(self.widget.rescuer_button.isEnabled())
        self.assertFalse(self.widget.rescued_button.isEnabled())
        self.assertIn("Включение", self.widget.status_label.text())

    def test_success_result_reenables_buttons_in_confirmed_mode(self) -> None:
        initial_context = self.context(
            wisp_enabled=True,
            wifi_enabled=True,
        )
        result = SimpleNamespace(
            internet_connections=initial_context.wisp_connections,
            wifi_networks=initial_context.wifi_networks,
        )
        messages: list[str] = []
        harness = SimpleNamespace(
            pending_rescue_modes={7: RESCUER_MODE},
            get_selected_router_id=lambda: 7,
            connection_results={7: result},
            rescue_mode_switch=self.widget,
            statusBar=lambda: SimpleNamespace(
                showMessage=lambda message, *_args: messages.append(message)
            ),
        )
        self.widget.set_context(initial_context)
        self.widget.set_busy(RESCUER_MODE)

        MainWindow.rescue_mode_action_completed(
            harness,
            7,
            RESCUER_MODE,
            True,
            "Готово",
            {},
        )

        self.assertEqual(harness.pending_rescue_modes, {})
        self.assertTrue(self.widget.rescuer_button.isEnabled())
        self.assertTrue(self.widget.rescued_button.isEnabled())
        self.assertTrue(
            self.widget.rescuer_button.property("modeActive")
        )
        self.assertEqual(messages, ["Готово"])

    def test_failure_result_restores_previous_mode_and_buttons(self) -> None:
        initial_context = self.context(
            wisp_enabled=False,
            wifi_enabled=True,
        )
        result = SimpleNamespace(
            internet_connections=initial_context.wisp_connections,
            wifi_networks=initial_context.wifi_networks,
        )
        harness = SimpleNamespace(
            pending_rescue_modes={7: RESCUED_MODE},
            get_selected_router_id=lambda: 7,
            connection_results={7: result},
            rescue_mode_switch=self.widget,
        )
        self.widget.set_context(initial_context)
        self.widget.set_busy(RESCUED_MODE)

        with patch("ui.main_window.QMessageBox.warning") as warning:
            MainWindow.rescue_mode_action_completed(
                harness,
                7,
                RESCUED_MODE,
                False,
                "Переход не выполнен",
                {},
            )

        self.assertEqual(harness.pending_rescue_modes, {})
        self.assertTrue(self.widget.rescuer_button.isEnabled())
        self.assertTrue(self.widget.rescued_button.isEnabled())
        self.assertTrue(
            self.widget.rescuer_button.property("modeActive")
        )
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
