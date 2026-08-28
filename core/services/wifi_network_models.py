from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WifiNetworkCapabilities:
    """Available radio bands and free access-point slots."""

    supported_bands: tuple[str, ...]
    available_slots: dict[str, tuple[str, ...]]
    physical_available_slots: dict[str, tuple[str, ...]]
    home_bridge_id: str
    mws_supported: bool = True
    legacy_supported: bool = False
    bandless_supported: bool = True
    management_mode: str = "monitoring"


@dataclass(frozen=True)
class WifiLogicalSnapshot:
    """Configuration and runtime maps for logical MWS networks."""

    config: dict[str, Any]
    runtime: dict[str, Any]

