from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget


class WindowIconManager(QObject):
    """Назначает фирменную иконку всем обычным окнам приложения."""

    _SUPPORTED_WINDOW_TYPES = {
        Qt.WindowType.Window,
        Qt.WindowType.Dialog,
        Qt.WindowType.Sheet,
        Qt.WindowType.Tool,
        Qt.WindowType.Drawer,
    }

    def __init__(
        self,
        icon: QIcon,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = QIcon(icon)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if (
            event.type()
            in {
                QEvent.Type.Polish,
                QEvent.Type.Show,
            }
            and isinstance(watched, QWidget)
            and watched.isWindow()
            and watched.windowType()
            in self._SUPPORTED_WINDOW_TYPES
        ):
            watched.setWindowIcon(self._icon)

        return super().eventFilter(watched, event)


def install_window_icon_manager(
    application: QApplication,
    icon: QIcon,
) -> WindowIconManager:
    manager = WindowIconManager(icon, application)
    application.installEventFilter(manager)
    return manager
