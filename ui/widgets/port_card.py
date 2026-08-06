from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.status_badge import StatusBadge


class PortCard(QFrame):
    """Раскрывающаяся карточка физического порта."""

    usb_open_requested = Signal(dict)

    def __init__(
        self,
        port: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # Каждая новая карточка порта всегда создаётся
        # свернутой. При обновлении текущего роутера объект
        # карточки сохраняется, поэтому его локальное состояние
        # не сбрасывается. При выборе другого роутера карточки
        # создаются заново и снова остаются свернутыми.
        self.port_key = self._make_port_key(port)
        self.port_data = dict(port)
        self.is_usb = (
            str(
                port.get("kind")
                or ""
            ).strip().lower()
            == "usb"
        )
        self.expanded = False
        self._animation_running = False
        self._details_signature: tuple[
            tuple[str, str],
            ...
        ] = ()

        self.setObjectName("routerPortCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.setStyleSheet(
            """
            QFrame#routerPortCard {
                background: #151e2d;
                border: 1px solid #283548;
                border-radius: 9px;
            }

            QLabel#portWhiteDot {
                background: #60a5fa;
                border: none;
                border-radius: 3px;
            }

            QLabel#routerPortTitle {
                border: none;
                background: transparent;
                font-size: 13px;
                font-weight: 700;
            }

            QLabel#routerPortMeta {
                border: none;
                background: transparent;
                font-size: 11px;
            }

            QLabel#portDetailCaption {
                border: none;
                background: transparent;
                color: #94a3b8;
                font-size: 11px;
            }

            QLabel#portDetailValue {
                border: none;
                background: transparent;
                color: #e2e8f0;
                font-size: 11px;
            }

            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 9)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.white_dot = QLabel()
        self.white_dot.setObjectName("portWhiteDot")
        self.white_dot.setFixedSize(6, 6)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.title_label = QLabel()
        self.title_label.setObjectName("routerPortTitle")

        self.meta_label = QLabel()
        self.meta_label.setObjectName("routerPortMeta")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextFormat(
            Qt.TextFormat.PlainText
        )

        title_box.addWidget(self.title_label)
        title_box.addWidget(self.meta_label)

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )

        self.smb_button = QPushButton("SMB")
        self.smb_button.setFixedWidth(115)
        self.smb_button.setToolTip(
            "Открыть USB-накопитель по SMB"
        )
        self.smb_button.clicked.connect(
            self._open_usb_device
        )

        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(
            self.expanded
        )
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if self.expanded
            else Qt.ArrowType.RightArrow
        )
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.toggle_button.setFixedSize(
            38,
            34,
        )
        self.toggle_button.setToolTip(
            "Скрыть подробности"
            if self.expanded
            else "Показать подробности"
        )
        self.toggle_button.clicked.connect(
            self._toggle_details
        )

        header.addWidget(
            self.white_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        header.addLayout(title_box, 1)
        header.addWidget(self.status_badge)
        header.addWidget(self.smb_button)
        header.addWidget(self.toggle_button)

        root.addLayout(header)

        self.details_widget = QWidget(self)
        self.details_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.details_layout = QGridLayout(
            self.details_widget
        )
        self.details_layout.setContentsMargins(
            16,
            5,
            4,
            3,
        )
        self.details_layout.setHorizontalSpacing(24)
        self.details_layout.setVerticalSpacing(2)

        # Две одинаковые группы:
        # подпись + значение | подпись + значение.
        self.details_layout.setColumnMinimumWidth(
            0,
            128,
        )
        self.details_layout.setColumnMinimumWidth(
            2,
            128,
        )
        self.details_layout.setColumnStretch(0, 0)
        self.details_layout.setColumnStretch(1, 1)
        self.details_layout.setColumnStretch(2, 0)
        self.details_layout.setColumnStretch(3, 1)

        root.addWidget(self.details_widget)

        self.height_animation = QPropertyAnimation(
            self.details_widget,
            b"maximumHeight",
            self,
        )
        self.height_animation.setDuration(170)
        self.height_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self.height_animation.finished.connect(
            self._finish_animation
        )

        self.update_port(port)

        if self.expanded:
            self.details_widget.setMaximumHeight(
                self._details_height()
            )
            self.details_widget.show()
        else:
            self.details_widget.setMaximumHeight(0)
            self.details_widget.hide()

    def update_port(
        self,
        port: dict[str, Any],
    ) -> None:
        self.port_data = dict(port)
        self.is_usb = (
            str(
                port.get("kind")
                or ""
            ).strip().lower()
            == "usb"
        )

        physical_name = str(
            port.get("physical_name")
            or port.get("name")
            or port.get("id")
            or "Порт"
        ).strip()
        assignment = str(
            port.get("role")
            or ""
        ).strip()
        kind = str(
            port.get("kind")
            or "ethernet"
        ).strip().lower()

        title = physical_name

        if (
            kind != "usb"
            and assignment
            and assignment != physical_name
        ):
            title = (
                f"{physical_name} · "
                f"назначение {assignment}"
            )

        self._set_text(
            self.title_label,
            title,
        )

        summary: list[str] = []

        speed_text = self._format_speed(
            port.get("speed_mbps")
        )
        if speed_text:
            summary.append(speed_text)

        duplex = str(
            port.get("duplex")
            or ""
        ).strip()
        if duplex:
            summary.append(
                "Full"
                if duplex.lower() == "full"
                else (
                    "Half"
                    if duplex.lower() == "half"
                    else duplex
                )
            )

        if kind == "usb":
            product = str(
                port.get("name")
                or ""
            ).strip()

            if product and product != physical_name:
                summary.append(product)

        summary_text = "  •  ".join(summary)

        self._set_text(
            self.meta_label,
            summary_text,
        )
        self.meta_label.setCursor(
            Qt.CursorShape.ArrowCursor
        )
        self.meta_label.setToolTip("")
        self.meta_label.setVisible(
            bool(summary_text)
        )

        enabled = bool(
            port.get("enabled", True)
        )
        connected = bool(
            port.get("connected")
        )

        self.smb_button.setVisible(
            self.is_usb
        )
        self.smb_button.setEnabled(
            self.is_usb
            and enabled
            and connected
        )

        if not enabled:
            self.status_badge.set_status(
                text="Выключен",
                state="disconnected",
            )
        elif connected:
            self.status_badge.set_status(
                text="Подключено",
                state="connected",
            )
        else:
            self.status_badge.set_status(
                text="Отключено",
                state="disconnected",
            )

        details = self._build_details(
            port=port,
            physical_name=physical_name,
            assignment=assignment,
            kind=kind,
        )
        self._set_details(details)

    def _open_usb_device(
        self,
        _checked: bool = False,
    ) -> None:
        if self.is_usb:
            self.usb_open_requested.emit(
                dict(self.port_data)
            )

    def _build_details(
        self,
        *,
        port: dict[str, Any],
        physical_name: str,
        assignment: str,
        kind: str,
    ) -> list[tuple[str, Any]]:
        if kind == "usb":
            return [
                ("Физический порт", physical_name),
                ("Тип", assignment),
                ("Устройство", port.get("name")),
                ("Производитель", port.get("manufacturer")),
                ("Серийный номер", port.get("serial")),
                ("Файловая система", port.get("filesystem")),
                ("Скорость", self._format_speed(
                    port.get("speed_mbps")
                )),
            ]

        return [
            ("Физический порт", physical_name),
            ("Интерфейс", port.get("interface_id")),
            ("Назначение", assignment),
            ("Состояние линии", self._format_link(
                port.get("link_state"),
                port.get("connected"),
            )),
            ("Скорость", self._format_speed(
                port.get("speed_mbps")
            )),
            ("Дуплекс", self._format_duplex(
                port.get("duplex")
            )),
            ("Автосогласование", self._format_on_off(
                port.get("auto_negotiation")
            )),
            ("Flow Control", self._format_on_off(
                port.get("flow_control")
            )),
            ("EEE", self._format_on_off(
                port.get("eee")
            )),
            ("Последнее изменение", self._format_elapsed(
                port.get("last_change")
            )),
            ("Диагностика кабеля", self._format_available(
                port.get("cable_diagnostics")
            )),
            ("Группа портов", self._format_link_group(
                port.get("link_group")
            )),
        ]

    def _set_details(
        self,
        details: list[tuple[str, Any]],
    ) -> None:
        visible = [
            (
                str(caption),
                str(value),
            )
            for caption, value in details
            if not self._is_empty(value)
        ]

        signature = tuple(visible)

        if signature == self._details_signature:
            return

        self._details_signature = signature
        self._clear_details()

        # Разделяем список пополам, как в PropertyGrid
        # карточек Wi-Fi-клиентов.
        midpoint = (
            len(visible) + 1
        ) // 2

        left_items = visible[:midpoint]
        right_items = visible[midpoint:]

        row_count = max(
            len(left_items),
            len(right_items),
        )

        for row in range(row_count):
            if row < len(left_items):
                self._add_detail_pair(
                    row=row,
                    column=0,
                    caption_text=left_items[row][0],
                    value_text=left_items[row][1],
                )

            if row < len(right_items):
                self._add_detail_pair(
                    row=row,
                    column=2,
                    caption_text=right_items[row][0],
                    value_text=right_items[row][1],
                )

        if (
            self.expanded
            and not self._animation_running
        ):
            self.details_widget.setMaximumHeight(
                self._details_height()
            )

    def _add_detail_pair(
        self,
        *,
        row: int,
        column: int,
        caption_text: str,
        value_text: str,
    ) -> None:
        caption = QLabel(caption_text)
        caption.setObjectName(
            "portDetailCaption"
        )
        caption.setFixedWidth(128)
        caption.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )

        value = QLabel(value_text)
        value.setObjectName(
            "portDetailValue"
        )
        value.setWordWrap(True)
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )
        value.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        self.details_layout.addWidget(
            caption,
            row,
            column,
        )
        self.details_layout.addWidget(
            value,
            row,
            column + 1,
        )

    def _toggle_details(
        self,
        checked: bool,
    ) -> None:
        self.expanded = checked

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

        self.height_animation.stop()
        self._animation_running = True

        if checked:
            self.details_widget.show()
            start_height = self.details_widget.maximumHeight()
            end_height = self._details_height()
        else:
            start_height = self.details_widget.height()
            end_height = 0

        self.height_animation.setStartValue(
            max(0, start_height)
        )
        self.height_animation.setEndValue(
            max(0, end_height)
        )
        self.height_animation.start()

    def _finish_animation(self) -> None:
        self._animation_running = False

        if self.expanded:
            self.details_widget.setMaximumHeight(
                self._details_height()
            )
        else:
            self.details_widget.setMaximumHeight(0)
            self.details_widget.hide()

        self.updateGeometry()

    def _details_height(self) -> int:
        self.details_layout.activate()
        return max(
            1,
            self.details_layout.sizeHint().height(),
        )

    def _clear_details(self) -> None:
        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _make_port_key(
        port: dict[str, Any],
    ) -> str:
        return (
            str(
                port.get("kind")
                or "ethernet"
            ).lower()
            + ":"
            + str(
                port.get("interface_id")
                or port.get("id")
                or port.get("physical_name")
                or "port"
            ).lower()
        )

    @staticmethod
    def _format_speed(
        value: Any,
    ) -> str | None:
        try:
            speed = float(value)
        except (TypeError, ValueError):
            return None

        if speed <= 0:
            return None

        if speed >= 1000:
            return f"{speed / 1000:g} Гбит/с"

        return f"{speed:g} Мбит/с"

    @staticmethod
    def _format_duplex(
        value: Any,
    ) -> str | None:
        text = str(value or "").strip().lower()

        if not text:
            return None

        if text == "full":
            return "Полный"

        if text == "half":
            return "Половинный"

        return text

    @staticmethod
    def _format_on_off(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return "Включено" if value else "Выключено"

        text = str(value).strip().lower()

        if text in {"on", "yes", "true", "1", "enabled"}:
            return "Включено"

        if text in {"off", "no", "false", "0", "disabled"}:
            return "Выключено"

        return str(value)

    @staticmethod
    def _format_link(
        link_state: Any,
        connected: Any,
    ) -> str:
        text = str(link_state or "").strip().lower()

        if text == "up" or bool(connected):
            return "Подключено"

        return "Отключено"

    @staticmethod
    def _format_elapsed(
        value: Any,
    ) -> str | None:
        try:
            seconds = max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

        days, remainder = divmod(
            seconds,
            86400,
        )
        hours, remainder = divmod(
            remainder,
            3600,
        )
        minutes, _ = divmod(
            remainder,
            60,
        )

        parts: list[str] = []

        if days:
            parts.append(f"{days} д.")

        if hours:
            parts.append(f"{hours} ч.")

        if minutes or not parts:
            parts.append(f"{minutes} мин.")

        return " ".join(parts[:2]) + " назад"

    @staticmethod
    def _format_available(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        return "Доступна" if bool(value) else "Недоступна"

    @staticmethod
    def _format_link_group(
        value: Any,
    ) -> str | None:
        if not isinstance(value, dict):
            return None

        if not value.get("supported"):
            return None

        group_id = value.get("group-id")
        enabled = bool(value.get("enabled"))
        master = bool(value.get("master"))

        parts = [
            (
                f"Группа {group_id}"
                if group_id is not None
                else "Поддерживается"
            ),
            (
                "включена"
                if enabled
                else "выключена"
            ),
        ]

        if master:
            parts.append("главный порт")

        return " • ".join(parts)

    @staticmethod
    def _is_empty(
        value: Any,
    ) -> bool:
        if value is None:
            return True

        return str(value).strip() in {
            "",
            "-",
            "—",
            "None",
            "null",
        }

    @staticmethod
    def _set_text(
        label: QLabel,
        value: str,
    ) -> None:
        if label.text() != value:
            label.setText(value)
