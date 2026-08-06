from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialogButtonBox,
    QMessageBox,
)

from ui.dialogs.wifi_network_dialog import (  # noqa: E402
    WiFiNetworkDialog,
)
from ui.message_boxes import ask_yes_no  # noqa: E402
from ui.widgets.client_card import ClientCard  # noqa: E402
from ui.widgets.wifi_settings_tab import (  # noqa: E402
    WiFiSettingsNetworkCard,
    WiFiSettingsTab,
)
from ui.widgets.wifi_network_card import WifiNetworkCard  # noqa: E402


class WiFiNetworkDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_create_shows_only_bands_with_free_slots(self) -> None:
        dialog = WiFiNetworkDialog(
            capabilities={
                "supported_bands": ["2.4", "5"],
                "available_slots": {
                    "2.4": ["WifiMaster0/AccessPoint2"],
                    "5": [],
                },
            }
        )
        try:
            self.assertFalse(dialog.band_checks["2.4"].isHidden())
            self.assertTrue(dialog.band_checks["2.4"].isChecked())
            self.assertTrue(dialog.band_checks["5"].isHidden())
            self.assertEqual(dialog.settings()["bands"], ["2.4"])
            back_button = dialog.buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            )
            self.assertEqual(back_button.text(), "Назад")
        finally:
            dialog.close()

    def test_edit_keeps_current_band_and_offers_only_free_band(self) -> None:
        dialog = WiFiNetworkDialog(
            network={
                "ssid": "Current",
                "band_states": {"2.4": {}},
                "mws_profile_id": "wlan1",
                "mws_band_editable": True,
            },
            capabilities={
                "supported_bands": ["2.4", "5", "6"],
                "available_slots": {
                    "2.4": [],
                    "5": ["WifiMaster1/AccessPoint2"],
                    "6": [],
                },
            },
        )
        try:
            self.assertFalse(dialog.band_checks["2.4"].isHidden())
            self.assertTrue(dialog.band_checks["2.4"].isEnabled())
            self.assertTrue(dialog.band_checks["2.4"].isChecked())
            self.assertFalse(dialog.band_checks["5"].isHidden())
            self.assertTrue(dialog.band_checks["5"].isEnabled())
            self.assertFalse(dialog.band_checks["5"].isChecked())
            self.assertTrue(dialog.band_checks["6"].isHidden())
        finally:
            dialog.close()

    def test_create_has_no_band_choice_when_all_slots_are_used(self) -> None:
        dialog = WiFiNetworkDialog(
            capabilities={
                "supported_bands": ["2.4", "5"],
                "available_slots": {"2.4": [], "5": []},
            }
        )
        try:
            self.assertTrue(dialog.band_checks["2.4"].isHidden())
            self.assertTrue(dialog.band_checks["5"].isHidden())
            ok_button = dialog.buttons.button(
                QDialogButtonBox.StandardButton.Ok
            )
            self.assertTrue(ok_button.isEnabled())
            self.assertEqual(dialog.settings()["bands"], [])
            self.assertFalse(dialog.settings()["enabled"])
        finally:
            dialog.close()

    def test_create_button_supports_mws_and_legacy_modes(self) -> None:
        tab = WiFiSettingsTab()
        try:
            tab.set_capabilities(
                {
                    "supported_bands": ["2.4", "5"],
                    "available_slots": {"2.4": [], "5": []},
                    "mws_supported": True,
                }
            )
            self.assertTrue(tab.create_button.isEnabled())
            self.assertIn(
                "без диапазона",
                tab.create_button.toolTip(),
            )

            tab.set_capabilities(
                {
                    "supported_bands": ["2.4"],
                    "available_slots": {
                        "2.4": ["WifiMaster0/AccessPoint2"]
                    },
                    "mws_supported": False,
                    "legacy_supported": True,
                    "bandless_supported": False,
                }
            )
            self.assertTrue(tab.create_button.isEnabled())
            self.assertIn("Legacy", tab.create_button.toolTip())

            tab.set_capabilities(
                {
                    "supported_bands": ["2.4"],
                    "available_slots": {"2.4": []},
                    "mws_supported": False,
                    "legacy_supported": True,
                    "bandless_supported": False,
                }
            )
            self.assertFalse(tab.create_button.isEnabled())
        finally:
            tab.close()

    def test_bandless_profile_button_opens_band_assignment(self) -> None:
        network = {
            "ssid": "Dormant",
            "mws_profile_id": "wlan2",
            "interfaces": [],
            "band_states": {},
            "enabled": False,
            "editable": True,
        }
        card = WiFiSettingsNetworkCard(network)
        emitted: list[dict] = []
        card.edit_requested.connect(lambda value: emitted.append(value))
        try:
            self.assertTrue(card.toggle_button.isEnabled())
            self.assertEqual(
                card.toggle_button.text(),
                "Назначить диапазон",
            )
            card.toggle_button.click()
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0]["mws_profile_id"], "wlan2")
        finally:
            card.close()

    def test_delete_button_stays_visible_on_wifi_settings_card(self) -> None:
        card = WiFiSettingsNetworkCard(
            {
                "ssid": "Main",
                "network_type": "wifi",
                "interfaces": ["WifiMaster0/AccessPoint0"],
                "deletable": False,
            }
        )
        try:
            self.assertFalse(card.delete_button.isHidden())
            self.assertFalse(card.delete_button.isEnabled())
            self.assertIn("нельзя удалить", card.delete_button.toolTip())

            card.update_network(
                {
                    "ssid": "Additional",
                    "network_type": "wifi",
                    "interfaces": ["WifiMaster0/AccessPoint2"],
                    "deletable": True,
                }
            )
            self.assertFalse(card.delete_button.isHidden())
            self.assertTrue(card.delete_button.isEnabled())
            self.assertEqual(card.delete_button.toolTip(), "")
        finally:
            card.close()

    def test_delete_button_is_hidden_on_monitoring_wifi_card(self) -> None:
        card = WifiNetworkCard(
            {
                "ssid": "Main",
                "network_type": "wifi",
                "interfaces": ["WifiMaster0/AccessPoint0"],
                "clients": [],
                "deletable": False,
            }
        )
        try:
            self.assertTrue(card.delete_button.isHidden())
            self.assertFalse(card.delete_button.isEnabled())

            card.update_network(
                {
                    "ssid": "Additional",
                    "network_type": "wifi",
                    "interfaces": ["WifiMaster0/AccessPoint2"],
                    "clients": [],
                    "deletable": True,
                }
            )
            self.assertTrue(card.delete_button.isHidden())
            self.assertFalse(card.delete_button.isEnabled())
        finally:
            card.close()

    def test_wifi_client_cards_keep_their_positions_on_refresh(self) -> None:
        clients = [
            {
                "mac": "00:00:00:00:10:01",
                "name": "Первый",
                "connection_kind": "wifi",
                "traffic_active": False,
            },
            {
                "mac": "00:00:00:00:10:02",
                "name": "Второй",
                "connection_kind": "wifi",
                "traffic_active": True,
            },
        ]
        network = {
            "_router_id": 98765,
            "ssid": "Stable order test",
            "network_type": "wifi",
            "interfaces": ["WifiMaster0/AccessPoint9"],
            "clients": clients,
        }
        card = WifiNetworkCard(network)
        try:
            first_cards = dict(card.client_cards)

            updated_clients = [
                {**clients[1], "traffic_active": False},
                {**clients[0], "traffic_active": True},
            ]
            card.update_network(
                {**network, "clients": updated_clients}
            )

            visible_order = []
            for index in range(card.clients_layout.count()):
                widget = card.clients_layout.itemAt(index).widget()
                if isinstance(widget, ClientCard):
                    visible_order.append(widget.client_key)

            self.assertEqual(
                visible_order,
                [
                    ClientCard.make_client_key(client)
                    for client in clients
                ],
            )
            self.assertTrue(
                all(
                    card.client_cards[key] is old_card
                    for key, old_card in first_cards.items()
                )
            )
        finally:
            card.close()

    def test_yes_no_confirmation_is_localized(self) -> None:
        observed: dict[str, str] = {}

        def fake_exec(message_box: QMessageBox) -> int:
            yes_button = message_box.button(
                QMessageBox.StandardButton.Yes
            )
            no_button = message_box.button(
                QMessageBox.StandardButton.No
            )
            observed["yes"] = yes_button.text()
            observed["no"] = no_button.text()
            return int(QMessageBox.StandardButton.No)

        with patch.object(QMessageBox, "exec", fake_exec):
            result = ask_yes_no(None, "Проверка", "Продолжить?")

        self.assertEqual(result, QMessageBox.StandardButton.No)
        self.assertEqual(observed, {"yes": "Да", "no": "Нет"})


if __name__ == "__main__":
    unittest.main()
