from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QStackedLayout,
    QTabWidget,
    QWidget,
)

from ui.main_window import MainWindow  # noqa: E402
from ui.controllers.router_connection_controller import (  # noqa: E402
    RouterConnectionController,
)
from ui.controllers.settings_tab_controller import (  # noqa: E402
    SettingsTabController,
)


class FakeItem:
    def __init__(self, router_id: int) -> None:
        self.router_id = router_id

    def data(self, _role) -> int:
        return self.router_id


class MainTabOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_follow_clients_and_journal_is_last(self) -> None:
        self.assertEqual(
            MainWindow.TAB_TITLES,
            (
                "Мониторинг",
                "Интернет",
                "Wi-Fi",
                "Клиенты",
                "Настройки",
                "Журнал",
            ),
        )

    def test_journal_is_rendered_as_right_corner_tab(self) -> None:
        tabs = QTabWidget()
        titles = list(MainWindow.TAB_TITLES)
        for title in titles:
            tabs.addTab(QWidget(), title)

        class Harness:
            tab_titles = titles

            def __init__(self) -> None:
                self.tabs = tabs

            configure_journal_corner_tab = (
                MainWindow.configure_journal_corner_tab
            )
            open_journal_tab = MainWindow.open_journal_tab

        harness = Harness()
        harness.configure_journal_corner_tab()
        tabs.resize(1000, 400)
        tabs.show()
        self.app.processEvents()

        self.assertFalse(
            tabs.isTabVisible(harness.journal_tab_index)
        )
        self.assertIsNotNone(tabs.cornerWidget())
        self.assertEqual(
            harness.journal_tab_button.text(),
            "Журнал",
        )
        button_right = (
            harness.journal_tab_button.mapTo(
                tabs,
                harness.journal_tab_button.rect().topLeft(),
            ).x()
            + harness.journal_tab_button.width()
        )
        self.assertGreaterEqual(button_right, tabs.width() - 2)

        harness.journal_tab_button.click()
        self.assertEqual(
            tabs.currentIndex(),
            harness.journal_tab_index,
        )
        self.assertTrue(
            harness.journal_tab_button.isChecked()
        )
        tabs.close()


class FakeDatabase:
    def __init__(self, routers: dict[int, dict]) -> None:
        self.routers = routers

    def get_router(self, router_id: int):
        return self.routers.get(router_id)


class FakeTab:
    def __init__(self) -> None:
        self.router_ids: list[int] = []

    def set_router(self, router_id: int) -> None:
        self.router_ids.append(router_id)


class LabelRecorder:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setToolTip(self, text: str) -> None:
        self.tooltip = text


class AddressRecorder:
    def __init__(self) -> None:
        self.addresses: tuple[object, object] | None = None

    def set_addresses(self, *, local_ip, keendns) -> None:
        self.addresses = (local_ip, keendns)


class ReparentTrackingWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.was_detached = False

    def setParent(self, parent, *args) -> None:  # type: ignore[no-untyped-def]
        if parent is None:
            self.was_detached = True
        super().setParent(parent, *args)


class RemovalTrackingTabWidget(QTabWidget):
    def __init__(self) -> None:
        super().__init__()
        self.remove_calls = 0

    def removeTab(self, index: int) -> None:
        self.remove_calls += 1
        super().removeTab(index)


class RouterSwitchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_selection_harness(self, result):
        calls: dict[str, list] = {
            "information": [],
            "result": [],
            "state": [],
            "developer": [],
        }
        harness = SimpleNamespace(
            ROUTER_ID_ROLE=Qt.ItemDataRole.UserRole,
            database=FakeDatabase(
                {
                    2: {
                        "id": 2,
                        "name": "Второй",
                    }
                }
            ),
            connection_results=(
                {2: result} if result is not None else {}
            ),
            internet_tab=FakeTab(),
            wifi_tab=FakeTab(),
            show_router_information=(
                lambda router, **kwargs: calls[
                    "information"
                ].append((router, kwargs))
            ),
            show_connection_result=(
                lambda selected_result: calls["result"].append(
                    selected_result
                )
            ),
            sync_developer_mode_for_selected_router=(
                lambda: calls["developer"].append(True)
            ),
            router_monitor_ui_state=lambda _router_id: "connected",
            set_router_monitor_ui_state=(
                lambda state: calls["state"].append(state)
            ),
            clear_router_information=lambda: None,
        )
        return harness, calls

    def test_connected_to_connected_preserves_live_pages(self) -> None:
        result = SimpleNamespace(success=True)
        harness, calls = self.make_selection_harness(result)

        controller = object.__new__(RouterConnectionController)
        controller.window = harness
        controller.router_selected(
            FakeItem(2),
            FakeItem(1),
        )

        self.assertEqual(len(calls["information"]), 1)
        self.assertTrue(
            calls["information"][0][1][
                "preserve_live_content"
            ]
        )
        self.assertEqual(calls["result"], [result])
        self.assertEqual(harness.internet_tab.router_ids, [2])
        self.assertEqual(harness.wifi_tab.router_ids, [2])

    def test_switch_to_offline_router_still_requests_full_clear(self) -> None:
        harness, calls = self.make_selection_harness(None)

        controller = object.__new__(RouterConnectionController)
        controller.window = harness
        controller.router_selected(
            FakeItem(2),
            FakeItem(1),
        )

        self.assertFalse(
            calls["information"][0][1][
                "preserve_live_content"
            ]
        )
        self.assertEqual(calls["result"], [])

    def test_preserved_information_does_not_call_clear_router_state(
        self,
    ) -> None:
        clear_calls: list[bool] = []
        harness = SimpleNamespace(
            connection_results={
                2: SimpleNamespace(success=True)
            },
            router_reachability={},
            router_title=LabelRecorder(),
            status_label=LabelRecorder(),
            router_address_links=AddressRecorder(),
            connected_address_label=LabelRecorder(),
            clear_router_state=lambda: clear_calls.append(True),
        )
        router = {
            "id": 2,
            "name": "Второй",
            "local_ip": "192.168.2.1",
            "keendns": None,
        }

        MainWindow.show_router_information(
            harness,
            router,
            preserve_live_content=True,
        )

        self.assertEqual(clear_calls, [])
        self.assertEqual(harness.router_title.text, "Второй")
        self.assertEqual(harness.router_title.tooltip, "Второй")
        self.assertEqual(harness.status_label.text, "● Online")

    def test_tab_pages_are_never_detached_into_windows(self) -> None:
        tabs = RemovalTrackingTabWidget()
        connected = [
            ReparentTrackingWidget()
            for _index in range(3)
        ]
        disconnected = [
            ReparentTrackingWidget()
            for _index in range(2)
        ] + [connected[-1]]
        containers: list[QWidget] = []
        stacks: list[QStackedLayout] = []
        for title, connected_page, disconnected_page in zip(
            ("Мониторинг", "Интернет", "Настройки"),
            connected,
            disconnected, strict=False,
        ):
            container = QWidget()
            stack = QStackedLayout(container)
            stack.addWidget(connected_page)
            if disconnected_page is not connected_page:
                stack.addWidget(disconnected_page)
            stack.setCurrentWidget(disconnected_page)
            containers.append(container)
            stacks.append(stack)
            tabs.addTab(container, title)

        harness = SimpleNamespace(
            tabs=tabs,
            tab_page_containers=containers,
            tab_page_stacks=stacks,
            connected_tab_widgets=connected,
            disconnected_tab_widgets=disconnected,
            settings_tab=connected[-1],
            tab_titles=("Мониторинг", "Интернет", "Настройки"),
            router_content_is_connected=False,
        )

        MainWindow.set_router_content_connected(harness, True)

        self.assertEqual(tabs.count(), 3)
        self.assertEqual(tabs.remove_calls, 0)
        self.assertEqual(
            [tabs.widget(index) for index in range(tabs.count())],
            containers,
        )
        self.assertTrue(tabs.updatesEnabled())
        self.assertTrue(harness.router_content_is_connected)
        self.assertTrue(
            all(
                stack.currentWidget() is widget
                for stack, widget in zip(stacks, connected, strict=False)
            )
        )
        self.assertTrue(all(not widget.was_detached for widget in connected))
        self.assertTrue(all(not widget.isWindow() for widget in connected))

        MainWindow.set_router_content_connected(harness, False)

        self.assertEqual(tabs.count(), 3)
        self.assertEqual(tabs.remove_calls, 0)
        self.assertTrue(
            all(
                stack.currentWidget() is widget
                for stack, widget in zip(stacks, disconnected, strict=False)
            )
        )
        self.assertTrue(
            all(not widget.isWindow() for widget in disconnected)
        )

    def test_offline_settings_keep_only_diagnostic_card_actions(self) -> None:
        online_content = QWidget()
        clear_button = QPushButton()
        harness = SimpleNamespace(
            settings_online_content=online_content,
            clear_diagnostic_button=clear_button,
        )

        controller = SettingsTabController(harness)
        controller.update_settings_connection_state(False)

        self.assertTrue(online_content.isHidden())
        self.assertTrue(clear_button.isHidden())

        controller.update_settings_connection_state(True)

        self.assertFalse(online_content.isHidden())
        self.assertFalse(clear_button.isHidden())

    def test_settings_profile_describes_supported_management_mode(self) -> None:
        label = QLabel()
        harness = SimpleNamespace(
            settings_compatibility_label=label,
        )

        SettingsTabController(harness).update_settings_router_profile(
            SimpleNamespace(
                router_capabilities={
                    "wifi_management_mode": "legacy",
                }
            ),
        )

        self.assertIn("legacy", label.text())
        self.assertEqual(label.property("supportMode"), "legacy")

    def test_settings_profile_renders_full_compatibility_matrix(self) -> None:
        label = QLabel()
        feature_list = QLabel()
        harness = SimpleNamespace(
            settings_compatibility_label=label,
            settings_capability_list=feature_list,
        )

        SettingsTabController(harness).update_settings_router_profile(
            SimpleNamespace(
                compatibility_profile={
                    "level": "limited",
                    "level_label": "Ограниченная поддержка",
                    "summary": "Изменение Wi-Fi отключено.",
                    "release": "5.00.C.12.0-0",
                    "features": [
                        {
                            "key": "monitoring",
                            "label": "Мониторинг устройства",
                            "state": "supported",
                            "state_label": "Поддерживается",
                            "detail": "Безопасный просмотр.",
                        },
                        {
                            "key": "wifi_management",
                            "label": "Управление Wi-Fi",
                            "state": "unavailable",
                            "state_label": "Недоступно",
                            "detail": "Только просмотр.",
                        },
                    ],
                }
            ),
        )

        self.assertIn("Ограниченная поддержка", label.text())
        self.assertEqual(label.property("supportMode"), "limited")
        self.assertIn("Мониторинг устройства", feature_list.text())
        self.assertIn("Управление Wi-Fi — Недоступно", feature_list.text())

    def test_background_update_keeps_user_selected_update_channel(self) -> None:
        combo = QComboBox()
        status = QLabel()
        selected_router_id = 8
        harness = SimpleNamespace(
            router_update_channel_combo=combo,
            router_update_status_label=status,
            get_selected_router_id=lambda: selected_router_id,
        )
        controller = SettingsTabController(harness)
        info = {
            "channel": "stable",
            "message": "Доступна KeeneticOS 5.1.4.",
            "channels": [
                {"name": "stable", "label": "Основной", "version": "5.1.4"},
                {"name": "draft", "label": "Тестовый", "version": "5.2"},
            ],
        }

        controller._show_router_update_info(info)
        combo.setCurrentIndex(combo.findData("draft"))
        controller._show_router_update_info(info)

        self.assertEqual(combo.currentData(), "draft")
        self.assertEqual(status.text(), "Доступна KeeneticOS 5.1.4.")

    def test_router_connection_automatically_loads_update_channels(self) -> None:
        class UpdateMonitor:
            def __init__(self) -> None:
                self.requests = 0

            def request_router_update_check(self) -> None:
                self.requests += 1

        monitor = UpdateMonitor()
        combo = QComboBox()
        combo.addItem("Основной — стабильные выпуски", "stable")
        harness = SimpleNamespace(
            router_update_channel_combo=combo,
            router_update_status_label=QLabel(),
            router_update_version_label=QLabel(),
            router_update_check_button=QPushButton(),
            router_update_apply_channel_button=QPushButton(),
            router_update_install_button=QPushButton(),
            router_monitors={8: monitor},
            connection_results={},
            get_selected_router_id=lambda: 8,
            router_is_fully_connected=lambda _router_id: True,
        )
        controller = SettingsTabController(harness)
        result = SimpleNamespace(
            version="5.01.C.4.0-1",
            version_data={"title": "5.1.4"},
        )

        controller.sync_router_update_for_selected_router(result)
        controller.sync_router_update_for_selected_router(result)

        self.assertEqual(monitor.requests, 1)
        self.assertEqual(combo.count(), 1)
        self.assertEqual(combo.currentData(), "")
        self.assertEqual(combo.currentText(), "Получение каналов...")
        self.assertFalse(harness.router_update_channel_combo.isEnabled())

    def test_reconnect_confirms_installed_router_update_version(self) -> None:
        combo = QComboBox()
        combo.addItem("Основной — 5.01.C.4.0-1", "stable")
        harness = SimpleNamespace(
            router_update_channel_combo=combo,
            router_update_status_label=QLabel(),
            router_update_version_label=QLabel(),
            router_update_check_button=QPushButton(),
            router_update_apply_channel_button=QPushButton(),
            router_update_install_button=QPushButton(),
            router_monitors={},
            connection_results={},
            get_selected_router_id=lambda: 8,
            router_is_fully_connected=lambda _router_id: True,
        )
        controller = SettingsTabController(harness)
        controller.router_update_info[8] = {
            "supported": True,
            "channel": "stable",
            "installed_version": "5.1.1",
            "available_version": "5.1.4",
            "update_available": False,
            "message": "Ожидается перезагрузка роутера.",
            "channels": [
                {
                    "name": "stable",
                    "label": "Основной",
                    "version": "5.01.C.4.0-1",
                }
            ],
        }
        controller.router_update_expected_versions[8] = "5.01.C.4.0-1"

        controller.sync_router_update_for_selected_router(
            SimpleNamespace(
                version="5.01.C.4.0-1",
                version_data={"title": "5.1.4"},
            )
        )

        self.assertNotIn(8, controller.router_update_expected_versions)
        self.assertEqual(
            controller.router_update_info[8]["installed_version"],
            "5.1.4",
        )
        self.assertIn(
            "Обновление завершено",
            harness.router_update_status_label.text(),
        )

    def test_compatibility_feature_state_drives_unsupported_controls(self) -> None:
        result = SimpleNamespace(
            compatibility_profile={
                "features": [
                    {
                        "key": "rescue_mode",
                        "state": "unavailable",
                    }
                ]
            }
        )

        state = MainWindow.compatibility_feature_state(
            result,
            "rescue_mode",
        )

        self.assertEqual(state, "unavailable")


if __name__ == "__main__":
    unittest.main()
