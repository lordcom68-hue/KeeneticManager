from __future__ import annotations

from typing import Any, Callable

from core.port_engine import PortEngine
from core.topology_engine import TopologyEngine


ResultFactory = Callable[..., Any]


class ConnectionResultBuilderService:
    """Builds the normalized application snapshot from read-only RCI data."""

    def __init__(
        self,
        client: Any,
        *,
        result_factory: ResultFactory,
    ) -> None:
        self.client = client
        self.result_factory = result_factory

    def build(
        self,
        system_data: dict[str, Any],
        version_data: dict[str, Any],
        internet_data: dict[str, Any],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any] | None = None,
        ping_check_config_data: dict[str, Any] | None = None,
        ping_check_status_data: dict[str, Any] | None = None,
        associations_data: Any = None,
        hotspot_data: Any = None,
        usb_data: Any = None,
        force_wifi_logical_refresh: bool = False,
        client_data_refreshed: bool = True,
    ) -> Any:
        system_status = self.client.system_status_service.build(
            system_data=system_data,
            version_data=version_data,
        )

        topology = TopologyEngine(
            internet_data=internet_data,
            interface_data=interface_data,
            manually_disabled=(
                self.client._manually_disabled_interfaces
            ),
        ).build()

        internet_connections = (
            self.client.filter_real_internet_connections(
                topology.connections,
                interface_data,
            )
        )
        internet_connections = (
            self.client.priority_service.merge_configured_global_connections(
                connections=internet_connections,
                interface_data=interface_data,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )

        self.client.enrich_connection_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
            ping_check_config_data=(
                ping_check_config_data or {}
            ),
            ping_check_status_data=(
                ping_check_status_data or {}
            ),
        )
        self.client.wireless_isp_service.enrich_wireless_isp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.client.enrich_wired_pppoe_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.client.enrich_wired_pptp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.client.enrich_wired_l2tp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.client.enrich_cable_protocol_change_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        internet_connections = (
            self.client.group_authenticated_cable_connections(
                connections=internet_connections,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )
        self.client.priority_service.sort_connections(internet_connections)

        wifi_networks = self.client.wifi_network_service.build_networks(
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            force_logical_refresh=force_wifi_logical_refresh,
            client_data_refreshed=client_data_refreshed,
        )
        client_inventory = list(
            self.client.wifi_network_service.client_inventory
        )
        self.client.wifi_client_service.enrich_clients(
            client_inventory,
            interface_config=(interface_config_data or {}),
        )
        route_url = str(self.client.base_url or "")
        if route_url != self.client._management_route_url:
            self.client._management_route_url = route_url
            self.client._management_local_ip = self.client.detect_local_source_ip(
                route_url
            )
        management_ip = self.client._management_local_ip
        for client in client_inventory:
            client["management_client"] = self.client.same_ip_address(
                client.get("ip"),
                management_ip,
            )

        wifi_capabilities_snapshot = (
            self.client.wifi_network_service.capabilities(
                interface_data=interface_data,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )
        wifi_capabilities = {
            "supported_bands": list(
                wifi_capabilities_snapshot.supported_bands
            ),
            "available_slots": {
                band: list(values)
                for band, values
                in wifi_capabilities_snapshot.available_slots.items()
            },
            "physical_available_slots": {
                band: list(values)
                for band, values
                in wifi_capabilities_snapshot.physical_available_slots.items()
            },
            "home_bridge_id": (
                wifi_capabilities_snapshot.home_bridge_id
            ),
            "mws_supported": (
                wifi_capabilities_snapshot.mws_supported
            ),
            "legacy_supported": (
                wifi_capabilities_snapshot.legacy_supported
            ),
            "bandless_supported": (
                wifi_capabilities_snapshot.bandless_supported
            ),
            "management_mode": (
                wifi_capabilities_snapshot.management_mode
            ),
        }

        ports = PortEngine(
            interface_data=interface_data,
            usb_data=usb_data,
        ).build()

        # show/interface может сохранять runtime-флаг public=True
        # у порта встроенного коммутатора даже после удаления
        # роли inet и возврата порта в домашний VLAN. Для
        # назначения LAN/WAN используем сохранённую конфигурацию
        # show/sc/interface, а не этот переходный runtime-флаг.
        self.client.correct_switch_port_assignments_from_config(
            ports=ports,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        management_ports = {
            str(item.get("interface") or "").strip().casefold()
            for item in client_inventory
            if bool(item.get("management_client"))
            and str(item.get("interface") or "").strip()
        }
        for port in ports:
            interface_id = str(
                port.get("interface_id") or port.get("id") or ""
            ).strip()
            port["management_port"] = (
                interface_id.casefold() in management_ports
            )

        return self.result_factory(
            success=True,
            message="Данные роутера получены.",
            connected_url=self.client.base_url,
            connection_type=self.client.connection_type,
            system_data=system_data,
            version_data=version_data,
            internet_data=internet_data,
            interface_data=interface_data,
            internet_connections=internet_connections,
            topology=topology,
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            wifi_networks=wifi_networks,
            clients=client_inventory,
            wifi_capabilities=wifi_capabilities,
            router_capabilities={
                "wifi": self.client.capabilities.wifi,
                "wifi_management_mode": (
                    self.client.capabilities.wifi_management_mode
                ),
                "logical_mws": self.client.capabilities.logical_mws,
                "legacy_wifi": self.client.capabilities.legacy_wifi,
                "keeneticos_major": (
                    self.client.capabilities.keeneticos_major
                ),
                "keeneticos_minor": (
                    self.client.capabilities.keeneticos_minor
                ),
            },
            compatibility_profile=(
                self.client.compatibility_profile.to_dict()
                if self.client.compatibility_profile is not None
                else None
            ),
            ports=ports,
            usb_data=usb_data,
            creation_options=(
                self.client.build_connection_creation_options(
                    interface_data=interface_data,
                    interface_config_data=(
                        interface_config_data or {}
                    ),
                )
            ),
            model=system_status.model,
            version=system_status.version,
            uptime=system_status.uptime,
            cpu_load=system_status.cpu_load,
            ram_usage=system_status.ram_usage,
            memory_total=system_status.memory_total,
            memory_free=system_status.memory_free,
        )

