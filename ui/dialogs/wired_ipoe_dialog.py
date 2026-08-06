from __future__ import annotations

import ipaddress
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class CableInternetCreationDialog(QDialog):
    """
    Единый мастер кабельного Интернета.

    Поддерживает обычное IPoE, PPPoE, PPTP и L2TP поверх
    создаваемого транспортного Ethernet/VLAN-интерфейса.

    Список доступных LAN-портов обновляется во время работы
    мастера. Это важно, если назначение порта было изменено
    параллельно в веб-интерфейсе Keenetic.
    """

    ports_refresh_requested = Signal()

    def __init__(
        self,
        ports: list[dict[str, Any]],
        existing_priority_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._creation_settings: dict[str, Any] | None = None

        self.setWindowTitle(
            "Создать кабельный Интернет"
        )
        self.setModal(True)
        self.setMinimumSize(520, 430)

        host_window = (
            parent.window()
            if parent is not None
            else None
        )
        host_width = (
            host_window.width()
            if host_window is not None
            else 950
        )
        host_height = (
            host_window.height()
            if host_window is not None
            else 760
        )

        self.resize(
            max(520, min(760, host_width - 80)),
            max(430, min(720, host_height - 80)),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel(
            "Новое подключение по кабелю Ethernet"
        )
        title.setObjectName(
            "internetSettingsTitle"
        )
        root.addWidget(title)

        intro = QLabel(
            "Выбранный Ethernet-порт будет исключён из домашней "
            "сети и назначен провайдеру. Не выбирайте порт, "
            "через который этот компьютер подключён к роутеру."
        )
        intro.setWordWrap(True)
        intro.setObjectName(
            "internetSettingsWarning"
        )
        root.addWidget(intro)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(
            QFrame.Shape.NoFrame
        )
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

        self.port_combo = QComboBox()
        self._available_ports: list[dict[str, Any]] = []
        self._existing_priority_count = max(
            0,
            int(existing_priority_count),
        )

        form.addRow(
            "Ethernet-порт:",
            self.port_combo,
        )

        self.port_warning = QLabel()
        self.port_warning.setWordWrap(True)
        self.port_warning.setObjectName(
            "internetSettingsNotice"
        )
        form.addRow("", self.port_warning)

        self.description_edit = QLineEdit(
            "Проводное подключение"
        )
        self.description_edit.setMaxLength(64)
        form.addRow(
            "Название:",
            self.description_edit,
        )

        auth_title = QLabel(
            "Авторизация у провайдера"
        )
        auth_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        form.addRow("", auth_title)

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem(
            "Не использовать",
            "none",
        )
        self.protocol_combo.addItem(
            "PPPoE",
            "pppoe",
        )
        self.protocol_combo.addItem(
            "PPTP",
            "pptp",
        )
        self.protocol_combo.addItem(
            "L2TP",
            "l2tp",
        )
        form.addRow(
            "Тип (протокол):",
            self.protocol_combo,
        )

        self.protocol_notice = QLabel()
        self.protocol_notice.setWordWrap(True)
        self.protocol_notice.setObjectName(
            "internetSettingsNotice"
        )
        self.protocol_notice.hide()
        form.addRow("", self.protocol_notice)

        self.auth_widget = QWidget()
        self.auth_form = QFormLayout(self.auth_widget)
        self.auth_form.setContentsMargins(0, 0, 0, 0)
        self.auth_form.setHorizontalSpacing(18)
        self.auth_form.setVerticalSpacing(8)
        self.auth_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.provider_username_edit = QLineEdit()
        self.provider_username_edit.setMaxLength(128)
        self.provider_username_edit.setPlaceholderText(
            "Логин, выданный провайдером"
        )
        self.auth_form.addRow(
            "Имя пользователя:",
            self.provider_username_edit,
        )

        self.provider_password_edit = QLineEdit()
        self.provider_password_edit.setMaxLength(256)
        self.provider_password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.provider_password_edit.setPlaceholderText(
            "Пароль, выданный провайдером"
        )
        self.auth_form.addRow(
            "Пароль:",
            self.provider_password_edit,
        )

        self.pppoe_service_edit = QLineEdit()
        self.pppoe_service_edit.setMaxLength(64)
        self.pppoe_service_edit.setPlaceholderText(
            "Необязательно"
        )
        self.auth_form.addRow(
            "Имя сервиса PPPoE:",
            self.pppoe_service_edit,
        )

        self.provider_server_edit = QLineEdit()
        self.provider_server_edit.setMaxLength(253)
        self.provider_server_edit.setPlaceholderText(
            "Адрес сервера PPTP/L2TP"
        )
        self.auth_form.addRow(
            "Сервер провайдера:",
            self.provider_server_edit,
        )

        self.ppp_mtu_spin = QSpinBox()
        self.ppp_mtu_spin.setRange(576, 1500)
        self.ppp_mtu_spin.setValue(1492)
        self.auth_form.addRow(
            "MTU протокола:",
            self.ppp_mtu_spin,
        )

        form.addRow("", self.auth_widget)

        self.mtu_spin = QSpinBox()
        self.mtu_spin.setRange(576, 1500)
        self.mtu_spin.setValue(1500)
        form.addRow("MTU:", self.mtu_spin)

        ipv4_title = QLabel("Настройки IPv4")
        ipv4_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        form.addRow("", ipv4_title)

        self.ipv4_mode_combo = QComboBox()
        self.ipv4_mode_combo.addItem(
            "Автоматически (DHCP)",
            "dhcp",
        )
        self.ipv4_mode_combo.addItem(
            "Вручную",
            "manual",
        )
        form.addRow(
            "Получение IPv4:",
            self.ipv4_mode_combo,
        )

        self.ipv4_address_edit = QLineEdit()
        self.ipv4_address_edit.setPlaceholderText(
            "Например: 192.0.2.10"
        )
        form.addRow(
            "IPv4-адрес:",
            self.ipv4_address_edit,
        )

        self.ipv4_prefix_spin = QSpinBox()
        self.ipv4_prefix_spin.setRange(1, 32)
        self.ipv4_prefix_spin.setValue(24)
        form.addRow(
            "Длина префикса:",
            self.ipv4_prefix_spin,
        )

        self.ipv4_gateway_edit = QLineEdit()
        self.ipv4_gateway_edit.setPlaceholderText(
            "Например: 192.0.2.1"
        )
        form.addRow(
            "Шлюз:",
            self.ipv4_gateway_edit,
        )

        self.dhcp_hostname_edit = QLineEdit(
            "Keenetic"
        )
        self.dhcp_hostname_edit.setMaxLength(63)
        form.addRow(
            "Имя DHCP-клиента:",
            self.dhcp_hostname_edit,
        )

        self.dhcp_routes_check = QCheckBox(
            "Принимать маршруты от DHCP-сервера"
        )
        self.dhcp_routes_check.setChecked(True)
        form.addRow(
            "Маршруты DHCP:",
            self.dhcp_routes_check,
        )

        self.dhcp_dns_routes_check = QCheckBox(
            "Создавать маршруты к DNS провайдера"
        )
        self.dhcp_dns_routes_check.setChecked(True)
        form.addRow(
            "Маршруты DNS:",
            self.dhcp_dns_routes_check,
        )

        dns_title = QLabel("DNS")
        dns_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        form.addRow("", dns_title)

        self.provider_dns_check = QCheckBox(
            "Использовать DNS-серверы провайдера"
        )
        self.provider_dns_check.setChecked(True)
        form.addRow(
            "DNS провайдера:",
            self.provider_dns_check,
        )

        self.dns_primary_edit = QLineEdit()
        self.dns_primary_edit.setPlaceholderText(
            "Необязательно, например 1.1.1.1"
        )
        form.addRow(
            "Основной DNS:",
            self.dns_primary_edit,
        )

        self.dns_secondary_edit = QLineEdit()
        self.dns_secondary_edit.setPlaceholderText(
            "Необязательно, например 8.8.8.8"
        )
        form.addRow(
            "Дополнительный DNS:",
            self.dns_secondary_edit,
        )

        mac_title = QLabel("WAN MAC")
        mac_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        form.addRow("", mac_title)

        self.mac_mode_combo = QComboBox()
        self.mac_mode_combo.addItem(
            "Заводской WAN MAC",
            "factory",
        )
        self.mac_mode_combo.addItem(
            "Указать вручную",
            "manual",
        )
        form.addRow(
            "Режим MAC:",
            self.mac_mode_combo,
        )

        self.mac_address_edit = QLineEdit()
        self.mac_address_edit.setMaxLength(17)
        self.mac_address_edit.setPlaceholderText(
            "02:11:22:33:44:55"
        )
        form.addRow(
            "Ручной MAC:",
            self.mac_address_edit,
        )

        policy_title = QLabel(
            "Приоритет и проверка доступности"
        )
        policy_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        form.addRow("", policy_title)

        self.priority_combo = QComboBox()
        self._rebuild_priority_options(
            self._existing_priority_count
        )
        form.addRow(
            "Приоритет:",
            self.priority_combo,
        )

        self.ping_mode_combo = QComboBox()
        self.ping_mode_combo.addItem(
            "Выключено",
            "disabled",
        )
        self.ping_mode_combo.addItem(
            "Автоматически (Keenetic)",
            "automatic",
        )
        self.ping_mode_combo.addItem(
            "ICMP echo (Ping)",
            "icmp",
        )
        self.ping_mode_combo.addItem(
            "TCP/TLS — проверка порта",
            "tls",
        )
        self.ping_mode_combo.addItem(
            "TCP — проверка порта",
            "connect",
        )
        form.addRow(
            "Ping Check:",
            self.ping_mode_combo,
        )

        self.ping_host_edit = QLineEdit(
            "8.8.8.8"
        )
        self.ping_host_edit.setMaxLength(253)
        form.addRow(
            "Узел проверки:",
            self.ping_host_edit,
        )

        self.ping_port_spin = QSpinBox()
        self.ping_port_spin.setRange(1, 65535)
        self.ping_port_spin.setValue(443)
        form.addRow(
            "TCP-порт:",
            self.ping_port_spin,
        )

        self.ping_interval_spin = QSpinBox()
        self.ping_interval_spin.setRange(3, 3600)
        self.ping_interval_spin.setValue(10)
        self.ping_interval_spin.setSuffix(" с")
        form.addRow(
            "Интервал:",
            self.ping_interval_spin,
        )

        self.ping_fails_spin = QSpinBox()
        self.ping_fails_spin.setRange(1, 10)
        self.ping_fails_spin.setValue(5)
        form.addRow(
            "Неудачных попыток:",
            self.ping_fails_spin,
        )

        content_root.addLayout(form)
        content_root.addStretch(1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName(
            "internetSettingsWarning"
        )
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.save_button = save_button
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )

        if save_button is not None:
            save_button.setText(
                "Создать подключение"
            )

        if cancel_button is not None:
            cancel_button.setText("Назад")

        self.buttons.accepted.connect(
            self._accept_if_valid
        )
        self.buttons.rejected.connect(
            self.reject
        )
        root.addWidget(self.buttons)

        self.port_combo.currentIndexChanged.connect(
            self._update_port_warning
        )
        self.protocol_combo.currentIndexChanged.connect(
            self._update_protocol_fields
        )
        self.ipv4_mode_combo.currentIndexChanged.connect(
            self._update_ipv4_fields
        )
        self.mac_mode_combo.currentIndexChanged.connect(
            self._update_mac_fields
        )
        self.ping_mode_combo.currentIndexChanged.connect(
            self._update_ping_fields
        )

        self.update_available_ports(
            ports,
            existing_priority_count=(
                self._existing_priority_count
            ),
        )

        self._ports_refresh_timer = QTimer(self)
        self._ports_refresh_timer.setInterval(1500)
        self._ports_refresh_timer.timeout.connect(
            self.ports_refresh_requested.emit
        )
        self._ports_refresh_timer.start()

        self._update_protocol_fields()
        self._update_port_warning()
        self._update_ipv4_fields()
        self._update_mac_fields()
        self._update_ping_fields()

    def _rebuild_priority_options(
        self,
        count: int,
    ) -> None:
        count = max(0, int(count))
        old_count = max(
            0,
            int(getattr(self, "_existing_priority_count", 0)),
        )
        current_data = self.priority_combo.currentData()
        was_last = (
            self.priority_combo.count() > 0
            and current_data is not None
            and str(current_data).lstrip("-").isdigit()
            and int(current_data) == old_count
        )

        try:
            selected_order = int(current_data)
        except (TypeError, ValueError):
            selected_order = count

        if was_last:
            selected_order = count

        self.priority_combo.blockSignals(True)
        self.priority_combo.clear()

        for order in range(count + 1):
            number = order + 1

            if order == 0:
                title = f"{number} — основной"
            elif order == count:
                title = f"{number} — последний резервный"
            else:
                title = f"{number} — резервный"

            self.priority_combo.addItem(title, order)

        selected_order = max(0, min(count, selected_order))
        selected_index = self.priority_combo.findData(
            selected_order
        )
        self.priority_combo.setCurrentIndex(
            selected_index if selected_index >= 0 else count
        )
        self.priority_combo.blockSignals(False)
        self._existing_priority_count = count

    @staticmethod
    def _port_display_name(
        port: dict[str, Any],
    ) -> str:
        explicit = str(
            port.get("port_display_name") or ""
        ).strip()

        if explicit:
            return explicit

        mode = str(port.get("mode") or "").strip().lower()
        label = str(
            port.get("port_label")
            or port.get("label")
            or "?"
        ).strip()

        if mode == "dedicated_wan" or label.upper() == "WAN":
            number = str(
                port.get("port_number") or "0"
            ).strip()
            return f"WAN (порт {number})"

        return f"LAN{label}"

    def update_available_ports(
        self,
        ports: list[dict[str, Any]] | None,
        *,
        existing_priority_count: int | None = None,
    ) -> None:
        """Обновляет LAN-порты без закрытия мастера."""
        selected = self.port_combo.currentData()
        selected_port_id = (
            str(selected.get("port_id") or "")
            if isinstance(selected, dict)
            else ""
        )

        normalized = [
            dict(port)
            for port in (ports or [])
            if isinstance(port, dict)
            and str(port.get("port_id") or "").strip()
        ]
        self._available_ports = normalized

        self.port_combo.blockSignals(True)
        self.port_combo.clear()

        selected_index = -1

        for index, port in enumerate(normalized):
            display_name = self._port_display_name(port)
            link = str(
                port.get("link") or "unknown"
            ).strip().lower()

            if link == "up":
                state = "кабель подключён"
            elif link == "down":
                state = "кабель не подключён"
            else:
                state = "состояние кабеля неизвестно"

            self.port_combo.addItem(
                f"{display_name} — {state}",
                dict(port),
            )

            if str(port.get("port_id") or "") == selected_port_id:
                selected_index = index

        if normalized:
            self.port_combo.setEnabled(True)
            self.port_combo.setCurrentIndex(
                selected_index if selected_index >= 0 else 0
            )
        else:
            self.port_combo.addItem(
                "Свободных LAN-портов нет — ожидается обновление",
                None,
            )
            self.port_combo.setEnabled(False)

        self.port_combo.blockSignals(False)

        if existing_priority_count is not None:
            count = max(0, int(existing_priority_count))

            if count != self._existing_priority_count:
                self._rebuild_priority_options(count)

        self._update_port_warning()

    def creation_settings(
        self,
    ) -> dict[str, Any] | None:
        if self._creation_settings is None:
            return None

        return dict(self._creation_settings)

    def _show_error(
        self,
        message: str,
    ) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def _update_port_warning(self) -> None:
        port = self.port_combo.currentData()

        if not isinstance(port, dict):
            self.port_warning.setText(
                "Свободных LAN-портов пока нет. Список обновляется "
                "автоматически каждые несколько секунд."
            )
            self._update_save_state()
            return

        display_name = self._port_display_name(port)
        mode = str(port.get("mode") or "").strip().lower()
        link = str(
            port.get("link")
            or ""
        ).lower()

        if mode == "dedicated_wan":
            role_text = (
                f"{display_name} сейчас включён в домашнюю сеть как "
                "LAN0. После создания он снова станет выделенным "
                "портом провайдера."
            )
        else:
            role_text = (
                f"{display_name} относится к домашней сети и может "
                "быть назначен провайдеру."
            )

        if link == "up":
            self.port_warning.setText(
                role_text
                + " На порту подключён кабель; устройства на этом "
                "кабеле потеряют доступ к домашней сети."
            )
        elif link == "down":
            self.port_warning.setText(role_text)
        else:
            self.port_warning.setText(
                role_text
                + " Физическое состояние кабеля определить не удалось."
            )

        self._update_save_state()

        current_name = (
            self.description_edit.text().strip()
            if hasattr(self, "description_edit")
            else ""
        )

        if (
            not current_name
            or current_name
            == "Проводное подключение"
            or current_name.startswith(
                "Проводное подключение — порт "
            )
            or current_name.startswith(
                "PPPoE-подключение — порт "
            )
        ):
            self._set_default_description()

    def _update_protocol_fields(self) -> None:
        protocol = str(
            self.protocol_combo.currentData()
            or "none"
        ).strip().lower()

        uses_auth = protocol != "none"
        self.auth_widget.setVisible(uses_auth)

        pppoe = protocol == "pppoe"
        uses_server = protocol in {"pptp", "l2tp"}

        self._set_form_row_visible(
            self.auth_form,
            self.pppoe_service_edit,
            pppoe,
        )
        self._set_form_row_visible(
            self.auth_form,
            self.provider_server_edit,
            uses_server,
        )
        if protocol == "pppoe":
            self.ppp_mtu_spin.setMaximum(1492)
            self.ppp_mtu_spin.setValue(1492)
        elif protocol == "pptp":
            self.ppp_mtu_spin.setMaximum(1492)
            self.ppp_mtu_spin.setValue(1400)
        elif protocol == "l2tp":
            self.ppp_mtu_spin.setMaximum(1500)
            self.ppp_mtu_spin.setValue(1500)

        self.protocol_notice.clear()
        self.protocol_notice.hide()

        self._set_default_description()
        self._update_save_state()

    @staticmethod
    def _set_form_row_visible(
        form: QFormLayout,
        field: QWidget,
        visible: bool,
    ) -> None:
        """Показывает или скрывает поле вместе с его подписью."""
        label = form.labelForField(field)

        if label is not None:
            label.setVisible(bool(visible))

        field.setVisible(bool(visible))

    def _set_default_description(self) -> None:
        if not hasattr(self, "description_edit"):
            return

        port = self.port_combo.currentData()
        port_name = (
            self._port_display_name(port)
            if isinstance(port, dict)
            else "не выбран"
        )
        protocol = str(
            self.protocol_combo.currentData()
            or "none"
        ).strip().lower()
        current = self.description_edit.text().strip()
        known_prefixes = (
            "Проводное подключение — порт ",
            "PPPoE-подключение — порт ",
            "PPTP-подключение — порт ",
            "L2TP-подключение — порт ",
        )

        if current and not current.startswith(known_prefixes):
            return

        if protocol == "pppoe":
            title = "PPPoE-подключение"
        elif protocol == "pptp":
            title = "PPTP-подключение"
        elif protocol == "l2tp":
            title = "L2TP-подключение"
        else:
            title = "Проводное подключение"

        self.description_edit.setText(
            f"{title} — {port_name}"
        )

    def _update_save_state(self) -> None:
        save_button = getattr(self, "save_button", None)

        if save_button is None:
            return

        port = self.port_combo.currentData()
        link = (
            str(port.get("link") or "unknown").lower()
            if isinstance(port, dict)
            else "unknown"
        )
        protocol = str(
            self.protocol_combo.currentData()
            or "none"
        ).strip().lower()
        supported = protocol in {"none", "pppoe", "pptp", "l2tp"}
        enabled = isinstance(port, dict) and supported
        save_button.setEnabled(enabled)

        if not isinstance(port, dict):
            tooltip = "Свободный LAN-порт пока не найден."
        elif not supported:
            tooltip = "Выбранный протокол пока не поддерживается."
        elif link == "up":
            tooltip = (
                "Создать подключение и вывести выбранный LAN-порт "
                "из домашней сети"
            )
        else:
            tooltip = "Создать подключение"

        save_button.setToolTip(tooltip)

    def _update_ipv4_fields(self) -> None:
        dhcp = (
            self.ipv4_mode_combo.currentData()
            == "dhcp"
        )

        for widget in (
            self.ipv4_address_edit,
            self.ipv4_prefix_spin,
            self.ipv4_gateway_edit,
        ):
            widget.setEnabled(not dhcp)

        for widget in (
            self.dhcp_hostname_edit,
            self.dhcp_routes_check,
            self.dhcp_dns_routes_check,
        ):
            widget.setEnabled(dhcp)

    def _update_mac_fields(self) -> None:
        self.mac_address_edit.setEnabled(
            self.mac_mode_combo.currentData()
            == "manual"
        )

    def _update_ping_fields(self) -> None:
        mode = str(
            self.ping_mode_combo.currentData()
            or "disabled"
        )
        manual = mode in {
            "icmp",
            "tls",
            "connect",
        }
        use_port = mode in {
            "tls",
            "connect",
        }

        self.ping_host_edit.setEnabled(manual)
        self.ping_interval_spin.setEnabled(manual)
        self.ping_fails_spin.setEnabled(manual)
        self.ping_port_spin.setEnabled(use_port)

        if mode == "tls":
            self.ping_port_spin.setValue(443)
            self.ping_host_edit.setText(
                self.ping_host_edit.text().strip()
                or "google.com"
            )
        elif mode == "connect":
            self.ping_port_spin.setValue(53)
            self.ping_host_edit.setText(
                self.ping_host_edit.text().strip()
                or "8.8.8.8"
            )

    @staticmethod
    def _normalize_mac(
        value: str,
    ) -> str:
        compact = "".join(
            character
            for character in str(value or "")
            if character.isalnum()
        ).upper()

        if (
            len(compact) != 12
            or any(
                character not in "0123456789ABCDEF"
                for character in compact
            )
        ):
            return ""

        return ":".join(
            compact[index:index + 2]
            for index in range(0, 12, 2)
        )

    @staticmethod
    def _normalize_host(
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if (
            not text
            or len(text) > 253
            or any(
                character.isspace()
                for character in text
            )
            or "://" in text
            or "/" in text
        ):
            return ""

        try:
            return str(
                ipaddress.ip_address(text)
            )
        except ValueError:
            pass

        try:
            ascii_name = (
                text.rstrip(".")
                .encode("idna")
                .decode("ascii")
            )
        except UnicodeError:
            return ""

        for label in ascii_name.split("."):
            if (
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(
                    character
                    not in (
                        "abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789-"
                    )
                    for character in label
                )
            ):
                return ""

        return ascii_name.lower()

    def _accept_if_valid(self) -> None:
        self._clear_error()

        port = self.port_combo.currentData()

        if not isinstance(port, dict):
            self._show_error(
                "Свободный Ethernet-порт не найден."
            )
            return

        description = (
            self.description_edit.text().strip()
        )

        if not description:
            self._show_error(
                "Укажите название подключения."
            )
            return

        protocol = str(
            self.protocol_combo.currentData()
            or "none"
        ).strip().lower()

        if protocol not in {"none", "pppoe", "pptp", "l2tp"}:
            self._show_error(
                "Выбранный протокол пока не поддерживается."
            )
            return

        protocol_settings: dict[str, Any] = {
            "type": protocol,
        }

        if protocol in {"pppoe", "pptp", "l2tp"}:
            protocol_title = protocol.upper()
            username = self.provider_username_edit.text().strip()
            password = self.provider_password_edit.text()

            if not username:
                self._show_error(
                    f"Укажите имя пользователя {protocol_title}."
                )
                return

            if not password:
                self._show_error(
                    f"Укажите пароль {protocol_title}."
                )
                return

            if any(character in "\r\n\x00" for character in username):
                self._show_error(
                    f"Имя пользователя {protocol_title} содержит "
                    "недопустимые символы."
                )
                return

            if any(character in "\r\n\x00" for character in password):
                self._show_error(
                    f"Пароль {protocol_title} содержит "
                    "недопустимые символы."
                )
                return

            protocol_settings.update(
                {
                    "username": username,
                    "password": password,
                    "mtu": self.ppp_mtu_spin.value(),
                }
            )

            if protocol == "pppoe":
                protocol_settings["service"] = (
                    self.pppoe_service_edit.text().strip()
                )
            else:
                server = self._normalize_host(
                    self.provider_server_edit.text()
                )

                if not server:
                    self._show_error(
                        f"Укажите корректный адрес {protocol_title}-сервера."
                    )
                    return

                protocol_settings["server"] = server

        settings: dict[str, Any] = {
            "port": dict(port),
            "description": description,
            "mtu": self.mtu_spin.value(),
            "priority_order": int(
                self.priority_combo.currentData()
            ),
            "use_provider_dns": (
                self.provider_dns_check.isChecked()
            ),
            "protocol": protocol_settings,
        }

        ipv4_mode = str(
            self.ipv4_mode_combo.currentData()
        )

        if ipv4_mode == "dhcp":
            hostname = (
                self.dhcp_hostname_edit.text()
                .strip()
            )

            if not hostname:
                self._show_error(
                    "Имя DHCP-клиента не может быть пустым."
                )
                return

            allowed = set(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789-."
            )

            if (
                any(
                    character not in allowed
                    for character in hostname
                )
                or hostname.startswith(("-", "."))
                or hostname.endswith(("-", "."))
            ):
                self._show_error(
                    "Имя DHCP-клиента может содержать только "
                    "латинские буквы, цифры, точки и дефисы."
                )
                return

            settings["ipv4"] = {
                "dhcp": True,
            }
            settings["dhcp_client"] = {
                "hostname": hostname,
                "routes": (
                    self.dhcp_routes_check.isChecked()
                ),
                "dns_routes": (
                    self.dhcp_dns_routes_check.isChecked()
                ),
            }
        else:
            try:
                address = ipaddress.ip_address(
                    self.ipv4_address_edit.text().strip()
                )
                gateway = ipaddress.ip_address(
                    self.ipv4_gateway_edit.text().strip()
                )
            except ValueError:
                self._show_error(
                    "Проверьте ручной IPv4-адрес и шлюз."
                )
                return

            if (
                not isinstance(
                    address,
                    ipaddress.IPv4Address,
                )
                or not isinstance(
                    gateway,
                    ipaddress.IPv4Address,
                )
            ):
                self._show_error(
                    "Для ручной настройки нужны адреса IPv4."
                )
                return

            settings["ipv4"] = {
                "dhcp": False,
                "address": str(address),
                "prefix": (
                    self.ipv4_prefix_spin.value()
                ),
                "gateway": str(gateway),
            }

        dns_values: list[str] = []

        for edit in (
            self.dns_primary_edit,
            self.dns_secondary_edit,
        ):
            text = edit.text().strip()

            if not text:
                continue

            try:
                normalized = str(
                    ipaddress.ip_address(text)
                )
            except ValueError:
                self._show_error(
                    f"Некорректный DNS-адрес: {text}"
                )
                return

            if normalized not in dns_values:
                dns_values.append(normalized)

        settings["manual_dns"] = dns_values

        mac_mode = str(
            self.mac_mode_combo.currentData()
        )

        if mac_mode == "factory":
            settings["mac"] = {
                "mode": "factory",
            }
        else:
            mac = self._normalize_mac(
                self.mac_address_edit.text()
            )

            if not mac:
                self._show_error(
                    "Укажите MAC-адрес в формате "
                    "AA:BB:CC:DD:EE:FF."
                )
                return

            octets = [
                int(part, 16)
                for part in mac.split(":")
            ]

            if (
                mac in {
                    "00:00:00:00:00:00",
                    "FF:FF:FF:FF:FF:FF",
                }
                or octets[0] & 1
            ):
                self._show_error(
                    "Нельзя использовать нулевой, "
                    "широковещательный или multicast MAC."
                )
                return

            settings["mac"] = {
                "mode": "manual",
                "address": mac,
            }

        ping_mode = str(
            self.ping_mode_combo.currentData()
            or "disabled"
        )

        if ping_mode in {
            "disabled",
            "automatic",
        }:
            settings["ping_check"] = {
                "mode": ping_mode,
            }
        else:
            host = self._normalize_host(
                self.ping_host_edit.text()
            )

            if not host:
                self._show_error(
                    "Укажите корректный узел Ping Check."
                )
                return

            ping_settings = {
                "mode": ping_mode,
                "host": host,
                "interval": (
                    self.ping_interval_spin.value()
                ),
                "max_fails": (
                    self.ping_fails_spin.value()
                ),
            }

            if ping_mode in {
                "tls",
                "connect",
            }:
                ping_settings["port"] = (
                    self.ping_port_spin.value()
                )

            settings["ping_check"] = ping_settings

        self._creation_settings = settings
        self.accept()


class CableProtocolChangeDialog(QDialog):
    """Быстрая смена IPoE/PPPoE/PPTP/L2TP на существующем WAN."""

    PROTOCOLS = (
        ("Не использовать (IPoE)", "none"),
        ("PPPoE", "pppoe"),
        ("PPTP", "pptp"),
        ("L2TP", "l2tp"),
    )

    def __init__(
        self,
        connection: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.connection = dict(connection)
        self._change_settings: dict[str, Any] | None = None

        current = str(
            connection.get("protocol_change_current")
            or connection.get("provider_protocol")
            or "none"
        ).strip().lower()
        current = {
            "ipoe": "none",
            "ethernet": "none",
        }.get(current, current)

        if current not in {"none", "pppoe", "pptp", "l2tp"}:
            current = "none"

        self.current_protocol = current
        self.interface_id = str(
            connection.get("interface_id")
            or connection.get("id")
            or ""
        ).strip()
        self.transport_id = str(
            connection.get("protocol_change_transport_id")
            or connection.get("transport_interface_id")
            or self.interface_id
        ).strip()
        self.port_label = str(
            connection.get("protocol_change_port_label")
            or connection.get("provider_port_label")
            or ""
        ).strip()
        self.link_active = bool(
            connection.get("protocol_change_link_active")
            or connection.get("delete_port_link_active")
        )

        display_name = str(
            connection.get("description")
            or connection.get("name")
            or self.interface_id
            or "Кабельный Интернет"
        ).strip()

        self.setWindowTitle(f"Сменить тип — {display_name}")
        self.setModal(True)
        self.setMinimumSize(520, 430)
        self.resize(620, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Смена протокола кабельного Интернета")
        title.setObjectName("internetSettingsTitle")
        root.addWidget(title)

        current_title = {
            "none": "IPoE",
            "pppoe": "PPPoE",
            "pptp": "PPTP",
            "l2tp": "L2TP",
        }[self.current_protocol]
        if self.port_label.upper() == "WAN":
            port_text = "WAN"
        elif self.port_label:
            port_text = f"LAN{self.port_label}"
        else:
            port_text = "—"
        summary = QLabel(
            f"Текущее подключение: {current_title}\n"
            f"Интерфейс: {self.interface_id}\n"
            f"Транспорт: {self.transport_id}\n"
            f"Порт: {port_text}"
        )
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(summary)

        notice_text = (
            "Транспортный IPv4, WAN MAC и физический порт будут "
            "сохранены. Приоритет и Ping Check будут перенесены "
            "на новый протокол. Подключение кратковременно "
            "перезапустится."
        )
        if self.link_active:
            notice_text += (
                " Кабель подключён: во время переключения связь "
                "с провайдером может прерваться."
            )

        notice = QLabel(notice_text)
        notice.setWordWrap(True)
        notice.setObjectName("internetSettingsWarning")
        root.addWidget(notice)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.description_edit = QLineEdit(display_name)
        self.description_edit.setMaxLength(64)
        form.addRow("Название:", self.description_edit)

        self.target_combo = QComboBox()
        for protocol_title, protocol_id in self.PROTOCOLS:
            self.target_combo.addItem(protocol_title, protocol_id)
        current_index = self.target_combo.findData(
            self.current_protocol
        )
        self.target_combo.setCurrentIndex(
            current_index if current_index >= 0 else 0
        )
        form.addRow("Новый протокол:", self.target_combo)

        self.auth_widget = QWidget(self)
        auth_form = QFormLayout(self.auth_widget)
        auth_form.setContentsMargins(0, 0, 0, 0)
        auth_form.setHorizontalSpacing(18)
        auth_form.setVerticalSpacing(8)

        self.username_edit = QLineEdit()
        self.username_edit.setMaxLength(128)
        self.username_edit.setText(
            str(connection.get("provider_username") or "").strip()
        )
        self.username_edit.setPlaceholderText(
            "Логин, выданный провайдером"
        )
        auth_form.addRow("Имя пользователя:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setMaxLength(256)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(
            "Пароль, выданный провайдером"
        )
        auth_form.addRow("Пароль:", self.password_edit)

        self.service_edit = QLineEdit()
        self.service_edit.setMaxLength(64)
        self.service_edit.setText(
            str(connection.get("pppoe_service") or "").strip()
        )
        self.service_edit.setPlaceholderText("Необязательно")
        auth_form.addRow("Имя сервиса PPPoE:", self.service_edit)

        self.server_edit = QLineEdit()
        self.server_edit.setMaxLength(253)
        self.server_edit.setText(
            str(connection.get("provider_server") or "").strip()
        )
        self.server_edit.setPlaceholderText("Адрес PPTP/L2TP-сервера")
        auth_form.addRow("Сервер провайдера:", self.server_edit)

        self.mtu_spin = QSpinBox()
        self.mtu_spin.setRange(576, 1500)
        auth_form.addRow("MTU протокола:", self.mtu_spin)

        form.addRow("", self.auth_widget)
        root.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("internetSettingsError")
        self.error_label.hide()
        root.addWidget(self.error_label)
        root.addStretch()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        if self.save_button is not None:
            self.save_button.setText("Сменить тип")
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        if cancel_button is not None:
            cancel_button.setText("Назад")

        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.target_combo.currentIndexChanged.connect(
            self._update_fields
        )
        self._update_fields()

    def change_settings(self) -> dict[str, Any] | None:
        if self._change_settings is None:
            return None
        return dict(self._change_settings)

    def _update_fields(self) -> None:
        target = str(
            self.target_combo.currentData() or "none"
        ).strip().lower()
        authenticated = target != "none"
        self.auth_widget.setVisible(True)

        auth_layout = self.auth_widget.layout()
        for field in (
            self.username_edit,
            self.password_edit,
        ):
            field.setVisible(authenticated)
            label = auth_layout.labelForField(field)
            if label is not None:
                label.setVisible(authenticated)

        self.service_edit.setVisible(target == "pppoe")
        label = auth_layout.labelForField(
            self.service_edit
        )
        if label is not None:
            label.setVisible(target == "pppoe")
        self.server_edit.setVisible(target in {"pptp", "l2tp"})
        label = auth_layout.labelForField(
            self.server_edit
        )
        if label is not None:
            label.setVisible(target in {"pptp", "l2tp"})

        default_mtu = {
            "none": 1500,
            "pppoe": 1492,
            "pptp": 1400,
            "l2tp": 1500,
        }.get(target, 1500)
        self.mtu_spin.setValue(default_mtu)
        self.mtu_spin.setEnabled(True)

        if self.save_button is not None:
            changed = target != self.current_protocol
            self.save_button.setEnabled(changed)
            self.save_button.setToolTip(
                "Сменить протокол подключения"
                if changed
                else "Выберите другой протокол"
            )

        self.error_label.clear()
        self.error_label.hide()

    def _accept_if_valid(self) -> None:
        target = str(
            self.target_combo.currentData() or "none"
        ).strip().lower()
        description = self.description_edit.text().strip()

        if target == self.current_protocol:
            self._show_error("Выберите другой протокол подключения.")
            return
        if target not in {"none", "pppoe", "pptp", "l2tp"}:
            self._show_error("Выбранный протокол не поддерживается.")
            return
        if not description:
            self._show_error("Укажите название подключения.")
            return

        result: dict[str, Any] = {
            "target_protocol": target,
            "description": description,
            "mtu": self.mtu_spin.value(),
        }

        if target != "none":
            username = self.username_edit.text().strip()
            password = self.password_edit.text()
            protocol_title = target.upper()

            if not username:
                self._show_error(
                    f"Укажите имя пользователя {protocol_title}."
                )
                return
            if not password:
                self._show_error(f"Укажите пароль {protocol_title}.")
                return
            if any(ch in "\r\n\x00" for ch in username + password):
                self._show_error(
                    "Учётные данные содержат недопустимые символы."
                )
                return

            result.update(
                {
                    "username": username,
                    "password": password,
                }
            )

            if target == "pppoe":
                service = self.service_edit.text().strip()
                if any(ch in "\r\n\x00" for ch in service):
                    self._show_error(
                        "Имя сервиса содержит недопустимые символы."
                    )
                    return
                result["service"] = service
            else:
                server = CableInternetCreationDialog._normalize_host(
                    self.server_edit.text()
                )
                if not server:
                    self._show_error(
                        f"Укажите корректный адрес {protocol_title}-сервера."
                    )
                    return
                result["server"] = server

        self._change_settings = result
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()


# Обратная совместимость для старых импортов и GUI-тестов.
WiredIpoeCreationDialog = CableInternetCreationDialog
