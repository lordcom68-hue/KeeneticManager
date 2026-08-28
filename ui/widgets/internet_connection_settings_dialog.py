from __future__ import annotations

import ipaddress
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def _connection_is_connected(connection: dict[str, Any]) -> bool:
    value = (
        connection.get("connected")
        or connection.get("link")
        or connection.get("status")
    )
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "yes", "true", "up", "online", "connected", "running",
    }


def _connection_is_enabled(connection: dict[str, Any]) -> bool:
    value = connection.get("enabled")
    if value is None:
        state = str(connection.get("state") or "").strip().lower()
        return state not in {"disabled", "off"}
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {
        "no", "false", "0", "off", "disabled",
    }


class InternetConnectionSettingsDialog(QDialog):
    """
    Просмотр и безопасное редактирование параметров WAN.

    Для поддерживаемых проводных подключений этот же диалог
    позволяет сменить IPoE, PPPoE, PPTP или L2TP. Отдельная
    кнопка смены типа в списке подключений не требуется.
    """

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
        self._protocol_change_settings: dict[str, Any] | None = None
        self.interface_id = str(
            connection.get("interface_id")
            or connection.get("id")
            or ""
        ).strip()

        self.original_description = self._text(
            connection.get("description")
            or connection.get("interface_name")
            or connection.get("name")
        )

        self.wireless_isp_settings_supported = bool(
            connection.get("wireless_isp_settings_supported")
        )
        self.original_wireless_band = str(
            connection.get("wireless_isp_band") or ""
        ).strip()
        self.original_wireless_band_title = str(
            connection.get("wireless_isp_band_title")
            or (
                "2,4 ГГц"
                if self.original_wireless_band == "2.4"
                else "5 ГГц"
                if self.original_wireless_band == "5"
                else "не определён"
            )
        ).strip()
        self.original_wireless_ssid = str(
            connection.get("wireless_isp_ssid")
            or connection.get("ssid")
            or ""
        ).strip()
        self.original_wireless_bssid = str(
            connection.get("wireless_isp_bssid")
            or ""
        ).strip().upper()
        self.original_wireless_open_network = bool(
            connection.get("wireless_isp_open_network")
        )
        self.original_wireless_password_configured = bool(
            connection.get("wireless_isp_password_configured")
        )

        self.protocol_change_supported = bool(
            connection.get("protocol_change_supported")
        )
        self.protocol_change_reason = str(
            connection.get("protocol_change_reason")
            or ""
        ).strip()
        current_protocol = str(
            connection.get("protocol_change_current")
            or connection.get("provider_protocol")
            or "none"
        ).strip().lower()
        self.current_protocol = {
            "ipoe": "none",
            "ethernet": "none",
        }.get(current_protocol, current_protocol)

        if self.current_protocol not in {
            "none",
            "pppoe",
            "pptp",
            "l2tp",
        }:
            self.current_protocol = "none"

        self.original_mtu = self._parse_mtu(
            connection.get("mtu")
        )
        self.original_use_provider_dns = bool(
            connection.get(
                "use_provider_dns",
                True,
            )
        )
        configured_dns = connection.get(
            "configured_dns"
        )

        if isinstance(configured_dns, str):
            configured_dns = [
                configured_dns
            ]

        if not isinstance(configured_dns, list):
            configured_dns = []

        self.original_manual_dns = [
            str(value).strip()
            for value in configured_dns
            if str(value).strip()
        ]

        self.ipv4_settings_supported = bool(
            connection.get(
                "ipv4_settings_supported",
                False,
            )
        )
        self.original_ipv4_dhcp = bool(
            connection.get(
                "ipv4_dhcp",
                True,
            )
        )
        self.original_ipv4_address = str(
            connection.get(
                "configured_ipv4_address"
            )
            or ""
        ).strip()

        try:
            self.original_ipv4_prefix = int(
                connection.get(
                    "configured_ipv4_prefix"
                )
            )
        except (TypeError, ValueError):
            self.original_ipv4_prefix = None

        self.original_ipv4_gateway = str(
            connection.get(
                "configured_ipv4_gateway"
            )
            or ""
        ).strip()

        self.dhcp_client_supported = bool(
            connection.get(
                "dhcp_client_supported",
                self.ipv4_settings_supported,
            )
        )
        self.original_dhcp_hostname = str(
            connection.get(
                "dhcp_client_hostname"
            )
            or "Keenetic"
        ).strip()
        self.original_dhcp_routes = bool(
            connection.get(
                "dhcp_client_routes",
                True,
            )
        )
        self.original_dhcp_dns_routes = bool(
            connection.get(
                "dhcp_client_dns_routes",
                True,
            )
        )

        self.mac_settings_supported = bool(
            connection.get(
                "mac_settings_supported",
                False,
            )
        )
        self.original_mac_mode = str(
            connection.get("mac_mode")
            or "factory"
        ).strip().lower()

        if self.original_mac_mode not in {
            "factory",
            "manual",
        }:
            self.original_mac_mode = "factory"

        self.original_manual_mac = str(
            connection.get(
                "configured_mac"
            )
            or ""
        ).strip().upper()

        self.ping_check_supported = bool(
            connection.get(
                "ping_check_supported",
                True,
            )
        )
        self.original_ping_check_enabled = bool(
            connection.get(
                "ping_check_enabled",
                False,
            )
        )
        self.original_ping_check_mode = str(
            connection.get(
                "ping_check_mode"
            )
            or (
                "icmp"
                if self.original_ping_check_enabled
                else "disabled"
            )
        ).strip().lower()

        if self.original_ping_check_mode not in {
            "disabled",
            "automatic",
            "icmp",
            "tls",
            "connect",
            "custom",
        }:
            self.original_ping_check_mode = (
                "icmp"
                if self.original_ping_check_enabled
                else "disabled"
            )

        self.original_ping_check_profile = str(
            connection.get(
                "ping_check_profile"
            )
            or ""
        ).strip()

        self.original_ping_check_host = str(
            connection.get(
                "ping_check_host"
            )
            or (
                "8.8.8.8"
                if self.original_ping_check_mode == "icmp"
                else ""
            )
        ).strip()

        try:
            self.original_ping_check_interval = int(
                connection.get(
                    "ping_check_interval",
                    10,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_interval = 10

        self.original_ping_check_interval = max(
            3,
            min(
                3600,
                self.original_ping_check_interval,
            ),
        )

        try:
            self.original_ping_check_max_fails = int(
                connection.get(
                    "ping_check_max_fails",
                    5,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_max_fails = 5

        self.original_ping_check_max_fails = max(
            1,
            min(
                10,
                self.original_ping_check_max_fails,
            ),
        )

        default_ping_port = (
            53
            if self.original_ping_check_mode == "connect"
            else 443
        )

        try:
            self.original_ping_check_port = int(
                connection.get(
                    "ping_check_port",
                    default_ping_port,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_port = (
                default_ping_port
            )

        self.original_ping_check_port = max(
            1,
            min(
                65535,
                self.original_ping_check_port,
            ),
        )

        display_name = (
            self.original_description
            or self.interface_id
            or "Интернет-подключение"
        )

        self.setWindowTitle(
            f"Настройка — {display_name}"
        )
        self.setModal(True)
        self.setMinimumSize(460, 360)

        host_window = (
            parent.window()
            if parent is not None
            else None
        )
        host_width = (
            host_window.width()
            if host_window is not None
            else 900
        )
        host_height = (
            host_window.height()
            if host_window is not None
            else 700
        )

        dialog_width = max(
            460,
            min(
                760,
                host_width - 70,
            ),
        )
        dialog_height = max(
            360,
            min(
                720,
                host_height - 70,
            ),
        )

        self.resize(
            dialog_width,
            dialog_height,
        )
        self.setMaximumSize(
            max(460, host_width - 30),
            max(360, host_height - 30),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(
            QFrame.Shape.NoFrame
        )
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll_content = QWidget(scroll_area)
        scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        content_layout = QVBoxLayout(
            scroll_content
        )
        content_layout.setContentsMargins(
            4,
            2,
            8,
            6,
        )
        content_layout.setSpacing(14)

        title = QLabel(display_name)
        title.setObjectName(
            "internetSettingsTitle"
        )
        title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content_layout.addWidget(title)

        if self.wireless_isp_settings_supported:
            notice_text = (
                "Можно изменить Wi-Fi-сеть Wireless ISP, пароль, "
                "BSSID, пользовательское имя, MTU, IPv4, DHCP, "
                "DNS и проверку доступности. Диапазон 2,4/5 ГГц "
                "закреплён за текущим интерфейсом."
            )
        elif self.protocol_change_supported:
            notice_text = (
                "Можно изменить тип проводного WAN, пользовательское "
                "имя, MTU, WAN MAC, IPv4, DHCP, DNS и проверку "
                "доступности. Недоступные настройки не отображаются."
            )
        else:
            notice_text = (
                "Можно изменить пользовательское имя, MTU, "
                "WAN MAC, IPv4, DHCP, DNS и проверку доступности. "
                "Недоступные настройки не отображаются."
            )
        notice = QLabel(notice_text)
        notice.setWordWrap(True)
        notice.setObjectName(
            "internetSettingsNotice"
        )
        content_layout.addWidget(notice)

        edit_form = QFormLayout()
        edit_form.setHorizontalSpacing(18)
        edit_form.setVerticalSpacing(9)
        edit_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        edit_form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

        self.description_edit = QLineEdit(
            self.original_description
        )
        self.description_edit.setMaxLength(64)
        self.description_edit.setPlaceholderText(
            "Например: Основной провайдер"
        )
        edit_form.addRow(
            "Пользовательское имя:",
            self.description_edit,
        )

        self.wireless_ssid_edit: QLineEdit | None = None
        self.wireless_open_network_check: QCheckBox | None = None
        self.wireless_password_edit: QLineEdit | None = None
        self.wireless_bssid_edit: QLineEdit | None = None

        if self.wireless_isp_settings_supported:
            wireless_section = QLabel(
                "Подключение к Wi-Fi провайдера"
            )
            wireless_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", wireless_section)

            band_value = QLabel(
                self.original_wireless_band_title
            )
            band_value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            band_value.setToolTip(
                "Для смены диапазона удалите это подключение и "
                "создайте Wireless ISP в другом диапазоне."
            )
            edit_form.addRow(
                "Диапазон:",
                band_value,
            )

            self.wireless_ssid_edit = QLineEdit(
                self.original_wireless_ssid
            )
            self.wireless_ssid_edit.setMaxLength(32)
            self.wireless_ssid_edit.setPlaceholderText(
                "Имя Wi-Fi-сети провайдера"
            )
            edit_form.addRow(
                "Wi-Fi-сеть (SSID):",
                self.wireless_ssid_edit,
            )

            self.wireless_open_network_check = QCheckBox(
                "Сеть не защищена паролем"
            )
            self.wireless_open_network_check.setChecked(
                self.original_wireless_open_network
            )
            edit_form.addRow(
                "Защита:",
                self.wireless_open_network_check,
            )

            self.wireless_password_edit = QLineEdit()
            self.wireless_password_edit.setEchoMode(
                QLineEdit.EchoMode.Password
            )
            self.wireless_password_edit.setMaxLength(64)
            self.wireless_password_edit.setPlaceholderText(
                (
                    "Оставьте пустым, чтобы сохранить текущий пароль"
                    if self.original_wireless_password_configured
                    else "Пароль WPA/WPA2 — 8–63 символа"
                )
            )
            edit_form.addRow(
                "Пароль Wi-Fi:",
                self.wireless_password_edit,
            )

            self.wireless_bssid_edit = QLineEdit(
                self.original_wireless_bssid
            )
            self.wireless_bssid_edit.setMaxLength(17)
            self.wireless_bssid_edit.setPlaceholderText(
                "Необязательно, например AA:BB:CC:DD:EE:FF"
            )
            self.wireless_bssid_edit.setToolTip(
                "Пустое поле разрешает подключаться к любой точке "
                "с выбранным SSID."
            )
            edit_form.addRow(
                "BSSID:",
                self.wireless_bssid_edit,
            )

            self.wireless_open_network_check.toggled.connect(
                self._update_wireless_security_fields
            )
            self._update_wireless_security_fields()

        self.protocol_combo: QComboBox | None = None
        self.protocol_auth_widget: QWidget | None = None
        self.protocol_username_edit: QLineEdit | None = None
        self.protocol_password_edit: QLineEdit | None = None
        self.protocol_service_edit: QLineEdit | None = None
        self.protocol_server_edit: QLineEdit | None = None
        self.protocol_note_label: QLabel | None = None
        self.save_button: QPushButton | None = None
        self._protocol_previous_target = self.current_protocol
        self._protocol_mtu_values: dict[str, str] = {}

        if self.protocol_change_supported:
            protocol_section = QLabel(
                "Авторизация у провайдера"
            )
            protocol_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", protocol_section)

            self.protocol_combo = QComboBox()
            for protocol_title, protocol_id in self.PROTOCOLS:
                self.protocol_combo.addItem(
                    protocol_title,
                    protocol_id,
                )

            protocol_index = self.protocol_combo.findData(
                self.current_protocol
            )
            self.protocol_combo.setCurrentIndex(
                protocol_index if protocol_index >= 0 else 0
            )
            self.protocol_combo.setToolTip(
                "Выберите IPoE, PPPoE, PPTP или L2TP. "
                "Физический порт и транспорт сохранятся."
            )
            edit_form.addRow(
                "Тип (протокол):",
                self.protocol_combo,
            )

            self.protocol_auth_widget = QWidget(self)
            protocol_auth_form = QFormLayout(
                self.protocol_auth_widget
            )
            protocol_auth_form.setContentsMargins(
                0,
                0,
                0,
                0,
            )
            protocol_auth_form.setHorizontalSpacing(18)
            protocol_auth_form.setVerticalSpacing(8)
            protocol_auth_form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )

            self.protocol_username_edit = QLineEdit()
            self.protocol_username_edit.setMaxLength(128)
            self.protocol_username_edit.setText(
                str(
                    connection.get("provider_username")
                    or ""
                ).strip()
            )
            self.protocol_username_edit.setPlaceholderText(
                "Логин, выданный провайдером"
            )
            protocol_auth_form.addRow(
                "Имя пользователя:",
                self.protocol_username_edit,
            )

            self.protocol_password_edit = QLineEdit()
            self.protocol_password_edit.setMaxLength(256)
            self.protocol_password_edit.setEchoMode(
                QLineEdit.EchoMode.Password
            )
            self.protocol_password_edit.setPlaceholderText(
                "Пароль, выданный провайдером"
            )
            protocol_auth_form.addRow(
                "Пароль:",
                self.protocol_password_edit,
            )

            self.protocol_service_edit = QLineEdit()
            self.protocol_service_edit.setMaxLength(64)
            self.protocol_service_edit.setText(
                str(
                    connection.get("pppoe_service")
                    or ""
                ).strip()
            )
            self.protocol_service_edit.setPlaceholderText(
                "Необязательно"
            )
            protocol_auth_form.addRow(
                "Имя сервиса PPPoE:",
                self.protocol_service_edit,
            )

            self.protocol_server_edit = QLineEdit()
            self.protocol_server_edit.setMaxLength(253)
            self.protocol_server_edit.setText(
                str(
                    connection.get("provider_server")
                    or ""
                ).strip()
            )
            self.protocol_server_edit.setPlaceholderText(
                "Адрес PPTP/L2TP-сервера"
            )
            protocol_auth_form.addRow(
                "Сервер провайдера:",
                self.protocol_server_edit,
            )

            edit_form.addRow(
                "",
                self.protocol_auth_widget,
            )

            self.protocol_note_label = QLabel()
            self.protocol_note_label.setWordWrap(True)
            self.protocol_note_label.setObjectName(
                "internetSettingsNotice"
            )
            edit_form.addRow(
                "",
                self.protocol_note_label,
            )

            self.protocol_combo.currentIndexChanged.connect(
                self._update_protocol_fields
            )
        self.mtu_edit = QLineEdit(
            (
                str(self.original_mtu)
                if self.original_mtu is not None
                else ""
            )
        )
        self.mtu_edit.setPlaceholderText(
            "576–9000"
        )
        self.mtu_edit.setMaxLength(4)
        edit_form.addRow(
            "MTU:",
            self.mtu_edit,
        )

        self.mac_mode_combo: QComboBox | None = None
        self.mac_address_edit: QLineEdit | None = None

        if self.mac_settings_supported:
            self.mac_mode_combo = QComboBox()
            self.mac_mode_combo.addItem(
                "Заводской WAN MAC",
                "factory",
            )
            self.mac_mode_combo.addItem(
                "Указать вручную",
                "manual",
            )

            mac_mode_index = (
                self.mac_mode_combo.findData(
                    self.original_mac_mode
                )
            )

            if mac_mode_index >= 0:
                self.mac_mode_combo.setCurrentIndex(
                    mac_mode_index
                )

            edit_form.addRow(
                "MAC-адрес:",
                self.mac_mode_combo,
            )

            self.mac_address_edit = QLineEdit(
                (
                    self.original_manual_mac
                    if self.original_mac_mode
                    == "manual"
                    else ""
                )
            )
            self.mac_address_edit.setPlaceholderText(
                "Например: 02:11:22:33:44:55"
            )
            self.mac_address_edit.setMaxLength(17)
            self.mac_address_edit.setToolTip(
                "Ручной WAN MAC в формате "
                "AA:BB:CC:DD:EE:FF"
            )
            edit_form.addRow(
                "Ручной MAC:",
                self.mac_address_edit,
            )

            self.mac_mode_combo.currentIndexChanged.connect(
                self._update_mac_fields
            )
            self._update_mac_fields()

            mac_warning = QLabel(
                "Изменение MAC активного WAN может "
                "временно разорвать соединение и обновить "
                "выданный провайдером IP-адрес."
            )
            mac_warning.setWordWrap(True)
            mac_warning.setObjectName(
                "internetSettingsWarning"
            )
            edit_form.addRow("", mac_warning)

        self.ipv4_dhcp_check: QCheckBox | None = None
        self.ipv4_address_edit: QLineEdit | None = None
        self.ipv4_prefix_edit: QLineEdit | None = None
        self.ipv4_gateway_edit: QLineEdit | None = None
        self.dhcp_hostname_edit: QLineEdit | None = None
        self.dhcp_routes_check: QCheckBox | None = None
        self.dhcp_dns_routes_check: QCheckBox | None = None

        if self.ipv4_settings_supported:
            self.ipv4_dhcp_check = QCheckBox(
                "Получать IPv4-адрес автоматически (DHCP)"
            )
            self.ipv4_dhcp_check.setChecked(
                self.original_ipv4_dhcp
            )
            self.ipv4_dhcp_check.setToolTip(
                "DHCP автоматически получает адрес, маску, "
                "DNS и шлюз от провайдера"
            )
            edit_form.addRow(
                "Настройка IPv4:",
                self.ipv4_dhcp_check,
            )

            self.ipv4_address_edit = QLineEdit(
                self.original_ipv4_address
            )
            self.ipv4_address_edit.setPlaceholderText(
                "Например: 192.0.2.10"
            )
            edit_form.addRow(
                "IPv4-адрес:",
                self.ipv4_address_edit,
            )

            prefix_text = (
                str(self.original_ipv4_prefix)
                if self.original_ipv4_prefix is not None
                else ""
            )
            self.ipv4_prefix_edit = QLineEdit(
                prefix_text
            )
            self.ipv4_prefix_edit.setPlaceholderText(
                "1–32, например 24"
            )
            self.ipv4_prefix_edit.setMaxLength(2)
            edit_form.addRow(
                "Длина префикса:",
                self.ipv4_prefix_edit,
            )

            self.ipv4_gateway_edit = QLineEdit(
                self.original_ipv4_gateway
            )
            self.ipv4_gateway_edit.setPlaceholderText(
                "Например: 192.0.2.1"
            )
            edit_form.addRow(
                "Шлюз:",
                self.ipv4_gateway_edit,
            )

            if self.dhcp_client_supported:
                self.dhcp_hostname_edit = QLineEdit(
                    self.original_dhcp_hostname
                )
                self.dhcp_hostname_edit.setMaxLength(63)
                self.dhcp_hostname_edit.setPlaceholderText(
                    "Например: Keenetic"
                )
                self.dhcp_hostname_edit.setToolTip(
                    "Имя, которое DHCP-клиент передаёт "
                    "серверу провайдера"
                )
                edit_form.addRow(
                    "Имя DHCP-клиента:",
                    self.dhcp_hostname_edit,
                )

                self.dhcp_routes_check = QCheckBox(
                    "Принимать маршруты от DHCP-сервера"
                )
                self.dhcp_routes_check.setChecked(
                    self.original_dhcp_routes
                )
                self.dhcp_routes_check.setToolTip(
                    "Разрешить провайдеру передавать "
                    "дополнительные маршруты по DHCP"
                )
                edit_form.addRow(
                    "Маршруты DHCP:",
                    self.dhcp_routes_check,
                )

                self.dhcp_dns_routes_check = QCheckBox(
                    "Создавать маршруты к DNS провайдера"
                )
                self.dhcp_dns_routes_check.setChecked(
                    self.original_dhcp_dns_routes
                )
                self.dhcp_dns_routes_check.setToolTip(
                    "Создавать маршруты к DNS-серверам, "
                    "полученным через этот DHCP-клиент"
                )
                edit_form.addRow(
                    "Маршруты DNS:",
                    self.dhcp_dns_routes_check,
                )

            self.ipv4_dhcp_check.toggled.connect(
                self._update_ipv4_fields
            )
            self._update_ipv4_fields(
                self.original_ipv4_dhcp
            )

            ipv4_warning = QLabel(
                "Изменение IPv4 активного интернет-канала "
                "может временно разорвать соединение с роутером."
            )
            ipv4_warning.setWordWrap(True)
            ipv4_warning.setObjectName(
                "internetSettingsWarning"
            )
            edit_form.addRow("", ipv4_warning)

        self.ping_check_mode_combo: QComboBox | None = None
        self.ping_check_host_edit: QLineEdit | None = None
        self.ping_check_port_spin: QSpinBox | None = None
        self.ping_check_interval_spin: QSpinBox | None = None
        self.ping_check_max_fails_spin: QSpinBox | None = None
        self.ping_check_note_label: QLabel | None = None
        self._last_ping_check_mode = (
            self.original_ping_check_mode
        )

        if self.ping_check_supported:
            ping_section = QLabel(
                "Проверка доступности интернет-канала"
            )
            ping_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", ping_section)

            self.ping_check_mode_combo = QComboBox()
            self.ping_check_mode_combo.addItem(
                "Выключено",
                "disabled",
            )
            self.ping_check_mode_combo.addItem(
                "Автоматически (Keenetic)",
                "automatic",
            )
            self.ping_check_mode_combo.addItem(
                "ICMP echo (Ping)",
                "icmp",
            )
            self.ping_check_mode_combo.addItem(
                "TCP/TLS — проверка порта",
                "tls",
            )
            self.ping_check_mode_combo.addItem(
                "TCP — проверка порта",
                "connect",
            )

            if self.original_ping_check_mode == "custom":
                self.ping_check_mode_combo.addItem(
                    (
                        "Существующий профиль "
                        "(не изменять)"
                    ),
                    "custom",
                )

            ping_mode_index = (
                self.ping_check_mode_combo.findData(
                    self.original_ping_check_mode
                )
            )

            if ping_mode_index < 0:
                ping_mode_index = 0

            self.ping_check_mode_combo.setCurrentIndex(
                ping_mode_index
            )
            self.ping_check_mode_combo.setToolTip(
                "Автоматический режим соответствует "
                "стандартной проверке веб-интерфейса Keenetic"
            )
            edit_form.addRow(
                "Режим Ping Check:",
                self.ping_check_mode_combo,
            )

            self.ping_check_host_edit = QLineEdit(
                self.original_ping_check_host
            )
            self.ping_check_host_edit.setMaxLength(253)
            self.ping_check_host_edit.setPlaceholderText(
                "Например: 8.8.8.8 или example.com"
            )
            edit_form.addRow(
                "Узел проверки:",
                self.ping_check_host_edit,
            )

            self.ping_check_port_spin = QSpinBox()
            self.ping_check_port_spin.setRange(
                1,
                65535,
            )
            self.ping_check_port_spin.setValue(
                self.original_ping_check_port
            )
            self.ping_check_port_spin.setToolTip(
                "TCP-порт удалённого узла. "
                "Для TLS обычно используется 443, "
                "для DNS по TCP — 53"
            )
            edit_form.addRow(
                "TCP-порт:",
                self.ping_check_port_spin,
            )

            self.ping_check_interval_spin = QSpinBox()
            self.ping_check_interval_spin.setRange(
                3,
                3600,
            )
            self.ping_check_interval_spin.setValue(
                self.original_ping_check_interval
            )
            self.ping_check_interval_spin.setSuffix(
                " с"
            )
            self.ping_check_interval_spin.setToolTip(
                "Период между проверками"
            )
            edit_form.addRow(
                "Интервал:",
                self.ping_check_interval_spin,
            )

            self.ping_check_max_fails_spin = QSpinBox()
            self.ping_check_max_fails_spin.setRange(
                1,
                10,
            )
            self.ping_check_max_fails_spin.setValue(
                self.original_ping_check_max_fails
            )
            self.ping_check_max_fails_spin.setToolTip(
                "Количество последовательных ошибок, "
                "после которых канал считается недоступным"
            )
            edit_form.addRow(
                "Неудачных попыток:",
                self.ping_check_max_fails_spin,
            )

            self.ping_check_note_label = QLabel()
            self.ping_check_note_label.setWordWrap(True)
            self.ping_check_note_label.setObjectName(
                "internetSettingsNotice"
            )
            edit_form.addRow(
                "",
                self.ping_check_note_label,
            )

            self.ping_check_mode_combo.currentIndexChanged.connect(
                self._update_ping_check_fields
            )
            self._update_ping_check_fields()

        self.provider_dns_check = QCheckBox(
            "Использовать DNS-серверы, полученные "
            "от провайдера"
        )
        self.provider_dns_check.setChecked(
            self.original_use_provider_dns
        )
        self.provider_dns_check.setToolTip(
            "Соответствует параметру ip.name-servers "
            "выбранного интерфейса"
        )
        edit_form.addRow(
            "DNS провайдера:",
            self.provider_dns_check,
        )

        primary_dns = (
            self.original_manual_dns[0]
            if len(self.original_manual_dns) >= 1
            else ""
        )
        secondary_dns = (
            self.original_manual_dns[1]
            if len(self.original_manual_dns) >= 2
            else ""
        )

        self.primary_dns_edit = QLineEdit(
            primary_dns
        )
        self.primary_dns_edit.setPlaceholderText(
            "Например: 1.1.1.1 или 2606:4700:4700::1111"
        )
        edit_form.addRow(
            "Основной DNS:",
            self.primary_dns_edit,
        )

        self.secondary_dns_edit = QLineEdit(
            secondary_dns
        )
        self.secondary_dns_edit.setPlaceholderText(
            "Например: 8.8.8.8 или 2001:4860:4860::8888"
        )
        edit_form.addRow(
            "Резервный DNS:",
            self.secondary_dns_edit,
        )

        dns_note_text = (
            "Оставьте оба поля пустыми, чтобы удалить "
            "ручные DNS этого подключения."
        )

        if len(self.original_manual_dns) > 2:
            dns_note_text += (
                "\nНа роутере найдено больше двух записей. "
                "При сохранении лишние DNS будут удалены."
            )

        dns_note = QLabel(
            dns_note_text
        )
        dns_note.setWordWrap(True)
        dns_note.setObjectName(
            "internetSettingsNotice"
        )
        edit_form.addRow("", dns_note)

        mtu_note = QLabel(
            "Изменяйте MTU только при необходимости. "
            "Неправильное значение может нарушить "
            "доступ к отдельным сайтам и сервисам."
        )
        mtu_note.setWordWrap(True)
        mtu_note.setObjectName(
            "internetSettingsWarning"
        )
        edit_form.addRow("", mtu_note)

        content_layout.addLayout(edit_form)

        details_title = QLabel(
            "Текущие параметры"
        )
        details_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        content_layout.addWidget(details_title)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

        fields = (
            (
                "Интерфейс",
                self.interface_id,
            ),
            (
                "Тип подключения",
                connection.get("type")
                or connection.get("connection_type"),
            ),
            (
                "Состояние",
                self._status_text(connection),
            ),
            (
                "IPv4-адрес",
                connection.get("address")
                or connection.get("ip"),
            ),
            (
                "IPv6-адрес",
                connection.get("ipv6_address"),
            ),
            (
                "Шлюз",
                connection.get("gateway"),
            ),
            (
                "DNS-серверы",
                connection.get("dns"),
            ),
            (
                "MAC-адрес",
                connection.get("mac"),
            ),
            (
                "Скорость линии",
                self._format_speed(
                    connection.get("link_speed_bps")
                ),
            ),
            (
                "Время подключения",
                connection.get("uptime"),
            ),
            (
                "Источник статистики",
                connection.get("traffic_source"),
            ),
            (
                "Приоритет",
                self._priority_text(connection),
            ),
        )

        for caption, value in fields:
            text = self._text(value)

            if not text:
                continue

            value_label = QLabel(text)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(
                f"{caption}:",
                value_label,
            )

        content_layout.addLayout(form)

        content_layout.addStretch(1)
        scroll_area.setWidget(
            scroll_content
        )
        root.addWidget(
            scroll_area,
            1,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        save_button = buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.save_button = save_button
        cancel_button = buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )

        if save_button is not None:
            save_button.setText("Сохранить")

        if cancel_button is not None:
            cancel_button.setText("Назад")

        buttons.accepted.connect(
            self._validate_and_accept
        )
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if self.protocol_combo is not None:
            self._update_protocol_fields()

    def _selected_protocol(self) -> str:
        if self.protocol_combo is None:
            return self.current_protocol

        target = str(
            self.protocol_combo.currentData()
            or self.current_protocol
        ).strip().lower()

        return (
            target
            if target in {"none", "pppoe", "pptp", "l2tp"}
            else self.current_protocol
        )

    def _protocol_is_changed(self) -> bool:
        return (
            self.protocol_change_supported
            and self._selected_protocol()
            != self.current_protocol
        )

    def _set_standard_settings_enabled(
        self,
        enabled: bool,
    ) -> None:
        enabled = bool(enabled)

        if self.mac_mode_combo is not None:
            self.mac_mode_combo.setEnabled(enabled)

        if self.mac_address_edit is not None:
            self.mac_address_edit.setEnabled(enabled)

        if self.ipv4_dhcp_check is not None:
            self.ipv4_dhcp_check.setEnabled(enabled)

        for widget in (
            self.ipv4_address_edit,
            self.ipv4_prefix_edit,
            self.ipv4_gateway_edit,
            self.dhcp_hostname_edit,
            self.dhcp_routes_check,
            self.dhcp_dns_routes_check,
            self.ping_check_mode_combo,
            self.ping_check_host_edit,
            self.ping_check_port_spin,
            self.ping_check_interval_spin,
            self.ping_check_max_fails_spin,
        ):
            if widget is not None:
                widget.setEnabled(enabled)

        self.provider_dns_check.setEnabled(enabled)
        self.primary_dns_edit.setEnabled(enabled)
        self.secondary_dns_edit.setEnabled(enabled)

        if enabled:
            self._update_mac_fields()

            if self.ipv4_dhcp_check is not None:
                self._update_ipv4_fields(
                    self.ipv4_dhcp_check.isChecked()
                )

            self._update_ping_check_fields()

    def _update_protocol_fields(
        self,
        _index: int | None = None,
    ) -> None:
        if self.protocol_combo is None:
            return

        previous = self._protocol_previous_target

        if hasattr(self, "mtu_edit"):
            self._protocol_mtu_values[previous] = (
                self.mtu_edit.text().strip()
            )

        target = self._selected_protocol()
        self._protocol_previous_target = target
        changed = target != self.current_protocol
        authenticated = target != "none" and changed

        if self.protocol_auth_widget is not None:
            auth_layout = self.protocol_auth_widget.layout()

            for field in (
                self.protocol_username_edit,
                self.protocol_password_edit,
            ):
                if field is None:
                    continue
                field.setVisible(authenticated)
                label = auth_layout.labelForField(field)
                if label is not None:
                    label.setVisible(authenticated)

            if self.protocol_service_edit is not None:
                visible = target == "pppoe" and changed
                self.protocol_service_edit.setVisible(visible)
                label = auth_layout.labelForField(
                    self.protocol_service_edit
                )
                if label is not None:
                    label.setVisible(visible)

            if self.protocol_server_edit is not None:
                visible = (
                    target in {"pptp", "l2tp"}
                    and changed
                )
                self.protocol_server_edit.setVisible(visible)
                label = auth_layout.labelForField(
                    self.protocol_server_edit
                )
                if label is not None:
                    label.setVisible(visible)

            self.protocol_auth_widget.setVisible(changed)

        default_mtu = {
            "none": 1500,
            "pppoe": 1492,
            "pptp": 1400,
            "l2tp": 1500,
        }[target]

        if hasattr(self, "mtu_edit"):
            target_mtu = self._protocol_mtu_values.get(
                target
            )

            if target_mtu is None:
                target_mtu = (
                    str(self.original_mtu)
                    if (
                        target == self.current_protocol
                        and self.original_mtu is not None
                    )
                    else str(default_mtu)
                )

            if self.mtu_edit.text().strip() != target_mtu:
                self.mtu_edit.setText(target_mtu)

            max_mtu = (
                1492
                if target == "pppoe"
                else 1500
            )
            self.mtu_edit.setPlaceholderText(
                f"576–{max_mtu}"
            )

        self._set_standard_settings_enabled(
            not changed
        )

        if self.protocol_note_label is not None:
            current_title = {
                "none": "IPoE",
                "pppoe": "PPPoE",
                "pptp": "PPTP",
                "l2tp": "L2TP",
            }[self.current_protocol]
            target_title = {
                "none": "IPoE",
                "pppoe": "PPPoE",
                "pptp": "PPTP",
                "l2tp": "L2TP",
            }[target]

            if changed:
                self.protocol_note_label.setText(
                    f"Будет выполнена смена {current_title} → "
                    f"{target_title}. Физический порт, транспортный "
                    "IPv4, WAN MAC, DNS, приоритет и Ping Check "
                    "будут сохранены автоматически. Остальные "
                    "поля временно заблокированы до завершения "
                    "смены протокола."
                )
            else:
                self.protocol_note_label.setText(
                    f"Текущий протокол: {current_title}. "
                    "Выберите другой вариант, чтобы сменить тип "
                    "подключения в этом же окне."
                )

        if self.save_button is not None:
            self.save_button.setText(
                "Сменить тип"
                if changed
                else "Сохранить"
            )
            self.save_button.setToolTip(
                (
                    "Создать новый протокол на существующем "
                    "транспорте и удалить прежний после проверки"
                )
                if changed
                else "Сохранить параметры подключения"
            )

    @classmethod
    def _normalize_provider_host(
        cls,
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
            or any(
                character in "\r\n\x00"
                for character in text
            )
        ):
            return ""

        try:
            return str(ipaddress.ip_address(text))
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

        labels = ascii_name.split(".")

        if not labels:
            return ""

        for label in labels:
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

    def _validated_protocol_change(
        self,
        *,
        description: str,
    ) -> dict[str, Any] | None:
        if not self._protocol_is_changed():
            return None

        target = self._selected_protocol()
        mtu_text = self.mtu_edit.text().strip()

        try:
            mtu = int(mtu_text)
        except ValueError:
            self._show_error(
                "MTU должен быть целым числом."
            )
            return None

        max_mtu = 1492 if target == "pppoe" else 1500

        if not 576 <= mtu <= max_mtu:
            self._show_error(
                f"Для выбранного протокола MTU должен "
                f"быть от 576 до {max_mtu}."
            )
            return None

        result: dict[str, Any] = {
            "target_protocol": target,
            "description": description,
            "mtu": mtu,
        }

        if target == "none":
            return result

        if (
            self.protocol_username_edit is None
            or self.protocol_password_edit is None
        ):
            self._show_error(
                "Поля авторизации провайдера не созданы."
            )
            return None

        username = (
            self.protocol_username_edit.text()
            .strip()
        )
        password = self.protocol_password_edit.text()
        protocol_title = target.upper()

        if not username:
            self._show_error(
                f"Укажите имя пользователя {protocol_title}."
            )
            return None

        if not password:
            self._show_error(
                f"Укажите пароль {protocol_title}."
            )
            return None

        if any(
            character in "\r\n\x00"
            for character in username + password
        ):
            self._show_error(
                "Учётные данные содержат недопустимые символы."
            )
            return None

        result.update(
            {
                "username": username,
                "password": password,
            }
        )

        if target == "pppoe":
            service = (
                self.protocol_service_edit.text().strip()
                if self.protocol_service_edit is not None
                else ""
            )

            if any(
                character in "\r\n\x00"
                for character in service
            ):
                self._show_error(
                    "Имя сервиса содержит недопустимые символы."
                )
                return None

            result["service"] = service
        else:
            server = self._normalize_provider_host(
                (
                    self.protocol_server_edit.text()
                    if self.protocol_server_edit is not None
                    else ""
                )
            )

            if not server:
                self._show_error(
                    f"Укажите корректный адрес "
                    f"{protocol_title}-сервера."
                )
                return None

            result["server"] = server

        return result

    def protocol_change_settings(
        self,
    ) -> dict[str, Any] | None:
        if self._protocol_change_settings is None:
            return None

        return dict(self._protocol_change_settings)

    @staticmethod
    def _valid_wireless_ssid(value: str) -> bool:
        text = str(value or "")

        if (
            not text
            or any(character in "\r\n\x00" for character in text)
        ):
            return False

        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return False

        return 1 <= size <= 32

    @staticmethod
    def _valid_wireless_password(value: str) -> bool:
        text = str(value or "")

        if any(character in "\r\n\x00" for character in text):
            return False

        if re.fullmatch(r"[0-9A-Fa-f]{64}", text):
            return True

        try:
            return 8 <= len(text.encode("utf-8")) <= 63
        except UnicodeError:
            return False

    def _update_wireless_security_fields(
        self,
        _checked: bool | None = None,
    ) -> None:
        if self.wireless_password_edit is None:
            return

        open_network = bool(
            self.wireless_open_network_check is not None
            and self.wireless_open_network_check.isChecked()
        )
        self.wireless_password_edit.setEnabled(
            not open_network
        )

    def _validated_wireless_isp_changes(
        self,
    ) -> dict[str, Any] | None:
        if not self.wireless_isp_settings_supported:
            return {}

        if (
            self.wireless_ssid_edit is None
            or self.wireless_open_network_check is None
            or self.wireless_password_edit is None
            or self.wireless_bssid_edit is None
        ):
            return {}

        result: dict[str, Any] = {}
        ssid = self.wireless_ssid_edit.text()

        if not self._valid_wireless_ssid(ssid):
            self._show_error(
                "SSID Wireless ISP должен занимать от 1 до 32 байт."
            )
            return None

        if ssid != self.original_wireless_ssid:
            result["ssid"] = ssid

        open_network = (
            self.wireless_open_network_check.isChecked()
        )
        password = self.wireless_password_edit.text()

        if open_network:
            if not self.original_wireless_open_network:
                result["open_network"] = True
        else:
            protection_changed = (
                open_network
                != self.original_wireless_open_network
            )

            if password:
                if not self._valid_wireless_password(password):
                    self._show_error(
                        "Пароль WPA/WPA2 должен содержать 8–63 "
                        "символа или 64 шестнадцатеричные цифры."
                    )
                    return None
                result["password"] = password
                if protection_changed:
                    result["open_network"] = False
            elif protection_changed:
                self._show_error(
                    "Для перехода с открытой сети на защищённую "
                    "укажите новый пароль Wi-Fi."
                )
                return None

        bssid = (
            self.wireless_bssid_edit.text()
            .strip()
            .upper()
            .replace("-", ":")
        )

        if bssid and not re.fullmatch(
            r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}",
            bssid,
        ):
            self._show_error(
                "BSSID должен иметь вид AA:BB:CC:DD:EE:FF."
            )
            return None

        if bssid != self.original_wireless_bssid:
            result["bssid"] = bssid

        return result

    def _validate_and_accept(self) -> None:
        if not self.interface_id:
            self._show_error(
                "Не удалось определить интерфейс."
            )
            return

        description = (
            self.description_edit.text()
            .strip()
        )

        if not description:
            self._show_error(
                "Пользовательское имя не может быть пустым."
            )
            return

        if self._protocol_is_changed():
            protocol_settings = (
                self._validated_protocol_change(
                    description=description,
                )
            )

            if protocol_settings is None:
                return

            self._protocol_change_settings = (
                protocol_settings
            )
            self.accept()
            return

        mtu_text = self.mtu_edit.text().strip()
        mtu: int | None = None

        if mtu_text:
            try:
                mtu = int(mtu_text)
            except ValueError:
                self._show_error(
                    "MTU должен быть целым числом."
                )
                return

            if not 576 <= mtu <= 9000:
                self._show_error(
                    "MTU должен находиться в диапазоне "
                    "от 576 до 9000."
                )
                return

        use_provider_dns = (
            self.provider_dns_check.isChecked()
        )

        manual_dns = self._validated_manual_dns()

        if manual_dns is None:
            return

        ipv4_settings = (
            self._validated_ipv4_settings()
        )

        if ipv4_settings is None:
            return

        dhcp_client_settings = (
            self._validated_dhcp_client_settings()
        )

        if dhcp_client_settings is None:
            return

        mac_settings = (
            self._validated_mac_settings()
        )

        if mac_settings is None:
            return

        ping_check_settings = (
            self._validated_ping_check_settings()
        )

        if ping_check_settings is None:
            return

        wireless_isp_changes = (
            self._validated_wireless_isp_changes()
        )

        if wireless_isp_changes is None:
            return

        if (
            description == self.original_description
            and mtu == self.original_mtu
            and use_provider_dns
            == self.original_use_provider_dns
            and manual_dns
            == self.original_manual_dns
            and ipv4_settings
            == self._original_ipv4_settings()
            and dhcp_client_settings
            == self._original_dhcp_client_settings()
            and mac_settings
            == self._original_mac_settings()
            and ping_check_settings
            == self._original_ping_check_settings()
            and not wireless_isp_changes
        ):
            self.reject()
            return

        self.accept()

    def changes(self) -> dict[str, Any]:
        if self._protocol_change_settings is not None:
            return {}

        result: dict[str, Any] = {}

        description = (
            self.description_edit.text()
            .strip()
        )

        if description != self.original_description:
            result["description"] = description

        mtu_text = self.mtu_edit.text().strip()
        mtu = (
            int(mtu_text)
            if mtu_text
            else None
        )

        if mtu != self.original_mtu:
            result["mtu"] = mtu

        use_provider_dns = (
            self.provider_dns_check.isChecked()
        )

        if (
            use_provider_dns
            != self.original_use_provider_dns
        ):
            result[
                "use_provider_dns"
            ] = use_provider_dns

        manual_dns = self._validated_manual_dns()

        if manual_dns is None:
            return result

        if manual_dns != self.original_manual_dns:
            result["manual_dns"] = manual_dns

        ipv4_settings = (
            self._validated_ipv4_settings()
        )

        if (
            ipv4_settings is not None
            and ipv4_settings
            != self._original_ipv4_settings()
        ):
            result["ipv4"] = ipv4_settings

        dhcp_client_settings = (
            self._validated_dhcp_client_settings()
        )

        if (
            dhcp_client_settings is not None
            and dhcp_client_settings
            != self._original_dhcp_client_settings()
        ):
            result[
                "dhcp_client"
            ] = dhcp_client_settings

        mac_settings = (
            self._validated_mac_settings()
        )

        if (
            mac_settings is not None
            and mac_settings
            != self._original_mac_settings()
        ):
            result["mac"] = mac_settings

        ping_check_settings = (
            self._validated_ping_check_settings()
        )

        if (
            ping_check_settings is not None
            and ping_check_settings
            != self._original_ping_check_settings()
        ):
            result[
                "ping_check"
            ] = ping_check_settings

        wireless_isp_changes = (
            self._validated_wireless_isp_changes()
        )

        if wireless_isp_changes:
            result["wireless_isp"] = (
                wireless_isp_changes
            )

        return result

    def _update_ping_check_fields(
        self,
        _index: int | None = None,
    ) -> None:
        mode = (
            str(
                self.ping_check_mode_combo.currentData()
                or "disabled"
            )
            if self.ping_check_mode_combo is not None
            else "disabled"
        )
        manual = mode in {
            "icmp",
            "tls",
            "connect",
        }
        port_enabled = mode in {
            "tls",
            "connect",
        }

        if (
            self.ping_check_port_spin is not None
            and mode != self._last_ping_check_mode
        ):
            if mode == "tls":
                self.ping_check_port_spin.setValue(
                    443
                )
            elif mode == "connect":
                self.ping_check_port_spin.setValue(
                    53
                )

        self._last_ping_check_mode = mode

        for widget in (
            self.ping_check_host_edit,
            self.ping_check_interval_spin,
            self.ping_check_max_fails_spin,
        ):
            if widget is not None:
                widget.setEnabled(manual)

        if self.ping_check_port_spin is not None:
            self.ping_check_port_spin.setEnabled(
                port_enabled
            )

        if self.ping_check_note_label is None:
            return

        if mode == "automatic":
            self.ping_check_note_label.setText(
                "Автоматический режим использует встроенный "
                "профиль Keenetic и проверяет несколько "
                "общедоступных узлов через TCP/443."
            )
        elif mode == "icmp":
            self.ping_check_note_label.setText(
                "ICMP-режим отправляет echo-запросы "
                "указанному IP-адресу или доменному имени."
            )
        elif mode == "tls":
            self.ping_check_note_label.setText(
                "TCP/TLS устанавливает TCP-соединение и "
                "выполняет TLS-проверку. Этот режим лучше "
                "обнаруживает перенаправление на портал "
                "провайдера."
            )
        elif mode == "connect":
            self.ping_check_note_label.setText(
                "TCP-режим проверяет возможность установить "
                "соединение с указанным портом без TLS."
            )
        elif mode == "custom":
            self.ping_check_note_label.setText(
                "На интерфейсе назначен сторонний или "
                "расширенный профиль. Он останется без "
                "изменений, пока не выбран другой режим."
            )
        else:
            self.ping_check_note_label.setText(
                "Проверка доступности для этого подключения "
                "будет отключена."
            )

    def _original_ping_check_settings(
        self,
    ) -> dict[str, Any]:
        if not self.ping_check_supported:
            return {}

        if self.original_ping_check_mode == "disabled":
            return {
                "mode": "disabled",
            }

        if self.original_ping_check_mode == "automatic":
            return {
                "mode": "automatic",
            }

        if self.original_ping_check_mode == "custom":
            return {
                "mode": "custom",
                "profile": (
                    self.original_ping_check_profile
                ),
            }

        result = {
            "mode": self.original_ping_check_mode,
            "host": self.original_ping_check_host,
            "interval": (
                self.original_ping_check_interval
            ),
            "max_fails": (
                self.original_ping_check_max_fails
            ),
        }

        if self.original_ping_check_mode in {
            "tls",
            "connect",
        }:
            result["port"] = (
                self.original_ping_check_port
            )

        return result

    @staticmethod
    def _normalize_ping_check_host(
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

        labels = ascii_name.split(".")

        if not labels:
            return ""

        for label in labels:
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

    def _validated_ping_check_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.ping_check_supported:
            return {}

        if self.ping_check_mode_combo is None:
            return {}

        mode = str(
            self.ping_check_mode_combo.currentData()
            or "disabled"
        )

        if mode == "disabled":
            return {
                "mode": "disabled",
            }

        if mode == "automatic":
            return {
                "mode": "automatic",
            }

        if mode == "custom":
            return self._original_ping_check_settings()

        if mode not in {
            "icmp",
            "tls",
            "connect",
        }:
            self._show_error(
                "Выбран неизвестный режим Ping Check."
            )
            return None

        if (
            self.ping_check_host_edit is None
            or self.ping_check_interval_spin is None
            or self.ping_check_max_fails_spin is None
        ):
            self._show_error(
                "Поля Ping Check не созданы."
            )
            return None

        host = self._normalize_ping_check_host(
            self.ping_check_host_edit.text()
        )

        if not host:
            self._show_error(
                "Укажите корректный IP-адрес или имя "
                "узла для Ping Check."
            )
            return None

        result = {
            "mode": mode,
            "host": host,
            "interval": (
                self.ping_check_interval_spin.value()
            ),
            "max_fails": (
                self.ping_check_max_fails_spin.value()
            ),
        }

        if mode in {
            "tls",
            "connect",
        }:
            if self.ping_check_port_spin is None:
                self._show_error(
                    "Поле TCP-порта не создано."
                )
                return None

            result["port"] = (
                self.ping_check_port_spin.value()
            )

        return result

    def _update_mac_fields(self) -> None:
        if (
            self.mac_mode_combo is None
            or self.mac_address_edit is None
        ):
            return

        self.mac_address_edit.setEnabled(
            self.mac_mode_combo.currentData()
            == "manual"
        )

    def _original_mac_settings(
        self,
    ) -> dict[str, Any]:
        if not self.mac_settings_supported:
            return {}

        if self.original_mac_mode == "manual":
            return {
                "mode": "manual",
                "address": self.original_manual_mac,
            }

        return {
            "mode": "factory",
        }

    @staticmethod
    def _normalize_mac_address(
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

    def _validated_mac_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.mac_settings_supported:
            return {}

        if (
            self.mac_mode_combo is None
            or self.mac_address_edit is None
        ):
            self._show_error(
                "Поля настройки MAC не созданы."
            )
            return None

        mode = str(
            self.mac_mode_combo.currentData()
            or "factory"
        )

        if mode == "factory":
            return {
                "mode": "factory",
            }

        normalized = self._normalize_mac_address(
            self.mac_address_edit.text()
        )

        if not normalized:
            self._show_error(
                "Укажите MAC-адрес в формате "
                "AA:BB:CC:DD:EE:FF."
            )
            return None

        octets = [
            int(part, 16)
            for part in normalized.split(":")
        ]

        if normalized in {
            "00:00:00:00:00:00",
            "FF:FF:FF:FF:FF:FF",
        }:
            self._show_error(
                "Нельзя использовать нулевой или "
                "широковещательный MAC-адрес."
            )
            return None

        if octets[0] & 1:
            self._show_error(
                "Нельзя использовать групповой "
                "(multicast) MAC-адрес."
            )
            return None

        return {
            "mode": "manual",
            "address": normalized,
        }

    def _update_ipv4_fields(
        self,
        automatic: bool,
    ) -> None:
        manual_enabled = not bool(automatic)

        for widget in (
            self.ipv4_address_edit,
            self.ipv4_prefix_edit,
            self.ipv4_gateway_edit,
        ):
            if widget is not None:
                widget.setEnabled(
                    manual_enabled
                )

        for widget in (
            self.dhcp_hostname_edit,
            self.dhcp_routes_check,
            self.dhcp_dns_routes_check,
        ):
            if widget is not None:
                widget.setEnabled(
                    bool(automatic)
                )

    def _original_dhcp_client_settings(
        self,
    ) -> dict[str, Any]:
        if not self.dhcp_client_supported:
            return {}

        return {
            "hostname": self.original_dhcp_hostname,
            "routes": self.original_dhcp_routes,
            "dns_routes": (
                self.original_dhcp_dns_routes
            ),
        }

    def _validated_dhcp_client_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.dhcp_client_supported:
            return {}

        if (
            self.ipv4_dhcp_check is None
            or not self.ipv4_dhcp_check.isChecked()
        ):
            # При ручном IPv4 параметры DHCP-клиента
            # не применяются и не считаются изменёнными.
            return self._original_dhcp_client_settings()

        if (
            self.dhcp_hostname_edit is None
            or self.dhcp_routes_check is None
            or self.dhcp_dns_routes_check is None
        ):
            self._show_error(
                "Поля DHCP-клиента не созданы."
            )
            return None

        hostname = (
            self.dhcp_hostname_edit.text()
            .strip()
        )

        if not hostname:
            self._show_error(
                "Имя DHCP-клиента не может быть пустым."
            )
            return None

        if len(hostname) > 63:
            self._show_error(
                "Имя DHCP-клиента не должно превышать "
                "63 символа."
            )
            return None

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
            return None

        return {
            "hostname": hostname,
            "routes": (
                self.dhcp_routes_check.isChecked()
            ),
            "dns_routes": (
                self.dhcp_dns_routes_check.isChecked()
            ),
        }

    def _original_ipv4_settings(
        self,
    ) -> dict[str, Any]:
        if not self.ipv4_settings_supported:
            return {}

        if self.original_ipv4_dhcp:
            return {
                "dhcp": True,
            }

        return {
            "dhcp": False,
            "address": self.original_ipv4_address,
            "prefix": self.original_ipv4_prefix,
            "gateway": self.original_ipv4_gateway,
        }

    def _validated_ipv4_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.ipv4_settings_supported:
            return {}

        if self.ipv4_dhcp_check is None:
            return {}

        if self.ipv4_dhcp_check.isChecked():
            return {
                "dhcp": True,
            }

        if (
            self.ipv4_address_edit is None
            or self.ipv4_prefix_edit is None
            or self.ipv4_gateway_edit is None
        ):
            self._show_error(
                "Поля IPv4 не созданы."
            )
            return None

        address_text = (
            self.ipv4_address_edit.text()
            .strip()
        )
        prefix_text = (
            self.ipv4_prefix_edit.text()
            .strip()
        )
        gateway_text = (
            self.ipv4_gateway_edit.text()
            .strip()
        )

        if not address_text:
            self._show_error(
                "Укажите ручной IPv4-адрес."
            )
            return None

        if not prefix_text:
            self._show_error(
                "Укажите длину префикса IPv4."
            )
            return None

        if not gateway_text:
            self._show_error(
                "Укажите IPv4-адрес шлюза."
            )
            return None

        try:
            address = ipaddress.ip_address(
                address_text
            )
        except ValueError:
            self._show_error(
                "Некорректный IPv4-адрес."
            )
            return None

        if not isinstance(
            address,
            ipaddress.IPv4Address,
        ):
            self._show_error(
                "В поле IPv4-адреса указан IPv6."
            )
            return None

        try:
            prefix = int(prefix_text)
        except ValueError:
            self._show_error(
                "Длина префикса должна быть целым числом."
            )
            return None

        if not 1 <= prefix <= 32:
            self._show_error(
                "Длина префикса должна быть от 1 до 32."
            )
            return None

        try:
            gateway = ipaddress.ip_address(
                gateway_text
            )
        except ValueError:
            self._show_error(
                "Некорректный IPv4-адрес шлюза."
            )
            return None

        if not isinstance(
            gateway,
            ipaddress.IPv4Address,
        ):
            self._show_error(
                "В поле шлюза указан IPv6."
            )
            return None

        return {
            "dhcp": False,
            "address": str(address),
            "prefix": prefix,
            "gateway": str(gateway),
        }

    def _manual_dns_values(self) -> list[str]:
        values = [
            self.primary_dns_edit.text().strip(),
            self.secondary_dns_edit.text().strip(),
        ]

        return [
            value
            for value in values
            if value
        ]

    def _validated_manual_dns(
        self,
    ) -> list[str] | None:
        values = self._manual_dns_values()
        normalized: list[str] = []

        for value in values:
            try:
                address = ipaddress.ip_address(
                    value
                )
            except ValueError:
                self._show_error(
                    (
                        f"Некорректный DNS-адрес: {value}. "
                        "Введите IPv4 или IPv6 адрес."
                    )
                )
                return None

            text = str(address)

            if text in normalized:
                self._show_error(
                    "Основной и резервный DNS не должны "
                    "совпадать."
                )
                return None

            normalized.append(text)

        return normalized

    def _show_error(
        self,
        message: str,
    ) -> None:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle(
            "Проверьте параметры"
        )
        box.setText(message)
        box.setIcon(
            QMessageBox.Icon.Warning
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok
        )

        ok_button = box.button(
            QMessageBox.StandardButton.Ok
        )

        if ok_button is not None:
            ok_button.setText("Хорошо")

        box.exec()

    @staticmethod
    def _parse_mtu(value: Any) -> int | None:
        try:
            mtu = int(float(value))
        except (TypeError, ValueError):
            return None

        return mtu if mtu > 0 else None

    @staticmethod
    def _text(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return ""

        if isinstance(value, (list, tuple, set)):
            items = [
                str(item).strip()
                for item in value
                if str(item).strip()
            ]
            return ", ".join(items)

        if isinstance(value, dict):
            items = [
                str(item).strip()
                for item in value.values()
                if str(item).strip()
            ]
            return ", ".join(items)

        text = str(value).strip()

        if text in {
            "",
            "-",
            "—",
            "None",
            "null",
            "False",
        }:
            return ""

        return text

    @classmethod
    def _status_text(
        cls,
        connection: dict[str, Any],
    ) -> str:
        enabled = _connection_is_enabled(
            connection
        )
        connected = (
            _connection_is_connected(
                connection
            )
        )

        if not enabled:
            return "Выключено"

        if connected:
            return "Подключено"

        return "Нет соединения"

    @staticmethod
    def _format_speed(value: Any) -> str:
        try:
            speed = float(value)
        except (TypeError, ValueError):
            return ""

        if speed <= 0:
            return ""

        if speed >= 1_000_000_000:
            return f"{speed / 1_000_000_000:.1f} Гбит/с"

        if speed >= 1_000_000:
            return f"{speed / 1_000_000:.0f} Мбит/с"

        if speed >= 1_000:
            return f"{speed / 1_000:.0f} Кбит/с"

        return f"{speed:.0f} бит/с"

    @staticmethod
    def _priority_text(
        connection: dict[str, Any],
    ) -> str:
        value = connection.get("global_order")

        try:
            order = int(value)
        except (TypeError, ValueError):
            return ""

        return str(order + 1)
