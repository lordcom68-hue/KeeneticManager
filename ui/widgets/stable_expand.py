from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QWidget


def set_expanded_without_flicker(
    owner: QWidget,
    content: QWidget,
    expanded: bool,
    after_change: Callable[[], None] | None = None,
) -> None:
    """Стабильно показывает или скрывает вложенный блок."""
    if content.isVisible() == expanded:
        if after_change is not None:
            after_change()
        return

    scroll_area = _nearest_scroll_area(owner)
    viewport = scroll_area.viewport() if scroll_area else None

    vertical_value = (
        scroll_area.verticalScrollBar().value()
        if scroll_area else None
    )
    horizontal_value = (
        scroll_area.horizontalScrollBar().value()
        if scroll_area else None
    )

    if viewport is not None:
        viewport.setUpdatesEnabled(False)

    content.setVisible(expanded)

    current: QWidget | None = owner
    while current is not None:
        layout = current.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()

        current.updateGeometry()

        if current is scroll_area:
            break

        current = current.parentWidget()

    if after_change is not None:
        after_change()

    QTimer.singleShot(
        0,
        lambda: _finish_change(
            owner,
            viewport,
            scroll_area,
            vertical_value,
            horizontal_value,
        ),
    )


def _finish_change(
    owner: QWidget,
    viewport: QWidget | None,
    scroll_area: QAbstractScrollArea | None,
    vertical_value: int | None,
    horizontal_value: int | None,
) -> None:
    if scroll_area is not None:
        if vertical_value is not None:
            scroll_area.verticalScrollBar().setValue(
                vertical_value
            )
        if horizontal_value is not None:
            scroll_area.horizontalScrollBar().setValue(
                horizontal_value
            )

    if viewport is not None:
        viewport.setUpdatesEnabled(True)
        viewport.update()

    owner.update()


def _nearest_scroll_area(
    widget: QWidget,
) -> QAbstractScrollArea | None:
    current = widget.parentWidget()

    while current is not None:
        if isinstance(current, QAbstractScrollArea):
            return current

        current = current.parentWidget()

    return None
