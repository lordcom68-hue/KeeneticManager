from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from ui.widgets.status_badge import StatusBadge
from ui.widgets.stable_expand import set_expanded_without_flicker

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class TrafficChart(QWidget):
    """Компактный график скорости приёма и передачи."""

    RX_COLOR = QColor("#60a5fa")
    TX_COLOR = QColor("#34d399")

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rx_values: list[float] = []
        self.tx_values: list[float] = []
        self.sample_times: list[float] = []
        self.setFixedHeight(104)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setToolTip(
            "Скорость за последние измерения. "
            "Голубая линия — приём, мятная — передача."
        )

    def set_values(
        self,
        rx_values: Sequence[float],
        tx_values: Sequence[float],
        sample_times: Sequence[float] | None = None,
    ) -> None:
        self.rx_values = self.normalize_values(rx_values)
        self.tx_values = self.normalize_values(tx_values)
        self.sample_times = self.normalize_times(sample_times)
        self.update()

    @staticmethod
    def normalize_values(values: Sequence[float]) -> list[float]:
        normalized: list[float] = []
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = 0.0
            normalized.append(
                number
                if math.isfinite(number) and number >= 0.0
                else 0.0
            )
        return normalized

    @staticmethod
    def normalize_times(
        values: Sequence[float] | None,
    ) -> list[float]:
        if values is None:
            return []
        normalized: list[float] = []
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return []
            if not math.isfinite(number):
                return []
            normalized.append(number)
        if any(
            current < previous
            for previous, current in zip(
                normalized,
                normalized[1:], strict=False,
            )
        ):
            return []
        return normalized

    @staticmethod
    def nice_scale_max(values: Sequence[float]) -> float:
        peak = max(values, default=0.0)
        if not math.isfinite(peak) or peak <= 0.0:
            return 1.0

        # Небольшой запас не даёт пику прилипать к верхней границе.
        target = peak * 1.08
        magnitude = 10.0 ** math.floor(math.log10(target))
        for multiplier in (1.0, 1.25, 1.5, 2.0, 2.5, 5.0, 10.0):
            candidate = magnitude * multiplier
            if candidate >= target:
                return candidate
        return magnitude * 10.0

    def paintEvent(self, event) -> None:
        del event

        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
        )

        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)

        grid_pen = QPen(
            QColor(71, 85, 105, 88),
            1,
        )
        painter.setPen(grid_pen)

        for fraction in (1.0 / 3.0, 2.0 / 3.0, 1.0):
            y = rect.top() + rect.height() * fraction
            painter.drawLine(
                QPointF(rect.left(), y),
                QPointF(rect.right(), y),
            )

        values = self.rx_values + self.tx_values
        max_value = self.nice_scale_max(values)

        self.draw_series(
            painter=painter,
            rect=rect,
            values=self.rx_values,
            color=self.RX_COLOR,
            max_value=max_value,
            width=1.8,
            sample_times=self.sample_times,
        )

        self.draw_series(
            painter=painter,
            rect=rect,
            values=self.tx_values,
            color=self.TX_COLOR,
            max_value=max_value,
            width=1.8,
            sample_times=self.sample_times,
        )
        painter.end()

    @staticmethod
    def draw_series(
        painter: QPainter,
        rect,
        values: list[float],
        color,
        max_value: float,
        width: float,
        sample_times: Sequence[float] | None = None,
    ) -> None:
        if not values:
            return

        path = QPainterPath()
        points = TrafficChart.series_points(
            rect=rect,
            values=values,
            max_value=max_value,
            sample_times=sample_times,
        )

        for index, point in enumerate(points):
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(points[-1], 2.5, 2.5)

    @staticmethod
    def series_points(
        rect: QRectF,
        values: Sequence[float],
        max_value: float,
        sample_times: Sequence[float] | None = None,
    ) -> list[QPointF]:
        if not values:
            return []

        count = len(values)
        valid_times = (
            sample_times is not None
            and len(sample_times) == count
            and count > 1
            and sample_times[-1] > sample_times[0]
        )
        points: list[QPointF] = []

        for index, value in enumerate(values):
            if valid_times:
                fraction = (
                    (sample_times[index] - sample_times[0])
                    / (sample_times[-1] - sample_times[0])
                )
            elif count > 1:
                fraction = index / (count - 1)
            else:
                fraction = 1.0

            points.append(
                QPointF(
                    rect.left() + rect.width() * fraction,
                    rect.bottom()
                    - rect.height() * max(0.0, value) / max_value,
                )
            )

        return points

    @staticmethod
    def format_speed(
        bits_per_second: float,
    ) -> str:
        if not math.isfinite(bits_per_second) or bits_per_second < 0:
            bits_per_second = 0.0

        if bits_per_second >= 1_000_000_000:
            value = bits_per_second / 1_000_000_000
            unit = "Гбит/с"
        elif bits_per_second >= 1_000_000:
            value = bits_per_second / 1_000_000
            unit = "Мбит/с"
        elif bits_per_second >= 1_000:
            value = bits_per_second / 1_000
            unit = "Кбит/с"
        else:
            return f"{bits_per_second:.0f} бит/с"

        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"{text.replace('.', ',')} {unit}"


class ConnectionCard(QFrame):
    state_change_requested = Signal(str, str, bool)

    _expanded_states: dict[str, bool] = {}

    def __init__(
        self,
        connection: dict,
        rx_history: Sequence[float],
        tx_history: Sequence[float],
        sample_times: Sequence[float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.connection: dict = {}
        self.interface_id = ""
        self.interface_name = ""
        self.display_name = ""
        self.state_key = ""
        self.expanded = False
        self.target_enabled = False
        self._toggle_in_progress = False
        self._detail_captions: tuple[str, ...] = ()
        self._detail_value_labels: dict[str, QLabel] = {}

        self._set_connection_identity(connection)
        self.expanded = self._expanded_states.get(
            self.state_key,
            False,
        )

        self.setObjectName("detailedConnectionCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        self.build_ui()
        self.update_connection(
            connection=connection,
            rx_history=rx_history,
            tx_history=tx_history,
            sample_times=sample_times,
        )

    def build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            18,
            14,
            18,
            14,
        )
        main_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)

        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(self.expanded)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if self.expanded
            else Qt.ArrowType.RightArrow
        )
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.toggle_button.setFixedSize(38, 34)
        self.toggle_button.setToolTip(
            "Скрыть подробности"
            if self.expanded
            else "Показать подробности"
        )
        self.toggle_button.clicked.connect(
            self.toggle_details
        )

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.role_title_label = QLabel()
        self.role_title_label.setObjectName(
            "connectionRoleTitle"
        )

        self.compact_name_label = QLabel()
        self.compact_name_label.setObjectName(
            "connectionInterfaceTitle"
        )
        self.compact_name_label.setWordWrap(True)

        self.interface_line_label = QLabel()
        self.interface_line_label.setWordWrap(True)

        title_box.addWidget(self.role_title_label)
        title_box.addWidget(self.compact_name_label)
        title_box.addWidget(self.interface_line_label)

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )

        self.action_button = QPushButton()
        self.action_button.setMinimumWidth(115)
        self.action_button.setMaximumWidth(115)
        self.action_button.clicked.connect(
            self.emit_state_change_request
        )

        header.addLayout(title_box, 1)
        header.addWidget(self.status_badge)
        header.addWidget(self.action_button)
        header.addWidget(self.toggle_button)

        main_layout.addLayout(header)

        self.details_widget = QWidget()
        details_layout = QVBoxLayout(
            self.details_widget
        )
        details_layout.setContentsMargins(
            0,
            8,
            0,
            0,
        )
        details_layout.setSpacing(12)

        speed_row = QHBoxLayout()
        speed_row.setSpacing(20)
        self.rx_label = QLabel()
        self.rx_label.setStyleSheet(
            "color: #60a5fa; font-weight: 600;"
        )
        self.tx_label = QLabel()
        self.tx_label.setStyleSheet(
            "color: #34d399; font-weight: 600;"
        )
        speed_row.addWidget(self.rx_label)
        speed_row.addWidget(self.tx_label)
        speed_row.addStretch()
        details_layout.addLayout(speed_row)

        self.chart = TrafficChart()
        details_layout.addWidget(self.chart)

        self.details_grid = QGridLayout()
        self.details_grid.setHorizontalSpacing(24)
        self.details_grid.setVerticalSpacing(7)
        details_layout.addLayout(self.details_grid)

        main_layout.addWidget(self.details_widget)
        self.details_widget.setVisible(
            self.expanded
        )

    def update_connection(
        self,
        connection: dict,
        rx_history: Sequence[float],
        tx_history: Sequence[float],
        sample_times: Sequence[float] | None = None,
    ) -> None:
        """Обновляет существующую карточку без пересоздания Qt-виджета."""
        previous_state_key = self.state_key
        self._set_connection_identity(connection)

        if (
            previous_state_key
            and previous_state_key != self.state_key
        ):
            self.expanded = self._expanded_states.get(
                self.state_key,
                False,
            )
            self.toggle_button.blockSignals(True)
            self.toggle_button.setChecked(
                self.expanded
            )
            self.toggle_button.blockSignals(False)
            self.details_widget.setVisible(
                self.expanded
            )

        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if self.expanded
            else Qt.ArrowType.RightArrow
        )
        self.toggle_button.setToolTip(
            "Скрыть подробности"
            if self.expanded
            else "Показать подробности"
        )

        role = str(
            self.connection.get("role")
            or "backup"
        )
        role_title = {
            "primary": "Основной Интернет",
            "backup": "Резервный Интернет",
            "vpn": "VPN-туннель",
        }.get(role, "Подключение")

        status = str(
            self.connection.get("status")
            or "Offline"
        )
        interface_enabled = bool(
            self.connection.get(
                "enabled",
                status == "Online",
            )
        )
        self.target_enabled = not interface_enabled

        self._set_label_text(
            self.role_title_label,
            role_title,
        )
        self._set_label_text(
            self.compact_name_label,
            self.display_name,
        )
        self._set_label_text(
            self.interface_line_label,
            f"Интерфейс: {self.interface_name}",
        )

        self.status_badge.set_status(
            text=(
                "Подключено"
                if status == "Online"
                else "Отключено"
            ),
            state=(
                "connected"
                if status == "Online"
                else "disconnected"
            ),
        )

        self.action_button.setText(
            "Включить"
            if self.target_enabled
            else "Выключить"
        )
        self.action_button.setEnabled(
            bool(self.interface_id)
        )

        self.chart.set_values(
            rx_values=rx_history,
            tx_values=tx_history,
            sample_times=sample_times,
        )

        rx_speed = self.format_speed(
            self.number(
                self.connection.get("rxspeed")
            )
        )
        tx_speed = self.format_speed(
            self.number(
                self.connection.get("txspeed")
            )
        )
        self._set_label_text(
            self.rx_label,
            f"↓ Приём  {rx_speed}",
        )
        self._set_label_text(
            self.tx_label,
            f"↑ Передача  {tx_speed}",
        )

        self._update_detail_fields(
            self._build_detail_fields(status)
        )

    def _set_connection_identity(
        self,
        connection: dict,
    ) -> None:
        self.connection = dict(connection)
        self.interface_id = str(
            self.connection.get("interface_id")
            or self.connection.get("name")
            or ""
        )
        self.interface_name = str(
            self.connection.get("interface_name")
            or self.connection.get("name")
            or self.interface_id
        )
        self.display_name = str(
            self.connection.get("display_name")
            or self.connection.get("port")
            or self.connection.get("name")
            or self.interface_id
        )
        self.state_key = self._make_state_key()

    def _build_detail_fields(
        self,
        status: str,
    ) -> list[tuple[str, object]]:
        return [
            ("Статус", status),
            ("Тип", self.connection.get("type")),
            (
                "Физический порт",
                self.connection.get("port"),
            ),
            (
                "Физический интерфейс",
                self.connection.get(
                    "physical_interface"
                ),
            ),
            (
                "Системное имя",
                self.connection.get(
                    "interface_name"
                ),
            ),
            (
                "Описание",
                self.connection.get("description"),
            ),
            (
                "IPv4-адрес",
                self.connection.get("address"),
            ),
            (
                "Маска подсети",
                self.connection.get("mask"),
            ),
            (
                "Шлюз IPv4",
                self.connection.get("gateway"),
            ),
            (
                "IPv6-адрес",
                self.connection.get("ipv6_address"),
            ),
            (
                "IPv6-префикс",
                self.connection.get("ipv6_prefix"),
            ),
            (
                "Шлюз IPv6",
                self.connection.get("ipv6_gateway"),
            ),
            (
                "MAC-адрес",
                self.connection.get("mac"),
            ),
            (
                "Время подключения",
                self.connection.get("uptime"),
            ),
            (
                "Принято",
                self.format_bytes(
                    self.number(
                        self.connection.get("rxbytes")
                    )
                ),
            ),
            (
                "Отправлено",
                self.format_bytes(
                    self.number(
                        self.connection.get("txbytes")
                    )
                ),
            ),
            (
                "DNS-серверы",
                self.format_dns(
                    self.connection.get("dns")
                ),
            ),
            ("SSID", self.connection.get("ssid")),
        ]

    def _update_detail_fields(
        self,
        fields: list[tuple[str, object]],
    ) -> None:
        visible = [
            (name, str(value))
            for name, value in fields
            if value not in (
                None,
                "",
                [],
                "—",
            )
        ]
        captions = tuple(
            name
            for name, _value in visible
        )

        if captions != self._detail_captions:
            self._detail_captions = captions
            self._clear_detail_grid()

            for index, (name, value) in enumerate(
                visible
            ):
                column = (index % 3) * 2
                row = index // 3

                name_label = QLabel(name)
                name_label.setObjectName(
                    "connectionFieldName"
                )

                value_label = QLabel(value)
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )

                self.details_grid.addWidget(
                    name_label,
                    row,
                    column,
                )
                self.details_grid.addWidget(
                    value_label,
                    row,
                    column + 1,
                )
                self._detail_value_labels[
                    name
                ] = value_label
            return

        for name, value in visible:
            label = self._detail_value_labels.get(name)
            if label is not None:
                self._set_label_text(
                    label,
                    value,
                )

    def _clear_detail_grid(self) -> None:
        self._detail_value_labels = {}

        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _set_label_text(
        label: QLabel,
        text: str,
    ) -> None:
        text = str(text)
        if label.text() != text:
            label.setText(text)

    def toggle_details(
        self,
        checked: bool,
    ) -> None:
        if self._toggle_in_progress:
            return

        self._toggle_in_progress = True
        self.expanded = checked
        self._expanded_states[self.state_key] = checked

        def update_toggle() -> None:
            self.toggle_button.setArrowType(
                Qt.ArrowType.DownArrow
                if checked
                else Qt.ArrowType.RightArrow
            )
            self.toggle_button.setToolTip(
                "Скрыть подробности"
                if checked
                else "Показать подробности"
            )

        set_expanded_without_flicker(
            owner=self,
            content=self.details_widget,
            expanded=checked,
            after_change=update_toggle,
        )

        from PySide6.QtCore import QTimer
        QTimer.singleShot(
            0,
            self._finish_stable_toggle,
        )

    def _finish_stable_toggle(self) -> None:
        self._toggle_in_progress = False

    def emit_state_change_request(self) -> None:
        self.state_change_requested.emit(
            self.interface_id,
            self.interface_name,
            self.target_enabled,
        )

    def _make_state_key(self) -> str:
        router_id = str(
            self.connection.get("_router_id")
            or "unknown-router"
        ).strip().lower()

        role = str(
            self.connection.get("role")
            or ""
        ).strip().lower()

        return (
            f"{router_id}|"
            f"{role}|"
            f"{self.interface_id.strip().lower()}"
        )

    @staticmethod
    def number(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def format_speed(
        bits_per_second: float,
    ) -> str:
        return TrafficChart.format_speed(
            bits_per_second
        )

    @staticmethod
    def format_bytes(
        value: float,
    ) -> str:
        units = (
            "Б",
            "КБ",
            "МБ",
            "ГБ",
            "ТБ",
        )
        size = max(value, 0.0)

        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.2f} {unit}"
            size /= 1024

        return f"{size:.2f} ТБ"

    @staticmethod
    def format_dns(value) -> str:
        if isinstance(value, list):
            return "\n".join(
                str(item)
                for item in value
                if item
            ) or "—"

        return str(value or "—")
