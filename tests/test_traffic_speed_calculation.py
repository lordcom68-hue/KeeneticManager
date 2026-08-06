from __future__ import annotations

import unittest
from unittest.mock import Mock

from core.keenetic_client import KeeneticClient


class TrafficSpeedCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = KeeneticClient(username="admin", password="secret")

    def test_speed_is_calculated_in_bits_per_second(self) -> None:
        self.client.traffic_service.clock = Mock(
            side_effect=[10.0, 12.0]
        )
        with self.subTest("two-second interval"):
            self.assertEqual(
                self.client.calculate_counter_speeds("ISP", 1000, 2000),
                (0.0, 0.0),
            )
            self.assertEqual(
                self.client.calculate_counter_speeds("ISP", 2000, 2500),
                (4000.0, 2000.0),
            )

    def test_missing_sample_does_not_destroy_valid_baseline(self) -> None:
        self.client.traffic_service.clock = Mock(
            side_effect=[10.0, 12.0]
        )
        with self.subTest("missing sample"):
            self.client.calculate_counter_speeds("ISP", 1000, 1000)
            self.assertEqual(
                self.client.calculate_counter_speeds("ISP", None, None),
                (0.0, 0.0),
            )
            self.assertEqual(
                self.client.calculate_counter_speeds("ISP", 2000, 2000),
                (4000.0, 4000.0),
            )

    def test_one_counter_reset_does_not_hide_other_direction(self) -> None:
        self.client.traffic_service.clock = Mock(
            side_effect=[10.0, 12.0]
        )
        with self.subTest("one direction reset"):
            self.client.calculate_counter_speeds("ISP", 1000, 5000)
            self.assertEqual(
                self.client.calculate_counter_speeds("ISP", 2000, 100),
                (4000.0, 0.0),
            )

    def test_impossible_value_is_rejected_per_direction(self) -> None:
        self.client.traffic_service.clock = Mock(
            side_effect=[10.0, 11.0]
        )
        with self.subTest("link ceiling"):
            self.client.calculate_counter_speeds("ISP", 0, 0)
            rx_speed, tx_speed = self.client.calculate_counter_speeds(
                "ISP",
                200_000_000,
                1_000,
                link_speed_bps=1_000_000_000,
            )
            self.assertEqual(rx_speed, 0.0)
            self.assertEqual(tx_speed, 8000.0)


if __name__ == "__main__":
    unittest.main()
