from __future__ import annotations

from typing import Any, Callable

from core.rci import RCI_ENDPOINTS


class RouterRefreshService:
    """Collect one cached read-only monitoring snapshot for a client."""

    CLIENT_REFRESH_INTERVAL_SECONDS = 5.0
    CONFIG_REFRESH_INTERVAL_SECONDS = 15.0
    USB_REFRESH_INTERVAL_SECONDS = 15.0

    def __init__(
        self,
        client: Any,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        self.client = client
        self.monotonic = monotonic
        self.cache: dict[str, Any] = {}
        self.cache_times: dict[str, float] = {}

    def clear(self) -> None:
        self.cache.clear()
        self.cache_times.clear()

    def supports_extended_monitoring(self) -> bool:
        """KeeneticOS 4 has no modern ping-check/hotspot RCI trees."""
        capabilities = getattr(self.client, "capabilities", None)
        try:
            major = int(
                getattr(capabilities, "keeneticos_major", 0) or 0
            )
        except (TypeError, ValueError):
            major = 0
        return major != 4

    def cached_value(
        self,
        key: str,
        *,
        now: float,
        interval_seconds: float,
        force: bool,
        loader: Callable[[], Any],
    ) -> Any:
        last_refresh = self.cache_times.get(key, 0.0)
        if (
            force
            or key not in self.cache
            or now - last_refresh >= interval_seconds
        ):
            self.cache[key] = loader()
            self.cache_times[key] = now
        return self.cache[key]

    def refresh(self, *, force_full: bool = False) -> Any:
        now = self.monotonic()
        extended_monitoring = self.supports_extended_monitoring()
        system_data = self.client.get_rci_data(
            endpoint=RCI_ENDPOINTS["system"].path,
            required=True,
        )
        internet_data = self.client.get_rci_data(
            endpoint=RCI_ENDPOINTS["internet_status"].path,
            required=False,
        )
        interface_data = self.client.interface_service.read_runtime()

        # Реальный порядок интернет-каналов хранится в ip.global.order.
        interface_config_data = self.cached_value(
            "interface_config",
            now=now,
            interval_seconds=self.CONFIG_REFRESH_INTERVAL_SECONDS,
            force=force_full,
            loader=self.client.interface_service.read_config,
        )
        if extended_monitoring:
            ping_check_config_data = self.cached_value(
                "ping_check_config",
                now=now,
                interval_seconds=self.CONFIG_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=self.client.ping_check_service.read_config,
            )
            ping_check_status_data = self.cached_value(
                "ping_check_status",
                now=now,
                interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=self.client.ping_check_service.read_status,
            )
        else:
            ping_check_config_data = {}
            ping_check_status_data = {}
        associations_data = self.cached_value(
            "associations",
            now=now,
            interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
            force=force_full,
            loader=lambda: self.client.get_rci_data(
                endpoint=RCI_ENDPOINTS["associations"].path,
                required=False,
            ),
        )
        if extended_monitoring:
            hotspot_data = self.cached_value(
                "hotspot",
                now=now,
                interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=lambda: self.client.get_rci_data(
                    endpoint=RCI_ENDPOINTS["hotspot"].path,
                    required=False,
                ),
            )
        else:
            hotspot_data = {}
        client_data_refreshed = bool(
            force_full
            or self.cache_times.get("associations") == now
            or (
                extended_monitoring
                and self.cache_times.get("hotspot") == now
            )
        )
        usb_data = self.cached_value(
            "usb",
            now=now,
            interval_seconds=self.USB_REFRESH_INTERVAL_SECONDS,
            force=force_full,
            loader=lambda: self.client.get_usb_data(
                system_data=system_data,
                interface_data=interface_data,
            ),
        )

        result = self.client.build_result(
            system_data=system_data,
            version_data=self.client.version_data,
            internet_data=internet_data,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
            ping_check_config_data=ping_check_config_data,
            ping_check_status_data=ping_check_status_data,
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            usb_data=usb_data,
            force_wifi_logical_refresh=(
                force_full
                or self.cache_times.get("interface_config") == now
            ),
            client_data_refreshed=client_data_refreshed,
        )
        result.connection_state = "connected"
        result.consecutive_failures = 0
        result.confirmed_offline = False
        result.transient_failure_suppressed = False
        result.last_result_message = result.message
        self.client._cache_successful_snapshot()
        return result
