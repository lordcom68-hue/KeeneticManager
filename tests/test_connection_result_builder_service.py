from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.services.connection_result_builder_service import (
    ConnectionResultBuilderService,
)


class ConnectionResultBuilderServiceTests(unittest.TestCase):
    def test_build_normalizes_snapshot_and_marks_management_path(self) -> None:
        status = SimpleNamespace(
            model="Test Router",
            version="5.1",
            uptime="1 ч.",
            cpu_load="12%",
            ram_usage="34%",
            memory_total=1024,
            memory_free=512,
        )
        wifi_snapshot = SimpleNamespace(
            supported_bands=["2.4", "5"],
            available_slots={"2.4": [1], "5": [2]},
            physical_available_slots={"2.4": [1], "5": [2]},
            home_bridge_id="Bridge0",
            mws_supported=True,
            legacy_supported=False,
            bandless_supported=True,
            management_mode="mws",
        )
        wifi_service = SimpleNamespace(
            client_inventory=[
                {
                    "ip": "192.0.2.10",
                    "interface": "GigabitEthernet0/1",
                }
            ],
            build_networks=MagicMock(return_value=[{"ssid": "Test"}]),
            capabilities=MagicMock(return_value=wifi_snapshot),
        )
        priority_service = SimpleNamespace(
            merge_configured_global_connections=MagicMock(
                side_effect=lambda **kwargs: kwargs["connections"]
            ),
            sort_connections=MagicMock(),
        )
        client = SimpleNamespace(
            system_status_service=SimpleNamespace(
                build=MagicMock(return_value=status)
            ),
            _manually_disabled_interfaces=set(),
            filter_real_internet_connections=MagicMock(
                side_effect=lambda connections, _interfaces: list(connections)
            ),
            priority_service=priority_service,
            enrich_connection_details=MagicMock(),
            wireless_isp_service=SimpleNamespace(
                enrich_wireless_isp_details=MagicMock()
            ),
            enrich_wired_pppoe_details=MagicMock(),
            enrich_wired_pptp_details=MagicMock(),
            enrich_wired_l2tp_details=MagicMock(),
            enrich_cable_protocol_change_details=MagicMock(),
            group_authenticated_cable_connections=MagicMock(
                side_effect=lambda **kwargs: kwargs["connections"]
            ),
            wifi_network_service=wifi_service,
            wifi_client_service=SimpleNamespace(enrich_clients=MagicMock()),
            base_url="http://192.0.2.1",
            connection_type="local",
            _management_route_url="",
            _management_local_ip=None,
            detect_local_source_ip=MagicMock(return_value="192.0.2.10"),
            same_ip_address=MagicMock(
                side_effect=lambda left, right: left == right
            ),
            correct_switch_port_assignments_from_config=MagicMock(),
            capabilities=SimpleNamespace(
                wifi=True,
                wifi_management_mode="mws",
                logical_mws=True,
                legacy_wifi=False,
                keeneticos_major=5,
                keeneticos_minor=1,
            ),
            compatibility_profile=SimpleNamespace(
                to_dict=lambda: {"support": "full"}
            ),
            build_connection_creation_options=MagicMock(
                return_value={"ethernet.cable": {"available": True}}
            ),
        )

        with (
            patch(
                "core.services.connection_result_builder_service.TopologyEngine"
            ) as topology_engine,
            patch(
                "core.services.connection_result_builder_service.PortEngine"
            ) as port_engine,
        ):
            topology = SimpleNamespace(connections=[{"interface_id": "ISP"}])
            topology_engine.return_value.build.return_value = topology
            port_engine.return_value.build.return_value = [
                {"interface_id": "GigabitEthernet0/1"}
            ]
            result = ConnectionResultBuilderService(
                client,
                result_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            ).build(
                system_data={"system": True},
                version_data={"version": True},
                internet_data={"internet": True},
                interface_data={"interfaces": True},
                interface_config_data={"config": True},
                associations_data={"associations": True},
                hotspot_data={"hotspot": True},
                usb_data={"usb": True},
                force_wifi_logical_refresh=True,
                client_data_refreshed=False,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.model, "Test Router")
        self.assertEqual(result.internet_connections, topology.connections)
        self.assertTrue(result.clients[0]["management_client"])
        self.assertTrue(result.ports[0]["management_port"])
        self.assertEqual(result.wifi_capabilities["management_mode"], "mws")
        self.assertEqual(result.compatibility_profile, {"support": "full"})
        self.assertEqual(client._management_route_url, client.base_url)
        wifi_service.build_networks.assert_called_once()
        client.correct_switch_port_assignments_from_config.assert_called_once()


if __name__ == "__main__":
    unittest.main()
