from __future__ import annotations

from typing import Any

from core.services.wired_peer_tunnel_service import WiredPeerTunnelService


class WiredPptpService(WiredPeerTunnelService):
    """PPTP-specific public facade over the shared peer-tunnel lifecycle."""

    def create_wired_pptp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        return self._create_wired_peer_tunnel(
            settings,
            protocol_name="PPTP",
            protocol_slug="pptp",
            default_mtu=1400,
            max_mtu=1492,
        )

    def delete_wired_pptp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        return self._delete_wired_protocol(
            interface_id,
            protocol_name="PPTP",
            protocol_slug="pptp",
        )
