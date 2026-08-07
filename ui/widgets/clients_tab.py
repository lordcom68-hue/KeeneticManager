from __future__ import annotations

import csv
import ipaddress
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.client_card import ClientCard
from ui.widgets.property_grid import PropertyGrid
from ui.widgets.status_badge import StatusBadge


class ClientSummaryChip(QFrame):
    activated = Signal(str)

    def __init__(
        self,
        caption: str,
        filter_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.filter_id = str(filter_id or "all")
        self.setObjectName("clientSummaryChip")
        self.setProperty("interactive", True)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Фильтр: {caption}")
        self.setToolTip(f"Показать: {caption.lower()}")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(7)

        self.value_label = QLabel("0")
        self.value_label.setObjectName("clientSummaryValue")
        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("clientSummaryCaption")

        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)

    def set_value(self, value: int) -> None:
        text = str(max(0, int(value)))
        if self.value_label.text() != text:
            self.value_label.setText(text)

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if bool(self.property("active")) == active:
            return
        self.setProperty("active", active)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit(self.filter_id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() in {
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        }:
            self.activated.emit(self.filter_id)
            event.accept()
            return
        super().keyPressEvent(event)


class ClientDetailsPanel(QFrame):
    message_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("clientDetailsPanel")
        self.setMinimumWidth(330)
        self.setMaximumWidth(440)
        self._client: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        names = QVBoxLayout()
        names.setSpacing(2)
        self.title_label = QLabel("Клиент не выбран")
        self.title_label.setObjectName("clientDetailsTitle")
        self.title_label.setWordWrap(True)
        self.address_label = QLabel(
            "Выберите устройство в списке слева."
        )
        self.address_label.setObjectName("clientDetailsSubtitle")
        self.address_label.setWordWrap(True)
        self.address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        names.addWidget(self.title_label)
        names.addWidget(self.address_label)

        self.status_badge = StatusBadge(
            text="Нет данных",
            state="unknown",
        )
        title_row.addLayout(names, 1)
        title_row.addWidget(
            self.status_badge,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        root.addLayout(title_row)

        self.connection_label = QLabel()
        self.connection_label.setObjectName("clientDetailsConnection")
        self.connection_label.setWordWrap(True)
        self.connection_label.hide()
        root.addWidget(self.connection_label)

        copy_row = QHBoxLayout()
        copy_row.setSpacing(8)
        self.copy_ip_button = QPushButton("Копировать IP")
        self.copy_mac_button = QPushButton("Копировать MAC")
        self.copy_ip_button.clicked.connect(self._copy_ip)
        self.copy_mac_button.clicked.connect(self._copy_mac)
        self.copy_ip_button.hide()
        self.copy_mac_button.hide()
        copy_row.addWidget(self.copy_ip_button)
        copy_row.addWidget(self.copy_mac_button)
        copy_row.addStretch()
        root.addLayout(copy_row)

        details_scroll = QScrollArea()
        details_scroll.setObjectName("clientDetailsScroll")
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        details_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        details_content = QWidget()
        details_layout = QVBoxLayout(details_content)
        details_layout.setContentsMargins(0, 4, 0, 4)
        details_layout.setSpacing(8)

        self.property_grid = PropertyGrid(
            details_content,
            caption_width=118,
            column_count=1,
        )
        details_layout.addWidget(self.property_grid)
        details_layout.addStretch()
        details_scroll.setWidget(details_content)
        root.addWidget(details_scroll, 1)

        self.clear()

    def clear(self) -> None:
        self._client = None
        self.title_label.setText("Клиент не выбран")
        self.address_label.setText(
            "Выберите устройство в списке слева."
        )
        self.status_badge.set_status(
            text="Нет данных",
            state="unknown",
        )
        self.connection_label.clear()
        self.connection_label.hide()
        self.copy_ip_button.hide()
        self.copy_mac_button.hide()
        self.property_grid.set_items([])

    def set_client(self, client: dict[str, Any]) -> None:
        self._client = dict(client)
        name = str(
            client.get("name")
            or client.get("hostname")
            or client.get("mac")
            or "Клиент"
        )
        ip = self._plain_value(client.get("ip"))
        mac = self._plain_value(client.get("mac"))
        address = "  •  ".join(
            value for value in (ip, mac) if value
        ) or "Адрес не определён"

        self.title_label.setText(name)
        self.address_label.setText(address)
        online = bool(client.get("online", True))
        blocked = bool(client.get("blocked"))
        if blocked:
            self.status_badge.set_status(
                text="Заблокирован",
                state="error",
            )
        elif online:
            self.status_badge.set_status(
                text="В сети",
                state="connected",
            )
        else:
            self.status_badge.set_status(
                text="Не в сети",
                state="unknown",
            )

        connection_kind = str(
            client.get("connection_kind") or "unknown"
        ).strip().lower()
        connection_text = str(
            client.get("connection_label")
            or (
                "Ethernet"
                if connection_kind == "wired"
                else (
                    client.get("band") or "Wi‑Fi"
                    if connection_kind == "wifi"
                    else "Тип подключения не определён"
                )
            )
        )
        self.connection_label.setText(connection_text)
        self.connection_label.show()

        self.copy_ip_button.setVisible(bool(ip))
        self.copy_mac_button.setVisible(bool(mac))
        self.property_grid.set_items(
            self._details(client)
        )

    @staticmethod
    def _plain_value(value: Any) -> str:
        text = str(value or "").strip()
        if text in {"", "-", "—", "0.0.0.0", "None"}:
            return ""
        return text

    @staticmethod
    def _traffic_value(
        client: dict[str, Any],
        primary: str,
        fallback: str,
    ) -> str | None:
        value = client.get(primary)
        if value is None:
            value = client.get(fallback)
        return ClientCard._format_bps(value)

    @classmethod
    def _details(
        cls,
        client: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        connection_kind = str(
            client.get("connection_kind") or "unknown"
        ).strip().lower()
        wired = connection_kind == "wired"
        connection_name = (
            "Ethernet"
            if wired
            else "Wi‑Fi"
            if connection_kind == "wifi"
            else "Не определено"
        )
        return [
            (
                "Подключение",
                client.get("connection_label")
                or connection_name,
            ),
            (
                "Сеть / SSID",
                client.get("ssid")
                or client.get("network_name"),
            ),
            (
                "Интерфейс / порт",
                ClientCard._interface_port_text(client),
            ),
            ("Диапазон", None if wired else client.get("band")),
            ("Канал", None if wired else client.get("channel")),
            (
                "Сигнал",
                ClientCard._format_dbm(client.get("rssi")),
            ),
            (
                "К клиенту",
                ClientCard._format_link_rate(client.get("txrate")),
            ),
            (
                "От клиента",
                ClientCard._format_link_rate(client.get("rxrate")),
            ),
            (
                "Текущий приём",
                cls._traffic_value(
                    client,
                    "to_client_bps",
                    "tx_bps",
                ),
            ),
            (
                "Текущая передача",
                cls._traffic_value(
                    client,
                    "from_client_bps",
                    "rx_bps",
                ),
            ),
            (
                "Получено",
                ClientCard._format_bytes(client.get("rxbytes")),
            ),
            (
                "Передано",
                ClientCard._format_bytes(client.get("txbytes")),
            ),
            ("В сети", client.get("uptime")),
            ("Последний раз", client.get("last_seen")),
            ("Заблокирован", client.get("blocked")),
            ("Зарегистрирован", client.get("registered")),
            ("Имя хоста", client.get("hostname")),
            ("Производитель", client.get("vendor")),
            ("Сегмент", client.get("segment")),
            ("Политика доступа", client.get("policy")),
            ("Дуплекс", client.get("duplex") if wired else None),
        ]

    def _copy_ip(self) -> None:
        if self._client is None:
            return
        self._copy_value(
            self._plain_value(self._client.get("ip")),
            "IP-адрес скопирован",
        )

    def _copy_mac(self) -> None:
        if self._client is None:
            return
        self._copy_value(
            self._plain_value(self._client.get("mac")),
            "MAC-адрес скопирован",
        )

    def _copy_value(self, value: str, message: str) -> None:
        if not value:
            return
        QApplication.clipboard().setText(value)
        self.message_requested.emit(message)


class ClientsTab(QWidget):
    refresh_requested = Signal()
    interface_state_change_requested = Signal(object, str, bool)
    wifi_access_change_requested = Signal(object, bool)
    message_requested = Signal(str)

    FILTERS = (
        ("all", "Все"),
        ("online", "В сети"),
        ("wifi", "Wi‑Fi"),
        ("wired", "Ethernet"),
        ("offline", "Не в сети"),
        ("blocked", "Заблокированы"),
        ("traffic", "Есть трафик"),
        ("unregistered", "Не зарегистрированы"),
    )
    SORTS = (
        ("stable", "Постоянный порядок"),
        ("activity", "По активности"),
        ("name", "По имени"),
        ("ip", "По IP-адресу"),
        ("type", "По типу подключения"),
    )
    EXPORT_HEADERS = (
        "Имя",
        "Статус",
        "Тип подключения",
        "Сеть / SSID",
        "Диапазон",
        "Интерфейс / порт",
        "IP-адрес",
        "MAC-адрес",
        "Сигнал",
        "К клиенту",
        "От клиента",
        "В сети / последний раз",
        "Зарегистрирован",
        "Заблокирован",
    )

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._router_id: int | None = None
        self._clients: list[dict[str, Any]] = []
        self._ports: list[dict[str, Any]] = []
        self._cards: dict[str, ClientCard] = {}
        self._port_buttons: dict[str, QPushButton] = {}
        self._pending_port_interfaces: set[str] = set()
        self._client_orders: dict[int | None, list[str]] = {}
        self._selected_client_key: str | None = None
        self._wifi_action_pending = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(7)

        header = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        title = QLabel("Клиенты")
        title.setObjectName("tabTitle")
        subtitle = QLabel(
            "Подключённые устройства Wi‑Fi и Ethernet выбранного роутера."
        )
        subtitle.setObjectName("clientTabSubtitle")
        names.addWidget(title)
        names.addWidget(subtitle)
        header.addLayout(names, 1)

        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(
            self.refresh_requested
        )
        self.export_button = QPushButton("Экспорт CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(
            self._export_visible_clients
        )
        self.export_button.setFixedHeight(34)
        self.refresh_button.setFixedHeight(34)
        header.addWidget(self.export_button)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        summary = QHBoxLayout()
        summary.setSpacing(8)
        self.summary_chips = {
            "total": ClientSummaryChip("Всего", "all", self),
            "online": ClientSummaryChip("В сети", "online", self),
            "wifi": ClientSummaryChip("Wi‑Fi", "wifi", self),
            "wired": ClientSummaryChip("Ethernet", "wired", self),
            "offline": ClientSummaryChip("Не в сети", "offline", self),
            "blocked": ClientSummaryChip(
                "Заблокированы",
                "blocked",
                self,
            ),
        }
        for chip in self.summary_chips.values():
            chip.activated.connect(self._activate_filter)
            summary.addWidget(chip)
        root.addLayout(summary)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText(
            "Поиск по имени, IP, MAC, сети или порту"
        )
        self.search_input.textChanged.connect(
            self._apply_filter
        )

        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(190)
        for filter_id, title_text in self.FILTERS:
            self.filter_combo.addItem(title_text, filter_id)
        self.filter_combo.currentIndexChanged.connect(
            self._apply_filter
        )

        self.sort_combo = QComboBox()
        self.sort_combo.setMinimumWidth(180)
        for sort_id, title_text in self.SORTS:
            self.sort_combo.addItem(title_text, sort_id)
        self.sort_combo.currentIndexChanged.connect(
            self._apply_filter
        )

        self.visible_count_label = QLabel("Показано: 0 из 0")
        self.visible_count_label.setObjectName("clientVisibleCount")

        self.reset_view_button = QPushButton("Сбросить")
        self.reset_view_button.setEnabled(False)
        self.reset_view_button.clicked.connect(
            self._reset_view
        )

        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.filter_combo)
        controls.addWidget(self.sort_combo)
        controls.addWidget(self.visible_count_label)
        controls.addWidget(self.reset_view_button)
        root.addLayout(controls)

        self.selected_client_panel = QFrame(self)
        self.selected_client_panel.setObjectName("selectedClientPanel")
        selected_root = QHBoxLayout(self.selected_client_panel)
        selected_root.setContentsMargins(10, 5, 10, 5)
        selected_root.setSpacing(7)
        self.selected_client_panel.setMaximumHeight(48)

        self.selected_client_label = QLabel("Клиент не выбран")
        self.selected_client_label.setObjectName("selectedClientLabel")
        self.selected_client_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        selected_root.addWidget(self.selected_client_label, 1)

        self.copy_selected_ip_button = QPushButton("IP")
        self.copy_selected_mac_button = QPushButton("MAC")
        self.copy_selected_all_button = QPushButton("Данные")
        self.wifi_access_button = QPushButton("Отключить Wi-Fi")
        self.wifi_access_button.setObjectName("clientWifiAccessButton")
        self.clear_selected_button = QPushButton("Снять выбор")
        self.copy_selected_ip_button.setToolTip("Копировать IP-адрес")
        self.copy_selected_mac_button.setToolTip("Копировать MAC-адрес")
        self.copy_selected_all_button.setToolTip("Копировать все данные клиента")
        self.copy_selected_ip_button.clicked.connect(
            self._copy_selected_ip
        )
        self.copy_selected_mac_button.clicked.connect(
            self._copy_selected_mac
        )
        self.copy_selected_all_button.clicked.connect(
            self._copy_selected_details
        )
        self.wifi_access_button.clicked.connect(
            self._request_selected_wifi_access_change
        )
        self.clear_selected_button.clicked.connect(
            self._clear_selected_client
        )
        selected_root.addWidget(self.copy_selected_ip_button)
        selected_root.addWidget(self.copy_selected_mac_button)
        selected_root.addWidget(self.copy_selected_all_button)
        selected_root.addWidget(self.wifi_access_button)
        selected_root.addWidget(self.clear_selected_button)
        root.addWidget(self.selected_client_panel)

        self.disabled_ports_panel = QFrame(self)
        self.disabled_ports_panel.setObjectName("disabledPortsPanel")
        self.disabled_ports_layout = QHBoxLayout(
            self.disabled_ports_panel
        )
        self.disabled_ports_layout.setContentsMargins(12, 8, 12, 8)
        self.disabled_ports_layout.setSpacing(8)
        disabled_ports_title = QLabel("Отключённые порты:")
        disabled_ports_title.setObjectName("disabledPortsTitle")
        self.disabled_ports_layout.addWidget(disabled_ports_title)
        self.disabled_ports_layout.addStretch()
        self.disabled_ports_panel.hide()
        root.addWidget(self.disabled_ports_panel)

        self.list_scroll = QScrollArea()
        self.list_scroll.setObjectName("clientsListScroll")
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 8, 0)
        self.cards_layout.setSpacing(10)
        self.empty_label = QLabel(
            "Подключённые клиенты не обнаружены."
        )
        self.empty_label.setObjectName("clientsEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch()
        self.list_scroll.setWidget(self.cards_container)

        root.addWidget(self.list_scroll, 1)

        self._update_summary()
        self._sync_selected_client()

    def set_router(self, router_id: int | None) -> None:
        normalized = int(router_id) if router_id is not None else None
        if normalized == self._router_id:
            return
        self._router_id = normalized
        self.clear(reset_controls=False)

    def set_networks(
        self,
        networks: list[dict[str, Any]] | None,
    ) -> None:
        self._clients = self.collect_clients(networks or [])
        self._remember_client_order()
        self._update_summary()
        self._apply_filter()
        self._render_disabled_ports()

    def set_clients(
        self,
        clients: list[dict[str, Any]] | None,
    ) -> None:
        self._clients = self._deduplicate_clients(clients or [])
        self._remember_client_order()
        self._update_summary()
        self._apply_filter()
        self._render_disabled_ports()

    def set_ports(
        self,
        ports: list[dict[str, Any]] | None,
    ) -> None:
        self._ports = [
            dict(port)
            for port in (ports or [])
            if isinstance(port, dict)
        ]
        self._render_disabled_ports()

    def clear(self, *, reset_controls: bool = False) -> None:
        self._clients = []
        self._ports = []
        self._selected_client_key = None
        self._wifi_action_pending = False
        self._pending_port_interfaces.clear()
        for card in self._cards.values():
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards = {}
        self._render_disabled_ports()
        if reset_controls:
            self.search_input.clear()
            self.filter_combo.setCurrentIndex(0)
            self.sort_combo.setCurrentIndex(0)
        self._update_summary()
        self._apply_filter()

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {"", "-", "—", "None"}
        return False

    @classmethod
    def collect_clients(
        cls,
        networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for network in networks:
            network_type = str(
                network.get("network_type") or "wifi"
            ).strip().lower()
            for raw_client in network.get("clients") or []:
                if not isinstance(raw_client, dict):
                    continue
                client = dict(raw_client)
                client.setdefault("connection_kind", network_type)
                client.setdefault("online", True)

                metadata = {
                    "network_name": network.get("name"),
                    "network_group": network.get("group"),
                    "ssid": network.get("ssid"),
                    "channel": network.get("channel"),
                }
                if network_type != "wired":
                    metadata["band"] = network.get("band")

                for key, value in metadata.items():
                    if cls._is_empty(client.get(key)) and not cls._is_empty(value):
                        client[key] = value

                key = ClientCard.make_client_key(client)
                if key not in result:
                    result[key] = client
                    order.append(key)
                    continue

                existing = result[key]
                if (
                    existing.get("connection_kind") == "wired"
                    and client.get("connection_kind") == "wifi"
                ):
                    existing, client = client, existing
                    result[key] = existing

                for field, value in client.items():
                    if cls._is_empty(existing.get(field)) and not cls._is_empty(value):
                        existing[field] = value

        return [result[key] for key in order]

    @classmethod
    def _deduplicate_clients(
        cls,
        clients: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for raw_client in clients:
            if not isinstance(raw_client, dict):
                continue
            client = dict(raw_client)
            client.setdefault("online", True)
            client.setdefault("blocked", False)
            client.setdefault("connection_kind", "unknown")
            key = ClientCard.make_client_key(client)
            if key not in result:
                result[key] = client
                order.append(key)
                continue

            existing = result[key]
            existing_priority = (
                bool(existing.get("online")),
                existing.get("connection_kind") != "unknown",
            )
            client_priority = (
                bool(client.get("online")),
                client.get("connection_kind") != "unknown",
            )
            if client_priority > existing_priority:
                existing, client = client, existing
                result[key] = existing

            for field, value in client.items():
                if cls._is_empty(existing.get(field)) and not cls._is_empty(value):
                    existing[field] = value

        return [result[key] for key in order]

    def _counts(self) -> dict[str, int]:
        return {
            "total": len(self._clients),
            "online": sum(
                bool(client.get("online", True))
                for client in self._clients
            ),
            "wifi": sum(
                client.get("connection_kind") == "wifi"
                for client in self._clients
            ),
            "wired": sum(
                client.get("connection_kind") == "wired"
                for client in self._clients
            ),
            "traffic": sum(
                bool(client.get("traffic_active"))
                for client in self._clients
            ),
            "unregistered": sum(
                not bool(client.get("registered"))
                for client in self._clients
            ),
            "offline": sum(
                not bool(client.get("online", True))
                for client in self._clients
            ),
            "blocked": sum(
                bool(client.get("blocked"))
                for client in self._clients
            ),
        }

    def _update_summary(self) -> None:
        counts = self._counts()
        for name, chip in self.summary_chips.items():
            chip.set_value(counts[name])

        filter_titles = {
            "all": f"Все ({counts['total']})",
            "online": f"В сети ({counts['online']})",
            "wifi": f"Wi‑Fi ({counts['wifi']})",
            "wired": f"Ethernet ({counts['wired']})",
            "offline": f"Не в сети ({counts['offline']})",
            "blocked": f"Заблокированы ({counts['blocked']})",
            "traffic": f"Есть трафик ({counts['traffic']})",
            "unregistered": (
                "Не зарегистрированы "
                f"({counts['unregistered']})"
            ),
        }
        for index in range(self.filter_combo.count()):
            filter_id = str(self.filter_combo.itemData(index))
            self.filter_combo.setItemText(
                index,
                filter_titles[filter_id],
            )

    def _activate_filter(self, filter_id: str) -> None:
        index = self.filter_combo.findData(str(filter_id or "all"))
        if index >= 0:
            self.filter_combo.setCurrentIndex(index)

    def _reset_view(self) -> None:
        self.search_input.clear()
        filter_index = self.filter_combo.findData("all")
        sort_index = self.sort_combo.findData("stable")
        if filter_index >= 0:
            self.filter_combo.setCurrentIndex(filter_index)
        if sort_index >= 0:
            self.sort_combo.setCurrentIndex(sort_index)
        self._apply_filter()

    def _select_client(self, client: dict[str, Any]) -> None:
        key = ClientCard.make_client_key(client)
        if not any(
            ClientCard.make_client_key(item) == key
            for item in self._clients
        ):
            return
        self._selected_client_key = key
        self._sync_selected_client()

    def _clear_selected_client(self) -> None:
        self._selected_client_key = None
        self._sync_selected_client()

    def _selected_client(self) -> dict[str, Any] | None:
        if not self._selected_client_key:
            return None
        return next(
            (
                client
                for client in self._clients
                if ClientCard.make_client_key(client)
                == self._selected_client_key
            ),
            None,
        )

    def _sync_selected_client(self) -> None:
        client = self._selected_client()
        if client is None:
            self._selected_client_key = None
            self.selected_client_label.setText("Клиент не выбран")
            self.copy_selected_ip_button.setEnabled(False)
            self.copy_selected_mac_button.setEnabled(False)
            self.copy_selected_all_button.setEnabled(False)
            self.wifi_access_button.setEnabled(False)
            self.clear_selected_button.setEnabled(False)
            self.selected_client_panel.hide()
            for card in self._cards.values():
                card.set_selected(False)
            return

        self.selected_client_panel.show()

        name = str(
            client.get("name")
            or client.get("hostname")
            or client.get("mac")
            or "Клиент"
        )
        ip = self._plain_client_value(client.get("ip"))
        mac = self._plain_client_value(client.get("mac"))
        address = "  •  ".join(
            value for value in (ip, mac) if value
        )
        self.selected_client_label.setText(
            f"Выбран: {name}"
            + (f"  •  {address}" if address else "")
        )
        self.copy_selected_ip_button.setEnabled(bool(ip))
        self.copy_selected_mac_button.setEnabled(bool(mac))
        self.copy_selected_all_button.setEnabled(True)
        self.clear_selected_button.setEnabled(True)
        self._sync_wifi_access_button(client)
        for key, card in self._cards.items():
            card.set_selected(key == self._selected_client_key)

    def _sync_wifi_access_button(self, client: dict[str, Any]) -> None:
        wifi_client = (
            str(client.get("connection_kind") or "").strip().lower()
            == "wifi"
        )
        wifi_blocked = bool(client.get("wifi_blocked"))
        online = bool(client.get("online", True))
        management_client = bool(client.get("management_client"))
        has_mac = bool(self._plain_client_value(client.get("mac")))
        has_target = bool(
            self._plain_client_value(client.get("segment_id"))
            or self._plain_client_value(client.get("ap"))
        )
        target_allowed = wifi_blocked

        self.wifi_access_button.setText(
            "Разрешить Wi-Fi"
            if target_allowed
            else "Отключить Wi-Fi"
        )
        self.wifi_access_button.setProperty("danger", not target_allowed)
        style = self.wifi_access_button.style()
        style.unpolish(self.wifi_access_button)
        style.polish(self.wifi_access_button)

        enabled = bool(
            wifi_client
            and has_mac
            and has_target
            and (online or target_allowed)
            and not self._wifi_action_pending
            and not (management_client and not target_allowed)
        )
        self.wifi_access_button.setEnabled(enabled)

        if not wifi_client:
            tooltip = "Действие доступно только для Wi-Fi клиентов."
        elif management_client and not target_allowed:
            tooltip = (
                "Этот компьютер используется для управления роутером; "
                "его отключение заблокировано."
            )
        elif not online and not target_allowed:
            tooltip = "Клиент сейчас не подключён к Wi-Fi."
        elif target_allowed:
            tooltip = "Снова разрешить клиенту подключаться к Wi-Fi."
        else:
            tooltip = (
                "Отключить клиента и запретить повторное подключение "
                "до ручного разрешения."
            )
        self.wifi_access_button.setToolTip(tooltip)

    def set_wifi_action_pending(self, pending: bool) -> None:
        self._wifi_action_pending = bool(pending)
        self._sync_selected_client()

    def _request_selected_wifi_access_change(self) -> None:
        client = self._selected_client()
        if client is None or self._wifi_action_pending:
            return
        allowed = bool(client.get("wifi_blocked"))
        self.wifi_access_change_requested.emit(dict(client), allowed)

    @staticmethod
    def _plain_client_value(value: Any) -> str:
        text = str(value or "").strip()
        if text in {"", "-", "—", "0.0.0.0", "None"}:
            return ""
        return text

    def _copy_selected_ip(self) -> None:
        client = self._selected_client()
        if client is None:
            return
        self._copy_selected_value(
            self._plain_client_value(client.get("ip")),
            "IP-адрес клиента скопирован.",
        )

    def _copy_selected_mac(self) -> None:
        client = self._selected_client()
        if client is None:
            return
        self._copy_selected_value(
            self._plain_client_value(client.get("mac")),
            "MAC-адрес клиента скопирован.",
        )

    def _copy_selected_details(self) -> None:
        client = self._selected_client()
        if client is None:
            return
        values = self._export_row(client)
        text = "\n".join(
            f"{caption}: {value}"
            for caption, value in zip(self.EXPORT_HEADERS, values, strict=False)
            if str(value).strip()
        )
        self._copy_selected_value(
            text,
            "Данные клиента скопированы.",
        )

    def _copy_selected_value(self, value: str, message: str) -> None:
        if not value:
            return
        QApplication.clipboard().setText(value)
        self.message_requested.emit(message)

    def _export_visible_clients(self) -> None:
        clients = self.filtered_clients()
        if not clients:
            self.message_requested.emit(
                "Нет клиентов для экспорта."
            )
            return

        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Экспорт списка клиентов",
            "keenetic_clients.csv",
            "CSV-файлы (*.csv);;Все файлы (*.*)",
        )
        if not filename:
            return

        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".csv")
        try:
            self.write_clients_csv(path, clients)
        except OSError as error:
            self.message_requested.emit(
                f"Не удалось сохранить список клиентов: {error}"
            )
            return

        self.message_requested.emit(
            f"Список клиентов сохранён: {path}"
        )

    @classmethod
    def write_clients_csv(
        cls,
        path: str | Path,
        clients: list[dict[str, Any]],
    ) -> None:
        destination = Path(path)
        with destination.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(cls.EXPORT_HEADERS)
            for client in clients:
                writer.writerow(cls._export_row(client))

    @staticmethod
    def _export_row(client: dict[str, Any]) -> tuple[str, ...]:
        online = bool(client.get("online", True))
        blocked = bool(client.get("blocked"))
        connection_kind = str(
            client.get("connection_kind") or "unknown"
        ).strip().lower()
        connection_name = {
            "wifi": "Wi‑Fi",
            "wired": "Ethernet",
        }.get(connection_kind, "Не определено")
        status = (
            "Заблокирован"
            if blocked
            else "В сети"
            if online
            else "Не в сети"
        )
        uptime = (
            client.get("uptime")
            if online
            else client.get("last_seen")
        )
        return (
            str(
                client.get("name")
                or client.get("hostname")
                or client.get("mac")
                or "Клиент"
            ),
            status,
            connection_name,
            str(
                client.get("ssid")
                or client.get("network_name")
                or ""
            ),
            str(client.get("band") or "")
            if connection_kind == "wifi"
            else "",
            ClientCard._interface_port_text(client),
            str(client.get("ip") or ""),
            str(client.get("mac") or ""),
            ClientCard._format_dbm(client.get("rssi")) or "",
            ClientCard._format_link_rate(client.get("txrate")) or "",
            ClientCard._format_link_rate(client.get("rxrate")) or "",
            str(uptime or ""),
            "Да" if bool(client.get("registered")) else "Нет",
            "Да" if blocked else "Нет",
        )

    def filtered_clients(self) -> list[dict[str, Any]]:
        search = self.search_input.text().strip().lower()
        filter_id = str(self.filter_combo.currentData() or "all")
        clients = [
            client
            for client in self._clients
            if self._matches_filter(client, filter_id)
            and self._matches_search(client, search)
        ]
        return sorted(
            clients,
            key=self._sort_key,
        )

    def _remember_client_order(self) -> None:
        order = self._client_orders.setdefault(
            self._router_id,
            [],
        )
        for client in self._clients:
            key = ClientCard.make_client_key(client)
            if key not in order:
                order.append(key)

    @staticmethod
    def _matches_filter(
        client: dict[str, Any],
        filter_id: str,
    ) -> bool:
        if filter_id == "wifi":
            return client.get("connection_kind") == "wifi"
        if filter_id == "wired":
            return client.get("connection_kind") == "wired"
        if filter_id == "online":
            return bool(client.get("online", True))
        if filter_id == "offline":
            return not bool(client.get("online", True))
        if filter_id == "blocked":
            return bool(client.get("blocked"))
        if filter_id == "traffic":
            return bool(client.get("traffic_active"))
        if filter_id == "unregistered":
            return not bool(client.get("registered"))
        return True

    @staticmethod
    def _matches_search(
        client: dict[str, Any],
        search: str,
    ) -> bool:
        if not search:
            return True
        fields = (
            "name",
            "hostname",
            "ip",
            "mac",
            "vendor",
            "ssid",
            "network_name",
            "connection_label",
            "interface",
            "port_label",
        )
        searchable = " ".join(
            str(client.get(field) or "")
            for field in fields
        ).lower()
        return search in searchable

    def _sort_key(self, client: dict[str, Any]) -> tuple[Any, ...]:
        sort_id = str(self.sort_combo.currentData() or "stable")
        name = str(
            client.get("name")
            or client.get("hostname")
            or client.get("mac")
            or ""
        ).lower()
        mac = str(client.get("mac") or "").lower()

        if sort_id == "stable":
            order = self._client_orders.setdefault(
                self._router_id,
                [],
            )
            key = ClientCard.make_client_key(client)
            try:
                position = order.index(key)
            except ValueError:
                order.append(key)
                position = len(order) - 1
            return (position, key)

        if sort_id == "name":
            return (name, mac)
        if sort_id == "ip":
            ip_text = str(client.get("ip") or "").strip()
            try:
                ip_value = ipaddress.ip_address(ip_text)
                return (0, ip_value.version, int(ip_value), name)
            except ValueError:
                return (1, 0, 0, name)
        if sort_id == "type":
            return (
                {
                    "wifi": 0,
                    "wired": 1,
                    "unknown": 2,
                }.get(str(client.get("connection_kind")), 3),
                str(client.get("band") or ""),
                name,
            )
        return (
            0 if bool(client.get("online", True)) else 1,
            1 if bool(client.get("blocked")) else 0,
            0 if bool(client.get("traffic_active")) else 1,
            {
                "wifi": 0,
                "wired": 1,
                "unknown": 2,
            }.get(str(client.get("connection_kind")), 3),
            name,
            mac,
        )

    def _apply_filter(self, *_args: Any) -> None:
        filtered = self.filtered_clients()
        all_by_key = {
            ClientCard.make_client_key(client): client
            for client in self._clients
        }

        for key in list(self._cards):
            if key in all_by_key:
                continue
            card = self._cards.pop(key)
            self.cards_layout.removeWidget(card)
            card.deleteLater()

        for client in self._clients:
            key = ClientCard.make_client_key(client)
            card = self._cards.get(key)
            if card is None:
                card = ClientCard(
                    client,
                    self.cards_container,
                    inline_details=True,
                    status_mode="online",
                )
                card.state_change_requested.connect(
                    self.interface_state_change_requested
                )
                card.client_selected.connect(
                    self._select_client
                )
                self._cards[key] = card
            else:
                card.update_client(client)
            interface_id = str(client.get("interface") or "").strip().casefold()
            card.set_port_action_pending(
                interface_id in self._pending_port_interfaces
            )

        visible_keys = {
            ClientCard.make_client_key(client)
            for client in filtered
        }
        for index, client in enumerate(filtered):
            key = ClientCard.make_client_key(client)
            card = self._cards[key]
            card.show()
            self.cards_layout.insertWidget(index, card)

        for key, card in self._cards.items():
            if key not in visible_keys:
                card.hide()

        self.empty_label.setVisible(not filtered)

        filter_id = str(
            self.filter_combo.currentData() or "all"
        )
        for chip in self.summary_chips.values():
            chip.set_active(chip.filter_id == filter_id)

        self.visible_count_label.setText(
            f"Показано: {len(filtered)} из {len(self._clients)}"
        )
        self.export_button.setEnabled(bool(filtered))
        self.reset_view_button.setEnabled(
            bool(self.search_input.text().strip())
            or filter_id != "all"
            or str(self.sort_combo.currentData() or "stable")
            != "stable"
        )
        self._sync_selected_client()

        self.cards_layout.activate()
        self.cards_container.updateGeometry()

    def _render_disabled_ports(self) -> None:
        client_controls = {
            str(client.get("interface") or "").strip().casefold()
            for client in self._clients
            if not bool(client.get("interface_enabled", True))
            and bool(client.get("port_control_available", False))
        }
        disabled: list[tuple[str, str]] = []
        for port in self._ports:
            if str(port.get("kind") or "").strip().lower() != "ethernet":
                continue
            if bool(port.get("enabled", True)):
                continue
            interface_id = str(
                port.get("interface_id") or port.get("id") or ""
            ).strip()
            if (
                not interface_id
                or interface_id.casefold() in client_controls
            ):
                continue
            display_name = str(
                port.get("physical_name")
                or port.get("name")
                or interface_id
            ).strip()
            display_name = re.sub(r"^LAN\s+(\d+)$", r"LAN\1", display_name)
            disabled.append((interface_id, display_name))

        active_ids = {interface_id for interface_id, _name in disabled}
        for interface_id in list(self._port_buttons):
            if interface_id in active_ids:
                continue
            button = self._port_buttons.pop(interface_id)
            self.disabled_ports_layout.removeWidget(button)
            button.deleteLater()

        for index, (interface_id, display_name) in enumerate(disabled, 1):
            button = self._port_buttons.get(interface_id)
            if button is None:
                button = QPushButton(self.disabled_ports_panel)
                button.clicked.connect(self._enable_disabled_port)
                self._port_buttons[interface_id] = button
            button.setText(f"Включить {display_name}")
            button.setProperty("interface_id", interface_id)
            button.setProperty("display_name", display_name)
            button.setToolTip(
                f"Включить физический порт {display_name}."
            )
            pending = interface_id.casefold() in self._pending_port_interfaces
            button.setEnabled(not pending)
            if pending:
                button.setText(f"Включение {display_name}…")
            self.disabled_ports_layout.insertWidget(index, button)
            button.show()

        self.disabled_ports_panel.setVisible(bool(disabled))

    def set_port_action_pending(
        self,
        interface_ids: list[str],
        pending: bool,
    ) -> None:
        normalized = {
            str(value or "").strip().casefold()
            for value in interface_ids
            if str(value or "").strip()
        }
        if pending:
            self._pending_port_interfaces.update(normalized)
        else:
            self._pending_port_interfaces.difference_update(normalized)
        self._apply_filter()
        self._render_disabled_ports()

    def _enable_disabled_port(self, checked: bool = False) -> None:
        del checked
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        interface_id = str(
            button.property("interface_id") or ""
        ).strip()
        display_name = str(
            button.property("display_name") or interface_id
        ).strip()
        if interface_id:
            self.interface_state_change_requested.emit(
                [interface_id],
                display_name,
                True,
            )
