from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseIntegrityTests(unittest.TestCase):
    def test_release_version_is_105_beta(self) -> None:
        from core.app_version import (
            APP_RELEASE_STATUS,
            APP_RELEASE_TRACK,
            APP_VERSION,
        )

        self.assertEqual(APP_VERSION, "1.0.5-beta")
        self.assertEqual(APP_RELEASE_STATUS, "beta")
        self.assertEqual(APP_RELEASE_TRACK, "beta")

    def test_release_builders_are_versioned_and_portable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source_builder = (
            root / "tools" / "build_release_source.ps1"
        ).read_text(encoding="utf-8")
        release_builder = (
            root / "tools" / "build_release.ps1"
        ).read_text(encoding="utf-8")

        self.assertNotIn("1.0.0-beta1-clean", source_builder)
        self.assertIn('profile_id = "beta"', source_builder)
        self.assertIn(
            'storage_namespace = "KeeneticManager-Beta"',
            source_builder,
        )
        self.assertIn("portable.flag", release_builder)
        self.assertIn("portable_profile.json", release_builder)
        self.assertIn("Compress-Archive", release_builder)

    def test_router_http_is_centralized_in_rci_transport(self) -> None:
        root = Path(__file__).resolve().parents[1] / "core"
        forbidden = (
            ".session.get(",
            ".session.post(",
            ".session.request(",
            "session.request(",
            "requests.get(",
            "requests.post(",
            "requests.request(",
        )
        offenders: list[str] = []
        for source_path in root.rglob("*.py"):
            if source_path.name == "rci_transport.py":
                continue
            source = source_path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in source:
                    offenders.append(
                        f"{source_path.relative_to(root)}: {marker}"
                    )

        self.assertEqual(offenders, [])

    def test_router_ordering_contract_is_present(self) -> None:
        from core.database import Database

        self.assertEqual(Database.SCHEMA_VERSION, 280)
        self.assertTrue(callable(getattr(Database, "set_router_order", None)))

        source_path = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        )
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("RouterListWidget", source)
        self.assertIn("router_order_changed", source)
        self.assertIn("def save_router_order", source)

    def test_dhcp_placeholder_tab_is_removed_and_journal_is_real(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("self.dhcp_tab", source)
        self.assertIn("self.journal_tab = JournalTab()", source)
        self.assertIn('"Журнал",', source)


    def test_router_credential_store_exports_required_contract(self) -> None:
        from core.credential_store import RouterCredentialStore

        required = (
            "new_reference",
            "get_password",
            "set_password",
            "delete_password",
        )
        for name in required:
            self.assertTrue(
                callable(getattr(RouterCredentialStore, name, None)),
                f"RouterCredentialStore не содержит {name}",
            )

    def test_database_final_schema_does_not_define_password_column(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "database.py"
        )
        source = source_path.read_text(encoding="utf-8").lower()

        self.assertNotIn(
            "password text not null",
            source,
            "Финальная схема снова определяет открытую колонку password",
        )
        self.assertIn("credential_ref text", source)

    def test_main_window_reads_secret_only_for_manual_connection(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        )
        source = source_path.read_text(encoding="utf-8")

        self.assertEqual(
            source.count("include_password=True"),
            1,
            "Пароль должен извлекаться только перед ручным подключением",
        )

    def test_runtime_paths_exports_required_diagnostic_helpers(self) -> None:
        from core.services import runtime_paths

        required = (
            "migrate_legacy_database",
            "router_history_path",
            "router_snapshot_cache_path",
            "legacy_router_history_path",
            "legacy_router_snapshot_cache_path",
        )
        for name in required:
            self.assertTrue(
                callable(getattr(runtime_paths, name, None)),
                f"Отсутствует обязательная функция {name}",
            )

    def test_diagnostics_service_imports_with_runtime_paths(self) -> None:
        from core.services.diagnostics_service import DiagnosticsService

        self.assertTrue(callable(DiagnosticsService))

    def test_runtime_paths_support_explicit_history_root(self) -> None:
        from core.services.runtime_paths import (
            router_history_path,
            router_snapshot_cache_path,
        )

        root = Path("test-history")
        self.assertEqual(
            router_history_path(7, root=root),
            root / "connection_attempts_router_7.jsonl",
        )
        self.assertEqual(
            router_snapshot_cache_path(7, root=root),
            root / "last_snapshot_router_7.json",
        )

    def test_rci_transport_exports_monitor_state_recorder(self) -> None:
        from core.services.rci_transport import RciTransport

        self.assertTrue(
            callable(getattr(RciTransport, "record_monitor_state", None)),
            "RciTransport не содержит record_monitor_state",
        )

    def test_connection_result_exports_monitoring_contract(self) -> None:
        from dataclasses import fields

        from core.keenetic_client import ConnectionResult

        required = {
            "clients",
            "connection_state",
            "consecutive_failures",
            "confirmed_offline",
            "transient_failure_suppressed",
            "last_result_message",
        }
        actual = {item.name for item in fields(ConnectionResult)}
        self.assertTrue(
            required.issubset(actual),
            f"ConnectionResult не содержит поля: {sorted(required - actual)}",
        )

        result = ConnectionResult(
            success=False,
            message="offline",
            connection_state="offline",
            confirmed_offline=True,
            consecutive_failures=3,
        )
        self.assertEqual(result.connection_state, "offline")
        self.assertTrue(result.confirmed_offline)

    def test_management_client_ip_comparison_is_normalized(self) -> None:
        from core.keenetic_client import KeeneticClient

        self.assertTrue(
            KeeneticClient.same_ip_address(
                "192.168.3.140",
                "192.168.3.140",
            )
        )
        self.assertFalse(
            KeeneticClient.same_ip_address(
                "192.168.3.140",
                "192.168.3.141",
            )
        )
        self.assertIsNone(
            KeeneticClient.detect_local_source_ip(
                "https://router.example.test"
            )
        )

    def test_management_client_inventory_is_built_with_result(self) -> None:
        import inspect

        from core.keenetic_client import KeeneticClient

        result_source = inspect.getsource(KeeneticClient.build_result)
        connect_source = inspect.getsource(KeeneticClient.connect_to_router)

        self.assertIn(
            "client_inventory = list(",
            result_source,
        )
        self.assertIn(
            "clients=client_inventory",
            result_source,
        )
        self.assertNotIn(
            "client_inventory = list(",
            connect_source,
        )

    def test_router_monitor_exports_reachability_adapter(self) -> None:
        import ast

        source_path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "connection_worker.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        router_monitor = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == "RouterMonitor"
            ),
            None,
        )
        method_names = {
            node.name
            for node in (router_monitor.body if router_monitor else [])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            "apply_reachability_state",
            method_names,
            "RouterMonitor не содержит apply_reachability_state",
        )


    def test_router_address_links_export_required_contract(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "router_address_links.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "class RouterAddressLink",
            source,
        )
        self.assertIn(
            "class RouterAddressLinks",
            source,
        )
        self.assertIn(
            "QDesktopServices.openUrl",
            source,
        )
        self.assertIn(
            "Копировать адрес",
            source,
        )
        self.assertIn(
            "TextSelectableByMouse",
            source,
        )
        self.assertIn(
            "LinksAccessibleByKeyboard",
            source,
        )

    def test_main_window_populates_router_address_links(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "RouterAddressLinks",
            source,
        )
        self.assertIn(
            "self.router_address_links.set_addresses",
            source,
        )
        self.assertIn(
            "self.router_address_links.clear_addresses",
            source,
        )


    def test_interface_service_exports_required_contract(self) -> None:
        from core.services.interface_service import (
            InterfaceService,
            InterfaceSnapshot,
        )

        required = (
            "read_runtime",
            "read_config",
            "read_snapshot",
            "set_enabled",
            "reconnect",
            "verify_admin_state",
            "get_statistics",
            "normalize_statistics",
            "interface_is_enabled",
            "interface_is_up",
        )

        self.assertTrue(callable(InterfaceSnapshot))

        for name in required:
            self.assertTrue(
                callable(
                    getattr(
                        InterfaceService,
                        name,
                        None,
                    )
                ),
                f"InterfaceService не содержит {name}",
            )

    def test_port_status_service_owns_safe_physical_port_mutations(self) -> None:
        from core.services.port_status_service import (
            PortSnapshot,
            PortStatusService,
        )

        self.assertTrue(callable(PortSnapshot))
        for name in (
            "snapshot",
            "is_management_port",
            "set_enabled",
            "set_enabled_result",
        ):
            self.assertTrue(callable(getattr(PortStatusService, name, None)), name)

        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.port_status_service = PortStatusService(self)", source)
        self.assertIn("self.port_status_service.set_enabled(", source)

    def test_traffic_service_owns_counter_calculation(self) -> None:
        from core.services.traffic_service import TrafficSample, TrafficService

        self.assertTrue(callable(TrafficSample))
        for name in (
            "calculate_counter_speeds",
            "measure_connection",
            "apply_sample",
            "extract_link_speed_bps",
            "reset",
            "clear",
        ):
            self.assertTrue(callable(getattr(TrafficService, name, None)), name)

        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.traffic_service = TrafficService(self)", source)
        self.assertIn("self.traffic_service.measure_connection(", source)
        self.assertNotIn("delta * 8.0 / elapsed", source)

        wifi_source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "services"
            / "wifi_network_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("traffic_service.calculate_counter_speeds(", wifi_source)
        self.assertNotIn("rx_delta * 8.0", wifi_source)

    def test_system_status_service_owns_device_metric_calculation(self) -> None:
        from core.services.system_status_service import (
            SystemStatus,
            SystemStatusService,
        )

        self.assertTrue(callable(SystemStatus))
        for name in (
            "build",
            "extract_model",
            "extract_version",
            "extract_cpu_load",
            "extract_uptime",
            "calculate_ram_usage",
        ):
            self.assertTrue(callable(getattr(SystemStatusService, name, None)), name)

        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.system_status_service = SystemStatusService()", source)
        self.assertIn("self.system_status_service.build(", source)
        self.assertNotIn("used_memory = max(", source)

    def test_keenetic_client_delegates_interface_actions(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "self.interface_service = InterfaceService(self)",
            source,
        )
        self.assertIn("self.interface_service.read_runtime()", source)
        self.assertIn("loader=self.interface_service.read_config", source)
        self.assertIn(
            "return self.interface_service.set_enabled",
            source,
        )
        self.assertIn(
            "return self.interface_service.reconnect",
            source,
        )

        self.assertNotIn(
            "/rci/interface/{action}",
            source,
            "HTTP-управление интерфейсом осталось в KeeneticClient",
        )

    def test_connection_worker_keeps_compatible_client_facade(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "client.set_interface_enabled",
            source,
        )
        self.assertIn(
            "client.reconnect_interface",
            source,
        )


    def test_wired_internet_service_exports_required_contract(self) -> None:
        from core.services.wired_internet_service import (
            WiredInternetService,
        )

        required = (
            "create_cable_internet",
            "create_dedicated_cable_internet",
            "create_wired_ipoe",
            "create_wired_pppoe",
            "create_wired_pptp",
            "create_wired_l2tp",
            "change_cable_internet_protocol",
            "delete_dedicated_wan",
            "delete_wired_ipoe",
            "delete_wired_pppoe",
            "delete_wired_pptp",
            "delete_wired_l2tp",
        )

        for name in required:
            self.assertTrue(
                callable(
                    getattr(
                        WiredInternetService,
                        name,
                        None,
                    )
                ),
                name,
            )

    def test_keenetic_client_wired_methods_are_compatibility_facades(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "self.wired_internet_service = WiredInternetService(self)",
            source,
        )
        self.assertIn(
            "return self.wired_internet_service.create_wired_ipoe",
            source,
        )
        self.assertIn(
            "return self.wired_internet_service.delete_wired_pppoe",
            source,
        )

        self.assertNotIn(
            "Создаёт проводной IPoE на свободном порту",
            source,
            "Большая IPoE-реализация осталась в KeeneticClient",
        )

    def test_connection_worker_routes_wired_actions_to_service(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "client.wired_internet_service.create_cable_internet",
            source,
        )
        self.assertIn(
            "client.wired_internet_service.delete_wired_l2tp",
            source,
        )


    def test_wireless_isp_service_exports_required_contract(self) -> None:
        from core.services.wireless_isp_service import (
            WirelessIspService,
        )

        required = (
            "wireless_isp_band_from_interface",
            "wireless_isp_configured_item",
            "enrich_wireless_isp_details",
            "wireless_isp_display_name",
            "is_wireless_isp_interface",
            "normalize_wireless_isp_band",
            "wireless_isp_interface_for_band",
            "parse_wireless_site_survey",
            "scan_wireless_isp",
            "create_wireless_isp",
            "update_wireless_isp_settings",
            "delete_wireless_isp",
        )

        for name in required:
            self.assertTrue(
                callable(
                    getattr(
                        WirelessIspService,
                        name,
                        None,
                    )
                ),
                name,
            )

    def test_keenetic_client_wireless_methods_are_facades(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            (
                "self.wireless_isp_service = "
                "WirelessIspService(self)"
            ),
            source,
        )
        self.assertIn(
            (
                "return self.wireless_isp_service"
                ".create_wireless_isp"
            ),
            source,
        )
        self.assertIn(
            (
                "return self.wireless_isp_service"
                ".delete_wireless_isp"
            ),
            source,
        )
        self.assertNotIn(
            "Нормализует разные ответы show site-survey",
            source,
            (
                "Разбор site-survey остался "
                "в KeeneticClient"
            ),
        )

    def test_worker_and_refresh_route_wireless_logic_to_service(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        worker_source = (
            root
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")
        client_source = (
            root
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            (
                "client.wireless_isp_service"
                ".create_wireless_isp"
            ),
            worker_source,
        )
        self.assertIn(
            (
                "client.wireless_isp_service"
                ".delete_wireless_isp"
            ),
            worker_source,
        )
        self.assertIn(
            (
                "self.wireless_isp_service"
                ".enrich_wireless_isp_details"
            ),
            client_source,
        )
        self.assertIn(
            (
                "self.wireless_isp_service"
                ".update_wireless_isp_settings"
            ),
            client_source,
        )


    def test_ping_check_service_exports_required_contract(self) -> None:
        from core.services.ping_check_service import PingCheckService, PingCheckSnapshot
        required = (
            "read_snapshot",
            "extract_ping_check_settings",
            "extract_ping_check_runtime",
            "update_ping_check_runtime_history",
            "build_ping_check_commands",
            "enrich_connection",
            "assigned_ping_profile",
        )
        self.assertTrue(callable(PingCheckSnapshot))
        for name in required:
            self.assertTrue(callable(getattr(PingCheckService, name, None)), name)

    def test_client_ping_check_methods_are_facades(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "core" / "keenetic_client.py").read_text(encoding="utf-8")
        self.assertIn("self.ping_check_service = PingCheckService(self)", source)
        self.assertIn("loader=self.ping_check_service.read_config", source)
        self.assertIn("loader=self.ping_check_service.read_status", source)
        self.assertIn("self.ping_check_service.enrich_connection(", source)
        self.assertNotIn("История Ping Check в рамках текущего сеанса", source)

    def test_wired_service_routes_ping_helpers_to_service(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "core" / "services" / "wired_internet_service.py").read_text(encoding="utf-8")
        self.assertIn("self.client.ping_check_service.find_ping_check_profile", source)
        self.assertIn("self.client.ping_check_service.assigned_ping_profile", source)


    def test_priority_service_exports_required_contract(self) -> None:
        from core.services.priority_service import PriorityService, PrioritySnapshot
        self.assertTrue(callable(PrioritySnapshot))
        for name in (
            "read_snapshot",
            "current_global_orders",
            "normalize_order_map",
            "priorities_with_inserted_interface",
            "priorities_without_interface",
            "merge_configured_global_connections",
            "sort_connections",
            "set_internet_priorities",
            "verify_global_interface_orders",
        ):
            self.assertTrue(callable(getattr(PriorityService,name,None)),name)

    def test_priority_logic_is_routed_to_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        client=(root / "core" / "keenetic_client.py").read_text(encoding="utf-8")
        wired=(root / "core" / "services" / "wired_internet_service.py").read_text(encoding="utf-8")
        wireless=(root / "core" / "services" / "wireless_isp_service.py").read_text(encoding="utf-8")
        self.assertIn("self.priority_service = PriorityService(self)",client)
        self.assertIn("self.priority_service.merge_configured_global_connections",client)
        self.assertIn("self.client.priority_service.set_internet_priorities",wired)
        self.assertIn("self.client.priority_service.set_internet_priorities",wireless)
        self.assertNotIn("SEND priorities=",client)


if __name__ == "__main__":
    unittest.main()
