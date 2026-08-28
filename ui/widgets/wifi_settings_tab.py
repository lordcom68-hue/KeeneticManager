from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.status_badge import StatusBadge


class WiFiSettingsNetworkCard(QFrame):
    """Карточка Wi‑Fi сети на отдельной вкладке настроек."""

    edit_requested = Signal(object)
    state_change_requested = Signal(object, str, bool)
    delete_requested = Signal(object)

    def __init__(
        self,
        network: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.network: dict[str, Any] = {}
        self.setObjectName("wifiSettingsNetworkCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._build_ui()
        self.update_network(network)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 15, 18, 15)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)

        names = QVBoxLayout()
        names.setSpacing(2)
        self.group_label = QLabel()
        self.group_label.setObjectName("connectionRoleTitle")
        self.ssid_label = QLabel()
        self.ssid_label.setObjectName("connectionInterfaceTitle")
        self.ssid_label.setWordWrap(True)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        names.addWidget(self.group_label)
        names.addWidget(self.ssid_label)
        names.addWidget(self.summary_label)

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )
        header.addLayout(names, 1)
        header.addWidget(self.status_badge)
        root.addLayout(header)

        self.band_label = QLabel()
        self.band_label.setWordWrap(True)
        self.security_label = QLabel()
        self.security_label.setWordWrap(True)
        self.visibility_label = QLabel()
        root.addWidget(self.band_label)
        root.addWidget(self.security_label)
        root.addWidget(self.visibility_label)

        actions = QHBoxLayout()
        actions.addStretch()

        self.edit_button = QPushButton("Изменить")
        self.toggle_button = QPushButton("Выключить")
        self.delete_button = QPushButton("Удалить")
        self.delete_button.setObjectName("dangerButton")

        self.edit_button.clicked.connect(
            lambda: self.edit_requested.emit(dict(self.network))
        )
        self.toggle_button.clicked.connect(self._toggle)
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(dict(self.network))
        )

        actions.addWidget(self.edit_button)
        actions.addWidget(self.toggle_button)
        actions.addWidget(self.delete_button)
        root.addLayout(actions)

    def update_network(self, network: dict[str, Any]) -> None:
        self.network = dict(network)
        ssid = str(
            network.get("ssid")
            or network.get("name")
            or "Wi‑Fi"
        )
        group = str(network.get("group") or "Wi‑Fi сеть")
        clients = int(
            network.get("client_count")
            or len(network.get("clients") or [])
        )
        enabled = bool(network.get("enabled", True))
        partial = bool(network.get("partially_enabled", False))
        logical_enabled = bool(network.get("mws_enabled", enabled))
        mismatch = bool(network.get("mws_state_mismatch", False))

        self.group_label.setText(group)
        self.ssid_label.setText(ssid)
        self.summary_label.setText(f"Подключённых клиентов: {clients}")

        if mismatch and logical_enabled and not enabled:
            self.status_badge.set_status(
                text="Включена в MWS, точки доступа выключены",
                state="warning",
            )
        elif mismatch and not logical_enabled and enabled:
            self.status_badge.set_status(
                text="Выключается",
                state="warning",
            )
        elif partial:
            self.status_badge.set_status(
                text="Частично включена",
                state="warning",
            )
        elif enabled:
            self.status_badge.set_status(
                text="Включена",
                state="connected",
            )
        else:
            self.status_badge.set_status(
                text="Выключена",
                state="disconnected",
            )

        self.band_label.setText(
            "Диапазоны: " + self._band_state_text(network)
        )
        security = str(
            network.get("security_mode")
            or network.get("security")
            or "Не определена"
        )
        security_text = {
            "wpa2": "WPA2-PSK",
            "wpa2_wpa3": "WPA2/WPA3-PSK",
            "wpa3": "WPA3-SAE",
            "owe": "OWE",
            "open": "Открытая сеть",
            "mixed": "Различается по диапазонам",
            "unknown": "Не определена",
        }.get(security.lower(), security)
        self.security_label.setText(
            f"Безопасность: {security_text}"
        )
        self.visibility_label.setText(
            "SSID: скрыт"
            if bool(network.get("hidden", False))
            else "SSID: видимый"
        )

        interfaces = self._interfaces(network)
        dormant_profile = bool(
            not interfaces
            and str(network.get("mws_profile_id") or "").strip()
        )
        self.toggle_button.setText(
            "Назначить диапазон"
            if dormant_profile
            else "Включить"
            if not enabled
            else "Выключить"
        )
        self.toggle_button.setEnabled(bool(interfaces) or dormant_profile)
        self.toggle_button.setToolTip(
            "Сначала назначьте сети свободный диапазон."
            if dormant_profile
            else ""
        )
        self.edit_button.setEnabled(
            bool(network.get("editable", interfaces))
        )
        deletable = bool(network.get("deletable", False))
        # Keep the action in a stable position on every Wi-Fi card.  System
        # networks remain protected, but their disabled delete button explains
        # why the operation is unavailable instead of silently disappearing.
        self.delete_button.setVisible(True)
        self.delete_button.setEnabled(deletable)
        self.delete_button.setToolTip(
            ""
            if deletable
            else (
                "Системную Wi-Fi сеть нельзя удалить. "
                "Её можно выключить или изменить."
            )
        )

    def _toggle(self) -> None:
        interfaces = self._interfaces(self.network)
        if not interfaces:
            if str(self.network.get("mws_profile_id") or "").strip():
                self.edit_requested.emit(dict(self.network))
            return
        target = not bool(self.network.get("enabled", True))
        display_name = str(
            self.network.get("ssid")
            or self.network.get("name")
            or "Wi‑Fi сеть"
        )
        self.state_change_requested.emit(
            dict(self.network),
            display_name,
            target,
        )

    @staticmethod
    def make_key(network: dict[str, Any]) -> str:
        profile_id = str(network.get("mws_profile_id") or "").strip()
        interfaces = sorted(
            interface_id.casefold()
            for interface_id in WiFiSettingsNetworkCard._interfaces(network)
        )
        identity = (
            "mws:" + profile_id.casefold()
            if profile_id
            else "interfaces:" + ",".join(interfaces)
            if interfaces
            else "name:"
            + str(
                network.get("ssid")
                or network.get("name")
                or network.get("interface_id")
                or ""
            ).casefold()
        )
        return "|".join(
            (
                str(network.get("_router_id") or "router"),
                str(network.get("group") or "").casefold(),
                identity,
            )
        )

    @staticmethod
    def _interfaces(network: dict[str, Any]) -> list[str]:
        raw = network.get("interfaces")
        if isinstance(raw, list):
            values = raw
        else:
            values = str(network.get("interface_id") or "").split(",")
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _band_state_text(network: dict[str, Any]) -> str:
        states = network.get("band_states")
        if not isinstance(states, dict) or not states:
            return str(network.get("band") or "не назначены")

        parts: list[str] = []
        order = {"2.4": 0, "5": 1, "6": 2}
        for band, state in sorted(
            states.items(),
            key=lambda item: order.get(str(item[0]), 9),
        ):
            title = {
                "2.4": "2,4 ГГц",
                "5": "5 ГГц",
                "6": "6 ГГц",
            }.get(str(band), str(band))
            enabled = bool(
                state.get("enabled")
                if isinstance(state, dict)
                else False
            )
            parts.append(
                f"{title} — {'включена' if enabled else 'выключена'}"
            )
        return "; ".join(parts)


class WiFiSettingsTab(QWidget):
    """Полная страница настроек Wi‑Fi после вкладки «Интернет»."""

    create_requested = Signal()
    edit_requested = Signal(object)
    state_change_requested = Signal(object, str, bool)
    delete_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.router_id: int | None = None
        self.capabilities: dict[str, Any] = {}
        self.network_cards: dict[str, WiFiSettingsNetworkCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Wi‑Fi")
        title.setObjectName("tabTitle")
        description = QLabel(
            "Создание, переименование, безопасность, "
            "включение и удаление беспроводных сетей."
        )
        description.setWordWrap(True)
        self.capability_label = QLabel(
            "Совместимость управления Wi‑Fi определяется…"
        )
        self.capability_label.setObjectName("wifiCapabilityLabel")
        self.capability_label.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(description)
        titles.addWidget(self.capability_label)

        self.create_button = QPushButton("Создать сеть")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self.create_requested)
        header.addLayout(titles, 1)
        header.addWidget(self.create_button)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.container = QWidget()
        self.cards_layout = QVBoxLayout(self.container)
        self.cards_layout.setContentsMargins(2, 2, 8, 2)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel(
            "Настроенные Wi‑Fi сети не обнаружены."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.cards_layout.insertWidget(0, self.empty_label)

    def set_router(self, router_id: int | None) -> None:
        self.router_id = router_id

    def set_capabilities(self, capabilities: dict[str, Any] | None) -> None:
        self.capabilities = dict(capabilities or {})
        supported = self.capabilities.get("supported_bands") or []
        raw_slots = self.capabilities.get("available_slots")
        has_free_slot = (
            any(
                isinstance(slots, (list, tuple, set)) and bool(slots)
                for slots in raw_slots.values()
            )
            if isinstance(raw_slots, dict)
            else bool(supported)
        )
        mws_supported = bool(
            self.capabilities.get("mws_supported", True)
        )
        legacy_supported = bool(
            self.capabilities.get("legacy_supported", False)
        )
        bandless_supported = bool(
            self.capabilities.get(
                "bandless_supported",
                mws_supported,
            )
        )
        default_management_mode = (
            "mws"
            if mws_supported
            else "legacy"
            if legacy_supported
            else "monitoring"
        )
        management_mode = str(
            self.capabilities.get("management_mode")
            or default_management_mode
        ).strip().lower()
        if management_mode == "mws" and mws_supported:
            self.capability_label.setText(
                "Поддержка: полное управление Wi‑Fi через MWS"
            )
            self.capability_label.setProperty("supportMode", "supported")
        elif management_mode == "legacy" and legacy_supported:
            self.capability_label.setText(
                "Поддержка: legacy‑режим для KeeneticOS 4"
            )
            self.capability_label.setProperty("supportMode", "legacy")
        else:
            self.capability_label.setText(
                "Поддержка: мониторинг доступен, изменение Wi‑Fi "
                "этим роутером не поддерживается"
            )
            self.capability_label.setProperty("supportMode", "monitoring")
        self.capability_label.style().unpolish(self.capability_label)
        self.capability_label.style().polish(self.capability_label)
        self.create_button.setEnabled(
            bool(supported)
            and (mws_supported or legacy_supported)
            and (has_free_slot or bandless_supported)
        )
        if not mws_supported and not legacy_supported:
            self.create_button.setToolTip(
                "Роутер не предоставляет поддерживаемый способ "
                "управления Wi-Fi сетями."
            )
        elif not supported:
            self.create_button.setToolTip(
                "Роутер не сообщил доступные радиодиапазоны."
            )
        elif not has_free_slot and bandless_supported:
            self.create_button.setToolTip(
                "Свободных точек Wi-Fi нет; сеть можно создать "
                "без диапазона и назначить его позже."
            )
        elif not has_free_slot:
            self.create_button.setToolTip(
                "На этом роутере нет свободных физических точек Wi-Fi."
            )
        elif legacy_supported:
            self.create_button.setToolTip(
                "Legacy-режим KeeneticOS 4: управление физическими "
                "точками доступа."
            )
        else:
            self.create_button.setToolTip("")

    def set_networks(self, networks: list[dict[str, Any]] | None) -> None:
        wifi_networks = [
            dict(network)
            for network in (networks or [])
            if str(network.get("network_type") or "wifi").lower()
            == "wifi"
        ]
        for network in wifi_networks:
            network["_router_id"] = self.router_id

        incoming = {
            WiFiSettingsNetworkCard.make_key(network): network
            for network in wifi_networks
        }

        for key in list(self.network_cards):
            if key in incoming:
                continue
            card = self.network_cards.pop(key)
            self.cards_layout.removeWidget(card)
            card.deleteLater()

        ordered: list[WiFiSettingsNetworkCard] = []
        for network in wifi_networks:
            key = WiFiSettingsNetworkCard.make_key(network)
            card = self.network_cards.get(key)
            if card is None:
                card = WiFiSettingsNetworkCard(network, self.container)
                card.edit_requested.connect(self.edit_requested)
                card.state_change_requested.connect(
                    self.state_change_requested
                )
                card.delete_requested.connect(self.delete_requested)
                self.network_cards[key] = card
            else:
                card.update_network(network)
            ordered.append(card)

        self.empty_label.setVisible(not ordered)
        offset = 1 if self.empty_label.isVisible() else 0
        for index, card in enumerate(ordered):
            self.cards_layout.insertWidget(index + offset, card)

    def clear(self) -> None:
        for card in self.network_cards.values():
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.network_cards = {}
        self.empty_label.show()
        self.capabilities = {}
        self.create_button.setEnabled(False)
