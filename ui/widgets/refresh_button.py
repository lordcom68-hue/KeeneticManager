from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton, QWidget


_REFRESH_PATH = (
    "M17.65 6.35C16.2 3.9 13.55 2.25 10.45 2.25"
    "C5.85 2.25 2.15 5.95 2.15 10.5"
    "S5.85 18.75 10.45 18.75"
    "C14.35 18.75 17.6 16.05 18.45 12.4"
    "H16.2C15.45 14.85 13.15 16.6 10.45 16.6"
    "C7.05 16.6 4.3 13.9 4.3 10.5"
    "S7.05 4.4 10.45 4.4"
    "C12.7 4.4 14.65 5.6 15.7 7.4"
    "H12.45V9.5H19.45V2.4H17.65V6.35Z"
)


class RefreshToolButton(QToolButton):
    """DPI-независимая кнопка с плоской иконкой обновления."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        glyph_size: int = 20,
    ) -> None:
        super().__init__(parent)
        self._glyph_size = max(14, int(glyph_size))
        self.setText("")

    @property
    def glyph_size(self) -> int:
        return self._glyph_size

    def glyph_rect(self) -> QRectF:
        size = float(
            min(self._glyph_size, self.width() - 6, self.height() - 6)
        )
        return QRectF(
            (self.width() - size) / 2.0,
            (self.height() - size) / 2.0,
            size,
            size,
        )

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Базовый класс рисует фон, рамку, hover и pressed-состояния.
        super().paintEvent(event)
        if not self.isEnabled():
            color = "#64748b"
        elif self.underMouse() or self.hasFocus():
            color = "#ffffff"
        else:
            color = "#cbd5e1"

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 21 21">'
            f'<path fill="{color}" d="{_REFRESH_PATH}"/>'
            "</svg>"
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        painter = QPainter(self)
        renderer.render(painter, self.glyph_rect())
        painter.end()


class AddToolButton(QToolButton):
    """DPI-независимая кнопка добавления с плоским знаком плюс."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        glyph_size: int = 18,
    ) -> None:
        super().__init__(parent)
        self._glyph_size = max(14, int(glyph_size))
        self.setText("")

    @property
    def glyph_size(self) -> int:
        return self._glyph_size

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if not self.isEnabled():
            color = QColor("#64748b")
        elif self.underMouse() or self.hasFocus():
            color = QColor("#ffffff")
        else:
            color = QColor("#cbd5e1")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        half_span = min(
            self._glyph_size / 2.0,
            (self.width() - 8) / 2.0,
            (self.height() - 8) / 2.0,
        )
        painter.drawLine(
            center_x - half_span,
            center_y,
            center_x + half_span,
            center_y,
        )
        painter.drawLine(
            center_x,
            center_y - half_span,
            center_x,
            center_y + half_span,
        )
        painter.end()
