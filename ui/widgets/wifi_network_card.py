from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
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

from ui.widgets.client_card import ClientCard
from ui.widgets.status_badge import StatusBadge
from ui.widgets.stable_expand import set_expanded_without_flicker


class WifiNetworkCard(QFrame):
    """Карточка Wi‑Fi сети или проводных клиентов."""

    state_change_requested = Signal(object, str, bool)
    delete_requested = Signal(object)

    _expanded_states: dict[str, bool] = {}
    _client_orders: dict[str, list[str]] = {}

    def __init__(
        self,
        network: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.network: dict[str, Any] = {}
        self.network_key = self.make_network_key(network)
        self.network_type = str(
            network.get("network_type")
            or "wifi"
        ).lower()
        self.expanded = self._expanded_states.get(
            self.network_key,
            False,
        )
        self._toggle_in_progress = False

        self.client_cards: dict[str, ClientCard] = {}
        self._client_order = self._client_orders.setdefault(
            self.network_key,
            [],
        )
        self._action_interfaces: list[str] = []
        self._action_display_name = ""
        self._action_target_enabled = False

        self.setObjectName("wifiNetworkCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.setStyleSheet(
            """
            QFrame#wifiNetworkCard {
                border: 1px solid palette(mid);
                border-radius: 10px;
            }
            QFrame#wifiClientCard {
                border: 1px solid palette(mid);
                border-radius: 9px;
            }
            QFrame#wifiClientMetric {
                border: 1px solid palette(mid);
                border-radius: 7px;
            }
            QLabel#wifiClientName {
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#wifiClientMetricName {
                font-size: 11px;
            }
            QLabel#wifiClientMetricValue {
                font-size: 13px;
                font-weight: 600;
            }
            """
        )

        self._build_ui()
        self.update_network(network)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)

        names = QVBoxLayout()
        names.setSpacing(2)

        self.role_label = QLabel()
        self.role_label.setObjectName(
            "connectionRoleTitle"
        )

        self.name_label = QLabel()
        self.name_label.setObjectName(
            "connectionInterfaceTitle"
        )
        self.name_label.setWordWrap(True)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)

        names.addWidget(self.role_label)
        names.addWidget(self.name_label)
        names.addWidget(self.summary_label)

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )

        self.action_button = QPushButton()
        self.action_button.setFixedWidth(115)
        self.action_button.clicked.connect(
            self._emit_main_action
        )

        self.delete_button = QPushButton("Удалить")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setFixedWidth(100)
        self.delete_button.clicked.connect(
            self._emit_delete
        )

        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(self.expanded)
        self.toggle_button.setFixedSize(38, 34)
        self.toggle_button.clicked.connect(
            self._toggle_details
        )
        self._update_arrow()

        header.addLayout(names, 1)
        header.addWidget(self.status_badge)
        header.addWidget(self.action_button)
        header.addWidget(self.delete_button)
        header.addWidget(self.toggle_button)
        root.addLayout(header)

        self.details_widget = QWidget(self)
        self.details_widget.setVisible(self.expanded)

        details = QVBoxLayout(self.details_widget)
        details.setContentsMargins(0, 8, 0, 0)
        details.setSpacing(10)

        self.clients_title = QLabel()
        self.clients_title.setObjectName(
            "wifiClientsTitle"
        )
        details.addWidget(self.clients_title)

        self.clients_container = QWidget(
            self.details_widget
        )
        self.clients_layout = QVBoxLayout(
            self.clients_container
        )
        self.clients_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        self.clients_layout.setSpacing(10)

        details.addWidget(self.clients_container)
        root.addWidget(self.details_widget)

    def update_network(
        self,
        network: dict[str, Any],
    ) -> None:
        self.network = network
        self.network_type = str(
            network.get("network_type")
            or "wifi"
        ).lower()

        wired = self.network_type == "wired"
        clients = network.get("clients") or []
        count = int(
            network.get("client_count")
            or len(clients)
        )

        self.role_label.setText(
            "Проводные подключения"
            if wired
            else str(
                network.get("group")
                or "Wi‑Fi сеть"
            )
        )
        self.name_label.setText(
            "Ethernet-клиенты"
            if wired
            else str(
                network.get("ssid")
                or network.get("name")
                or "Wi‑Fi"
            )
        )
        self.summary_label.setText(
            f"Клиентов: {count}"
            if wired
            else (
                f"Диапазоны: "
                f"{self._band_summary(network)}"
                f"  •  Клиентов: {count}"
            )
        )

        enabled = bool(
            network.get("enabled", True)
        )
        online = bool(
            network.get("online", enabled)
        )
        logical_enabled = bool(
            network.get("mws_enabled", enabled)
        )
        mismatch = bool(
            network.get("mws_state_mismatch", False)
        )

        if wired:
            self.status_badge.set_status(
                text=(
                    "Подключено"
                    if count
                    else "Нет клиентов"
                ),
                state=(
                    "connected"
                    if count
                    else "disconnected"
                ),
            )
        else:
            if mismatch and logical_enabled and not online:
                status_text = "MWS включена, точки доступа выключены"
                status_state = "warning"
            elif mismatch and not logical_enabled and online:
                status_text = "Выключается"
                status_state = "warning"
            else:
                status_text = (
                    "Подключено"
                    if enabled and online
                    else "Отключено"
                )
                status_state = (
                    "connected"
                    if enabled and online
                    else "disconnected"
                )
            self.status_badge.set_status(
                text=status_text,
                state=status_state,
            )

        self.action_button.setVisible(not wired)
        self.delete_button.setVisible(False)
        self.delete_button.setEnabled(False)
        self.delete_button.setToolTip("")

        if not wired:
            interfaces = self._wifi_interfaces(network)
            target = not enabled

            self.action_button.setText(
                "Включить"
                if target
                else "Выключить"
            )
            self.action_button.setEnabled(
                bool(interfaces)
            )

            self._action_interfaces = interfaces
            self._action_display_name = (
                self.name_label.text()
            )
            self._action_target_enabled = target

        self.clients_title.setText(
            "Подключённые проводные клиенты"
            if wired
            else "Подключённые Wi‑Fi клиенты"
        )

        self._update_clients(clients)

    def _update_clients(
        self,
        clients: list[dict[str, Any]],
    ) -> None:
        incoming: dict[str, dict[str, Any]] = {}
        for client in clients:
            key = ClientCard.make_client_key(client)
            incoming.setdefault(key, client)
            if key not in self._client_order:
                self._client_order.append(key)

        for key in list(self.client_cards):
            if key in incoming:
                continue

            card = self.client_cards.pop(key)
            self.clients_layout.removeWidget(card)
            card.deleteLater()

        ordered_cards: list[ClientCard] = []

        for key in self._client_order:
            client = incoming.get(key)
            if client is None:
                continue
            card = self.client_cards.get(key)

            if card is None:
                card = ClientCard(
                    client,
                    self.clients_container,
                )
                card.state_change_requested.connect(
                    self.state_change_requested
                )
                self.client_cards[key] = card
                self.clients_layout.addWidget(card)
            else:
                card.update_client(client)

            ordered_cards.append(card)

        for index, card in enumerate(ordered_cards):
            if self.clients_layout.indexOf(card) != index:
                self.clients_layout.insertWidget(index, card)

        if not clients:
            if not hasattr(self, "empty_label"):
                self.empty_label = QLabel(
                    "Подключённые устройства не обнаружены."
                )
                self.empty_label.setAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )
                self.empty_label.setWordWrap(True)
                self.clients_layout.addWidget(
                    self.empty_label
                )
            self.empty_label.show()
        elif hasattr(self, "empty_label"):
            self.empty_label.hide()

    def _emit_delete(
        self,
        checked: bool = False,
    ) -> None:
        if not bool(self.network.get("deletable", False)):
            return

        self.delete_requested.emit(dict(self.network))

    def _emit_main_action(
        self,
        checked: bool = False,
    ) -> None:
        if not self._action_interfaces:
            return

        self.state_change_requested.emit(
            dict(self.network),
            self._action_display_name,
            self._action_target_enabled,
        )

    def _toggle_details(
        self,
        checked: bool,
    ) -> None:
        if self._toggle_in_progress:
            return

        self._toggle_in_progress = True
        self.expanded = checked
        self._expanded_states[self.network_key] = checked

        set_expanded_without_flicker(
            owner=self,
            content=self.details_widget,
            expanded=checked,
            after_change=self._update_arrow,
        )

        from PySide6.QtCore import QTimer
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
    def make_network_key(
        network: dict[str, Any],
    ) -> str:
        router_id = str(
            network.get("_router_id")
            or "unknown-router"
        ).strip().lower()

        network_type = str(
            network.get("network_type")
            or "wifi"
        ).casefold()
        group = str(
            network.get("group")
            or ""
        ).casefold()

        raw_interfaces = network.get("interfaces")
        if isinstance(raw_interfaces, list):
            values = raw_interfaces
        else:
            values = str(
                network.get("interface_id") or ""
            ).split(",")
        profile_id = str(
            network.get("mws_profile_id") or ""
        ).strip()
        interfaces = sorted(
            str(value or "").strip().casefold()
            for value in values
            if str(value or "").strip()
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

        return (
            f"{router_id}|"
            f"{network_type}|"
            f"{group}|"
            f"{identity}"
        )

    @staticmethod
    def _band_summary(
        network: dict[str, Any],
    ) -> str:
        states = network.get("band_states")

        if not isinstance(states, dict) or not states:
            return str(network.get("band") or "Wi‑Fi")

        order = {"2.4": 0, "5": 1, "6": 2}
        parts: list[str] = []

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
                f"{title} {'вкл.' if enabled else 'выкл.'}"
            )

        return " / ".join(parts)

    @staticmethod
    def _wifi_interfaces(
        network: dict[str, Any],
    ) -> list[str]:
        values = network.get("interfaces")

        if not isinstance(values, list):
            values = str(
                network.get("interface_id")
                or ""
            ).split(",")

        result: list[str] = []

        for value in values:
            text = str(value).strip()

            if text and text not in result:
                result.append(text)

        return result
