from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SettingsTabController:
    """Строит вкладку настроек и синхронизирует её состояние."""

    def __init__(self, window: Any) -> None:
        self.window = window
        self.router_update_info: dict[int, dict[str, Any]] = {}
        self.router_update_busy: dict[int, str] = {}
        self.router_update_auto_check_requested: set[int] = set()
        self.router_update_auto_check_in_progress: set[int] = set()
        self.router_update_expected_versions: dict[int, str] = {}
        self._router_update_combo_router_id: int | None = None

    def create_settings_tab(self) -> QWidget:
        page = QScrollArea()
        self.window.settings_scroll_area = page
        page.setObjectName("settingsScrollArea")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        page_layout = QVBoxLayout(content)
        page_layout.setContentsMargins(
            18,
            18,
            18,
            18,
        )
        page_layout.setSpacing(14)

        self.window.settings_online_content = QWidget()
        online_layout = QVBoxLayout(
            self.window.settings_online_content
        )
        online_layout.setContentsMargins(0, 0, 0, 0)
        online_layout.setSpacing(14)

        monitoring_card, monitoring_layout = (
            self.window.create_dashboard_card(
                "Обновление и доступность"
            )
        )
        monitoring_description = QLabel(
            "Управляет фоновым обновлением подключённых роутеров "
            "и независимой проверкой их доступности."
        )
        monitoring_description.setWordWrap(True)
        monitoring_layout.addWidget(monitoring_description)
        if hasattr(self.window, "left_panel_layout"):
            self.window.left_panel_layout.removeWidget(
                self.window.monitor_controls
            )
        monitoring_layout.addWidget(self.window.monitor_controls)
        online_layout.addWidget(monitoring_card)

        update_card, update_layout = self.window.create_dashboard_card(
            "Обновление KeeneticOS"
        )
        update_description = QLabel(
            "Проверка и установка обновлений выполняются через штатный "
            "менеджер компонентов роутера. Во время установки питание "
            "устройства отключать нельзя."
        )
        update_description.setWordWrap(True)
        self.window.router_update_version_label = QLabel(
            "Текущая версия: будет определена после подключения."
        )
        self.window.router_update_version_label.setObjectName("mutedLabel")

        channel_row = QWidget()
        channel_layout = QHBoxLayout(channel_row)
        channel_layout.setContentsMargins(0, 0, 0, 0)
        channel_layout.setSpacing(8)
        channel_label = QLabel("Канал обновления:")
        self.window.router_update_channel_combo = QComboBox()
        self.window.router_update_channel_combo.addItem(
            "Каналы будут получены после подключения",
            "",
        )
        self.window.router_update_channel_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.window.router_update_apply_channel_button = QPushButton(
            "Применить канал"
        )
        channel_layout.addWidget(channel_label)
        channel_layout.addWidget(self.window.router_update_channel_combo, 1)
        channel_layout.addWidget(
            self.window.router_update_apply_channel_button
        )

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.window.router_update_check_button = QPushButton(
            "Проверить обновления"
        )
        self.window.router_update_install_button = QPushButton(
            "Установить обновление"
        )
        self.window.router_update_install_button.setObjectName(
            "primaryButton"
        )
        action_layout.addWidget(self.window.router_update_check_button)
        action_layout.addWidget(self.window.router_update_install_button)
        action_layout.addStretch()

        self.window.router_update_status_label = QLabel(
            "Нажмите «Проверить обновления», чтобы получить состояние."
        )
        self.window.router_update_status_label.setObjectName("mutedLabel")
        self.window.router_update_status_label.setWordWrap(True)

        update_layout.addWidget(update_description)
        update_layout.addWidget(self.window.router_update_version_label)
        update_layout.addWidget(channel_row)
        update_layout.addWidget(action_row)
        update_layout.addWidget(self.window.router_update_status_label)
        online_layout.addWidget(update_card)

        compatibility_card, compatibility_layout = (
            self.window.create_dashboard_card(
                "Совместимость роутера"
            )
        )
        self.window.settings_compatibility_label = QLabel(
            "Профиль возможностей будет определён после подключения."
        )
        self.window.settings_compatibility_label.setObjectName(
            "settingsCompatibilityLabel"
        )
        self.window.settings_compatibility_label.setWordWrap(True)
        compatibility_layout.addWidget(
            self.window.settings_compatibility_label
        )
        self.window.settings_capability_list = QLabel(
            "Функции будут перечислены после подключения."
        )
        self.window.settings_capability_list.setObjectName(
            "settingsCapabilityList"
        )
        self.window.settings_capability_list.setWordWrap(True)
        self.window.settings_capability_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.window.settings_capability_list.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        compatibility_layout.addWidget(
            self.window.settings_capability_list
        )
        self.window.router_self_check_button = QPushButton(
            "Запустить самопроверку"
        )
        self.window.router_self_check_button.setToolTip(
            "Проверить подключения и получение данных без изменения роутера"
        )
        self.window.router_self_check_problem_button = QPushButton(
            "Повторить проблемные"
        )
        self.window.router_self_check_problem_button.setToolTip(
            "Повторить только проверки с предупреждениями или ошибками"
        )
        self.window.router_self_check_history_button = QPushButton(
            "История проверок"
        )
        self.window.router_self_check_history_button.setToolTip(
            "Открыть сохранённые результаты и сравнение изменений"
        )
        self.window.router_self_check_status_label = QLabel(
            "Самопроверка ещё не запускалась."
        )
        self.window.router_self_check_status_label.setObjectName("mutedLabel")
        self.window.router_self_check_status_label.setWordWrap(True)
        self.window.router_self_check_actions = QWidget()
        self_check_actions_layout = QHBoxLayout(
            self.window.router_self_check_actions
        )
        self_check_actions_layout.setContentsMargins(0, 0, 0, 0)
        self_check_actions_layout.setSpacing(8)
        self_check_actions_layout.addWidget(self.window.router_self_check_button)
        self_check_actions_layout.addWidget(
            self.window.router_self_check_problem_button
        )
        self_check_actions_layout.addWidget(
            self.window.router_self_check_history_button
        )
        self_check_actions_layout.addStretch()
        compatibility_layout.addWidget(self.window.router_self_check_actions)
        compatibility_layout.addWidget(self.window.router_self_check_status_label)
        online_layout.addWidget(compatibility_card)

        # Совместимое имя сохраняет прежний контракт интерфейса.
        self.window.settings_connected_content = self.window.settings_online_content

        self.window.developer_card, developer_layout = (
            self.window.create_dashboard_card(
                "Режим разработчика"
            )
        )

        description = QLabel(
            "Собирает безопасные RCI-снимки и журнал ошибок. "
            "Пароли, токены, ключи и другие секреты "
            "удаляются перед сохранением."
        )
        description.setWordWrap(True)

        self.window.developer_mode_checkbox = QCheckBox(
            "Включить режим разработчика"
        )
        self.window.developer_mode_checkbox.setChecked(False)

        self.window.create_diagnostic_button = QPushButton(
            "Создать диагностический архив"
        )
        self.window.clear_diagnostic_button = QPushButton(
            "Очистить диагностические данные"
        )
        self.window.inspect_firmware_button = QPushButton(
            "Анализировать файл прошивки"
        )
        self.window.inspect_firmware_button.setToolTip(
            "Прочитать локальный BIN-файл без изменения и отправки на роутер"
        )
        self.window.compare_firmwares_button = QPushButton(
            "Сравнить две прошивки"
        )
        self.window.compare_firmwares_button.setToolTip(
            "Сравнить модели, архитектуры, версии, целостность и RCI-признаки"
        )
        self.window.check_router_firmware_button = QPushButton(
            "Проверить прошивку для выбранного роутера"
        )
        self.window.check_router_firmware_button.setToolTip(
            "Сопоставить локальный BIN-файл с моделью и версией "
            "подключённого роутера"
        )
        self.window.firmware_catalog_button = QPushButton(
            "Открыть каталог прошивок"
        )
        self.window.firmware_catalog_button.setToolTip(
            "Открыть сохранённые метаданные и отчёты без копий BIN-файлов"
        )

        self.window.diagnostic_status_label = QLabel(
            "Режим разработчика для выбранного "
            "роутера выключен."
        )
        self.window.diagnostic_status_label.setWordWrap(True)

        developer_layout.addWidget(description)
        developer_layout.addWidget(
            self.window.developer_mode_checkbox
        )
        developer_layout.addWidget(
            self.window.create_diagnostic_button
        )
        developer_layout.addWidget(
            self.window.clear_diagnostic_button
        )
        developer_layout.addWidget(
            self.window.inspect_firmware_button
        )
        developer_layout.addWidget(
            self.window.compare_firmwares_button
        )
        developer_layout.addWidget(
            self.window.check_router_firmware_button
        )
        developer_layout.addWidget(
            self.window.firmware_catalog_button
        )
        developer_layout.addWidget(
            self.window.diagnostic_status_label
        )

        page_layout.addWidget(
            self.window.settings_online_content
        )
        page_layout.addWidget(
            self.window.developer_card
        )
        page_layout.addStretch()

        self.window.create_diagnostic_button.clicked.connect(
            self.window.create_diagnostic_archive
        )
        self.window.clear_diagnostic_button.clicked.connect(
            self.window.clear_diagnostic_data
        )
        self.window.inspect_firmware_button.clicked.connect(
            self.window.inspect_firmware
        )
        self.window.compare_firmwares_button.clicked.connect(
            self.window.compare_firmwares
        )
        self.window.check_router_firmware_button.clicked.connect(
            self.window.check_firmware_for_selected_router
        )
        self.window.firmware_catalog_button.clicked.connect(
            self.window.open_firmware_catalog
        )
        self.window.router_self_check_button.clicked.connect(
            self.window.router_self_check_controller.run_full
        )
        self.window.router_self_check_problem_button.clicked.connect(
            self.window.router_self_check_controller.run_problems
        )
        self.window.router_self_check_history_button.clicked.connect(
            self.window.router_self_check_controller.open_history
        )
        self.window.developer_mode_checkbox.toggled.connect(
            self.developer_mode_changed
        )
        self.window.router_update_channel_combo.currentIndexChanged.connect(
            self.update_router_update_controls
        )
        self.window.router_update_apply_channel_button.clicked.connect(
            self.apply_router_update_channel
        )
        self.window.router_update_check_button.clicked.connect(
            self.check_router_update
        )
        self.window.router_update_install_button.clicked.connect(
            self.install_router_update
        )

        self.sync_developer_mode_for_selected_router()
        page.setWidget(content)
        return page

    def update_settings_connection_state(
        self,
        connected: bool,
    ) -> None:
        """Показывает требующие подключения настройки только онлайн."""
        connected = bool(connected)
        if hasattr(self.window, "settings_online_content"):
            self.window.settings_online_content.setVisible(connected)
        if hasattr(self.window, "clear_diagnostic_button"):
            self.window.clear_diagnostic_button.setVisible(connected)
        if hasattr(self.window, "router_self_check_button"):
            self.window.router_self_check_controller.update_controls()
        self.update_router_update_controls()

    def update_settings_router_profile(self, result: object) -> None:
        self.sync_router_update_for_selected_router(result)
        if not hasattr(self.window, "settings_compatibility_label"):
            return
        profile = getattr(
            result,
            "compatibility_profile",
            None,
        )
        if isinstance(profile, dict) and profile.get("features"):
            level = str(profile.get("level") or "monitoring")
            level_label = str(
                profile.get("level_label") or "Только мониторинг"
            )
            summary = str(profile.get("summary") or "").strip()
            release = str(profile.get("release") or "").strip()
            cache_note = (
                " · профиль подтверждён из кэша"
                if profile.get("cache_hit")
                else ""
            )
            text = level_label
            if release:
                text += f" · KeeneticOS {release}"
            if summary:
                text += f"\n{summary}"
            text += cache_note
            self.window.settings_compatibility_label.setText(text)
            support_mode = level
            capability_lines: list[str] = []
            details: list[str] = []
            state_icons = {
                "supported": "●",
                "limited": "◐",
                "monitoring": "◉",
                "unavailable": "○",
                "unknown": "…",
            }
            for raw_feature in profile.get("features") or []:
                if not isinstance(raw_feature, dict):
                    continue
                state = str(raw_feature.get("state") or "unknown")
                label = str(raw_feature.get("label") or "Функция")
                state_label = str(
                    raw_feature.get("state_label") or "Определяется"
                )
                capability_lines.append(
                    f"{state_icons.get(state, '…')} {label} — {state_label}"
                )
                detail = str(raw_feature.get("detail") or "").strip()
                if detail:
                    details.append(f"{label}: {detail}")
            if hasattr(self.window, "settings_capability_list"):
                capability_text = (
                    "\n".join(capability_lines)
                    or "Список функций пока недоступен."
                )
                self.window.settings_capability_list.setText(capability_text)
                line_count = max(1, len(capability_text.splitlines()))
                self.window.settings_capability_list.setMinimumHeight(
                    self.window.settings_capability_list.fontMetrics().lineSpacing()
                    * line_count
                    + 24
                )
                self.window.settings_capability_list.setToolTip(
                    "\n".join(details)
                )
            self._apply_settings_compatibility_style(
                support_mode,
            )
            return

        capabilities = getattr(
            result,
            "router_capabilities",
            None,
        )
        if not isinstance(capabilities, dict):
            capabilities = {}
        mode = str(
            capabilities.get("wifi_management_mode") or "monitoring"
        ).strip().lower()
        if mode == "mws":
            text = (
                "Полная поддержка: мониторинг и управление Wi‑Fi "
                "через MWS."
            )
            support_mode = "supported"
        elif mode == "legacy":
            text = (
                "Совместимый legacy‑режим KeeneticOS 4: управление "
                "физическими точками доступа."
            )
            support_mode = "legacy"
        else:
            text = (
                "Ограниченная поддержка: мониторинг доступен, "
                "изменение Wi‑Fi для этой прошивки отключено."
            )
            support_mode = "monitoring"
        self.window.settings_compatibility_label.setText(text)
        self._apply_settings_compatibility_style(
            support_mode,
        )

    def _apply_settings_compatibility_style(
        self,
        support_mode: str,
    ) -> None:
        self.window.settings_compatibility_label.setProperty(
            "supportMode",
            support_mode,
        )
        self.window.settings_compatibility_label.style().unpolish(
            self.window.settings_compatibility_label
        )
        self.window.settings_compatibility_label.style().polish(
            self.window.settings_compatibility_label
        )

    def developer_mode_changed(
        self,
        enabled: bool,
    ) -> None:
        router_id = self.window.get_selected_router_id()

        if router_id is None:
            blocker = QSignalBlocker(
                self.window.developer_mode_checkbox
            )
            self.window.developer_mode_checkbox.setChecked(False)
            del blocker
            self.window.update_diagnostic_controls()
            return

        self.window.developer_mode_by_router[router_id] = bool(enabled)
        self.window.update_diagnostic_controls()

        connected = self.window.router_is_fully_connected(router_id)
        if enabled:
            self.window.diagnostic_status_label.setText(
                (
                    "Режим разработчика включён. Можно создать "
                    "диагностический архив текущего подключения."
                    if connected
                    else (
                        "Режим разработчика включён. Роутер сейчас "
                        "не подключён, поэтому будет создан "
                        "офлайн-архив с локальными журналами и "
                        "историей попыток подключения."
                    )
                )
            )
        else:
            self.window.diagnostic_status_label.setText(
                "Режим разработчика для выбранного роутера выключен."
            )

        self.window.statusBar().showMessage(
            (
                f"Режим разработчика включён для роутера {router_id}"
                if enabled
                else (
                    f"Режим разработчика выключен для роутера {router_id}"
                )
            ),
            3000,
        )

    def sync_developer_mode_for_selected_router(
        self,
    ) -> None:
        router_id = self.window.get_selected_router_id()
        selected = router_id is not None
        connected = self.window.router_is_fully_connected(router_id)
        enabled = bool(
            selected
            and self.window.developer_mode_by_router.get(
                router_id,
                False,
            )
        )

        blocker = QSignalBlocker(
            self.window.developer_mode_checkbox
        )
        self.window.developer_mode_checkbox.setChecked(enabled)
        del blocker

        self.update_settings_connection_state(connected)

        if not selected:
            self.window.diagnostic_status_label.setText(
                "Выберите роутер для создания диагностического архива."
            )
        elif enabled and connected:
            self.window.diagnostic_status_label.setText(
                "Режим разработчика включён для выбранного роутера."
            )
        elif enabled:
            self.window.diagnostic_status_label.setText(
                "Режим разработчика включён. Архив можно создать "
                "даже без активного подключения к роутеру."
            )
        else:
            self.window.diagnostic_status_label.setText(
                "Режим разработчика для выбранного роутера выключен."
            )

        if hasattr(self.window, "router_self_check_status_label"):
            self.window.router_self_check_controller.sync_label()

        self.sync_router_update_for_selected_router()

        self.window.update_diagnostic_controls()

    def disable_developer_mode_for_router(
        self,
        router_id: int,
    ) -> None:
        # Отключение HTTP-сессии не выключает режим разработчика.
        # Это необходимо для создания офлайн-диагностики.
        if router_id == self.window.get_selected_router_id():
            self.sync_developer_mode_for_selected_router()

    def sync_router_update_for_selected_router(
        self,
        result: object | None = None,
    ) -> None:
        if not hasattr(self.window, "router_update_status_label"):
            return
        router_id = self.window.get_selected_router_id()
        connected = self.window.router_is_fully_connected(router_id)
        if router_id is None:
            self._show_update_channel_placeholder(
                "Каналы недоступны — роутер не выбран"
            )
            self.window.router_update_version_label.setText(
                "Текущая версия: роутер не выбран."
            )
            self.window.router_update_status_label.setText(
                "Выберите и подключите роутер."
            )
            self.update_router_update_controls()
            return

        current_result = result or self.window.connection_results.get(router_id)
        version_data = getattr(current_result, "version_data", None)
        display_version = (
            str(version_data.get("title") or "").strip()
            if isinstance(version_data, dict)
            else ""
        )
        version = display_version or str(
            getattr(current_result, "version", "") or ""
        ).strip()
        self.window.router_update_version_label.setText(
            f"Текущая версия: {version or 'не определена'}"
        )
        info = self.router_update_info.get(router_id)
        if info:
            known_version = str(info.get("installed_version") or "").strip()
            expected_version = str(
                self.router_update_expected_versions.get(router_id) or ""
            ).strip()
            if (
                version
                and expected_version
                and self._update_versions_match(version, expected_version)
            ):
                info = {
                    **info,
                    "installed_version": version,
                    "available_version": "",
                    "update_available": False,
                    "message": (
                        f"Обновление завершено: установлена KeeneticOS "
                        f"{version}."
                    ),
                }
                self.router_update_expected_versions.pop(router_id, None)
                self.router_update_info[router_id] = info
            elif (
                version
                and known_version
                and version != known_version
                and not expected_version
            ):
                info = {
                    **info,
                    "installed_version": version,
                    "available_version": "",
                    "update_available": False,
                    "message": (
                        f"Роутер снова подключён с KeeneticOS {version}. "
                        "Проверьте обновления повторно."
                    ),
                }
                self.router_update_info[router_id] = info
            self._show_router_update_info(info)
        elif connected:
            self._request_initial_router_update_check(router_id)
        else:
            self._show_update_channel_placeholder(
                "Каналы недоступны — роутер не подключён"
            )
            self.window.router_update_status_label.setText(
                "Обновление доступно только при активном подключении к роутеру."
            )
        self.update_router_update_controls()

    def update_router_update_controls(self, *_args: object) -> None:
        if not hasattr(self.window, "router_update_check_button"):
            return
        router_id = self.window.get_selected_router_id()
        connected = self.window.router_is_fully_connected(router_id)
        busy = bool(router_id is not None and router_id in self.router_update_busy)
        info = self.router_update_info.get(router_id or -1, {})
        supported = bool(info.get("supported", True))
        selected_channel = self._selected_update_channel()
        saved_channel = str(info.get("channel") or "")
        channel_dirty = bool(selected_channel and selected_channel != saved_channel)

        self.window.router_update_channel_combo.setEnabled(
            connected and supported and not busy
        )
        self.window.router_update_check_button.setEnabled(
            connected and supported and not busy
        )
        self.window.router_update_apply_channel_button.setEnabled(
            connected and supported and not busy and channel_dirty
        )
        self.window.router_update_install_button.setEnabled(
            connected
            and supported
            and not busy
            and not channel_dirty
            and bool(info.get("update_available"))
        )

    def check_router_update(self) -> None:
        router_id, monitor = self._selected_update_monitor()
        if router_id is None or monitor is None:
            return
        self._start_router_update_check(router_id, monitor, automatic=False)

    def apply_router_update_channel(self) -> None:
        router_id, monitor = self._selected_update_monitor()
        if router_id is None or monitor is None:
            return
        self._remember_settings_scroll()
        channel = self._selected_update_channel()
        self.router_update_busy[router_id] = "channel"
        self.window.router_update_status_label.setText(
            "Сохранение канала обновления..."
        )
        self.update_router_update_controls()
        monitor.request_router_update_channel(channel)

    def install_router_update(self) -> None:
        router_id, monitor = self._selected_update_monitor()
        if router_id is None or monitor is None:
            return
        info = self.router_update_info.get(router_id, {})
        available = str(info.get("available_version") or "новая версия")
        channel = self._selected_update_channel()
        channel_label = {
            "stable": "Основной",
            "preview": "Предварительный",
            "draft": "Тестовый",
        }.get(channel, channel)

        confirmation = QMessageBox(self.window)
        confirmation.setWindowTitle("Установка KeeneticOS")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText(
            f"Установить KeeneticOS {available} из канала «{channel_label}»?"
        )
        confirmation.setInformativeText(
            "Роутер скачает компоненты и перезагрузится. Обычно это занимает "
            "несколько минут. Не отключайте питание устройства."
        )
        confirmation.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        yes_button = confirmation.button(QMessageBox.StandardButton.Yes)
        no_button = confirmation.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText("Да")
        if no_button is not None:
            no_button.setText("Нет")
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return

        self._remember_settings_scroll()
        self.router_update_busy[router_id] = "install"
        self.window.router_update_status_label.setText(
            "Подготовка и запуск установки KeeneticOS..."
        )
        self.update_router_update_controls()
        monitor.request_router_update_install(channel)

    def router_update_completed(
        self,
        router_id: int,
        operation: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        self._remember_settings_scroll()
        payload = dict(details) if isinstance(details, dict) else {}
        automatic = router_id in self.router_update_auto_check_in_progress
        self.router_update_auto_check_in_progress.discard(router_id)
        self.router_update_busy.pop(router_id, None)
        if operation == "install" and not success:
            self.router_update_expected_versions.pop(router_id, None)

        if operation == "check" and success:
            self.router_update_info[router_id] = payload
            if router_id == self.window.get_selected_router_id():
                self._show_router_update_info(payload)
            self.update_router_update_controls()
            return

        if operation == "channel" and success:
            previous = self.router_update_info.get(router_id, {})
            self.router_update_info[router_id] = {
                **previous,
                "supported": True,
                "channel": str(payload.get("channel") or ""),
                "available_version": "",
                "update_available": False,
                "message": message,
            }
            if router_id == self.window.get_selected_router_id():
                self.window.router_update_status_label.setText(
                    message + " Выполняется проверка обновлений..."
                )
                monitor = self.window.router_monitors.get(router_id)
                if monitor is not None:
                    self.router_update_busy[router_id] = "check"
                    self.update_router_update_controls()
                    monitor.request_router_update_check()
                    return

        elif operation == "install" and success:
            info = self.router_update_info.get(router_id, {})
            expected_version = str(
                info.get("available_version")
                or payload.get("available_version")
                or ""
            ).strip()
            if expected_version:
                self.router_update_expected_versions[router_id] = (
                    expected_version
                )
            self.router_update_info[router_id] = {
                **info,
                "update_available": False,
                "message": message,
            }
            if router_id == self.window.get_selected_router_id():
                self.window.router_update_status_label.setText(message)
                QMessageBox.information(
                    self.window,
                    "Обновление KeeneticOS",
                    message,
                )
            self.update_router_update_controls()
            return

        if router_id == self.window.get_selected_router_id():
            self.window.router_update_status_label.setText(message)
            if not success and not automatic:
                QMessageBox.warning(
                    self.window,
                    "Обновление KeeneticOS",
                    message,
                )
        self.update_router_update_controls()

    def router_update_monitor_stopped(self, router_id: int) -> None:
        self.router_update_busy.pop(router_id, None)
        self.router_update_auto_check_requested.discard(router_id)
        self.router_update_auto_check_in_progress.discard(router_id)
        self.router_update_info.pop(router_id, None)
        self.router_update_expected_versions.pop(router_id, None)
        if router_id == self.window.get_selected_router_id():
            self._show_update_channel_placeholder(
                "Каналы недоступны — роутер не подключён"
            )
            self.update_router_update_controls()

    def _show_router_update_info(self, info: dict[str, Any]) -> None:
        channels = info.get("channels")
        channel = str(info.get("channel") or "")
        combo = self.window.router_update_channel_combo
        router_id = self.window.get_selected_router_id()
        same_router = router_id == self._router_update_combo_router_id
        selection_in_progress = combo.view().isVisible()
        selected_channel = str(combo.currentData() or "")
        preserve_user_selection = bool(
            same_router
            and selected_channel
            and selected_channel != channel
        )

        desired_items: list[tuple[str, str]] = []
        if isinstance(channels, (list, tuple)):
            for item in channels:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                label = str(item.get("label") or name).strip()
                version = str(item.get("version") or "").strip()
                text = f"{label} — {version}" if version else label
                desired_items.append((text, name))

        current_items = [
            (combo.itemText(index), str(combo.itemData(index) or ""))
            for index in range(combo.count())
        ]
        if (
            desired_items
            and desired_items != current_items
            and not selection_in_progress
        ):
            target_channel = (
                selected_channel
                if preserve_user_selection
                and any(name == selected_channel for _, name in desired_items)
                else channel
            )
            blocker = QSignalBlocker(
                combo
            )
            combo.clear()
            for text, name in desired_items:
                combo.addItem(text, name)
            target_index = combo.findData(target_channel)
            if target_index >= 0:
                combo.setCurrentIndex(target_index)
            del blocker
        if channel and not selection_in_progress and not preserve_user_selection:
            index = combo.findData(channel)
            if index >= 0:
                blocker = QSignalBlocker(
                    combo
                )
                combo.setCurrentIndex(index)
                del blocker
        if not selection_in_progress:
            self._router_update_combo_router_id = router_id
        self.window.router_update_status_label.setText(
            str(info.get("message") or "Состояние обновления получено.")
        )

    def _selected_update_channel(self) -> str:
        return str(self.window.router_update_channel_combo.currentData() or "")

    def _request_initial_router_update_check(self, router_id: int) -> None:
        if router_id in self.router_update_auto_check_requested:
            return
        monitor = self.window.router_monitors.get(router_id)
        if monitor is None:
            self.window.router_update_status_label.setText(
                "Ожидание запуска мониторинга роутера..."
            )
            return
        self.router_update_auto_check_requested.add(router_id)
        self.router_update_auto_check_in_progress.add(router_id)
        self._show_update_channel_placeholder("Получение каналов...")
        self._start_router_update_check(router_id, monitor, automatic=True)

    def _start_router_update_check(
        self,
        router_id: int,
        monitor: Any,
        *,
        automatic: bool,
    ) -> None:
        self._remember_settings_scroll()
        self.router_update_busy[router_id] = "check"
        self.window.router_update_status_label.setText(
            "Получение каналов и проверка обновлений..."
            if automatic
            else "Проверка обновлений на сервере Keenetic..."
        )
        self.update_router_update_controls()
        monitor.request_router_update_check()

    def _show_update_channel_placeholder(self, text: str) -> None:
        combo = self.window.router_update_channel_combo
        if (
            combo.count() == 1
            and not combo.itemData(0)
            and combo.itemText(0) == text
        ):
            return
        blocker = QSignalBlocker(combo)
        combo.clear()
        combo.addItem(text, "")
        del blocker
        self._router_update_combo_router_id = None

    @staticmethod
    def _update_versions_match(installed: str, expected: str) -> bool:
        left = str(installed or "").strip().casefold().lstrip("v")
        right = str(expected or "").strip().casefold().lstrip("v")
        if left == right:
            return True

        display_pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
        internal_pattern = re.compile(
            r"^(\d+)\.(\d+)\.[a-z]\.(\d+)(?:\.|$)"
        )

        def semantic_version(value: str) -> tuple[int, int, int] | None:
            display_match = display_pattern.match(value)
            if display_match:
                return tuple(int(part) for part in display_match.groups())
            internal_match = internal_pattern.match(value)
            if internal_match:
                return tuple(int(part) for part in internal_match.groups())
            return None

        left_version = semantic_version(left)
        right_version = semantic_version(right)
        return bool(
            left_version is not None
            and right_version is not None
            and left_version == right_version
        )

    def _remember_settings_scroll(self) -> None:
        """Не даёт Qt прокрутить страницу при потере фокуса кнопкой."""
        area = getattr(self.window, "settings_scroll_area", None)
        if not isinstance(area, QScrollArea):
            return
        self._settings_scroll_anchor = area.verticalScrollBar().value()
        area.viewport().setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._restore_settings_scroll)

    def _restore_settings_scroll(self) -> None:
        area = getattr(self.window, "settings_scroll_area", None)
        value = getattr(self, "_settings_scroll_anchor", None)
        if not isinstance(area, QScrollArea) or value is None:
            return
        area.verticalScrollBar().setValue(int(value))

    def _selected_update_monitor(self) -> tuple[int | None, Any | None]:
        router_id = self.window.get_selected_router_id()
        monitor = (
            self.window.router_monitors.get(router_id)
            if router_id is not None
            else None
        )
        if monitor is None:
            self.window.statusBar().showMessage(
                "Выбранный роутер не подключён.",
                4000,
            )
        return router_id, monitor
