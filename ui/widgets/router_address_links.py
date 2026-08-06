from __future__ import annotations

import html
import ipaddress
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QPalette,
)
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


UrlOpener = Callable[[QUrl], bool]


class RouterAddressLink(QLabel):
    """Выделяемая ссылка на веб-интерфейс роутера."""

    open_requested = Signal(str)
    address_copied = Signal(str)

    def __init__(
        self,
        caption: str,
        default_scheme: str,
        parent: QWidget | None = None,
        *,
        url_opener: UrlOpener | None = None,
    ) -> None:
        super().__init__(parent)

        normalized_scheme = str(
            default_scheme or ""
        ).strip().lower()

        if normalized_scheme not in {
            "http",
            "https",
        }:
            raise ValueError(
                "default_scheme должен быть http или https"
            )

        self.caption = str(caption).strip()
        self.default_scheme = normalized_scheme
        self._address = ""
        self._url = QUrl()
        self._url_opener: UrlOpener = (
            url_opener
            or QDesktopServices.openUrl
        )

        self.setTextFormat(
            Qt.TextFormat.RichText
        )
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.setOpenExternalLinks(False)
        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setMinimumWidth(0)
        self.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.linkActivated.connect(
            self.open_in_browser
        )
        self.customContextMenuRequested.connect(
            self.show_context_menu
        )

        self.set_address("")

    @property
    def address(self) -> str:
        return self._address

    @property
    def web_url(self) -> str:
        if not self._url.isValid():
            return ""

        return self._url.toString()

    @property
    def can_open(self) -> bool:
        return bool(
            self._address
            and self._url.isValid()
            and self._url.scheme().lower()
            in {"http", "https"}
            and self._url.host()
        )

    def set_url_opener(
        self,
        opener: UrlOpener,
    ) -> None:
        self._url_opener = opener

    def set_address(
        self,
        address: object,
    ) -> None:
        self._address = str(
            address or ""
        ).strip()
        self._url = self.build_web_url(
            self._address,
            self.default_scheme,
        )
        self.refresh_text()

    def clear_address(self) -> None:
        self.set_address("")

    def refresh_text(self) -> None:
        caption = html.escape(
            self.caption,
            quote=False,
        )

        if not self._address:
            self.setText(
                f"<span>{caption}: —</span>"
            )
            self.setToolTip(
                f"{self.caption} не указан."
            )
            return

        escaped_address = html.escape(
            self._address,
            quote=False,
        )

        if self.can_open:
            escaped_url = html.escape(
                self.web_url,
                quote=True,
            )
            self.setText(
                f"<span>{caption}: "
                f'<a href="{escaped_url}">'
                f"{escaped_address}</a></span>"
            )
            self.setToolTip(
                "Нажмите, чтобы открыть веб-интерфейс.\n"
                "Текст можно выделить и скопировать.\n"
                "Правый клик — дополнительные действия."
            )
        else:
            self.setText(
                f"<span>{caption}: "
                f"{escaped_address}</span>"
            )
            self.setToolTip(
                "Адрес можно выделить и скопировать, "
                "но для открытия поддерживаются только "
                "HTTP и HTTPS."
            )

    def update_link_palette(self) -> None:
        palette = self.palette()
        palette.setColor(
            QPalette.ColorRole.Link,
            palette.color(
                QPalette.ColorRole.Highlight
            ),
        )
        palette.setColor(
            QPalette.ColorRole.LinkVisited,
            palette.color(
                QPalette.ColorRole.Highlight
            ),
        )
        self.setPalette(palette)

    def showEvent(self, event) -> None:
        self.update_link_palette()
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)

        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            self.update_link_palette()

    def open_in_browser(
        self,
        _activated_href: str = "",
    ) -> bool:
        del _activated_href

        if not self.can_open:
            return False

        self.open_requested.emit(
            self.web_url
        )
        return bool(
            self._url_opener(
                QUrl(self._url)
            )
        )

    def copy_address(self) -> bool:
        if not self._address:
            return False

        QGuiApplication.clipboard().setText(
            self._address
        )
        self.address_copied.emit(
            self._address
        )
        return True

    def show_context_menu(
        self,
        position: QPoint,
    ) -> None:
        if not self._address:
            return

        menu = QMenu(self)
        open_action = menu.addAction(
            "Открыть в браузере"
        )
        open_action.setEnabled(
            self.can_open
        )
        copy_action = menu.addAction(
            "Копировать адрес"
        )

        selected = menu.exec(
            self.mapToGlobal(position)
        )

        if selected is open_action:
            self.open_in_browser()
        elif selected is copy_action:
            self.copy_address()

    @staticmethod
    def build_web_url(
        address: object,
        default_scheme: str,
    ) -> QUrl:
        raw = str(address or "").strip()
        scheme = str(
            default_scheme or ""
        ).strip().lower()

        if (
            not raw
            or scheme not in {
                "http",
                "https",
            }
        ):
            return QUrl()

        if "://" in raw:
            candidate = raw
        else:
            candidate_host = raw

            # QUrl требует квадратные скобки вокруг IPv6-хоста.
            try:
                ip_value = ipaddress.ip_address(
                    raw.strip("[]")
                )
            except ValueError:
                ip_value = None

            if (
                ip_value is not None
                and ip_value.version == 6
            ):
                candidate_host = (
                    f"[{ip_value.compressed}]"
                )

            candidate = (
                f"{scheme}://{candidate_host}"
            )

        url = QUrl(candidate)

        if (
            not url.isValid()
            or url.scheme().lower()
            not in {"http", "https"}
            or not url.host()
        ):
            return QUrl()

        return url


class RouterAddressLinks(QWidget):
    """Две постоянные строки: локальный IP и KeenDNS."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setObjectName(
            "routerAddressLinks"
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        layout.setSpacing(0)

        self.local_ip_link = RouterAddressLink(
            "Локальный IP",
            "http",
            self,
        )
        self.local_ip_link.setObjectName(
            "routerLocalIpLink"
        )

        self.keendns_link = RouterAddressLink(
            "KeenDNS",
            "https",
            self,
        )
        self.keendns_link.setObjectName(
            "routerKeenDnsLink"
        )

        layout.addWidget(
            self.local_ip_link
        )
        layout.addWidget(
            self.keendns_link
        )

    def set_addresses(
        self,
        *,
        local_ip: object = "",
        keendns: object = "",
    ) -> None:
        self.local_ip_link.set_address(
            local_ip
        )
        self.keendns_link.set_address(
            keendns
        )

    def clear_addresses(self) -> None:
        self.set_addresses()
