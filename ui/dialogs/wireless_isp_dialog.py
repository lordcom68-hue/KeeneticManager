from __future__ import annotations

import ipaddress
import re
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class WirelessIspCreationDialog(QDialog):
    """Единый мастер Wireless ISP для диапазонов 2,4 и 5 ГГц."""

    scan_requested = Signal(str)
    options_refresh_requested = Signal()

    BAND_ITEMS = (
        ("2,4 ГГц", "2.4"),
        ("5 ГГц", "5"),
    )

    def __init__(
        self,
        *,
        bands: list[dict[str, Any]],
        existing_priority_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._creation_settings: dict[str, Any] | None = None
        self._bands: list[dict[str, Any]] = []
        self._scan_results: dict[str, list[dict[str, Any]]] = {
            "2.4": [],
            "5": [],
        }
        self._existing_priority_count = max(
            0,
            int(existing_priority_count),
        )

        self.setWindowTitle("Создать Wireless ISP")
        self.setModal(True)
        self.setMinimumSize(540, 470)

        host_window = parent.window() if parent is not None else None
        host_width = host_window.width() if host_window is not None else 980
        host_height = host_window.height() if host_window is not None else 780
        self.resize(
            max(540, min(780, host_width - 80)),
            max(470, min(740, host_height - 80)),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Новое подключение Wireless ISP")
        title.setObjectName("internetSettingsTitle")
        root.addWidget(title)

        intro = QLabel(
            "Выберите диапазон, найдите точку доступа и укажите "
            "параметры подключения. Для каждого диапазона можно "
            "создать одно независимое подключение Wireless ISP."
        )
        intro.setWordWrap(True)
        intro.setObjectName("internetSettingsNotice")
        root.addWidget(intro)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget(scroll)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(4, 2, 8, 8)
        content_root.setSpacing(14)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

        self.band_combo = QComboBox()
        form.addRow("Диапазон:", self.band_combo)

        self.band_status_label = QLabel()
        self.band_status_label.setWordWrap(True)
        self.band_status_label.setObjectName("internetSettingsNotice")
        form.addRow("", self.band_status_label)

        scan_row = QWidget()
        scan_layout = QHBoxLayout(scan_row)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setSpacing(8)

        self.network_combo = QComboBox()
        self.network_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.network_combo.setMinimumContentsLength(28)
        self.scan_button = QPushButton("Найти сети")
        self.scan_button.setMinimumHeight(30)
        self.scan_button.setToolTip(
            "Выполнить сканирование выбранного диапазона. "
            "Во время сканирования Wi-Fi может кратковременно "
            "приостановить передачу данных."
        )

        scan_layout.addWidget(self.network_combo, 1)
        scan_layout.addWidget(self.scan_button)
        form.addRow("Wi-Fi-сеть:", scan_row)

        self.scan_status_label = QLabel(
            "Нажмите «Найти сети» либо введите SSID вручную."
        )
        self.scan_status_label.setWordWrap(True)
        self.scan_status_label.setObjectName("internetSettingsNotice")
        form.addRow("", self.scan_status_label)

        self.hidden_network_check = QCheckBox(
            "Сеть скрыта (SSID не транслируется)"
        )
        form.addRow("Скрытая сеть:", self.hidden_network_check)

        self.manual_ssid_edit = QLineEdit()
        self.manual_ssid_edit.setMaxLength(32)
        self.manual_ssid_edit.setPlaceholderText("Имя Wi-Fi-сети (SSID)")
        form.addRow("SSID:", self.manual_ssid_edit)

        self.description_edit = QLineEdit("Wireless ISP 2,4 ГГц")
        self.description_edit.setMaxLength(64)
        form.addRow("Название:", self.description_edit)

        self.open_network_check = QCheckBox("Сеть не защищена паролем")
        form.addRow("Защита:", self.open_network_check)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMaxLength(64)
        self.password_edit.setPlaceholderText("Пароль WPA/WPA2 — 8–63 символа")
        form.addRow("Пароль Wi-Fi:", self.password_edit)

        self.bind_bssid_check = QCheckBox(
            "Привязаться к выбранной точке доступа (BSSID)"
        )
        self.bind_bssid_check.setChecked(False)
        self.bind_bssid_check.setToolTip(
            "При включении роутер будет подключаться только к "
            "выбранному BSSID, даже если рядом есть сеть с тем же SSID."
        )
        form.addRow("BSSID:", self.bind_bssid_check)

        ipv4_title = QLabel("Параметры IPv4")
        ipv4_title.setObjectName("internetSettingsSectionTitle")
        form.addRow("", ipv4_title)

        self.ipv4_dhcp_check = QCheckBox(
            "Получать IPv4-адрес автоматически (DHCP)"
        )
        self.ipv4_dhcp_check.setChecked(True)
        form.addRow("Настройка IPv4:", self.ipv4_dhcp_check)

        self.ipv4_address_edit = QLineEdit()
        self.ipv4_address_edit.setPlaceholderText("Например: 192.0.2.10")
        form.addRow("IPv4-адрес:", self.ipv4_address_edit)

        self.ipv4_prefix_spin = QSpinBox()
        self.ipv4_prefix_spin.setRange(1, 32)
        self.ipv4_prefix_spin.setValue(24)
        form.addRow("Длина префикса:", self.ipv4_prefix_spin)

        self.ipv4_gateway_edit = QLineEdit()
        self.ipv4_gateway_edit.setPlaceholderText("Например: 192.0.2.1")
        form.addRow("Шлюз:", self.ipv4_gateway_edit)

        self.dhcp_hostname_edit = QLineEdit("Keenetic")
        self.dhcp_hostname_edit.setMaxLength(63)
        form.addRow("Имя DHCP-клиента:", self.dhcp_hostname_edit)

        self.dhcp_routes_check = QCheckBox(
            "Принимать маршруты от DHCP-сервера"
        )
        self.dhcp_routes_check.setChecked(True)
        form.addRow("Маршруты DHCP:", self.dhcp_routes_check)

        self.dhcp_dns_routes_check = QCheckBox(
            "Создавать маршруты к DNS провайдера"
        )
        self.dhcp_dns_routes_check.setChecked(True)
        form.addRow("Маршруты DNS:", self.dhcp_dns_routes_check)

        self.provider_dns_check = QCheckBox(
            "Использовать DNS, полученные через Wireless ISP"
        )
        self.provider_dns_check.setChecked(True)
        form.addRow("DNS провайдера:", self.provider_dns_check)

        self.primary_dns_edit = QLineEdit()
        self.primary_dns_edit.setPlaceholderText("Например: 1.1.1.1")
        form.addRow("Основной DNS:", self.primary_dns_edit)

        self.secondary_dns_edit = QLineEdit()
        self.secondary_dns_edit.setPlaceholderText("Например: 8.8.8.8")
        form.addRow("Дополнительный DNS:", self.secondary_dns_edit)

        self.mtu_spin = QSpinBox()
        self.mtu_spin.setRange(576, 1500)
        self.mtu_spin.setValue(1500)
        form.addRow("MTU:", self.mtu_spin)

        self.priority_combo = QComboBox()
        form.addRow("Приоритет:", self.priority_combo)

        ping_title = QLabel("Проверка доступности интернет-канала")
        ping_title.setObjectName("internetSettingsSectionTitle")
        form.addRow("", ping_title)

        self.ping_mode_combo = QComboBox()
        self.ping_mode_combo.addItem("Выключено", "disabled")
        self.ping_mode_combo.addItem("Автоматически (Keenetic)", "automatic")
        self.ping_mode_combo.addItem("ICMP echo (Ping)", "icmp")
        self.ping_mode_combo.addItem("TCP/TLS — проверка порта", "tls")
        self.ping_mode_combo.addItem("TCP — проверка порта", "connect")
        form.addRow("Режим Ping Check:", self.ping_mode_combo)

        self.ping_host_edit = QLineEdit("8.8.8.8")
        self.ping_host_edit.setMaxLength(253)
        form.addRow("Узел проверки:", self.ping_host_edit)

        self.ping_port_spin = QSpinBox()
        self.ping_port_spin.setRange(1, 65535)
        self.ping_port_spin.setValue(443)
        form.addRow("TCP-порт:", self.ping_port_spin)

        self.ping_interval_spin = QSpinBox()
        self.ping_interval_spin.setRange(3, 3600)
        self.ping_interval_spin.setValue(10)
        self.ping_interval_spin.setSuffix(" с")
        form.addRow("Интервал:", self.ping_interval_spin)

        self.ping_max_fails_spin = QSpinBox()
        self.ping_max_fails_spin.setRange(1, 10)
        self.ping_max_fails_spin.setValue(5)
        form.addRow("Ошибок до переключения:", self.ping_max_fails_spin)

        content_root.addLayout(form)
        content_root.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        if self.create_button is not None:
            self.create_button.setText("Создать")
        if cancel_button is not None:
            cancel_button.setText("Назад")
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.band_combo.currentIndexChanged.connect(self._band_changed)
        self.network_combo.currentIndexChanged.connect(self._network_changed)
        self.network_combo.activated.connect(
            self._network_selected_by_user
        )
        self.manual_ssid_edit.textEdited.connect(
            self._manual_ssid_changed
        )
        self.scan_button.clicked.connect(self._request_scan)
        self.hidden_network_check.toggled.connect(self._update_network_fields)
        self.open_network_check.toggled.connect(self._update_security_fields)
        self.ipv4_dhcp_check.toggled.connect(self._update_ipv4_fields)
        self.ping_mode_combo.currentIndexChanged.connect(
            self._update_ping_fields
        )

        self._options_refresh_timer = QTimer(self)
        self._options_refresh_timer.setInterval(2000)
        self._options_refresh_timer.timeout.connect(
            self.options_refresh_requested.emit
        )
        self._options_refresh_timer.start()

        self.update_available_bands(
            bands,
            existing_priority_count=self._existing_priority_count,
        )
        self._update_network_fields()
        self._update_security_fields()
        self._update_ipv4_fields(True)
        self._update_ping_fields()

    def update_available_bands(
        self,
        bands: list[dict[str, Any]],
        *,
        existing_priority_count: int,
    ) -> None:
        selected_band = str(self.band_combo.currentData() or "2.4")
        self._bands = [dict(item) for item in bands if isinstance(item, dict)]
        by_id = {
            str(item.get("id") or "").strip(): item
            for item in self._bands
        }

        self.band_combo.blockSignals(True)
        self.band_combo.clear()
        for title, band_id in self.BAND_ITEMS:
            item = by_id.get(band_id, {})
            available = bool(item.get("available", False))
            configured = bool(item.get("configured", False))
            text = title
            if configured:
                current_ssid = str(item.get("ssid") or "").strip()
                text += " — уже настроено"
                if current_ssid:
                    text += f" ({current_ssid})"
            elif not available:
                text += " — недоступно"
            self.band_combo.addItem(text, band_id)
            model_item = self.band_combo.model().item(
                self.band_combo.count() - 1
            )
            if model_item is not None:
                model_item.setEnabled(available)

        index = self.band_combo.findData(selected_band)
        if index < 0 or not self._combo_index_enabled(index):
            index = next(
                (
                    candidate
                    for candidate in range(self.band_combo.count())
                    if self._combo_index_enabled(candidate)
                ),
                0,
            )
        self.band_combo.setCurrentIndex(index)
        self.band_combo.blockSignals(False)

        self._existing_priority_count = max(
            0,
            int(existing_priority_count),
        )
        previous_priority = self.priority_combo.currentData()
        self.priority_combo.clear()
        for order in range(self._existing_priority_count + 1):
            title = "Основной канал" if order == 0 else f"Резервный {order}"
            self.priority_combo.addItem(title, order)
        priority_index = self.priority_combo.findData(previous_priority)
        if priority_index < 0:
            priority_index = self.priority_combo.count() - 1
        self.priority_combo.setCurrentIndex(max(0, priority_index))

        self._band_changed()

    def _combo_index_enabled(self, index: int) -> bool:
        if index < 0 or index >= self.band_combo.count():
            return False
        model_item = self.band_combo.model().item(index)
        return bool(model_item is not None and model_item.isEnabled())

    def _band_info(self, band: str | None = None) -> dict[str, Any]:
        target = str(band or self.band_combo.currentData() or "").strip()
        return next(
            (
                dict(item)
                for item in self._bands
                if str(item.get("id") or "").strip() == target
            ),
            {},
        )

    def _band_changed(self, _index: int | None = None) -> None:
        band = str(self.band_combo.currentData() or "2.4")
        title = "2,4 ГГц" if band == "2.4" else "5 ГГц"
        info = self._band_info(band)
        available = bool(info.get("available", False))
        self.band_status_label.setText(
            (
                f"Будет использован интерфейс {info.get('interface_id')}."
                if available
                else "Этот диапазон уже занят или не поддерживается роутером."
            )
        )
        current_description = self.description_edit.text().strip()
        if not current_description or current_description.startswith("Wireless ISP"):
            self.description_edit.setText(f"Wireless ISP {title}")
        self._populate_networks_for_band(band)
        if self.create_button is not None:
            self.create_button.setEnabled(available)

    @staticmethod
    def _network_key(network: Any) -> tuple[str, str]:
        if not isinstance(network, dict):
            return ("", "")

        return (
            str(network.get("ssid") or "").strip(),
            str(network.get("bssid") or "").strip().upper(),
        )

    def _populate_networks_for_band(self, band: str) -> None:
        previous_data = self.network_combo.currentData()
        previous_key = self._network_key(previous_data)

        self.network_combo.blockSignals(True)
        self.network_combo.clear()

        for network in self._scan_results.get(band, []):
            ssid = str(network.get("ssid") or "").strip()
            if not ssid:
                continue

            bssid = str(
                network.get("bssid") or ""
            ).strip().upper()
            channel = network.get("channel")
            signal = network.get("signal")
            suffix: list[str] = []

            if channel not in (None, ""):
                suffix.append(f"канал {channel}")

            if signal not in (None, ""):
                try:
                    suffix.append(f"{int(float(signal))} dBm")
                except (TypeError, ValueError):
                    suffix.append(str(signal))

            if bssid:
                suffix.append(bssid)

            display = ssid + (
                " — " + ", ".join(suffix)
                if suffix
                else ""
            )
            self.network_combo.addItem(
                display,
                dict(network),
            )

        if self.network_combo.count() == 0:
            self.network_combo.addItem(
                "Сети ещё не найдены",
                {},
            )

        selected_index = -1

        if previous_key != ("", ""):
            for index in range(
                self.network_combo.count()
            ):
                if self._network_key(
                    self.network_combo.itemData(index)
                ) == previous_key:
                    selected_index = index
                    break

        if selected_index < 0:
            selected_index = 0

        self.network_combo.setCurrentIndex(
            selected_index
        )
        self.network_combo.blockSignals(False)

        # Первый найденный SSID подставляется только пока
        # пользователь ещё ничего не ввёл. Последующие фоновые
        # обновления списка не перезаписывают ручной текст.
        if not self.manual_ssid_edit.text():
            self._apply_selected_network_to_fields(
                overwrite_ssid=True,
            )
        else:
            self._network_changed()

    def set_scan_pending(self, band: str) -> None:
        if str(self.band_combo.currentData() or "") == str(band):
            self.scan_button.setEnabled(False)
            self.scan_button.setText("Поиск...")
            self.scan_status_label.setText(
                "Роутер сканирует выбранный диапазон. Подождите несколько секунд."
            )

    def update_scan_results(
        self,
        *,
        band: str,
        success: bool,
        message: str,
        networks: list[dict[str, Any]],
    ) -> None:
        normalized_band = str(band or "").strip()
        if normalized_band not in self._scan_results:
            return
        if success:
            self._scan_results[normalized_band] = [
                dict(item) for item in networks if isinstance(item, dict)
            ]
        if str(self.band_combo.currentData() or "") == normalized_band:
            self.scan_button.setEnabled(True)
            self.scan_button.setText("Найти сети")
            self.scan_status_label.setText(
                message
                or (
                    f"Найдено сетей: {len(self._scan_results[normalized_band])}."
                    if success
                    else "Не удалось выполнить сканирование."
                )
            )
            self._populate_networks_for_band(normalized_band)

    def _request_scan(self) -> None:
        band = str(self.band_combo.currentData() or "").strip()
        if band not in {"2.4", "5"}:
            return
        self.set_scan_pending(band)
        self.scan_requested.emit(band)

    def _selected_network_matches_ssid(self) -> bool:
        data = self.network_combo.currentData()

        if not isinstance(data, dict):
            return False

        selected_ssid = str(
            data.get("ssid") or ""
        ).strip()

        return bool(
            selected_ssid
            and selected_ssid
            == self.manual_ssid_edit.text().strip()
        )

    def _apply_selected_network_to_fields(
        self,
        *,
        overwrite_ssid: bool,
    ) -> None:
        data = self.network_combo.currentData()

        if not isinstance(data, dict):
            self._network_changed()
            return

        ssid = str(data.get("ssid") or "").strip()

        if overwrite_ssid and ssid:
            self.manual_ssid_edit.setText(ssid)

        if "open" in data:
            self.open_network_check.setChecked(
                bool(data.get("open"))
            )

        self._network_changed()

    def _network_selected_by_user(
        self,
        _index: int | None = None,
    ) -> None:
        self._apply_selected_network_to_fields(
            overwrite_ssid=True,
        )

    def _manual_ssid_changed(
        self,
        _text: str,
    ) -> None:
        self._network_changed()

    def _network_changed(self, _index: int | None = None) -> None:
        data = self.network_combo.currentData()
        bssid_available = bool(
            isinstance(data, dict)
            and str(data.get("bssid") or "").strip()
            and self._selected_network_matches_ssid()
            and not self.hidden_network_check.isChecked()
        )
        self.bind_bssid_check.setEnabled(
            bssid_available
        )

        if not bssid_available:
            self.bind_bssid_check.setChecked(False)

    def _update_network_fields(self, _checked: bool | None = None) -> None:
        # SSID можно вводить вручную для любой сети — скрытой
        # или обычной — и даже без предварительного сканирования.
        self.network_combo.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.manual_ssid_edit.setEnabled(True)
        self._network_changed()

    def _update_security_fields(self, _checked: bool | None = None) -> None:
        self.password_edit.setEnabled(not self.open_network_check.isChecked())

    def _update_ipv4_fields(self, checked: bool) -> None:
        manual = not bool(checked)
        self.ipv4_address_edit.setEnabled(manual)
        self.ipv4_prefix_spin.setEnabled(manual)
        self.ipv4_gateway_edit.setEnabled(manual)
        self.dhcp_hostname_edit.setEnabled(not manual)
        self.dhcp_routes_check.setEnabled(not manual)
        self.dhcp_dns_routes_check.setEnabled(not manual)

    def _update_ping_fields(self, _index: int | None = None) -> None:
        mode = str(self.ping_mode_combo.currentData() or "disabled")
        manual = mode in {"icmp", "tls", "connect"}
        tcp = mode in {"tls", "connect"}
        self.ping_host_edit.setEnabled(manual)
        self.ping_port_spin.setEnabled(tcp)
        self.ping_interval_spin.setEnabled(manual)
        self.ping_max_fails_spin.setEnabled(manual)
        if mode == "tls" and self.ping_port_spin.value() == 53:
            self.ping_port_spin.setValue(443)
        elif mode == "connect" and self.ping_port_spin.value() == 443:
            self.ping_port_spin.setValue(53)

    @staticmethod
    def _valid_ssid(value: str) -> bool:
        text = str(value or "")
        if not text or any(character in "\r\n\x00" for character in text):
            return False
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return False
        return 1 <= size <= 32

    @staticmethod
    def _valid_wifi_password(value: str) -> bool:
        text = str(value or "")
        if any(character in "\r\n\x00" for character in text):
            return False
        if re.fullmatch(r"[0-9A-Fa-f]{64}", text):
            return True
        try:
            return 8 <= len(text.encode("utf-8")) <= 63
        except UnicodeError:
            return False

    @staticmethod
    def _normalize_ipv4(value: str) -> str:
        try:
            parsed = ipaddress.ip_address(str(value or "").strip())
        except ValueError:
            return ""
        return str(parsed) if parsed.version == 4 else ""

    def _validated_manual_dns(self) -> list[str] | None:
        result: list[str] = []
        for edit in (self.primary_dns_edit, self.secondary_dns_edit):
            value = edit.text().strip()
            if not value:
                continue
            try:
                normalized = str(ipaddress.ip_address(value))
            except ValueError:
                self._show_error(f"Некорректный DNS-адрес: {value}")
                return None
            if normalized in result:
                self._show_error("DNS-серверы не должны совпадать.")
                return None
            result.append(normalized)
        return result

    def _validated_ping_check(self) -> dict[str, Any] | None:
        mode = str(self.ping_mode_combo.currentData() or "disabled")
        if mode in {"disabled", "automatic"}:
            return {"mode": mode}
        host = self.ping_host_edit.text().strip()
        if (
            not host
            or len(host) > 253
            or any(character.isspace() for character in host)
            or "://" in host
            or "/" in host
        ):
            self._show_error("Укажите корректный узел Ping Check.")
            return None
        result: dict[str, Any] = {
            "mode": mode,
            "host": host,
            "interval": self.ping_interval_spin.value(),
            "max_fails": self.ping_max_fails_spin.value(),
        }
        if mode in {"tls", "connect"}:
            result["port"] = self.ping_port_spin.value()
        return result

    def _show_error(self, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Wireless ISP", text)

    def _validate_and_accept(self) -> None:
        band = str(self.band_combo.currentData() or "").strip()
        info = self._band_info(band)
        if band not in {"2.4", "5"} or not info.get("available"):
            self._show_error("Выбранный диапазон сейчас недоступен.")
            return

        data = self.network_combo.currentData()
        selected_network = (
            dict(data)
            if isinstance(data, dict)
            else {}
        )
        ssid = self.manual_ssid_edit.text()

        if not self._valid_ssid(ssid):
            self._show_error("Укажите SSID длиной до 32 байт.")
            return

        description = self.description_edit.text().strip()
        if not description:
            self._show_error("Название подключения не может быть пустым.")
            return

        open_network = self.open_network_check.isChecked()
        password = self.password_edit.text()
        if not open_network and not self._valid_wifi_password(password):
            self._show_error(
                "Пароль WPA/WPA2 должен содержать 8–63 символа "
                "или 64 шестнадцатеричные цифры."
            )
            return

        ipv4: dict[str, Any]
        dhcp_client: dict[str, Any] | None = None
        if self.ipv4_dhcp_check.isChecked():
            hostname = self.dhcp_hostname_edit.text().strip()
            if (
                not hostname
                or len(hostname) > 63
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", hostname)
                or hostname.endswith(("-", "."))
            ):
                self._show_error("Укажите корректное имя DHCP-клиента.")
                return
            ipv4 = {"dhcp": True}
            dhcp_client = {
                "hostname": hostname,
                "routes": self.dhcp_routes_check.isChecked(),
                "dns_routes": self.dhcp_dns_routes_check.isChecked(),
            }
        else:
            address = self._normalize_ipv4(self.ipv4_address_edit.text())
            gateway = self._normalize_ipv4(self.ipv4_gateway_edit.text())
            if not address or not gateway:
                self._show_error("Укажите корректные IPv4-адрес и шлюз.")
                return
            ipv4 = {
                "dhcp": False,
                "address": address,
                "prefix": self.ipv4_prefix_spin.value(),
                "gateway": gateway,
            }

        manual_dns = self._validated_manual_dns()
        if manual_dns is None:
            return
        ping_check = self._validated_ping_check()
        if ping_check is None:
            return

        bssid = ""
        if (
            self.bind_bssid_check.isChecked()
            and self._selected_network_matches_ssid()
        ):
            bssid = str(
                selected_network.get("bssid") or ""
            ).strip().upper()

        self._creation_settings = {
            "band": band,
            "interface_id": str(info.get("interface_id") or ""),
            "description": description,
            "ssid": ssid,
            "hidden": self.hidden_network_check.isChecked(),
            "open_network": open_network,
            "password": "" if open_network else password,
            "bssid": bssid,
            "mtu": self.mtu_spin.value(),
            "ipv4": ipv4,
            "use_provider_dns": self.provider_dns_check.isChecked(),
            "manual_dns": manual_dns,
            "priority_order": int(self.priority_combo.currentData() or 0),
            "ping_check": ping_check,
        }
        if dhcp_client is not None:
            self._creation_settings["dhcp_client"] = dhcp_client
        self.accept()

    def creation_settings(self) -> dict[str, Any] | None:
        if self._creation_settings is None:
            return None
        return dict(self._creation_settings)
