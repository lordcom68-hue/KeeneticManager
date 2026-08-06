from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.port_overview import PortGlyph, PortOverview  # noqa: E402


class PortGlyphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _port(**overrides):
        port = {
            "kind": "ethernet",
            "physical_name": "LAN 1",
            "interface_id": "GigabitEthernet0/1",
            "role": "LAN",
            "connected": True,
            "enabled": True,
            "speed_mbps": 1000,
            "duplex": "full",
        }
        port.update(overrides)
        return port

    def test_localizes_speed_and_keeps_duplex_in_tooltip(self) -> None:
        glyph = PortGlyph(self._port())

        self.assertEqual(glyph._speed_text(), "1 Гбит/с")
        self.assertNotIn("FDX", glyph.toolTip())
        self.assertIn("Скорость: 1 Гбит/с", glyph.toolTip())
        self.assertIn("Дуплекс: Полный дуплекс", glyph.toolTip())
        self.assertIn("Интерфейс: GigabitEthernet0/1", glyph.toolTip())

    def test_formats_common_port_speeds(self) -> None:
        glyph = PortGlyph(self._port(speed_mbps=100))
        self.assertEqual(glyph._speed_text(), "100 Мбит/с")

        glyph.update_port(self._port(speed_mbps=2500))
        self.assertEqual(glyph._speed_text(), "2.5 Гбит/с")

    def test_disconnected_port_has_no_link_details(self) -> None:
        glyph = PortGlyph(self._port(connected=False))

        self.assertIn("Отключён", glyph.toolTip())
        self.assertNotIn("Скорость:", glyph.toolTip())
        self.assertNotIn("Дуплекс:", glyph.toolTip())

    def test_hover_state_is_shared_by_ethernet_and_usb(self) -> None:
        for kind in ("ethernet", "usb"):
            glyph = PortGlyph(self._port(kind=kind))
            QApplication.sendEvent(glyph, QEvent(QEvent.Type.Enter))
            self.assertTrue(glyph._hovered)
            QApplication.sendEvent(glyph, QEvent(QEvent.Type.Leave))
            self.assertFalse(glyph._hovered)

    def test_common_glyph_size_allows_two_label_rows(self) -> None:
        ethernet = PortGlyph(self._port())
        usb = PortGlyph(self._port(kind="usb", physical_name="USB"))

        self.assertEqual((ethernet.width(), ethernet.height()), (54, 72))
        self.assertEqual((usb.width(), usb.height()), (54, 72))


class PortOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_reuses_one_glyph_component_for_ethernet_and_usb(self) -> None:
        overview = PortOverview()
        ports = [
            PortGlyphTests._port(interface_id="Ethernet0"),
            PortGlyphTests._port(
                kind="usb",
                physical_name="USB",
                interface_id="Usb0",
            ),
        ]

        overview.set_ports(ports)

        self.assertEqual(len(overview._glyphs), 2)
        self.assertTrue(
            all(isinstance(glyph, PortGlyph) for glyph in overview._glyphs.values())
        )
        self.assertEqual((overview.width(), overview.height()), (340, 72))


if __name__ == "__main__":
    unittest.main()
