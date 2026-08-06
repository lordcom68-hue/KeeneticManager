from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class RouterCapabilities:
    """Capabilities inferred from safe `show/version` data."""

    usb: bool = False
    storage: bool = False
    wireguard: bool = False
    openvpn: bool = False
    zerotier: bool = False
    mobile: bool = False
    smb: bool = False
    wifi: bool = False
    mws_component: bool = False
    logical_mws: bool = False
    legacy_wifi: bool = False
    wifi_management_mode: str = "monitoring"
    keeneticos_major: int = 0
    keeneticos_minor: int = 0

    @classmethod
    def from_version_data(
        cls,
        version_data: dict[str, Any] | None,
    ) -> "RouterCapabilities":
        ndw = (
            version_data.get("ndw", {})
            if isinstance(version_data, dict)
            else {}
        )

        if not isinstance(ndw, dict):
            ndw = {}

        features = _tokens(ndw.get("features"))
        components = _tokens(ndw.get("components"))
        all_tokens = features | components

        usb_markers = {
            "usb",
            "usb_2",
            "usb_3",
            "usb2",
            "usb3",
            "single_usb_port",
            "dual_usb_port",
            "usb_port",
            "usbmodem",
            "usblte",
        }

        has_usb = bool(all_tokens & usb_markers)
        release = str(
            version_data.get("release")
            if isinstance(version_data, dict)
            else ""
        ).strip()
        version_match = re.search(r"(?:^|\D)(\d+)(?:\.(\d+))?", release)
        major = int(version_match.group(1)) if version_match else 0
        minor = (
            int(version_match.group(2) or 0)
            if version_match
            else 0
        )
        wifi = bool(
            {
                "corewireless",
                "wireless",
                "wifi",
                "mws",
            }
            & all_tokens
        )
        mws_component = "mws" in all_tokens
        # The logical MWS RCI tree used by the application is exposed by
        # KeeneticOS 5.1+.  KeeneticOS 5.0 may advertise the mws component,
        # while show/{sc/,}mws/wlan is absent and logs an error for each probe.
        logical_mws = bool(
            mws_component
            and (major > 5 or (major == 5 and minor >= 1))
        )
        legacy_wifi = bool(wifi and major == 4)
        wifi_management_mode = (
            "mws"
            if logical_mws
            else "legacy"
            if legacy_wifi
            else "monitoring"
        )

        return cls(
            usb=has_usb,
            storage=has_usb and "storage" in components,
            wireguard=bool(
                {"wireguard", "wireguard-server"} & all_tokens
            ),
            openvpn="openvpn" in all_tokens,
            zerotier="zerotier" in all_tokens,
            mobile=bool(
                {"usbmodem", "usblte", "lte", "qmi"} & all_tokens
            ),
            smb=bool(
                {"smb", "tsmb", "cifs"} & all_tokens
            ),
            wifi=wifi,
            mws_component=mws_component,
            logical_mws=logical_mws,
            legacy_wifi=legacy_wifi,
            wifi_management_mode=wifi_management_mode,
            keeneticos_major=major,
            keeneticos_minor=minor,
        )


def _tokens(value: Any) -> set[str]:
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
