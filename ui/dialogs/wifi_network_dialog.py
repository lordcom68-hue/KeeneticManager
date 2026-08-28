from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core.services.wifi_network_service import WiFiNetworkService


class WiFiNetworkDialog(QDialog):
    """Создание или изменение домашней Wi‑Fi сети."""

    def __init__(
        self,
        *,
        network: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.network = dict(network or {})
        self.capabilities = dict(capabilities or {})
        self.edit_mode = bool(network)
        self.bandless_supported = bool(
            self.capabilities.get(
                "bandless_supported",
                self.capabilities.get("mws_supported", True),
            )
        )

        self.setWindowTitle(
            "Изменить Wi‑Fi сеть"
            if self.edit_mode
            else "Создать Wi‑Fi сеть"
        )
        self.setMinimumWidth(510)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        description = QLabel(
            (
                "Изменения применяются ко всем диапазонам, "
                "в которых работает выбранная сеть."
                if self.edit_mode
                else (
                    "Одинаковый SSID будет создан на всех "
                    "выбранных диапазонах."
                    + (
                        " Диапазоны можно не выбирать и назначить позже."
                        if self.bandless_supported
                        else " Для KeeneticOS 4 выберите хотя бы один диапазон."
                    )
                )
            )
        )
        description.setWordWrap(True)
        root.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)

        self.ssid_edit = QLineEdit(
            str(self.network.get("ssid") or "")
        )
        self.ssid_edit.setMaxLength(32)
        self.ssid_edit.setPlaceholderText("Имя беспроводной сети")
        form.addRow("SSID:", self.ssid_edit)

        self.security_combo = QComboBox()
        current_security = str(
            self.network.get("security_mode") or "wpa2"
        )
        self.initial_security = WiFiNetworkService.normalize_security_mode(
            current_security
        )
        mws_supported = bool(
            self.capabilities.get("mws_supported", True)
        )
        security_options = list(
            WiFiNetworkService.MWS_SECURITY_OPTIONS
            if mws_supported
            else WiFiNetworkService.LEGACY_SECURITY_OPTIONS
        )
        option_modes = {mode for _label, mode in security_options}
        if self.initial_security not in option_modes:
            current_label = next(
                (
                    label
                    for label, mode
                    in WiFiNetworkService.MWS_SECURITY_OPTIONS
                    if mode == self.initial_security
                ),
                f"Текущий режим ({current_security})",
            )
            security_options.insert(
                0,
                (current_label, self.initial_security),
            )
        for label, mode in security_options:
            self.security_combo.addItem(label, mode)
        index = self.security_combo.findData(
            self.initial_security
        )
        self.security_combo.setCurrentIndex(max(index, 0))
        form.addRow("Безопасность:", self.security_combo)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMaxLength(64)
        self.password_edit.setPlaceholderText(
            (
                "Оставьте пустым, чтобы не менять пароль"
                if self.edit_mode
                else "От 8 до 63 символов"
            )
        )
        form.addRow("Пароль:", self.password_edit)

        self.show_password_check = QCheckBox("Показать пароль")
        self.show_password_check.toggled.connect(
            self._toggle_password_visibility
        )
        form.addRow("", self.show_password_check)

        self.hidden_check = QCheckBox(
            "Скрывать имя сети (SSID)"
        )
        self.hidden_check.setChecked(
            bool(self.network.get("hidden", False))
        )
        form.addRow("Видимость:", self.hidden_check)

        self.enabled_check = QCheckBox(
            "Включить сеть после создания"
        )
        self.enabled_check.setChecked(True)
        self.enabled_check.setVisible(not self.edit_mode)
        if not self.edit_mode:
            form.addRow("Состояние:", self.enabled_check)

        root.addLayout(form)

        bands_group = QGroupBox("Диапазоны")
        bands_layout = QVBoxLayout(bands_group)
        bands_layout.setContentsMargins(14, 12, 14, 12)

        supported = {
            str(item)
            for item in self.capabilities.get(
                "supported_bands",
                ["2.4", "5"],
            )
        }
        current_bands = self._current_bands()
        raw_slots = self.capabilities.get("available_slots")
        availability_known = isinstance(raw_slots, dict)
        available_bands = (
            {
                str(band)
                for band, slots in raw_slots.items()
                if isinstance(slots, (list, tuple, set)) and slots
            }
            if availability_known
            else set(supported)
        )
        visible_bands = (
            current_bands | available_bands
            if self.edit_mode
            else available_bands
        ) & supported
        self.band_editable = bool(
            not self.edit_mode
            or self.network.get(
                "band_editable",
                self.network.get("mws_band_editable", False),
            )
        )

        self.band_checks: dict[str, QCheckBox] = {}
        for band, title in (
            ("2.4", "2,4 ГГц"),
            ("5", "5 ГГц"),
            ("6", "6 ГГц"),
        ):
            checkbox = QCheckBox(title)
            checkbox.setVisible(band in visible_bands)
            checkbox.setEnabled(
                band in visible_bands and self.band_editable
            )
            checkbox.setChecked(
                band in current_bands
                if self.edit_mode
                else band in visible_bands
            )
            bands_layout.addWidget(checkbox)
            self.band_checks[band] = checkbox

        if self.edit_mode:
            note = QLabel(
                (
                    "Можно добавить свободный диапазон или убрать "
                    "используемый этой сетью. Занятые другими сетями "
                    "диапазоны не показываются."
                    if self.band_editable
                    else (
                        "Для этой системной или физической сети "
                        "роутер не разрешает безопасно менять "
                        "список диапазонов."
                    )
                )
            )
            note.setWordWrap(True)
            note.setObjectName("wifiDialogNote")
            bands_layout.addWidget(note)
        elif not visible_bands:
            note = QLabel(
                (
                    "Свободных точек доступа нет. Сеть будет создана "
                    "без диапазона и не появится в эфире, пока один "
                    "из диапазонов не освободится."
                    if self.bandless_supported
                    else (
                        "Свободных физических точек доступа нет. "
                        "Создание сети на этом роутере невозможно."
                    )
                )
            )
            note.setWordWrap(True)
            note.setObjectName("wifiDialogNote")
            bands_layout.addWidget(note)

        root.addWidget(bands_group)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_checked)
        self.buttons.rejected.connect(self.reject)
        ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        )
        back_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        if ok_button is not None:
            ok_button.setText(
                "Сохранить" if self.edit_mode else "Создать"
            )
        if back_button is not None:
            back_button.setText("Назад")
        if not self.edit_mode and not visible_bands:
            self.enabled_check.setChecked(False)
            self.enabled_check.setEnabled(False)
            if not self.bandless_supported and ok_button is not None:
                ok_button.setEnabled(False)
        root.addWidget(self.buttons)

        self.security_combo.currentIndexChanged.connect(
            self._sync_security_controls
        )
        self._sync_security_controls()

    def settings(self) -> dict[str, Any]:
        password = self.password_edit.text()
        security = str(
            self.security_combo.currentData() or "wpa2"
        )
        bands = [
            band
            for band, checkbox in self.band_checks.items()
            if checkbox.isChecked() and not checkbox.isHidden()
        ]
        return {
            "ssid": self.ssid_edit.text().strip(),
            "security": security,
            "password": password,
            "change_password": (
                WiFiNetworkService.security_uses_password(security)
                and (bool(password) or not self.edit_mode)
            ),
            "change_security": (
                not self.edit_mode
                or security != self.initial_security
            ),
            "hidden": self.hidden_check.isChecked(),
            "enabled": self.enabled_check.isChecked() and bool(bands),
            "bands": bands,
            "mws_profile_id": str(
                self.network.get("mws_profile_id") or ""
            ).strip(),
        }

    def _accept_checked(self) -> None:
        settings = self.settings()
        ssid = settings["ssid"]
        security = settings["security"]
        password = settings["password"]

        if not settings["bands"] and not self.bandless_supported:
            QMessageBox.warning(
                self,
                "Диапазон не выбран",
                "Для KeeneticOS 4 выберите хотя бы один диапазон Wi-Fi.",
            )
            return

        if not WiFiNetworkService.valid_ssid(ssid):
            QMessageBox.warning(
                self,
                "Некорректный SSID",
                "Имя сети должно содержать от 1 до 32 байт.",
            )
            self.ssid_edit.setFocus()
            return

        password_required = (
            WiFiNetworkService.security_uses_password(security)
            and (
                not self.edit_mode
                or bool(settings["change_password"])
            )
        )
        if not WiFiNetworkService.valid_password(
            password,
            required=password_required,
        ):
            QMessageBox.warning(
                self,
                "Некорректный пароль",
                "Пароль Wi-Fi должен содержать от 8 до 63 символов.",
            )
            self.password_edit.setFocus()
            return

        self.accept()

    def _sync_security_controls(self) -> None:
        password_used = WiFiNetworkService.security_uses_password(
            self.security_combo.currentData()
        )
        self.password_edit.setEnabled(password_used)
        self.show_password_check.setEnabled(password_used)
        if not password_used:
            self.password_edit.clear()
            self.show_password_check.setChecked(False)

    def _toggle_password_visibility(self, visible: bool) -> None:
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _current_bands(self) -> set[str]:
        band_states = self.network.get("band_states")
        if isinstance(band_states, dict):
            result = {
                str(band)
                for band in band_states
                if str(band) in {"2.4", "5", "6"}
            }
            if result:
                return result

        result: set[str] = set()
        text = str(self.network.get("band") or "").lower()
        if "2.4" in text or "2,4" in text:
            result.add("2.4")
        if "5" in text:
            result.add("5")
        if "6" in text:
            result.add("6")
        return result
