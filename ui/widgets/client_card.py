from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.signal_bar import SegmentedSignalBar
from ui.widgets.property_grid import PropertyGrid
from ui.widgets.stable_expand import set_expanded_without_flicker


class ClientCard(QFrame):
    """Постоянная карточка клиента без пересоздания при обновлении."""

    state_change_requested = Signal(object, str, bool)
    client_selected = Signal(object)

    _expanded_states: dict[str, bool] = {}

    def __init__(
        self,
        client: dict[str, Any],
        parent: QWidget | None = None,
        *,
        inline_details: bool = True,
        status_mode: str = "traffic",
    ) -> None:
        super().__init__(parent)

        self.client: dict[str, Any] = {}
        self.inline_details = bool(inline_details)
        self.status_mode = str(status_mode or "traffic")
        self.client_key = self.make_client_key(client)
        self.expanded = (
            self._expanded_states.get(
                self.client_key,
                False,
            )
            if self.inline_details
            else False
        )
        self._toggle_in_progress = False
        self._port_action_pending = False

        self.setObjectName("wifiClientCard")
        self.setProperty("selected", False)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        self._build_ui()
        self.update_client(client)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.activity = QFrame()
        self.activity.setFixedSize(12, 12)

        names = QVBoxLayout()
        names.setSpacing(2)

        self.name_label = QLabel()
        self.name_label.setObjectName("wifiClientName")
        self.name_label.setWordWrap(True)

        self.address_label = QLabel()
        self.address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        names.addWidget(self.name_label)
        names.addWidget(self.address_label)

        self.badge = QLabel()
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setMinimumWidth(64)
        self.badge.setMaximumWidth(180)
        self.badge.setFixedHeight(24)

        self.signal_bar = SegmentedSignalBar(parent=self)

        self.registration_label = QLabel()
        self.registration_label.setMinimumWidth(135)
        self.registration_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.port_button = QPushButton()
        self.port_button.setMinimumWidth(145)
        self.port_button.setMaximumWidth(145)
        self.port_button.clicked.connect(
            self._emit_port_action
        )
        self.port_button.hide()

        # Родитель задаётся сразу: setVisible(True) ниже не должен успеть
        # показать кнопку как отдельное системное окно во время создания
        # карточки в уже видимом интерфейсе.
        self.toggle_button = QToolButton(self)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(self.expanded)
        self.toggle_button.setFixedSize(34, 30)
        self.toggle_button.clicked.connect(
            self._toggle_details
        )
        self._update_arrow()
        self.toggle_button.setVisible(self.inline_details)

        header.addWidget(self.activity)
        header.addLayout(names, 1)
        header.addWidget(self.badge)
        header.addWidget(self.signal_bar)
        header.addWidget(self.registration_label)
        header.addWidget(self.toggle_button)
        root.addLayout(header)

        speed_row = QFrame()
        speed_row.setObjectName(
            "wifiClientSpeedRow"
        )
        speed_layout = QHBoxLayout(
            speed_row
        )
        speed_layout.setContentsMargins(
            10,
            7,
            10,
            7,
        )
        speed_layout.setSpacing(14)

        self.down_rate_label = QLabel(
            "⇩ —"
        )
        self.down_rate_label.setObjectName(
            "wifiClientCompactRate"
        )
        self.down_rate_label.setFixedWidth(190)
        self.down_rate_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.up_rate_label = QLabel(
            "⇧ —"
        )
        self.up_rate_label.setObjectName(
            "wifiClientCompactRate"
        )
        self.up_rate_label.setFixedWidth(190)
        self.up_rate_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.uptime_label = QLabel("—")
        self.uptime_label.setObjectName(
            "wifiClientCompactUptime"
        )
        self.uptime_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )
        self.uptime_label.setMinimumWidth(135)

        speed_layout.addWidget(
            self.down_rate_label
        )
        speed_layout.addWidget(
            self.up_rate_label
        )
        speed_layout.addStretch()
        speed_layout.addWidget(
            self.uptime_label
        )
        speed_layout.addWidget(
            self.port_button
        )

        speed_row.setStyleSheet(
            """
            QFrame#wifiClientSpeedRow {
                background: rgba(11, 18, 32, 46);
                border: 1px solid #283548;
                border-radius: 7px;
            }

            QLabel#wifiClientCompactRate {
                color: #e2e8f0;
                background: transparent;
                border: none;
                font-family: Consolas;
                font-size: 13px;
                font-weight: 600;
            }

            QLabel#wifiClientCompactUptime {
                color: palette(text);
                background: transparent;
                border: none;
                font-size: 11px;
            }
            """
        )

        root.addWidget(speed_row)

        self.extra_widget = QWidget(self)
        self.extra_widget.setVisible(
            self.expanded and self.inline_details
        )

        extra_layout = QVBoxLayout(
            self.extra_widget
        )
        extra_layout.setContentsMargins(
            0,
            4,
            0,
            0,
        )
        extra_layout.setSpacing(0)

        self.property_grid = PropertyGrid(
            self.extra_widget,
            caption_width=128,
        )
        extra_layout.addWidget(
            self.property_grid
        )

        root.addWidget(self.extra_widget)

    def update_client(
        self,
        client: dict[str, Any],
    ) -> None:
        self.client = client

        wired = (
            client.get("connection_kind")
            == "wired"
        )

        name = str(
            client.get("name")
            or client.get("mac")
            or "Клиент"
        )
        ip = str(client.get("ip") or "0.0.0.0")
        mac = str(client.get("mac") or "—")

        self._set_label_text(
            self.name_label,
            name,
        )
        self._set_label_text(
            self.address_label,
            f"{ip}  •  {mac}",
        )

        if self.status_mode == "online":
            online = bool(client.get("online", True))
            blocked = bool(client.get("blocked"))
            status = "blocked" if blocked else (
                "online" if online else "offline"
            )
        else:
            status = (
                "active"
                if bool(client.get("traffic_active"))
                else "idle"
            )

        if (
            self.activity.property("client_status")
            != status
        ):
            self.activity.setProperty(
                "client_status",
                status,
            )
            colors = {
                "active": ("#34d399", "#6ee7b7"),
                "online": ("#34d399", "#6ee7b7"),
                "blocked": ("#fb7185", "#fda4af"),
                "offline": ("#64748b", "#94a3b8"),
                "idle": ("#64748b", "#94a3b8"),
            }
            fill, border = colors[status]
            self.activity.setStyleSheet(
                f"background:{fill};"
                f"border:1px solid {border};"
                "border-radius:6px;"
            )

        badge_text, badge_style = self._badge_data(
            client,
            wired,
        )
        self._set_label_text(
            self.badge,
            badge_text,
        )

        if (
            self.badge.property("badge_style")
            != badge_style
        ):
            self.badge.setStyleSheet(
                badge_style
            )
            self.badge.setProperty(
                "badge_style",
                badge_style,
            )

        known_wifi = client.get("connection_kind") == "wifi"
        self.signal_bar.setVisible(known_wifi)
        self.signal_bar.update_rssi(client.get("rssi"))

        self._set_label_text(
            self.registration_label,
            (
                "✓ Зарегистрирован"
                if client.get("registered")
                else "Не зарегистрирован"
            ),
        )

        self._set_label_text(
            self.down_rate_label,
            (
                "⇩ "
                + self._format_link_rate(
                    client.get("txrate")
                )
            ),
        )
        self._set_label_text(
            self.up_rate_label,
            (
                "⇧ "
                + self._format_link_rate(
                    client.get("rxrate")
                )
            ),
        )
        self.down_rate_label.setToolTip(
            (
                "Согласованная скорость Ethernet-порта."
                if wired
                else "PHY-скорость от роутера к клиенту."
            )
        )
        self.up_rate_label.setToolTip(
            (
                "Согласованная скорость Ethernet-порта."
                if wired
                else (
                    "PHY-скорость от клиента к роутеру."
                    if client.get("rxrate") is not None
                    else (
                        "KeeneticOS этого роутера не передаёт "
                        "обратную PHY-скорость."
                    )
                )
            )
        )
        self.badge.setToolTip(
            str(
                client.get("connection_label")
                or client.get("port_label")
                or badge_text
            )
        )
        if bool(client.get("online", True)):
            presence_text = (
                "В сети: "
                + str(client.get("uptime") or "—")
            )
        else:
            presence_text = (
                "Последний раз: "
                + self._format_last_seen(client.get("last_seen"))
            )
        self._set_label_text(
            self.uptime_label,
            presence_text,
        )

        details = self._build_all_details(
            client=client,
            wired=wired,
            mac=mac,
        )

        self.property_grid.set_items(
            details
        )

        self._update_port_button(
            client,
            name,
            wired,
        )

    def set_selected(self, selected: bool) -> None:
        normalized = bool(selected)
        if bool(self.property("selected")) == normalized:
            return
        self.setProperty("selected", normalized)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self.client_selected.emit(dict(self.client))
        super().mousePressEvent(event)

    @staticmethod
    def _set_label_text(
        label: QLabel,
        value: Any,
    ) -> None:
        text = str(value)

        if label.text() != text:
            label.setText(text)

    def _build_all_details(
        self,
        *,
        client: dict[str, Any],
        wired: bool,
        mac: str,
    ) -> list[tuple[str, Any]]:
        """
        Возвращает максимально полный набор данных клиента.

        Сначала идут основные известные поля, затем добавляются
        все дополнительные значения, которые конкретный роутер
        вернул сверх стандартного набора.
        """
        connection_kind = str(
            client.get("connection_kind") or "unknown"
        ).strip().lower()
        connection_name = (
            "Ethernet"
            if connection_kind == "wired"
            else "Wi-Fi"
            if connection_kind == "wifi"
            else "Не определено"
        )
        online = bool(client.get("online", True))
        blocked = bool(client.get("blocked"))

        details: list[
            tuple[str, Any]
        ] = [
            ("MAC", mac),
            (
                "IP-адрес",
                client.get("ip"),
            ),
            (
                "IPv6",
                client.get("ipv6")
                or client.get("ipv6_address")
                or client.get("ipv6-address"),
            ),
            (
                "Имя устройства",
                client.get("name"),
            ),
            (
                "Имя хоста",
                client.get("hostname")
                or client.get("host_name")
                or client.get("host-name"),
            ),
            (
                "Производитель",
                client.get("vendor"),
            ),
            (
                "Тип подключения",
                client.get("connection_label")
                or connection_name,
            ),
            (
                "Интерфейс / порт",
                self._interface_port_text(client),
            ),
            (
                "Точка доступа",
                client.get("ap"),
            ),
            (
                "Диапазон",
                client.get("band"),
            ),
            (
                "Режим Wi-Fi",
                client.get("mode"),
            ),
            (
                "Стандарт",
                client.get("standard")
                or client.get("protocol"),
            ),
            (
                "Канал",
                client.get("channel"),
            ),
            (
                "Ширина канала",
                client.get("channel_width"),
            ),
            (
                "MCS",
                client.get("mcs"),
            ),
            (
                "NSS",
                client.get("nss")
                or client.get("streams"),
            ),
            (
                "RSSI",
                self._format_dbm(
                    client.get("rssi")
                ),
            ),
            (
                "SNR",
                self._format_db(
                    client.get("snr")
                ),
            ),
            (
                "Шум",
                self._format_dbm(
                    client.get("noise")
                ),
            ),
            (
                "К клиенту",
                self._format_link_rate(
                    client.get("txrate")
                ),
            ),
            (
                "От клиента",
                self._format_link_rate(
                    client.get("rxrate")
                ),
            ),
            (
                "Текущий приём",
                self._format_bps(
                    client.get("to_client_bps")
                    if client.get("to_client_bps")
                    is not None
                    else client.get("tx_bps")
                ),
            ),
            (
                "Текущая передача",
                self._format_bps(
                    client.get("from_client_bps")
                    if client.get("from_client_bps")
                    is not None
                    else client.get("rx_bps")
                ),
            ),
            (
                "Получено",
                self._format_bytes(
                    client.get("rxbytes")
                ),
            ),
            (
                "Передано",
                self._format_bytes(
                    client.get("txbytes")
                ),
            ),
            (
                "Время подключения",
                client.get("uptime") if online else None,
            ),
            (
                "Последний раз",
                self._format_last_seen(client.get("last_seen"))
                if not online
                else None,
            ),
            (
                "Состояние",
                "Заблокирован"
                if blocked
                else "В сети"
                if online
                else "Не в сети",
            ),
            (
                "Зарегистрирован",
                client.get("registered"),
            ),
            (
                "Активен",
                client.get("traffic_active"),
            ),
            (
                "Сегмент",
                client.get("segment"),
            ),
            (
                "VLAN",
                client.get("vlan")
                or client.get("vlan_id")
                or client.get("vlan-id"),
            ),
            (
                "SSID",
                client.get("ssid"),
            ),
            (
                "BSSID",
                client.get("bssid"),
            ),
            (
                "Дуплекс",
                client.get("duplex"),
            ),
            (
                "Скорость порта",
                self._format_link_rate(
                    client.get("port_speed")
                    or client.get("port-speed")
                )
                if wired
                else None,
            ),
            (
                "Состояние порта",
                client.get("interface_enabled"),
            ),
            (
                "Политика доступа",
                client.get("policy"),
            ),
            (
                "Mesh",
                client.get("mesh"),
            ),
            (
                "Roaming",
                client.get("roaming"),
            ),
            (
                "802.11k",
                client.get("80211k")
                or client.get("k"),
            ),
            (
                "802.11v",
                client.get("80211v")
                or client.get("v"),
            ),
            (
                "802.11r",
                client.get("80211r")
                or client.get("r"),
            ),
        ]

        known_keys = {
            "mac",
            "ip",
            "ipv6",
            "ipv6_address",
            "ipv6-address",
            "name",
            "hostname",
            "host_name",
            "host-name",
            "vendor",
            "connection_kind",
            "connection_label",
            "interface",
            "port_label",
            "ap",
            "band",
            "mode",
            "standard",
            "protocol",
            "channel",
            "channel_width",
            "mcs",
            "nss",
            "streams",
            "rssi",
            "snr",
            "noise",
            "txrate",
            "rxrate",
            "to_client_bps",
            "from_client_bps",
            "tx_bps",
            "rx_bps",
            "rxbytes",
            "txbytes",
            "uptime",
            "last_seen",
            "last-seen",
            "online",
            "blocked",
            "registered",
            "traffic_active",
            "segment",
            "vlan",
            "vlan_id",
            "vlan-id",
            "ssid",
            "bssid",
            "duplex",
            "port_speed",
            "port-speed",
            "interface_enabled",
            "policy",
            "mesh",
            "roaming",
            "80211k",
            "80211v",
            "80211r",
            "k",
            "v",
            "r",
            # Служебная диагностика не выводится.
            "rate_debug",
        }

        for key, value in client.items():
            if key in known_keys:
                continue

            if PropertyGrid.is_empty(value):
                continue

            details.append(
                (
                    self._humanize_key(key),
                    value,
                )
            )

        return details

    @staticmethod
    def _humanize_key(
        key: Any,
    ) -> str:
        text = str(key)
        text = text.replace("_", " ")
        text = text.replace("-", " ")
        return " ".join(
            part.capitalize()
            for part in text.split()
        )

    @staticmethod
    def _format_last_seen(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text in {"-", "—", "None"}:
            return "—"

        date_match = re.fullmatch(
            r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?",
            text,
        )
        if date_match:
            year, month, day, hour, minute = date_match.groups()
            return f"{day}.{month}.{year} {hour}:{minute}"

        try:
            seconds = max(0, int(float(text)))
        except (TypeError, ValueError):
            return text

        if seconds < 60:
            return f"{seconds} сек. назад"
        if seconds < 3600:
            return f"{seconds // 60} мин. назад"
        if seconds < 86400:
            return f"{seconds // 3600} ч. назад"
        return f"{seconds // 86400} д. назад"

    @staticmethod
    def _format_dbm(
        value: Any,
    ) -> str | None:
        try:
            return f"{float(value):.0f} dBm"
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_db(
        value: Any,
    ) -> str | None:
        try:
            return f"{float(value):.0f} dB"
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_bps(
        value: Any,
    ) -> str | None:
        try:
            bps = max(
                0.0,
                float(value),
            )
        except (TypeError, ValueError):
            return None

        if bps >= 1_000_000_000:
            return (
                f"{bps / 1_000_000_000:.2f} "
                f"Гбит/с"
            )

        if bps >= 1_000_000:
            return (
                f"{bps / 1_000_000:.2f} "
                f"Мбит/с"
            )

        if bps >= 1_000:
            return (
                f"{bps / 1_000:.1f} "
                f"Кбит/с"
            )

        return f"{bps:.0f} бит/с"

    def _update_port_button(
        self,
        client: dict[str, Any],
        name: str,
        wired: bool,
    ) -> None:
        if (
            not wired
            or bool(client.get("blocked"))
            or not bool(client.get("port_control_available", True))
        ):
            self.port_button.hide()
            return

        interface_id = str(
            client.get("interface")
            or ""
        ).strip()
        physical = self._safe_ethernet_interface(
            interface_id
        )

        if physical is None:
            self.port_button.hide()
            return

        enabled = bool(
            client.get("interface_enabled", True)
        )
        if not bool(client.get("online", True)) and enabled:
            self.port_button.hide()
            return

        target = not enabled

        self.port_button.setEnabled(True)
        if bool(client.get("management_client")) and not target:
            self.port_button.setText("Порт управления")
            self.port_button.setToolTip(
                "Этот компьютер использует порт для связи с роутером. "
                "Отключение заблокировано, чтобы не потерять управление."
            )
            self.port_button.setEnabled(False)
            self.port_button.show()
            return

        self.port_button.setText(
            "Включить порт"
            if target
            else "Выключить порт"
        )
        self.port_button.setToolTip(
            "Включить физический Ethernet-порт."
            if target
            else "Отключить физический Ethernet-порт."
        )
        self.port_button.setProperty(
            "interface_id",
            physical,
        )
        self.port_button.setProperty(
            "display_name",
            name,
        )
        self.port_button.setProperty(
            "target_enabled",
            target,
        )
        if self._port_action_pending:
            self.port_button.setText("Выполняется…")
            self.port_button.setToolTip(
                "Приложение ожидает подтверждение состояния порта."
            )
            self.port_button.setEnabled(False)
        self.port_button.show()

    def set_port_action_pending(self, pending: bool) -> None:
        self._port_action_pending = bool(pending)
        self._update_port_button(
            self.client,
            str(
                self.client.get("name")
                or self.client.get("mac")
                or "Клиент"
            ),
            self.client.get("connection_kind") == "wired",
        )

    def _emit_port_action(
        self,
        checked: bool = False,
    ) -> None:
        interface_id = str(
            self.port_button.property("interface_id")
            or ""
        ).strip()

        if not interface_id:
            return

        self.state_change_requested.emit(
            [interface_id],
            str(
                self.port_button.property("display_name")
                or interface_id
            ),
            bool(
                self.port_button.property("target_enabled")
            ),
        )

    def _toggle_details(
        self,
        checked: bool,
    ) -> None:
        self.client_selected.emit(dict(self.client))
        if self._toggle_in_progress:
            return

        self._toggle_in_progress = True
        self.expanded = checked
        self._expanded_states[self.client_key] = checked

        has_details = (
            self.property_grid.has_items()
        )

        set_expanded_without_flicker(
            owner=self,
            content=self.extra_widget,
            expanded=(
                checked
                and has_details
            ),
            after_change=self._update_arrow,
        )

        QTimer.singleShot(
            0,
            self._finish_stable_toggle,
        )

    def _finish_stable_toggle(self) -> None:
        self._toggle_in_progress = False

    def _update_arrow(self) -> None:
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if self.toggle_button.isChecked()
            else Qt.ArrowType.RightArrow
        )

    @staticmethod
    def make_client_key(
        client: dict[str, Any],
    ) -> str:
        return str(
            client.get("mac")
            or client.get("name")
            or id(client)
        ).strip().lower()

    @staticmethod
    def _safe_ethernet_interface(
        value: str,
    ) -> str | None:
        lowered = value.lower()

        if any(
            marker in lowered
            for marker in (
                "bridge",
                "home",
                "guest",
                "vlan",
                "wifi",
                "wlan",
                "accesspoint",
            )
        ):
            return None

        if re.fullmatch(
            r"(?:gigabit|fast)?ethernet\d+(?:/\d+)?",
            lowered,
        ):
            return value

        return None

    @staticmethod
    def _badge_data(
        client: dict[str, Any],
        wired: bool,
    ) -> tuple[str, str]:
        common = (
            "border-radius:7px;"
            "padding:2px 7px;"
            "font-size:11px;"
            "font-weight:700;"
        )

        connection_kind = str(
            client.get("connection_kind") or "unknown"
        ).strip().lower()

        if wired:
            return (
                "Ethernet",
                common
                + "background:rgba(52,211,153,31);"
                + "color:#6ee7b7;"
                + "border:1px solid rgba(52,211,153,102);",
            )

        if connection_kind != "wifi":
            return (
                "Не определено",
                common
                + "background:rgba(100,116,139,31);"
                + "color:#cbd5e1;"
                + "border:1px solid rgba(148,163,184,102);",
            )

        band = str(
            client.get("band")
            or ""
        ).lower()

        if "2.4" in band:
            return (
                "2.4 GHz",
                common
                + "background:rgba(96,165,250,31);"
                + "color:#93c5fd;"
                + "border:1px solid rgba(96,165,250,102);",
            )

        if "5" in band:
            return (
                "5 GHz",
                common
                + "background:rgba(167,139,250,31);"
                + "color:#c4b5fd;"
                + "border:1px solid rgba(167,139,250,102);",
            )

        if "6" in band:
            return (
                "6 GHz",
                common
                + "background:rgba(244,114,182,31);"
                + "color:#f9a8d4;"
                + "border:1px solid rgba(244,114,182,102);",
            )

        return (
            "Wi-Fi",
            common
            + "background:rgba(100,116,139,31);"
            + "color:#cbd5e1;"
            + "border:1px solid rgba(148,163,184,102);",
        )

    @staticmethod
    def _format_link_rate(
        value: Any,
    ) -> str:
        """
        Показывает только PHY-скорость соединения.

        Текущий трафик намеренно не используется как запасное
        значение, поскольку это другой показатель.
        """
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return "нет данных"

        if rate <= 0:
            return "нет данных"

        if rate >= 1000:
            return f"{rate / 1000:.2f} Гбит/с"

        return f"{rate:.1f} Мбит/с"

    @staticmethod
    def _interface_port_text(
        client: dict[str, Any],
    ) -> str | None:
        interface_id = str(
            client.get("interface")
            or ""
        ).strip()
        port_label = str(
            client.get("port_label")
            or ""
        ).strip()

        if port_label and interface_id and interface_id != "—":
            return f"{port_label} ({interface_id})"
        if port_label:
            return port_label
        return interface_id or None

    @staticmethod
    def _format_bytes(value: Any) -> str:
        try:
            size = float(value or 0)
        except (TypeError, ValueError):
            return "—"

        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if size < 1024 or unit == "ТБ":
                return f"{size:.2f} {unit}"
            size /= 1024

        return "—"
