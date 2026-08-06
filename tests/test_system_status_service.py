from __future__ import annotations

import unittest

from core.services.system_status_service import SystemStatus, SystemStatusService


class SystemStatusServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SystemStatusService()

    def test_build_normalizes_complete_router_snapshot(self) -> None:
        status = self.service.build(
            system_data={
                "system": {
                    "hostname": "fallback-name",
                    "uptime": 90061,
                    "cpuload": "7.25",
                    "memtotal": "524288",
                    "memfree": 100000,
                    "memory": "182908/524288",
                }
            },
            version_data={
                "model": "Sprinter (KN-3711)",
                "release": "5.01.C.1.0-0",
            },
        )

        self.assertIsInstance(status, SystemStatus)
        self.assertEqual(status.model, "Sprinter (KN-3711)")
        self.assertEqual(status.version, "5.01.C.1.0-0")
        self.assertEqual(status.uptime, "1 дн. 1 ч. 1 мин.")
        self.assertEqual(status.cpu_load, "7.2%")
        self.assertEqual(status.ram_usage, "34.9%")
        self.assertEqual(status.memory_total, 524288)
        self.assertEqual(status.memory_free, 100000)

    def test_build_falls_back_to_hostname_and_component_version(self) -> None:
        status = self.service.build(
            system_data={"system": {"hostname": "Home-Router"}},
            version_data={
                "components": [
                    {
                        "component": "ndm",
                        "current": "4.3.6",
                    }
                ]
            },
        )

        self.assertEqual(status.model, "Home-Router")
        self.assertEqual(status.version, "4.3.6")
        self.assertIsNone(status.uptime)
        self.assertIsNone(status.cpu_load)
        self.assertIsNone(status.ram_usage)

    def test_extractors_accept_nested_case_insensitive_aliases(self) -> None:
        data = {
            "items": [
                {
                    "DEVICE_MODEL": "Runner 4G",
                    "OS-Version": {"title": "4.2.1"},
                    "CPU_LOAD": "18%",
                    "UP_TIME": "61",
                }
            ]
        }

        self.assertEqual(self.service.extract_model(data), "Runner 4G")
        self.assertEqual(self.service.extract_version(data), "4.2.1")
        self.assertEqual(self.service.extract_cpu_load(data), "18%")
        self.assertEqual(self.service.extract_uptime(data), "1 мин.")

    def test_cpu_load_handles_numbers_text_and_invalid_non_finite_values(self) -> None:
        self.assertEqual(self.service.extract_cpu_load({"cpu": 8}), "8%")
        self.assertEqual(self.service.extract_cpu_load({"cpu": "busy"}), "busy")
        self.assertIsNone(self.service.extract_cpu_load({"cpu": "NaN"}))
        self.assertIsNone(self.service.extract_cpu_load({"cpu": ""}))
        self.assertIsNone(self.service.extract_cpu_load({}))

    def test_ram_usage_prefers_direct_percentage_then_used_total_pair(self) -> None:
        self.assertEqual(
            self.service.calculate_ram_usage(1000, 100, {"memload": "42%"}),
            "42%",
        )
        self.assertEqual(
            self.service.calculate_ram_usage(1000, 100, {"memory-load": 120}),
            "100%",
        )
        self.assertEqual(
            self.service.calculate_ram_usage(1000, 100, {"memory": "1/4"}),
            "25%",
        )

    def test_ram_usage_falls_back_to_free_buffers_and_cache(self) -> None:
        usage = self.service.calculate_ram_usage(
            memory_total=1000,
            memory_free=100,
            data={"membuffers": 100, "memcache": 200},
        )

        self.assertEqual(usage, "60%")
        self.assertEqual(
            self.service.calculate_ram_usage(100, 500, {}),
            "0%",
        )
        self.assertIsNone(self.service.calculate_ram_usage(None, 10, {}))

    def test_invalid_memory_values_do_not_break_status_refresh(self) -> None:
        self.assertIsNone(self.service.parse_memory_pair(None))
        self.assertIsNone(self.service.parse_memory_pair("bad"))
        self.assertIsNone(self.service.parse_memory_pair("bad/100"))
        self.assertIsNone(self.service.parse_memory_pair("-1/100"))
        self.assertIsNone(self.service.parse_memory_pair("1/0"))
        self.assertIsNone(self.service.parse_memory_pair("nan/100"))
        self.assertIsNone(
            self.service.extract_integer({"memtotal": True}, ("memtotal",))
        )
        self.assertIsNone(
            self.service.extract_integer({"memtotal": "inf"}, ("memtotal",))
        )

    def test_uptime_formats_all_boundaries_and_preserves_unknown_text(self) -> None:
        self.assertEqual(self.service.format_uptime_seconds(5), "5 сек.")
        self.assertEqual(self.service.format_uptime_seconds(3600), "1 ч. 0 мин.")
        self.assertEqual(
            self.service.format_uptime_seconds(86400),
            "1 дн. 0 ч. 0 мин.",
        )
        self.assertEqual(self.service.format_uptime_seconds(-1), "—")
        self.assertEqual(self.service.extract_uptime({"uptime": "unknown"}), "unknown")
        self.assertEqual(self.service.extract_uptime({"uptime": "inf"}), "inf")

    def test_version_helpers_cover_scalar_mapping_and_component_lists(self) -> None:
        self.assertEqual(self.service.value_to_version_text(5.1), "5.1")
        self.assertEqual(
            self.service.value_to_version_text({"installed": "5.0"}),
            "5.0",
        )
        self.assertIsNone(self.service.value_to_version_text({"other": "x"}))
        self.assertIsNone(self.service.value_to_version_text([]))
        self.assertEqual(
            self.service.find_component_version(
                [{"id": "core", "installed": {"release": "4.1"}}]
            ),
            "4.1",
        )
        self.assertIsNone(self.service.find_component_version([{"id": "ui"}]))

    def test_keenetic_client_keeps_compatible_system_helpers(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(username="admin", password="secret")

        self.assertIsInstance(client.system_status_service, SystemStatusService)
        self.assertEqual(client.extract_model({"model": "Peak"}), "Peak")
        self.assertEqual(client.extract_version({"release": "5.0"}), "5.0")
        self.assertEqual(client.extract_cpu_load({"cpuload": 12}), "12%")
        self.assertEqual(client.extract_uptime({"uptime": 60}), "1 мин.")
        self.assertEqual(client.parse_memory_pair("5/10"), (5.0, 10.0))
        self.assertEqual(client.format_percentage(25.25), "25.2%")
        self.assertEqual(client.extract_integer({"value": "10.9"}, ("value",)), 10)


if __name__ == "__main__":
    unittest.main()
