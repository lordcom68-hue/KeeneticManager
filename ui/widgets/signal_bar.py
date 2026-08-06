from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class SegmentedSignalBar(QWidget):
    """Компактная семисегментная шкала уровня Wi‑Fi."""

    def __init__(
        self,
        rssi: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rssi = self._to_float(rssi)
        self.setFixedSize(150, 22)

    def update_rssi(self, value: Any) -> None:
        rssi = self._to_float(value)

        if rssi == self._rssi:
            return

        self._rssi = rssi
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(150, 22)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        segment_count = 7
        gap = 3.0
        segment_height = 7.0
        segment_width = (
            float(self.width())
            - gap * (segment_count - 1)
        ) / segment_count

        active = QColor("#7dd3fc")
        inactive = QColor("#2f3947")
        border = QColor("#4b5563")
        filled = self._filled_segments()

        for index in range(segment_count):
            rect = QRectF(
                index * (segment_width + gap),
                7.0,
                segment_width,
                segment_height,
            )
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(
                active if index < filled else inactive
            )
            painter.drawRoundedRect(
                rect,
                3.0,
                3.0,
            )

    def _filled_segments(self) -> int:
        if self._rssi is None:
            return 0

        value = max(-100.0, min(-40.0, self._rssi))
        return max(
            1,
            min(
                7,
                int((value + 100.0) // 10.0) + 1,
            ),
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
