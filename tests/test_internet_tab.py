from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.internet_tab import (  # noqa: E402
    InternetManagementItem,
    InternetTab,
)


class InternetTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_other_connections_is_direct_action_without_vpn_submenu(
        self,
    ) -> None:
        tab = InternetTab()
        emitted: list[tuple[str, str]] = []
        tab.connection_creation_requested.connect(
            lambda type_id, title: emitted.append((type_id, title))
        )

        try:
            actions = {
                action.text(): action
                for action in tab.create_connection_menu.actions()
            }
            self.assertEqual(
                list(actions),
                [
                    "Кабельный Интернет",
                    "Wireless ISP",
                    "Мобильное подключение",
                    "Другие подключения",
                ],
            )

            other = actions["Другие подключения"]
            self.assertIsNone(other.menu())
            self.assertEqual(other.data(), "other.connections")
            other.trigger()
            self.assertEqual(
                emitted,
                [("other.connections", "Другие подключения")],
            )
        finally:
            tab.close()

    def test_inactive_vpn_delete_button_is_hidden(self) -> None:
        item = InternetManagementItem(
            {
                "interface_id": "OpenVPN0",
                "name": "Рабочий VPN",
                "type": "OpenVPN",
                "enabled": True,
                "delete_supported": False,
            }
        )
        try:
            self.assertTrue(item.delete_button.isHidden())
        finally:
            item.close()

    def test_supported_vpn_delete_button_remains_available(self) -> None:
        item = InternetManagementItem(
            {
                "interface_id": "WireGuard0",
                "name": "WireGuard",
                "type": "WireGuard",
                "enabled": True,
                "delete_supported": True,
            }
        )
        try:
            self.assertFalse(item.delete_button.isHidden())
            self.assertTrue(item.delete_button.isEnabled())
        finally:
            item.close()

    def test_inactive_non_vpn_delete_button_stays_visible(self) -> None:
        item = InternetManagementItem(
            {
                "interface_id": "GigabitEthernet0/Vlan2",
                "name": "Кабельный Интернет",
                "type": "IPoE",
                "enabled": True,
                "delete_supported": False,
            }
        )
        try:
            self.assertFalse(item.delete_button.isHidden())
            self.assertFalse(item.delete_button.isEnabled())
        finally:
            item.close()


if __name__ == "__main__":
    unittest.main()
