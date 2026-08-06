from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PortGlyph(QWidget):
    """Схематичное изображение одного физического порта."""

    def __init__(
        self,
        port: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._port: dict[str, Any] = {}
        self._hovered = False
        self.setFixedSize(54, 72)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.setMouseTracking(True)
        self.update_port(port)

    def update_port(
        self,
        port: dict[str, Any],
    ) -> None:
        self._port = dict(port)
        self.setToolTip(
            self._build_tooltip(port)
        )
        self.setAccessibleName(
            str(
                port.get("physical_name")
                or port.get("name")
                or "Порт"
            )
        )
        self.update()

    def paintEvent(self, event) -> None:
        del event

        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        connected = bool(
            self._port.get("connected")
        )
        enabled = bool(
            self._port.get("enabled", True)
        )
        kind = str(
            self._port.get("kind")
            or "ethernet"
        ).lower()
        physical_name = str(
            self._port.get("physical_name")
            or self._port.get("name")
            or "—"
        )
        role = str(
            self._port.get("role")
            or ""
        )

        self._draw_hover_background(painter)

        if kind == "usb":
            self._draw_usb(
                painter,
                connected=connected,
                enabled=enabled,
            )
        else:
            self._draw_rj45(
                painter,
                connected=connected,
                enabled=enabled,
            )

        self._draw_status_indicator(
            painter,
            connected=connected,
            enabled=enabled,
        )

        if kind != "usb" and role.upper() == "WAN":
            self._draw_wan_badge(painter)

        self._draw_labels(
            painter,
            physical_name=physical_name,
            connected=connected,
            kind=kind,
            role=role,
        )

    def _draw_rj45(
        self,
        painter: QPainter,
        *,
        connected: bool,
        enabled: bool,
    ) -> None:
        outline = QColor(
            "#60a5fa"
            if connected
            else "#64748b"
        )
        fill = QColor(
            "#17344a"
            if connected
            else "#202936"
        )

        if not enabled:
            outline = QColor("#475569")
            fill = QColor("#1f2937")

        painter.setPen(
            QPen(outline, 1.1)
        )
        painter.setBrush(fill)

        path = QPainterPath()
        path.moveTo(6, 31)
        path.lineTo(6, 16)
        path.lineTo(14, 16)
        path.lineTo(14, 12)
        path.lineTo(40, 12)
        path.lineTo(40, 16)
        path.lineTo(48, 16)
        path.lineTo(48, 31)
        path.lineTo(43, 36)
        path.lineTo(11, 36)
        path.closeSubpath()
        painter.drawPath(path)

        number = self._port_number()

        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(7, 18, 40, 14),
            Qt.AlignmentFlag.AlignCenter,
            number,
        )

    def _draw_usb(
        self,
        painter: QPainter,
        *,
        connected: bool,
        enabled: bool,
    ) -> None:
        outline = QColor(
            "#60a5fa"
            if connected
            else "#64748b"
        )
        fill = QColor(
            "#17344a"
            if connected
            else "#202936"
        )

        if not enabled:
            outline = QColor("#475569")
            fill = QColor("#1f2937")

        painter.setPen(
            QPen(outline, 1.1)
        )
        painter.setBrush(fill)
        painter.drawRoundedRect(
            QRectF(8, 15, 38, 21),
            4,
            4,
        )

        painter.drawRect(
            QRectF(17, 10, 20, 5)
        )

        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setPointSize(6)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(8, 15, 38, 21),
            Qt.AlignmentFlag.AlignCenter,
            "USB",
        )

    def _draw_hover_background(
        self,
        painter: QPainter,
    ) -> None:
        if not self._hovered:
            return

        fill = QColor("#334155")
        fill.setAlpha(92)
        border = QColor("#64748b")
        border.setAlpha(105)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 0.9))
        painter.drawRoundedRect(
            QRectF(1, 1, 52, 70),
            8,
            8,
        )

    @staticmethod
    def _draw_status_indicator(
        painter: QPainter,
        *,
        connected: bool,
        enabled: bool,
    ) -> None:
        color = QColor(
            "#34d399"
            if connected and enabled
            else "#64748b"
        )

        if connected and enabled:
            ring = QColor(color)
            ring.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring, 1.0))
            painter.drawEllipse(QRectF(42, 3, 10, 10))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(44, 5, 6, 6))

    @staticmethod
    def _draw_wan_badge(
        painter: QPainter,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1d4ed8"))
        painter.drawRoundedRect(
            QRectF(3, 3, 24, 10),
            4,
            4,
        )
        font = QFont()
        font.setPointSize(5)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#dbeafe"))
        painter.drawText(
            QRectF(3, 3, 24, 10),
            Qt.AlignmentFlag.AlignCenter,
            "WAN",
        )

    def _draw_labels(
        self,
        painter: QPainter,
        *,
        physical_name: str,
        connected: bool,
        kind: str,
        role: str,
    ) -> None:
        painter.setPen(QColor("#ffffff"))

        name_font = QFont()
        name_font.setPointSize(7)
        painter.setFont(name_font)

        short_name = self._short_name(
            physical_name,
            kind,
            role,
            str(self._port.get("label") or "0"),
        )
        painter.drawText(
            QRectF(1, 39, 52, 11),
            Qt.AlignmentFlag.AlignCenter,
            short_name,
        )

        speed_font = QFont()
        speed_font.setPointSizeF(6.2)
        painter.setFont(speed_font)
        painter.setPen(QColor("#dbe5ef"))

        speed = self._speed_text()

        if not connected:
            speed = "—"

        painter.drawText(
            QRectF(0, 55, 54, 11),
            Qt.AlignmentFlag.AlignCenter,
            speed,
        )

    def _port_number(self) -> str:
        physical_name = str(
            self._port.get("physical_name")
            or ""
        )

        if physical_name == "WAN":
            if str(self._port.get("role") or "").upper() == "WAN":
                return "W"

            return str(self._port.get("label") or "0")

        if physical_name.upper().startswith("LAN"):
            parts = physical_name.split()

            if parts:
                return parts[-1]

        return "•"

    @staticmethod
    def _short_name(
        physical_name: str,
        kind: str,
        role: str,
        label: str,
    ) -> str:
        if kind == "usb":
            return "USB"

        if physical_name == "WAN" and role.upper() != "WAN":
            return f"LAN{label or '0'}"

        return physical_name.replace(
            "LAN ",
            "LAN",
        )

    def _duplex_text(self) -> str:
        duplex = str(
            self._port.get("duplex")
            or ""
        ).lower()

        if duplex == "full":
            return "Полный дуплекс"

        if duplex == "half":
            return "Полудуплекс"

        return "Не определён"

    def _speed_text(self) -> str:
        value = self._port.get(
            "speed_mbps"
        )

        try:
            speed = float(value)
        except (TypeError, ValueError):
            return "—"

        if speed >= 1000:
            return f"{speed / 1000:g} Гбит/с"

        return f"{speed:g} Мбит/с"

    def _build_tooltip(
        self,
        port: dict[str, Any],
    ) -> str:
        physical_name = str(
            port.get("physical_name")
            or port.get("name")
            or "Порт"
        )
        role = str(
            port.get("role")
            or ""
        )
        connected = bool(
            port.get("connected")
        )
        enabled = bool(
            port.get("enabled", True)
        )

        lines = [
            physical_name,
            (
                "Подключён"
                if connected and enabled
                else (
                    "Выключен"
                    if not enabled
                    else "Отключён"
                )
            ),
        ]

        if role and role != physical_name:
            lines.append(
                f"Назначение: {role}"
            )

        if connected:
            speed = self._speed_text()
            duplex = self._duplex_text()

            if speed != "—":
                lines.append(f"Скорость: {speed}")

            if duplex != "Не определён":
                lines.append(f"Дуплекс: {duplex}")

        interface_id = str(
            port.get("interface_id")
            or port.get("id")
            or ""
        ).strip()

        if interface_id:
            lines.append(f"Интерфейс: {interface_id}")

        return "\n".join(lines)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class PortOverview(QWidget):
    """Компактная схема всех физических портов роутера."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._glyphs: dict[
            str,
            PortGlyph,
        ] = {}

        self.setObjectName("portOverview")
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

        # Постоянно резервируем место под максимальную схему:
        # WAN + четыре LAN + USB. Поэтому подключение роутера
        # больше не меняет геометрию верхней панели.
        self.setFixedSize(
            340,
            72,
        )
        self.setStyleSheet(
            """
            QWidget#portOverview {
                background: transparent;
                border: none;
            }

            QLabel#portOverviewTitle {
                color: #94a3b8;
                background: transparent;
                border: none;
                font-size: 11px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.ports_row = QHBoxLayout()
        self.ports_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        self.ports_row.setSpacing(2)
        self.ports_row.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignTop
        )

        root.addLayout(self.ports_row)

        self.set_ports([])

    def set_ports(
        self,
        ports: list[dict[str, Any]] | None,
    ) -> None:
        ports = list(ports or [])

        incoming: dict[
            str,
            dict[str, Any],
        ] = {}

        for index, port in enumerate(ports):
            kind = str(
                port.get("kind")
                or "ethernet"
            ).lower()
            identity = (
                port.get("interface_id")
                or port.get("id")
                or index
            )
            key = (
                f"{kind}:{identity}"
            ).lower()
            incoming[key] = port

        for key in list(self._glyphs):
            if key in incoming:
                continue

            glyph = self._glyphs.pop(key)
            self.ports_row.removeWidget(
                glyph
            )
            glyph.deleteLater()

        for index, (
            key,
            port,
        ) in enumerate(incoming.items()):
            glyph = self._glyphs.get(key)

            if glyph is None:
                glyph = PortGlyph(
                    port,
                    self,
                )
                self._glyphs[key] = glyph
                self.ports_row.addWidget(
                    glyph
                )
            else:
                glyph.update_port(port)

            if (
                self.ports_row.indexOf(glyph)
                != index
            ):
                self.ports_row.insertWidget(
                    index,
                    glyph,
                )

        # Виджет намеренно не скрывается при пустом списке.
        # До подключения он остаётся прозрачным контейнером
        # фиксированного размера.
        self.setVisible(True)
        self.update()

    def clear(self) -> None:
        self.set_ports([])
