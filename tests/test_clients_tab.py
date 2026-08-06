from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QToolButton,
    QWidget,
)

from ui.widgets.client_card import ClientCard  # noqa: E402
from ui.widgets.clients_tab import ClientsTab  # noqa: E402


class OrphanToolButtonWatch(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.shown: list[QToolButton] = []

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QToolButton)
            and watched.isWindow()
            and watched.parentWidget() is None
        ):
            self.shown.append(watched)
        return False


class ClientsTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def networks() -> list[dict]:
        return [
            {
                "network_type": "wifi",
                "name": "Home",
                "ssid": "Home",
                "band": "5 ГГц",
                "channel": 44,
                "clients": [
                    {
                        "connection_kind": "wifi",
                        "mac": "00:00:00:00:00:01",
                        "name": "Phone",
                        "ip": "192.168.1.20",
                        "registered": True,
                        "traffic_active": True,
                        "txrate": 866,
                        "rxrate": 433,
                        "rssi": -54,
                    },
                    {
                        "connection_kind": "wifi",
                        "mac": "00:00:00:00:00:02",
                        "name": "Tablet",
                        "ip": "192.168.1.3",
                        "registered": False,
                        "traffic_active": False,
                    },
                ],
            },
            {
                "network_type": "wired",
                "name": "Проводные подключения",
                "clients": [
                    {
                        "connection_kind": "wired",
                        "mac": "00:00:00:00:00:03",
                        "name": "Desktop",
                        "ip": "192.168.1.10",
                        "registered": True,
                        "traffic_active": False,
                        "interface": "GigabitEthernet0/1",
                        "port_label": "Порт 1",
                        "interface_enabled": True,
                        "txrate": 1000,
                        "rxrate": 1000,
                    }
                ],
            },
        ]

    def test_collects_clients_and_inherits_network_metadata(self) -> None:
        clients = ClientsTab.collect_clients(self.networks())
        by_name = {client["name"]: client for client in clients}

        self.assertEqual(len(clients), 3)
        self.assertEqual(by_name["Phone"]["ssid"], "Home")
        self.assertEqual(by_name["Phone"]["band"], "5 ГГц")
        self.assertEqual(by_name["Phone"]["channel"], 44)
        self.assertEqual(
            by_name["Desktop"]["connection_kind"],
            "wired",
        )

    def test_duplicate_mac_prefers_wifi_connection(self) -> None:
        networks = self.networks()
        networks[1]["clients"].append(
            {
                "connection_kind": "wired",
                "mac": "00:00:00:00:00:01",
                "name": "Stale wired copy",
                "ip": "192.168.1.20",
            }
        )

        clients = ClientsTab.collect_clients(networks)
        duplicate = next(
            client
            for client in clients
            if client["mac"] == "00:00:00:00:00:01"
        )
        self.assertEqual(len(clients), 3)
        self.assertEqual(duplicate["connection_kind"], "wifi")
        self.assertEqual(duplicate["name"], "Phone")

    def test_summary_search_filters_and_ip_sort(self) -> None:
        tab = ClientsTab()
        try:
            tab.set_networks(self.networks())
            self.assertEqual(
                tab.summary_chips["total"].value_label.text(),
                "3",
            )
            self.assertEqual(
                tab.summary_chips["wifi"].value_label.text(),
                "2",
            )
            self.assertEqual(
                tab.summary_chips["wired"].value_label.text(),
                "1",
            )

            tab.search_input.setText("tablet")
            self.assertEqual(
                [item["name"] for item in tab.filtered_clients()],
                ["Tablet"],
            )

            tab.search_input.clear()
            tab.filter_combo.setCurrentIndex(
                tab.filter_combo.findData("unregistered")
            )
            self.assertEqual(
                [item["name"] for item in tab.filtered_clients()],
                ["Tablet"],
            )

            tab.filter_combo.setCurrentIndex(
                tab.filter_combo.findData("all")
            )
            tab.sort_combo.setCurrentIndex(
                tab.sort_combo.findData("ip")
            )
            self.assertEqual(
                [item["name"] for item in tab.filtered_clients()],
                ["Tablet", "Desktop", "Phone"],
            )
        finally:
            tab.close()

    def test_summary_chips_are_quick_filters_and_reset_view(self) -> None:
        tab = ClientsTab()
        try:
            tab.set_networks(self.networks())
            tab.summary_chips["wifi"].activated.emit("wifi")

            self.assertEqual(tab.filter_combo.currentData(), "wifi")
            self.assertEqual(
                [client["name"] for client in tab.filtered_clients()],
                ["Phone", "Tablet"],
            )
            self.assertTrue(
                bool(tab.summary_chips["wifi"].property("active"))
            )
            self.assertEqual(
                tab.visible_count_label.text(),
                "Показано: 2 из 3",
            )
            self.assertTrue(tab.reset_view_button.isEnabled())

            tab.search_input.setText("phone")
            tab.sort_combo.setCurrentIndex(
                tab.sort_combo.findData("name")
            )
            tab.reset_view_button.click()

            self.assertEqual(tab.search_input.text(), "")
            self.assertEqual(tab.filter_combo.currentData(), "all")
            self.assertEqual(tab.sort_combo.currentData(), "stable")
            self.assertFalse(tab.reset_view_button.isEnabled())
        finally:
            tab.close()

    def test_csv_export_contains_current_client_fields(self) -> None:
        clients = ClientsTab.collect_clients(self.networks())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "clients.csv"
            ClientsTab.write_clients_csv(destination, clients)
            content = destination.read_text(encoding="utf-8-sig")

        self.assertIn("Имя;Статус;Тип подключения", content)
        self.assertIn("Phone;В сети;Wi‑Fi;Home;5 ГГц", content)
        self.assertIn("Desktop;В сети;Ethernet", content)

    def test_cards_are_reused_and_expand_inline(self) -> None:
        tab = ClientsTab()
        try:
            tab.set_networks(self.networks())
            key = ClientCard.make_client_key(tab._clients[0])
            card = tab._cards[key]
            self.assertFalse(card.toggle_button.isHidden())
            if not card.toggle_button.isChecked():
                card.toggle_button.click()
                self.app.processEvents()
            self.assertFalse(card.extra_widget.isHidden())
            self.assertTrue(card.property_grid.has_items())

            updated = self.networks()
            updated[0]["clients"][0]["name"] = "New Phone"
            tab.set_networks(updated)

            self.assertIs(tab._cards[key], card)
            self.assertEqual(card.name_label.text(), "New Phone")
            self.assertFalse(card.extra_widget.isHidden())
            self.assertFalse(hasattr(tab, "details_panel"))
        finally:
            tab.close()

    def test_selected_client_actions_survive_refresh(self) -> None:
        tab = ClientsTab()
        messages: list[str] = []
        tab.message_requested.connect(messages.append)
        try:
            tab.set_networks(self.networks())
            selected = tab._clients[0]
            key = ClientCard.make_client_key(selected)
            card = tab._cards[key]
            card.client_selected.emit(dict(selected))

            self.assertTrue(bool(card.property("selected")))
            self.assertIn("Phone", tab.selected_client_label.text())
            self.assertTrue(tab.copy_selected_ip_button.isEnabled())
            self.assertTrue(tab.copy_selected_mac_button.isEnabled())

            tab.copy_selected_ip_button.click()
            self.assertEqual(
                QApplication.clipboard().text(),
                "192.168.1.20",
            )
            tab.copy_selected_mac_button.click()
            self.assertEqual(
                QApplication.clipboard().text(),
                "00:00:00:00:00:01",
            )
            tab.copy_selected_all_button.click()
            copied = QApplication.clipboard().text()
            self.assertIn("Имя: Phone", copied)
            self.assertIn("IP-адрес: 192.168.1.20", copied)
            self.assertIn("MAC-адрес: 00:00:00:00:00:01", copied)

            updated = [dict(client) for client in reversed(tab._clients)]
            next(
                client
                for client in updated
                if ClientCard.make_client_key(client) == key
            )["name"] = "Phone renamed"
            tab.set_clients(updated)

            self.assertEqual(tab._selected_client_key, key)
            self.assertTrue(bool(tab._cards[key].property("selected")))
            self.assertIn(
                "Phone renamed",
                tab.selected_client_label.text(),
            )

            tab.clear_selected_button.click()
            self.assertIsNone(tab._selected_client_key)
            self.assertFalse(bool(tab._cards[key].property("selected")))
            self.assertFalse(tab.copy_selected_all_button.isEnabled())
            self.assertIn("Данные клиента скопированы.", messages)
        finally:
            tab.close()

    def test_compact_wifi_access_action_is_reversible_and_guarded(self) -> None:
        tab = ClientsTab()
        emitted: list[tuple[dict, bool]] = []
        tab.wifi_access_change_requested.connect(
            lambda client, allowed: emitted.append((client, allowed))
        )
        try:
            self.assertTrue(tab.selected_client_panel.isHidden())
            self.assertLessEqual(tab.summary_chips["total"].height(), 44)

            active = {
                "connection_kind": "wifi",
                "mac": "00:00:00:00:00:91",
                "name": "Phone",
                "ip": "192.168.1.91",
                "online": True,
                "ap": "WifiMaster1/AccessPoint0",
                "segment_id": "Bridge0",
                "wifi_blocked": False,
            }
            tab.set_clients([active])
            tab._select_client(active)
            self.assertFalse(tab.selected_client_panel.isHidden())
            self.assertTrue(tab.wifi_access_button.isEnabled())
            self.assertEqual(tab.wifi_access_button.text(), "Отключить Wi-Fi")
            tab.wifi_access_button.click()
            self.assertEqual(emitted[-1][1], False)

            blocked = dict(active, online=False, wifi_blocked=True, blocked=True)
            tab.set_clients([blocked])
            self.assertTrue(tab.wifi_access_button.isEnabled())
            self.assertEqual(tab.wifi_access_button.text(), "Разрешить Wi-Fi")
            tab.wifi_access_button.click()
            self.assertEqual(emitted[-1][1], True)

            protected = dict(active, management_client=True)
            tab.set_clients([protected])
            self.assertFalse(tab.wifi_access_button.isEnabled())
        finally:
            tab.close()

    def test_client_toggle_never_appears_as_a_separate_window(self) -> None:
        parent = QWidget()
        watch = OrphanToolButtonWatch()
        self.app.installEventFilter(watch)
        parent.show()
        self.app.processEvents()
        card = None
        try:
            card = ClientCard(
                self.networks()[0]["clients"][0],
                parent,
            )
            card.show()
            self.app.processEvents()

            self.assertEqual(watch.shown, [])
            self.assertIs(card.toggle_button.parentWidget(), card)
            self.assertFalse(card.toggle_button.isWindow())
        finally:
            self.app.removeEventFilter(watch)
            if card is not None:
                card.close()
            parent.close()

    def test_default_order_does_not_follow_refresh_order(self) -> None:
        tab = ClientsTab()
        try:
            initial = self.networks()[0]["clients"]
            tab.set_clients(initial)
            first_order = [
                ClientCard.make_client_key(client)
                for client in tab.filtered_clients()
            ]

            updated = [dict(client) for client in reversed(initial)]
            updated[0]["traffic_active"] = True
            updated[1]["traffic_active"] = False
            tab.set_clients(updated)

            self.assertEqual(
                [
                    ClientCard.make_client_key(client)
                    for client in tab.filtered_clients()
                ],
                first_order,
            )
            self.assertEqual(
                tab.sort_combo.currentData(),
                "stable",
            )
        finally:
            tab.close()

    def test_wired_port_action_is_relayed(self) -> None:
        tab = ClientsTab()
        emitted: list[tuple[object, str, bool]] = []
        tab.interface_state_change_requested.connect(
            lambda interfaces, name, enabled: emitted.append(
                (interfaces, name, enabled)
            )
        )
        try:
            tab.set_networks(self.networks())
            wired = next(
                client
                for client in tab._clients
                if client["connection_kind"] == "wired"
            )
            card = tab._cards[ClientCard.make_client_key(wired)]
            self.assertFalse(card.port_button.isHidden())
            card.port_button.click()
            self.assertEqual(
                emitted,
                [(["GigabitEthernet0/1"], "Desktop", False)],
            )
        finally:
            tab.close()

    def test_inventory_supports_online_offline_and_blocked_filters(self) -> None:
        tab = ClientsTab()
        try:
            tab.set_clients(
                [
                    {
                        "mac": "00:00:00:00:00:31",
                        "name": "Online",
                        "connection_kind": "wifi",
                        "online": True,
                    },
                    {
                        "mac": "00:00:00:00:00:32",
                        "name": "Offline",
                        "connection_kind": "unknown",
                        "online": False,
                        "last_seen": "вчера",
                    },
                    {
                        "mac": "00:00:00:00:00:33",
                        "name": "Blocked",
                        "connection_kind": "wired",
                        "online": False,
                        "blocked": True,
                    },
                ]
            )

            self.assertEqual(
                tab.summary_chips["online"].value_label.text(),
                "1",
            )
            self.assertEqual(
                tab.summary_chips["offline"].value_label.text(),
                "2",
            )
            self.assertEqual(
                tab.summary_chips["blocked"].value_label.text(),
                "1",
            )

            tab.filter_combo.setCurrentIndex(
                tab.filter_combo.findData("offline")
            )
            self.assertEqual(
                [item["name"] for item in tab.filtered_clients()],
                ["Offline", "Blocked"],
            )

            tab.filter_combo.setCurrentIndex(
                tab.filter_combo.findData("blocked")
            )
            self.assertEqual(
                [item["name"] for item in tab.filtered_clients()],
                ["Blocked"],
            )
        finally:
            tab.close()

    def test_offline_client_has_no_port_action(self) -> None:
        tab = ClientsTab()
        try:
            tab.set_clients(
                [
                    {
                        "mac": "00:00:00:00:00:41",
                        "name": "Old cable client",
                        "connection_kind": "wired",
                        "online": False,
                        "interface": "GigabitEthernet0/1",
                    }
                ]
            )
            card = next(iter(tab._cards.values()))
            self.assertTrue(card.port_button.isHidden())
            self.assertEqual(card.badge.text(), "Ethernet")
        finally:
            tab.close()

    def test_disabled_offline_port_can_be_enabled_again(self) -> None:
        tab = ClientsTab()
        emitted: list[tuple[object, str, bool]] = []
        tab.interface_state_change_requested.connect(
            lambda interfaces, name, enabled: emitted.append(
                (interfaces, name, enabled)
            )
        )
        try:
            tab.set_clients(
                [
                    {
                        "mac": "00:00:00:00:00:43",
                        "name": "Disconnected desktop",
                        "connection_kind": "wired",
                        "online": False,
                        "interface": "GigabitEthernet0/2",
                        "interface_enabled": False,
                        "port_control_available": True,
                    }
                ]
            )
            card = next(iter(tab._cards.values()))
            self.assertFalse(card.port_button.isHidden())
            self.assertEqual(card.port_button.text(), "Включить порт")
            card.port_button.click()
            self.assertEqual(
                emitted,
                [
                    (
                        ["GigabitEthernet0/2"],
                        "Disconnected desktop",
                        True,
                    )
                ],
            )
        finally:
            tab.close()

    def test_management_client_port_cannot_be_disabled(self) -> None:
        card = ClientCard(
            {
                "mac": "00:00:00:00:00:44",
                "name": "This computer",
                "connection_kind": "wired",
                "online": True,
                "interface": "GigabitEthernet0/2",
                "interface_enabled": True,
                "management_client": True,
            },
            status_mode="online",
        )
        try:
            self.assertFalse(card.port_button.isHidden())
            self.assertFalse(card.port_button.isEnabled())
            self.assertEqual(card.port_button.text(), "Порт управления")
        finally:
            card.close()

    def test_disabled_port_recovery_is_available_without_client(self) -> None:
        tab = ClientsTab()
        emitted: list[tuple[object, str, bool]] = []
        tab.interface_state_change_requested.connect(
            lambda interfaces, name, enabled: emitted.append(
                (interfaces, name, enabled)
            )
        )
        try:
            tab.set_ports(
                [
                    {
                        "id": "GigabitEthernet0/1",
                        "interface_id": "GigabitEthernet0/1",
                        "kind": "ethernet",
                        "name": "LAN 2",
                        "physical_name": "LAN 2",
                        "enabled": False,
                    }
                ]
            )
            self.assertFalse(tab.disabled_ports_panel.isHidden())
            button = tab._port_buttons["GigabitEthernet0/1"]
            self.assertEqual(button.text(), "Включить LAN2")
            button.click()
            self.assertEqual(
                emitted,
                [(["GigabitEthernet0/1"], "LAN2", True)],
            )

            tab.set_ports(
                [
                    {
                        "id": "GigabitEthernet0/1",
                        "kind": "ethernet",
                        "enabled": True,
                    }
                ]
            )
            self.assertTrue(tab.disabled_ports_panel.isHidden())
        finally:
            tab.close()

    def test_unknown_badge_and_last_seen_are_readable(self) -> None:
        card = ClientCard(
            {
                "mac": "00:00:00:00:00:42",
                "name": "Old client",
                "connection_kind": "unknown",
                "online": False,
                "last_seen": 25,
            },
            inline_details=True,
            status_mode="online",
        )
        try:
            self.assertEqual(card.badge.text(), "Не определено")
            self.assertGreaterEqual(
                card.badge.maximumWidth(),
                card.badge.sizeHint().width(),
            )
            self.assertEqual(
                card.uptime_label.text(),
                "Последний раз: 25 сек. назад",
            )
        finally:
            card.close()

    def test_main_window_uses_real_clients_tab(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.clients_tab = ClientsTab()", source)
        self.assertIn("self.clients_tab.set_networks(", source)
        self.assertIn("self.clients_tab.set_clients(", source)
        self.assertIn("self.clients_tab.set_ports(", source)
        self.assertIn(
            "self.clients_tab.wifi_access_change_requested.connect(",
            source,
        )
        self.assertIn(
            "monitor.wifi_client_access_completed.connect(",
            source,
        )


if __name__ == "__main__":
    unittest.main()
