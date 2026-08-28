from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
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

    def create_settings_tab(self) -> QWidget:
        page = QScrollArea()
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

    def update_settings_router_profile(self, result: object) -> None:
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

        self.window.update_diagnostic_controls()

    def disable_developer_mode_for_router(
        self,
        router_id: int,
    ) -> None:
        # Отключение HTTP-сессии не выключает режим разработчика.
        # Это необходимо для создания офлайн-диагностики.
        if router_id == self.window.get_selected_router_id():
            self.sync_developer_mode_for_selected_router()
