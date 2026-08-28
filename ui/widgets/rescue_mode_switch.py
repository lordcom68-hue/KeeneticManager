from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.services.rescue_mode_service import (
    MIXED_MODE,
    RESCUED_MODE,
    RESCUER_MODE,
    RescueModeContext,
)


class RescueModeSwitch(QFrame):
    """Компактный переключатель ролей WISP/точки доступа."""

    mode_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._context: RescueModeContext | None = None
        self._busy = False

        self.setObjectName("rescueModeSwitch")
        self.setMinimumWidth(370)
        self.setMaximumWidth(450)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(11, 9, 11, 10)
        root.setSpacing(7)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(8)

        title = QLabel("Режим работы")
        title.setObjectName("rescueModeTitle")

        self.status_label = QLabel("● Недоступен")
        self.status_label.setObjectName("rescueModeStatus")
        self.status_label.setProperty("modeState", "unavailable")

        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(7)

        self.rescuer_button = QPushButton("Спасатель")
        self.rescuer_button.setObjectName("rescuerModeButton")
        self.rescuer_button.setProperty("modeActive", False)
        self.rescuer_button.setAccessibleName(
            "Включить режим Спасатель"
        )
        self.rescuer_button.clicked.connect(
            lambda _checked=False: self.mode_requested.emit(
                RESCUER_MODE
            )
        )

        self.rescued_button = QPushButton("Спасаемый")
        self.rescued_button.setObjectName("rescuedModeButton")
        self.rescued_button.setProperty("modeActive", False)
        self.rescued_button.setAccessibleName(
            "Включить режим Спасаемый"
        )
        self.rescued_button.clicked.connect(
            lambda _checked=False: self.mode_requested.emit(
                RESCUED_MODE
            )
        )

        self.mixed_button = QPushButton("Смешанный")
        self.mixed_button.setObjectName("mixedModeButton")
        self.mixed_button.setProperty("modeActive", False)
        self.mixed_button.setAccessibleName(
            "Включить смешанный режим"
        )
        self.mixed_button.clicked.connect(
            lambda _checked=False: self.mode_requested.emit(
                MIXED_MODE
            )
        )

        actions.addWidget(self.rescuer_button, 1)
        actions.addWidget(self.rescued_button, 1)
        actions.addWidget(self.mixed_button, 1)

        root.addLayout(heading)
        root.addLayout(actions)

        self.setStyleSheet(
            """
            QFrame#rescueModeSwitch {
                background: rgba(15, 23, 42, 38);
                border: 1px solid rgba(100, 116, 139, 70);
                border-radius: 10px;
            }

            QLabel#rescueModeTitle {
                color: #cbd5e1;
                font-size: 11px;
                font-weight: 600;
            }

            QLabel#rescueModeStatus {
                color: #94a3b8;
                font-size: 11px;
                font-weight: 600;
            }

            QLabel#rescueModeStatus[modeState="rescuer"] {
                color: #34d399;
            }

            QLabel#rescueModeStatus[modeState="rescued"] {
                color: #60a5fa;
            }

            QLabel#rescueModeStatus[modeState="mixed"],
            QLabel#rescueModeStatus[modeState="inactive"],
            QLabel#rescueModeStatus[modeState="busy"] {
                color: #fbbf24;
            }

            QPushButton#rescuerModeButton,
            QPushButton#rescuedModeButton,
            QPushButton#mixedModeButton {
                min-height: 30px;
                padding: 0 8px;
                color: #cbd5e1;
                background: rgba(30, 41, 59, 76);
                border: 1px solid rgba(100, 116, 139, 92);
                border-radius: 7px;
                font-weight: 600;
            }

            QPushButton#rescuerModeButton:hover:enabled,
            QPushButton#rescuedModeButton:hover:enabled,
            QPushButton#mixedModeButton:hover:enabled {
                color: #ffffff;
                background: rgba(96, 165, 250, 28);
                border-color: #60a5fa;
            }

            QPushButton#rescuerModeButton[modeActive="true"] {
                color: #d1fae5;
                background: rgba(52, 211, 153, 31);
                border-color: #34d399;
            }

            QPushButton#rescuedModeButton[modeActive="true"] {
                color: #dbeafe;
                background: rgba(96, 165, 250, 34);
                border-color: #60a5fa;
            }

            QPushButton#mixedModeButton[modeActive="true"] {
                color: #fef3c7;
                background: rgba(251, 191, 36, 30);
                border-color: #fbbf24;
            }

            QPushButton#rescuerModeButton:disabled,
            QPushButton#rescuedModeButton:disabled,
            QPushButton#mixedModeButton:disabled {
                color: #64748b;
                background: rgba(15, 23, 42, 28);
                border-color: rgba(100, 116, 139, 55);
            }
            """
        )
        self.clear()

    @staticmethod
    def _refresh_style(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _set_active_mode(self, mode: str) -> None:
        for button, button_mode in (
            (self.rescuer_button, RESCUER_MODE),
            (self.rescued_button, RESCUED_MODE),
            (self.mixed_button, MIXED_MODE),
        ):
            button.setProperty(
                "modeActive",
                mode == button_mode,
            )
            self._refresh_style(button)

    def set_context(self, context: RescueModeContext) -> None:
        self._context = context
        self._busy = False
        self._set_active_mode(context.mode)

        status = {
            RESCUER_MODE: "● Спасатель",
            RESCUED_MODE: "● Спасаемый",
            MIXED_MODE: "● Смешанный",
            "inactive": "● Оба выключены",
        }.get(context.mode, "● Недоступен")
        self.status_label.setText(status)
        self.status_label.setProperty("modeState", context.mode)
        self._refresh_style(self.status_label)

        enabled = bool(context.available)
        self.rescuer_button.setEnabled(enabled)
        self.rescued_button.setEnabled(enabled)
        self.mixed_button.setEnabled(enabled)

        targets = ""
        if context.available:
            targets = (
                "\nWireless ISP: "
                + ", ".join(context.wisp_names)
                + "\nWi-Fi: "
                + ", ".join(context.wifi_names)
            )
        self.setToolTip(context.message + targets)
        self.rescuer_button.setToolTip(
            "Выключить Wireless ISP и включить раздачу Wi-Fi."
            + targets
        )
        self.rescued_button.setToolTip(
            "Включить Wireless ISP и выключить раздачу Wi-Fi."
            + targets
        )
        self.mixed_button.setToolTip(
            "Включить Wireless ISP и раздачу Wi-Fi одновременно."
            + targets
        )

    def set_busy(self, mode: str) -> None:
        self._busy = True
        self._set_active_mode("")
        self.rescuer_button.setEnabled(False)
        self.rescued_button.setEnabled(False)
        self.mixed_button.setEnabled(False)
        title = {
            RESCUER_MODE: "Спасатель",
            RESCUED_MODE: "Спасаемый",
            MIXED_MODE: "Смешанный",
        }.get(mode, "режим")
        self.status_label.setText(f"● Включение: {title}")
        self.status_label.setProperty("modeState", "busy")
        self._refresh_style(self.status_label)
        self.setToolTip("Роутер применяет новый режим работы.")

    def clear(self) -> None:
        self._context = None
        self._busy = False
        self._set_active_mode("")
        self.status_label.setText("● Недоступен")
        self.status_label.setProperty("modeState", "unavailable")
        self._refresh_style(self.status_label)
        self.rescuer_button.setEnabled(False)
        self.rescued_button.setEnabled(False)
        self.mixed_button.setEnabled(False)
        self.setToolTip(
            "Подключитесь к роутеру с настроенным Wireless ISP "
            "и Wi-Fi сетью."
        )
