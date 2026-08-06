from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.connection_card import TrafficChart  # noqa: E402


class TrafficChartTests(unittest.TestCase):
    def test_second_series_does_not_fill_area_between_points(self) -> None:
        image = QImage(101, 101, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        rect = QRectF(0, 0, 100, 100)

        TrafficChart.draw_series(
            painter=painter,
            rect=rect,
            values=[0.0, 0.0, 0.0],
            color=QColor("#60a5fa"),
            max_value=100.0,
            width=1.0,
        )
        TrafficChart.draw_series(
            painter=painter,
            rect=rect,
            values=[0.0, 100.0, 0.0],
            color=QColor("#34d399"),
            max_value=100.0,
            width=1.0,
        )
        painter.end()

        self.assertEqual(image.pixelColor(50, 50).alpha(), 0)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_invalid_samples_are_replaced_with_zero(self) -> None:
        values = TrafficChart.normalize_values(
            [10.0, -1.0, math.nan, math.inf, "bad"]  # type: ignore[list-item]
        )
        self.assertEqual(values, [10.0, 0.0, 0.0, 0.0, 0.0])

    def test_scale_has_headroom_and_uses_readable_step(self) -> None:
        self.assertEqual(
            TrafficChart.nice_scale_max([100_800.0]),
            125_000.0,
        )

    def test_real_sample_intervals_control_horizontal_position(self) -> None:
        points = TrafficChart.series_points(
            rect=QRectF(0, 0, 300, 100),
            values=[0.0, 50.0, 100.0],
            max_value=100.0,
            sample_times=[10.0, 11.0, 13.0],
        )
        self.assertAlmostEqual(points[1].x(), 100.0)
        self.assertAlmostEqual(points[-1].x(), 300.0)

    def test_speed_format_is_compact_and_localized(self) -> None:
        self.assertEqual(TrafficChart.format_speed(100_800), "100,8 Кбит/с")
        self.assertEqual(TrafficChart.format_speed(1_000_000), "1 Мбит/с")
        self.assertEqual(TrafficChart.format_speed(math.nan), "0 бит/с")


if __name__ == "__main__":
    unittest.main()
