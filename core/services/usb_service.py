from __future__ import annotations

from typing import Any

from core.rci import RCI_ENDPOINTS, RouterCapabilities


class UsbService:
    """Detect and read optional USB-related RCI sections safely."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.rci_endpoint: str | None = None

    def reset(self) -> None:
        self.rci_endpoint = None

    def get_data(
        self,
        *,
        system_data: dict[str, Any] | None = None,
        interface_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Read USB data only after show/version confirms USB support.

        The unused snapshot arguments remain part of the contract because
        RouterRefreshService passes them for all optional data loaders.
        """
        del system_data
        del interface_data

        if self.rci_endpoint == "":
            return {}

        capabilities = self.detect_capabilities_from_version()
        if not capabilities["supported"]:
            self.rci_endpoint = ""
            return {}

        if self.rci_endpoint is None:
            self.rci_endpoint = self.detect_rci_endpoint(
                allow_usb=capabilities["usb"],
                allow_storage=capabilities["storage"],
            )

        if not self.rci_endpoint:
            return {}

        data = self.client.get_rci_data(
            endpoint=self.rci_endpoint,
            required=False,
        )
        return data if isinstance(data, dict) else {}

    def detect_capabilities_from_version(self) -> dict[str, bool]:
        """Keep the legacy USB capability result shape."""
        self.client.capabilities = RouterCapabilities.from_version_data(
            self.client.version_data
        )
        return {
            "supported": self.client.capabilities.usb,
            "usb": self.client.capabilities.usb,
            "storage": self.client.capabilities.storage,
        }

    @staticmethod
    def split_capability_tokens(value: Any) -> set[str]:
        if isinstance(value, str):
            return {
                item.strip().lower()
                for item in value.split(",")
                if item.strip()
            }
        if isinstance(value, (list, tuple, set)):
            return {
                str(item).strip().lower()
                for item in value
                if str(item).strip()
            }
        return set()

    def detect_rci_endpoint(
        self,
        *,
        allow_usb: bool,
        allow_storage: bool,
    ) -> str:
        """Probe only endpoints advertised by show/version."""
        endpoints: list[str] = []
        if allow_usb:
            endpoints.append(RCI_ENDPOINTS["usb"].path)
        if allow_storage:
            endpoints.append(RCI_ENDPOINTS["storage"].path)

        for endpoint in endpoints:
            supported, _data = self.probe_optional_rci_endpoint(endpoint)
            if supported:
                return endpoint
        return ""

    def probe_optional_rci_endpoint(
        self,
        endpoint: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Probe one optional endpoint without making it required."""
        response = self.client.transport.request(
            method="GET",
            path=f"/rci/{endpoint}",
            endpoint=str(endpoint).strip().strip("/").lower(),
            required=False,
            timeout=self.client.timeout,
            allow_reauthentication=True,
        )
        if response is None:
            return False, {}

        try:
            data = response.json()
        except ValueError:
            return False, {}
        if not isinstance(data, dict):
            return False, {}

        error_text = self.client.extract_rci_error(data)
        if error_text:
            lowered = error_text.lower()
            if any(
                marker in lowered
                for marker in ("not found", "unknown", "unsupported")
            ):
                return False, {}
        return True, data
