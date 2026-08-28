"""Service layer exports, loaded lazily to avoid import cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "ConnectionDetailsService": (
        "connection_details_service",
        "ConnectionDetailsService",
    ),
    "ConnectionResultBuilderService": (
        "connection_result_builder_service",
        "ConnectionResultBuilderService",
    ),
    "ConnectionClassificationService": (
        "connection_classification_service",
        "ConnectionClassificationService",
    ),
    "ConnectionCreationOptionsService": (
        "connection_creation_options_service",
        "ConnectionCreationOptionsService",
    ),
    "CableProtocolChangeService": (
        "cable_protocol_change_service",
        "CableProtocolChangeService",
    ),
    "CableProtocolLifecycleService": (
        "cable_protocol_lifecycle_service",
        "CableProtocolLifecycleService",
    ),
    "CrashReportService": (
        "crash_report_service",
        "CrashReportService",
    ),
    "DiagnosticsService": ("diagnostics_service", "DiagnosticsService"),
    "DedicatedWanService": (
        "dedicated_wan_service",
        "DedicatedWanService",
    ),
    "DedicatedWanLifecycleService": (
        "dedicated_wan_lifecycle_service",
        "DedicatedWanLifecycleService",
    ),
    "FirmwareCatalogRecord": (
        "firmware_catalog_service",
        "FirmwareCatalogRecord",
    ),
    "FirmwareCatalogImportResult": (
        "firmware_catalog_service",
        "FirmwareCatalogImportResult",
    ),
    "FirmwareCatalogService": (
        "firmware_catalog_service",
        "FirmwareCatalogService",
    ),
    "FirmwareInspectionCancelled": (
        "firmware_inspection_service",
        "FirmwareInspectionCancelled",
    ),
    "FirmwareCapabilityEvidence": (
        "firmware_inspection_service",
        "FirmwareCapabilityEvidence",
    ),
    "FirmwareCapabilityProfile": (
        "firmware_inspection_service",
        "FirmwareCapabilityProfile",
    ),
    "FirmwareComparisonField": (
        "firmware_inspection_service",
        "FirmwareComparisonField",
    ),
    "FirmwareComparisonResult": (
        "firmware_inspection_service",
        "FirmwareComparisonResult",
    ),
    "FirmwareRouterCheckItem": (
        "firmware_inspection_service",
        "FirmwareRouterCheckItem",
    ),
    "FirmwareRouterCheckResult": (
        "firmware_inspection_service",
        "FirmwareRouterCheckResult",
    ),
    "FirmwareRouterCheckSummary": (
        "firmware_catalog_service",
        "FirmwareRouterCheckSummary",
    ),
    "FirmwareInspectionError": (
        "firmware_inspection_service",
        "FirmwareInspectionError",
    ),
    "FirmwareInspectionResult": (
        "firmware_inspection_service",
        "FirmwareInspectionResult",
    ),
    "FirmwareInspectionService": (
        "firmware_inspection_service",
        "FirmwareInspectionService",
    ),
    "KeeneticAuthenticationError": (
        "errors",
        "KeeneticAuthenticationError",
    ),
    "KeeneticConnectionError": ("errors", "KeeneticConnectionError"),
    "KeeneticError": ("errors", "KeeneticError"),
    "RciTransport": ("rci_transport", "RciTransport"),
    "InterfaceConfigurationService": (
        "interface_configuration_service",
        "InterfaceConfigurationService",
    ),
    "InterfaceService": ("interface_service", "InterfaceService"),
    "InterfaceSnapshot": ("interface_service", "InterfaceSnapshot"),
    "JournalEventAnalyzer": (
        "journal_event_analyzer",
        "JournalEventAnalyzer",
    ),
    "OperationResult": ("operation_result", "OperationResult"),
    "OperationState": ("operation_result", "OperationState"),
    "OperationHistoryService": (
        "operation_history_service",
        "OperationHistoryService",
    ),
    "PortSnapshot": ("port_status_service", "PortSnapshot"),
    "PortStatusService": ("port_status_service", "PortStatusService"),
    "PingCheckService": ("ping_check_service", "PingCheckService"),
    "PingCheckSnapshot": ("ping_check_service", "PingCheckSnapshot"),
    "PriorityService": ("priority_service", "PriorityService"),
    "PrioritySnapshot": ("priority_service", "PrioritySnapshot"),
    "WiredL2tpService": (
        "wired_l2tp_service",
        "WiredL2tpService",
    ),
    "WiredPppoeService": (
        "wired_pppoe_service",
        "WiredPppoeService",
    ),
    "WiredPptpService": (
        "wired_pptp_service",
        "WiredPptpService",
    ),
    "WiredProtocolServiceBase": (
        "wired_protocol_service_base",
        "WiredProtocolServiceBase",
    ),
    "WiredIpoeService": (
        "wired_ipoe_service",
        "WiredIpoeService",
    ),
    "WiredIpoeLifecycleService": (
        "wired_ipoe_lifecycle_service",
        "WiredIpoeLifecycleService",
    ),
    "WiredInternetService": (
        "wired_internet_service",
        "WiredInternetService",
    ),
    "WiredTunnelLifecycleService": (
        "wired_tunnel_lifecycle_service",
        "WiredTunnelLifecycleService",
    ),
    "WiredDeletionInspectionService": (
        "wired_deletion_inspection_service",
        "WiredDeletionInspectionService",
    ),
    "WiredConnectionDispatcher": (
        "wired_connection_dispatcher",
        "WiredConnectionDispatcher",
    ),
    "WirelessIspService": ("wireless_isp_service", "WirelessIspService"),
    "WiFiNetworkService": ("wifi_network_service", "WiFiNetworkService"),
    "WiFiClientService": ("wifi_client_service", "WiFiClientService"),
    "WifiLogicalSnapshot": (
        "wifi_network_service",
        "WifiLogicalSnapshot",
    ),
    "WifiNetworkCapabilities": (
        "wifi_network_service",
        "WifiNetworkCapabilities",
    ),
    "ReachabilityResult": ("reachability", "ReachabilityResult"),
    "ReachabilityTracker": ("reachability", "ReachabilityTracker"),
    "CompatibilityFeature": (
        "router_compatibility_service",
        "CompatibilityFeature",
    ),
    "RouterCompatibilityProfile": (
        "router_compatibility_service",
        "RouterCompatibilityProfile",
    ),
    "RouterCompatibilityService": (
        "router_compatibility_service",
        "RouterCompatibilityService",
    ),
    "RouterRefreshService": (
        "router_refresh_service",
        "RouterRefreshService",
    ),
    "RouterSelfCheckCancelled": (
        "router_self_check_service",
        "RouterSelfCheckCancelled",
    ),
    "RouterSelfCheckItem": (
        "router_self_check_service",
        "RouterSelfCheckItem",
    ),
    "RouterSelfCheckResult": (
        "router_self_check_service",
        "RouterSelfCheckResult",
    ),
    "RouterSelfCheckService": (
        "router_self_check_service",
        "RouterSelfCheckService",
    ),
    "RESCUED_MODE": ("rescue_mode_service", "RESCUED_MODE"),
    "RESCUER_MODE": ("rescue_mode_service", "RESCUER_MODE"),
    "RescueModeContext": ("rescue_mode_service", "RescueModeContext"),
    "RescueModeService": ("rescue_mode_service", "RescueModeService"),
    "SystemLogService": ("system_log_service", "SystemLogService"),
    "SystemStatus": ("system_status_service", "SystemStatus"),
    "SystemStatusService": (
        "system_status_service",
        "SystemStatusService",
    ),
    "TrafficSample": ("traffic_service", "TrafficSample"),
    "TrafficService": ("traffic_service", "TrafficService"),
    "UsbService": ("usb_service", "UsbService"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    export = _EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = export
    value = getattr(
        import_module(f"{__name__}.{module_name}"),
        attribute_name,
    )
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))
