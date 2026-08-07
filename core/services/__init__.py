"""Service layer exports, loaded lazily to avoid import cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "DiagnosticsService": ("diagnostics_service", "DiagnosticsService"),
    "KeeneticAuthenticationError": (
        "errors",
        "KeeneticAuthenticationError",
    ),
    "KeeneticConnectionError": ("errors", "KeeneticConnectionError"),
    "KeeneticError": ("errors", "KeeneticError"),
    "RciTransport": ("rci_transport", "RciTransport"),
    "InterfaceService": ("interface_service", "InterfaceService"),
    "InterfaceSnapshot": ("interface_service", "InterfaceSnapshot"),
    "OperationResult": ("operation_result", "OperationResult"),
    "OperationState": ("operation_result", "OperationState"),
    "PortSnapshot": ("port_status_service", "PortSnapshot"),
    "PortStatusService": ("port_status_service", "PortStatusService"),
    "PingCheckService": ("ping_check_service", "PingCheckService"),
    "PingCheckSnapshot": ("ping_check_service", "PingCheckSnapshot"),
    "PriorityService": ("priority_service", "PriorityService"),
    "PrioritySnapshot": ("priority_service", "PrioritySnapshot"),
    "WiredInternetService": (
        "wired_internet_service",
        "WiredInternetService",
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
