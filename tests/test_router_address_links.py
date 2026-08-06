from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


try:
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtWidgets import QApplication

    from core.database import Database
    from ui.widgets.router_address_links import (
        RouterAddressLink,
        RouterAddressLinks,
    )

    PYSIDE_AVAILABLE = True
except ModuleNotFoundError:
    PYSIDE_AVAILABLE = False


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(
        self,
        service: str,
        account: str,
    ) -> str | None:
        return self.values.get(
            (service, account)
        )

    def set_password(
        self,
        service: str,
        account: str,
        password: str,
    ) -> None:
        self.values[
            (service, account)
        ] = password

    def delete_password(
        self,
        service: str,
        account: str,
    ) -> None:
        self.values.pop(
            (service, account),
            None,
        )


def build_store():
    from core.credential_store import (
        RouterCredentialStore,
    )

    return RouterCredentialStore(
        backend=MemoryBackend()
    )


@unittest.skipUnless(
    PYSIDE_AVAILABLE,
    "PySide6 отсутствует в тестовой среде",
)
class RouterAddressLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QApplication.instance()
            or QApplication([])
        )

    def test_local_ip_gets_http_scheme(self) -> None:
        link = RouterAddressLink(
            "Локальный IP",
            "http",
        )
        link.set_address("192.168.1.1")

        self.assertEqual(
            link.address,
            "192.168.1.1",
        )
        self.assertEqual(
            link.web_url,
            "http://192.168.1.1",
        )
        self.assertTrue(link.can_open)

    def test_keendns_gets_https_scheme(self) -> None:
        link = RouterAddressLink(
            "KeenDNS",
            "https",
        )
        link.set_address(
            "home.keenetic.pro"
        )

        self.assertEqual(
            link.web_url,
            "https://home.keenetic.pro",
        )

    def test_explicit_http_or_https_is_preserved(self) -> None:
        link = RouterAddressLink(
            "KeenDNS",
            "https",
        )
        link.set_address(
            "http://router.example:8080/path"
        )

        self.assertEqual(
            link.web_url,
            "http://router.example:8080/path",
        )

    def test_ipv6_is_wrapped_in_brackets(self) -> None:
        link = RouterAddressLink(
            "Локальный IP",
            "http",
        )
        link.set_address("2001:db8::1")

        self.assertEqual(
            link.web_url,
            "http://[2001:db8::1]",
        )

    def test_only_http_and_https_can_be_opened(self) -> None:
        link = RouterAddressLink(
            "KeenDNS",
            "https",
        )
        link.set_address(
            "ftp://router.example"
        )

        self.assertFalse(link.can_open)
        self.assertEqual(
            link.web_url,
            "",
        )
        self.assertIn(
            "router.example",
            link.text(),
        )

    def test_link_is_selectable_and_keyboard_accessible(self) -> None:
        link = RouterAddressLink(
            "KeenDNS",
            "https",
        )
        flags = link.textInteractionFlags()

        self.assertTrue(
            bool(
                flags
                & Qt.TextInteractionFlag.TextSelectableByMouse
            )
        )
        self.assertTrue(
            bool(
                flags
                & Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
        )
        self.assertTrue(
            bool(
                flags
                & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
        )

    def test_activation_uses_injected_browser_opener(self) -> None:
        opened: list[str] = []

        def opener(url: QUrl) -> bool:
            opened.append(url.toString())
            return True

        link = RouterAddressLink(
            "KeenDNS",
            "https",
            url_opener=opener,
        )
        link.set_address(
            "home.keenetic.pro"
        )
        link.linkActivated.emit(
            link.web_url
        )

        self.assertEqual(
            opened,
            ["https://home.keenetic.pro"],
        )

    def test_copy_address_uses_raw_address(self) -> None:
        link = RouterAddressLink(
            "Локальный IP",
            "http",
        )
        link.set_address(
            "192.168.1.1:8080"
        )

        self.assertTrue(
            link.copy_address()
        )
        self.assertEqual(
            QApplication.clipboard().text(),
            "192.168.1.1:8080",
        )

    def test_empty_address_is_not_a_link(self) -> None:
        link = RouterAddressLink(
            "Локальный IP",
            "http",
        )
        link.set_address("")

        self.assertFalse(link.can_open)
        self.assertEqual(link.address, "")
        self.assertIn("—", link.text())

    def test_composite_widget_updates_both_addresses(self) -> None:
        widget = RouterAddressLinks()
        widget.set_addresses(
            local_ip="192.168.10.1",
            keendns="office.keenetic.pro",
        )

        self.assertEqual(
            widget.local_ip_link.web_url,
            "http://192.168.10.1",
        )
        self.assertEqual(
            widget.keendns_link.web_url,
            "https://office.keenetic.pro",
        )

        widget.clear_addresses()
        self.assertEqual(
            widget.local_ip_link.address,
            "",
        )
        self.assertEqual(
            widget.keendns_link.address,
            "",
        )


@unittest.skipUnless(
    PYSIDE_AVAILABLE,
    "PySide6 отсутствует в тестовой среде",
)
class MainWindowAddressIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QApplication.instance()
            or QApplication([])
        )

    def test_selected_router_populates_offline_links(self) -> None:
        import ui.main_window as main_window_module
        from core.router_status_worker import (
            RouterStatusWorker,
        )

        with tempfile.TemporaryDirectory() as directory:
            database = Database(
                database_path=(
                    Path(directory)
                    / "routers.db"
                ),
                credential_store=build_store(),
            )
            router_id = database.add_router(
                name="Test router",
                local_ip="192.168.50.1",
                keendns="test.keenetic.pro",
                connection_mode="auto",
                username="admin",
                password=None,
                group_name="",
            )

            original_database = (
                main_window_module.Database
            )
            original_start = (
                RouterStatusWorker.start
            )

            main_window_module.Database = (
                lambda: database
            )
            RouterStatusWorker.start = (
                lambda self: None
            )

            window = None

            try:
                window = (
                    main_window_module.MainWindow()
                )
                window.select_router_by_id(
                    router_id
                )
                self.app.processEvents()

                self.assertEqual(
                    window.router_address_links
                    .local_ip_link.address,
                    "192.168.50.1",
                )
                self.assertEqual(
                    window.router_address_links
                    .local_ip_link.web_url,
                    "http://192.168.50.1",
                )
                self.assertEqual(
                    window.router_address_links
                    .keendns_link.address,
                    "test.keenetic.pro",
                )
                self.assertEqual(
                    window.router_address_links
                    .keendns_link.web_url,
                    "https://test.keenetic.pro",
                )
                self.assertEqual(window.refresh_button.text(), "")
                self.assertEqual(
                    window.refresh_button.glyph_size,
                    20,
                )
            finally:
                if window is not None:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()

                main_window_module.Database = (
                    original_database
                )
                RouterStatusWorker.start = (
                    original_start
                )


if __name__ == "__main__":
    unittest.main()
