from __future__ import annotations

import math
import unittest
from typing import Any

from core.services.traffic_service import TrafficSample, TrafficService


class FakeInterfaceService:
    def __init__(self) -> None:
        self.statistics: dict[str, Any] = {}

    def select_traffic_statistics(
        self,
        connection: dict[str, Any],
        interface_data: dict[str, Any],
        *,
        number_reader,
    ) -> tuple[str, dict[str, Any]]:
        del interface_data, number_reader
        return str(connection.get("interface_id") or ""), self.statistics

    @staticmethod
    def normalize_statistics(data: Any) -> dict[str, Any]:
        return dict(data) if isinstance(data, dict) else {}


class FakeClient:
    def __init__(self) -> None:
        self.interface_service = FakeInterfaceService()


class TrafficServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.service = TrafficService(self.client)

    def test_short_interval_keeps_older_baseline(self) -> None:
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 1_000, 2_000, sampled_at=10.0
            ),
            (0.0, 0.0),
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 1_100, 2_100, sampled_at=10.1
            ),
            (0.0, 0.0),
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 2_000, 3_000, sampled_at=11.0
            ),
            (8_000.0, 8_000.0),
        )

    def test_long_interval_rebases_before_next_sample(self) -> None:
        self.service.calculate_counter_speeds(
            "ISP", 1_000, 1_000, sampled_at=10.0
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 5_000, 5_000, sampled_at=41.0
            ),
            (0.0, 0.0),
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 6_000, 6_000, sampled_at=43.0
            ),
            (4_000.0, 4_000.0),
        )

    def test_missing_direction_does_not_hide_other_direction(self) -> None:
        self.service.calculate_counter_speeds(
            "ISP", 1_000, 1_000, sampled_at=10.0
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", None, 2_000, sampled_at=12.0
            ),
            (0.0, 4_000.0),
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 3_000, 3_000, sampled_at=14.0
            ),
            (4_000.0, 4_000.0),
        )

    def test_reset_and_impossible_spike_are_isolated(self) -> None:
        self.service.calculate_counter_speeds(
            "ISP", 10_000, 10_000, sampled_at=10.0
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP",
                20_000_000,
                11_000,
                link_speed_bps=1_000_000,
                sampled_at=11.0,
            ),
            (0.0, 8_000.0),
        )
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", 100, 12_000, sampled_at=12.0
            ),
            (0.0, 8_000.0),
        )

    def test_invalid_values_do_not_create_counter_state(self) -> None:
        self.assertEqual(
            self.service.calculate_counter_speeds(
                "ISP", math.nan, -1.0, sampled_at=10.0
            ),
            (0.0, 0.0),
        )
        self.assertEqual(self.service.counters, {})
        self.assertEqual(self.service.normalize_rate(math.inf), 0.0)
        self.assertEqual(self.service.normalize_rate(-1), 0.0)
        self.assertEqual(self.service.normalize_rate("12.5"), 12.5)

    def test_link_speed_supports_router_and_localized_units(self) -> None:
        self.assertEqual(
            self.service.extract_link_speed_bps({"speed": 1000}),
            1_000_000_000.0,
        )
        self.assertEqual(
            self.service.extract_link_speed_bps({"speed": "2.5 Gbit/s"}),
            2_500_000_000.0,
        )
        self.assertEqual(
            self.service.extract_link_speed_bps({"speed": "100 Мбит/с"}),
            100_000_000.0,
        )
        self.assertIsNone(
            self.service.extract_link_speed_bps({"speed": math.nan})
        )

    def test_measurement_and_connection_mapping_use_one_timestamp(self) -> None:
        self.client.interface_service.statistics = {
            "rxbytes": 1_500,
            "txbytes": 2_500,
            "rxpackets": 15,
            "txpackets": 25,
        }
        sample = self.service.measure_connection(
            connection={"interface_id": "ISP"},
            interface_data={"ISP": {"speed": 1000}},
            sampled_at=50.0,
        )
        connection: dict[str, Any] = {}
        self.service.apply_sample(connection, sample)

        self.assertIsInstance(sample, TrafficSample)
        self.assertEqual(sample.source_id, "ISP")
        self.assertEqual(connection["traffic_sample_time"], 50.0)
        self.assertEqual(connection["rxbytes"], 1_500.0)
        self.assertEqual(connection["txpackets"], 25.0)
        self.assertEqual(connection["rxspeed"], 0.0)


if __name__ == "__main__":
    unittest.main()
