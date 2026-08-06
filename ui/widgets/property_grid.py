from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)


class PropertyGrid(QWidget):
    """
    Компактная двухколоночная таблица свойств.

    - скрывает пустые значения;
    - делит поля поровну между двумя колонками;
    - обновляется только при реальном изменении данных;
    - не создаёт рамки и лишние фоны.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        caption_width: int = 128,
        column_count: int = 2,
    ) -> None:
        super().__init__(parent)

        self.caption_width = caption_width
        self.column_count = 1 if int(column_count) == 1 else 2
        self._signature: tuple[
            tuple[str, str],
            ...
        ] = ()

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(
            4,
            4,
            4,
            4,
        )
        self.grid.setHorizontalSpacing(22)
        self.grid.setVerticalSpacing(2)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)

    def set_items(
        self,
        items: Iterable[
            tuple[str, Any]
        ],
    ) -> None:
        visible: list[
            tuple[str, str]
        ] = []

        for caption, value in items:
            if self.is_empty(value):
                continue

            visible.append(
                (
                    str(caption),
                    self.format_value(value),
                )
            )

        signature = tuple(visible)

        if signature == self._signature:
            return

        self._signature = signature
        self._clear()

        if not visible:
            self.hide()
            return

        self.show()

        if self.column_count == 1:
            left = visible
            right: list[tuple[str, str]] = []
        else:
            midpoint = (
                len(visible) + 1
            ) // 2
            left = visible[:midpoint]
            right = visible[midpoint:]

        row_count = max(
            len(left),
            len(right),
        )

        for row in range(row_count):
            if row < len(left):
                self._add_pair(
                    row=row,
                    column=0,
                    caption=left[row][0],
                    value=left[row][1],
                )

            if row < len(right):
                self._add_pair(
                    row=row,
                    column=2,
                    caption=right[row][0],
                    value=right[row][1],
                )

        self.updateGeometry()

    def has_items(self) -> bool:
        return bool(self._signature)

    def _add_pair(
        self,
        *,
        row: int,
        column: int,
        caption: str,
        value: str,
    ) -> None:
        caption_label = QLabel(caption)
        caption_label.setObjectName(
            "propertyGridCaption"
        )
        caption_label.setFixedWidth(
            self.caption_width
        )
        caption_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )
        caption_label.setStyleSheet(
            """
            QLabel#propertyGridCaption {
                color: #94a3b8;
                background: transparent;
                border: none;
                font-size: 11px;
            }
            """
        )

        value_label = QLabel(value)
        value_label.setObjectName(
            "propertyGridValue"
        )
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )
        value_label.setStyleSheet(
            """
            QLabel#propertyGridValue {
                color: #e2e8f0;
                background: transparent;
                border: none;
                font-size: 11px;
            }
            """
        )

        self.grid.addWidget(
            caption_label,
            row,
            column,
        )
        self.grid.addWidget(
            value_label,
            row,
            column + 1,
        )

    def _clear(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def is_empty(
        value: Any,
    ) -> bool:
        if value is None:
            return True

        if isinstance(value, bool):
            return False

        if isinstance(value, str):
            return value.strip() in {
                "",
                "-",
                "—",
                "none",
                "None",
                "null",
                "unknown",
            }

        if isinstance(
            value,
            (
                list,
                tuple,
                set,
                dict,
            ),
        ):
            return len(value) == 0

        return False

    @staticmethod
    def format_value(
        value: Any,
    ) -> str:
        if isinstance(value, bool):
            return (
                "Да"
                if value
                else "Нет"
            )

        if isinstance(value, dict):
            return "; ".join(
                f"{key}: "
                f"{PropertyGrid.format_value(item)}"
                for key, item in value.items()
                if not PropertyGrid.is_empty(item)
            )

        if isinstance(
            value,
            (
                list,
                tuple,
                set,
            ),
        ):
            return ", ".join(
                PropertyGrid.format_value(item)
                for item in value
                if not PropertyGrid.is_empty(item)
            )

        return str(value)
