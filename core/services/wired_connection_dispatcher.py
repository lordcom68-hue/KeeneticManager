from __future__ import annotations

from typing import Any


class WiredConnectionDispatcher:
    """Route cable setup requests to a protocol-specific operation."""

    SUPPORTED_PROTOCOLS = frozenset(
        {"none", "ipoe", "pppoe", "pptp", "l2tp"}
    )

    def __init__(self, operations: Any) -> None:
        self.operations = operations

    @staticmethod
    def protocol_type(settings: dict[str, Any]) -> str:
        protocol = settings.get("protocol")
        if not isinstance(protocol, dict):
            return "none"
        value = str(protocol.get("type") or "none").strip().lower()
        return value or "none"

    @classmethod
    def connection_type(cls, settings: dict[str, Any]) -> str:
        return {
            "pppoe": "ethernet.pppoe",
            "pptp": "ethernet.pptp",
            "l2tp": "ethernet.l2tp",
        }.get(cls.protocol_type(settings), "ethernet.ipoe")

    def create(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        port = settings.get("port")
        if (
            isinstance(port, dict)
            and str(port.get("mode") or "").strip().lower()
            == "dedicated_wan"
        ):
            return self.operations.create_dedicated_cable_internet(settings)

        protocol_type = self.protocol_type(settings)
        if protocol_type in {"none", "ipoe"}:
            ipoe_settings = dict(settings)
            ipoe_settings.pop("protocol", None)
            return self.operations.create_wired_ipoe(ipoe_settings)
        if protocol_type == "pppoe":
            return self.operations.create_wired_pppoe(settings)
        if protocol_type == "pptp":
            return self.operations.create_wired_pptp(settings)
        if protocol_type == "l2tp":
            return self.operations.create_wired_l2tp(settings)
        return (
            False,
            (
                f"Создание протокола {protocol_type.upper()} "
                "пока не поддерживается."
            ),
            {"preflight_rejected": True},
        )

    @staticmethod
    def interface_settings(
        settings: dict[str, Any],
        *,
        description: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "description": description,
            "mtu": settings.get("mtu", 1500),
            "ipv4": dict(settings.get("ipv4") or {"dhcp": True}),
            "use_provider_dns": bool(
                settings.get("use_provider_dns", True)
            ),
            "manual_dns": list(settings.get("manual_dns") or []),
            "mac": dict(settings.get("mac") or {"mode": "factory"}),
            "ping_check": dict(
                settings.get("ping_check") or {"mode": "disabled"}
            ),
        }
        if result["ipv4"].get("dhcp"):
            result["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )
        return result
