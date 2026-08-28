from __future__ import annotations

from typing import Any

from core.services.wired_peer_tunnel_service import WiredPeerTunnelService


class WiredL2tpService(WiredPeerTunnelService):
    """L2TP-specific public facade over the shared peer-tunnel lifecycle."""

    def create_wired_l2tp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        return self._create_wired_peer_tunnel(
            settings,
            protocol_name="L2TP",
            protocol_slug="l2tp",
            default_mtu=1500,
            max_mtu=1500,
        )

    def delete_wired_l2tp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        return self._delete_wired_protocol(
            interface_id,
            protocol_name="L2TP",
            protocol_slug="l2tp",
        )
