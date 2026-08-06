from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class RouterMonitorActionPanel(QFrame):
    """Компактное состояние мониторинга и одно актуальное действие."""

    STATE_DATA = {
        "none": ("", "#64748B", None),
        "disconnected": (
            "Мониторинг не запущен",
            "#64748B",
            "connect",
        ),
        "connecting": (
            "Подключение…",
            "#FBBF24",
            "stop",
        ),
        "connected": (
            "Мониторинг активен",
            "#34D399",
            "disconnect",
        ),
        "retrying": (
            "Повторная попытка…",
            "#FBBF24",
            "stop",
        ),
        "unstable": (
            "Связь нестабильна",
            "#FB7185",
            "stop",
        ),
        "reconnecting": (
            "Переподключение…",
            "#FBBF24",
            "stop",
        ),
        "authentication_required": (
            "Требуется авторизация",
            "#FB7185",
            "stop",
        ),
        "stopping": (
            "Остановка мониторинга…",
            "#94A3B8",
            None,
        ),
    }

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._state = "none"
        self.setObjectName("routerMonitorActionPanel")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(8)

        self.status_dot = QFrame(self)
        self.status_dot.setObjectName("routerMonitorStatusDot")
        self.status_dot.setFixedSize(8, 8)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("routerMonitorStatusText")

        self.connect_button = QPushButton(
            "Подключиться",
            self,
        )
        self.connect_button.setObjectName(
            "routerConnectAction"
        )
        self.connect_button.setMinimumWidth(104)

        self.disconnect_button = QPushButton(
            "Отключить",
            self,
        )
        self.disconnect_button.setObjectName(
            "routerDisconnectAction"
        )
        self.disconnect_button.setMinimumWidth(96)

        layout.addWidget(self.status_dot)
        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.connect_button)
        layout.addWidget(self.disconnect_button)

        self.setStyleSheet(
            """
            QFrame#routerMonitorActionPanel {
                background: rgba(30, 41, 59, 87);
                border: 1px solid palette(mid);
                border-radius: 9px;
            }

            QLabel#routerMonitorStatusText {
                background: transparent;
                border: none;
                font-size: 11px;
                font-weight: 600;
            }

            QPushButton#routerConnectAction {
                color: #ffffff;
                background: #2563eb;
                border: 1px solid #3b82f6;
                border-radius: 7px;
                padding: 0 11px;
                font-weight: 600;
            }

            QPushButton#routerConnectAction:hover {
                background: #3b82f6;
                border-color: #60a5fa;
            }

            QPushButton#routerDisconnectAction {
                background: transparent;
                border: 1px solid #64748b;
                border-radius: 7px;
                padding: 0 10px;
                font-weight: 600;
            }

            QPushButton#routerDisconnectAction:hover {
                background: rgba(96, 165, 250, 26);
                border-color: #60a5fa;
            }
            """
        )

        self.set_state("none")

    def set_state(
        self,
        state: str,
    ) -> None:
        normalized = str(state or "").strip().lower()

        if normalized not in self.STATE_DATA:
            normalized = "none"

        self._state = normalized
        text, color, action = self.STATE_DATA[normalized]

        self.status_label.setText(text)
        self.status_dot.setStyleSheet(
            f"background:{color}; border:none; border-radius:4px;"
        )

        show_connect = action == "connect"
        show_disconnect = action in {
            "disconnect",
            "stop",
        }
        self.connect_button.setVisible(show_connect)
        self.connect_button.setEnabled(show_connect)
        self.disconnect_button.setVisible(show_disconnect)
        self.disconnect_button.setEnabled(show_disconnect)
        self.disconnect_button.setText(
            "Отключить"
            if action == "disconnect"
            else "Остановить"
        )
        self.disconnect_button.setToolTip(
            (
                "Остановить мониторинг выбранного роутера"
                if action == "disconnect"
                else "Остановить текущую попытку подключения"
            )
            if show_disconnect
            else ""
        )
        self.connect_button.setToolTip(
            "Подключить выбранный роутер к мониторингу"
            if show_connect
            else ""
        )
        self.setVisible(normalized != "none")

    def state(self) -> str:
        return self._state
