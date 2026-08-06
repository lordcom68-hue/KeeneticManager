from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget


class StatusBadge(QFrame):
    """
    Минималистичная статусная плашка:
    - без фона;
    - без рамки;
    - маленький белый кружок;
    - текст цветом текущей темы.
    """

    STATE_COLORS = {
        "connected": "#34d399",
        "online": "#34d399",
        "enabled": "#34d399",
        "success": "#34d399",
        "warning": "#fbbf24",
        "checking": "#fbbf24",
        "connecting": "#fbbf24",
        "disconnected": "#fb7185",
        "offline": "#fb7185",
        "disabled": "#fb7185",
        "error": "#fb7185",
        "neutral": "#64748b",
        "unknown": "#64748b",
    }

    def __init__(self, text: str = "", state: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state

        self.setObjectName("statusBadge")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QFrame#statusBadge { background: transparent; border: none; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        self.dot = QFrame(self)
        self.dot.setFixedSize(8, 8)

        self.text_label = QLabel(self)
        self.text_label.setStyleSheet(
            "color:#cbd5e1; background:transparent; border:none;"
            "font-size:11px; font-weight:700;"
        )
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.dot)
        layout.addWidget(self.text_label)

        self.set_status(text, state)

    def set_status(self, text: str, state: str | None = None) -> None:
        if state is not None:
            self._state = state
        self.text_label.setText(str(text).strip().upper())
        color = self.STATE_COLORS.get(
            str(self._state or "").strip().lower(),
            self.STATE_COLORS["unknown"],
        )
        self.dot.setStyleSheet(
            f"background:{color}; border:none; border-radius:4px;"
        )

    def set_state(self, state: str) -> None:
        self.set_status(self.text_label.text(), state)

    def set_text(self, text: str) -> None:
        self.text_label.setText(str(text).strip().upper())

    def text(self) -> str:
        return self.text_label.text()

    def state(self) -> str:
        return self._state
