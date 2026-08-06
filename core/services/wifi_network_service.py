from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass
from typing import Any

from core.rci.capabilities import RouterCapabilities
from core.wifi_engine import WifiEngine


@dataclass(frozen=True)
class WifiNetworkCapabilities:
    """Доступные радиодиапазоны и свободные точки доступа."""

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
    """Конфигурация и runtime-карта логических MWS-сетей."""

    config: dict[str, Any]
    runtime: dict[str, Any]


class WiFiNetworkService:
    """
    Чтение и управление домашними Wi‑Fi сетями Keenetic.

    Сервис отделяет от KeeneticClient:
    - сбор сетей и клиентов;
    - сохранение выключенных сетей;
    - расчёт активности клиентов;
    - создание, изменение и удаление точек доступа.
    """

    CONFIG_ENDPOINT = "show/sc/interface"
    RUNTIME_ENDPOINT = "show/interface"
    LOGICAL_CONFIG_QUERY = {
        "show": {"sc": {"mws": {"wlan": {}}}}
    }
    LOGICAL_RUNTIME_QUERY = {
        "show": {"mws": {"wlan": {}}}
    }
    LOGICAL_CACHE_TTL_SECONDS = 15.0
    LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 16
    LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.5
    LOGICAL_SAVED_VERIFY_ATTEMPTS = 5
    LOGICAL_SAVED_VERIFY_DELAY_SECONDS = 0.4

    BAND_TO_MASTER = {
        "2.4": "WifiMaster0",
        "5": "WifiMaster1",
        "6": "WifiMaster2",
    }
    BAND_TITLES = {
        "2.4": "2,4 ГГц",
        "5": "5 ГГц",
        "6": "6 ГГц",
    }
    BAND_TO_MWS = {
        "2.4": "0",
        "5": "1",
        "6": "2",
    }
    MWS_TO_BAND = {
        "0": "2.4",
        "1": "5",
        "2": "6",
    }
    FIRST_ADDITIONAL_SLOT_INDEX = 2

    def __init__(self, client: Any) -> None:
        self.client = client
        self.known_networks: dict[str, dict[str, Any]] = {}
        self.client_counters: dict[
            str,
            tuple[float, float, float, float, float],
        ] = {}
        self.client_inventory: list[dict[str, Any]] = []
        self.client_port_bindings: dict[str, dict[str, Any]] = {}
        self._logical_snapshot = WifiLogicalSnapshot(
            config={},
            runtime={},
        )
        self._logical_snapshot_time = 0.0
        self._logical_management_supported: bool | None = None
        self._logical_capability_signature: tuple[Any, ...] | None = None

    # ------------------------------------------------------------------
    # Read model
    # ------------------------------------------------------------------
    def build_networks(
        self,
        *,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
        associations_data: Any,
        hotspot_data: Any,
        force_logical_refresh: bool = True,
        client_data_refreshed: bool = True,
    ) -> list[dict[str, Any]]:
        combined = self._combined_interface_data(
            runtime=interface_data,
            config=interface_config_data,
        )
        engine = WifiEngine(
            interface_data=combined,
            associations_data=associations_data,
            hotspot_data=hotspot_data,
        )
        current = engine.build()
        self.update_client_activity(
            current,
            fresh_sample=client_data_refreshed,
        )
        current = self._enrich_network_metadata(
            current,
            interface_config_data,
        )
        # The MWS profile list is authoritative, but it changes much
        # less often than runtime counters. A forced read is performed
        # after actions, manual refreshes and on the slow config cadence.
        logical_snapshot = self.read_logical_snapshot(
            force=force_logical_refresh
        )
        current = self._reconcile_logical_networks(
            current,
            logical_snapshot,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
        )
        active_profiles = (
            set(logical_snapshot.config)
            if self._logical_management_supported is True
            else None
        )
        result = self.merge_known_networks(
            current,
            active_mws_profiles=active_profiles,
        )
        self.client_inventory = self._merge_client_port_bindings(
            engine.build_inventory(result),
            runtime=interface_data,
            config=(interface_config_data or {}),
        )
        return result

    def capabilities(
        self,
        *,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> WifiNetworkCapabilities:
        all_ids = {
            str(key)
            for key in set(interface_data) | set(interface_config_data)
        }
        supported: list[str] = []
        available: dict[str, tuple[str, ...]] = {}

        logical = self.read_logical_snapshot()
        reserved_interfaces = self._logical_mapped_interfaces(logical)

        for band, master in self.BAND_TO_MASTER.items():
            master_supported = any(
                interface_id == master
                or interface_id.startswith(master + "/")
                for interface_id in all_ids
            )
            if not master_supported:
                continue

            supported.append(band)
            inventory: list[tuple[int, str]] = []
            prefix = master + "/AccessPoint"

            for interface_id in all_ids:
                if not interface_id.startswith(prefix):
                    continue
                index = self._access_point_index(interface_id)
                if (
                    index is None
                    or index < self.FIRST_ADDITIONAL_SLOT_INDEX
                ):
                    continue
                inventory.append((index, interface_id))

            free_slots: list[str] = []
            for _index, interface_id in sorted(inventory):
                if interface_id.casefold() in reserved_interfaces:
                    continue

                config_item = interface_config_data.get(interface_id)
                if not isinstance(config_item, dict):
                    config_item = {}
                runtime_item = interface_data.get(interface_id)
                if not isinstance(runtime_item, dict):
                    runtime_item = {}

                configured_ssid = self._first_text(
                    config_item,
                    ("ssid", "network-name"),
                )
                runtime_ssid = self._first_text(
                    runtime_item,
                    ("ssid", "network-name"),
                )
                if (
                    self.valid_ssid(configured_ssid)
                    or self.valid_ssid(runtime_ssid)
                ):
                    continue

                free_slots.append(interface_id)

            # Never invent AccessPointN names. Keenetic exposes its real
            # slot inventory in show/interface and show/sc/interface.
            available[band] = tuple(free_slots)

        bridge = self._home_bridge_id(interface_config_data)
        physical_available = dict(available)
        occupied_bands = self._logical_occupied_bands(
            logical.config,
            segment=bridge,
        )
        for band in occupied_bands:
            if band in available:
                available[band] = ()
        return WifiNetworkCapabilities(
            supported_bands=tuple(supported),
            available_slots=available,
            physical_available_slots=physical_available,
            home_bridge_id=bridge,
            mws_supported=(
                self._logical_management_supported is True
            ),
            legacy_supported=(
                self._legacy_mode_supported() and bool(supported)
            ),
            bandless_supported=(
                self._logical_management_supported is True
            ),
            management_mode=self._wifi_management_mode(),
        )

    @classmethod
    def _logical_occupied_bands(
        cls,
        profiles: dict[str, Any],
        *,
        segment: str,
        exclude_profile_id: str = "",
    ) -> set[str]:
        occupied: set[str] = set()
        target_segment = str(segment or "").strip().casefold()
        excluded = str(exclude_profile_id or "").strip().casefold()
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            if excluded and str(profile_id).casefold() == excluded:
                continue
            bind = profile.get("bind")
            profile_segment = (
                str(bind.get("interface") or "").strip().casefold()
                if isinstance(bind, dict)
                else ""
            )
            if profile_segment != target_segment:
                continue
            occupied.update(cls._logical_profile_bands(profile))
        return occupied

    @classmethod
    def _logical_mapped_interfaces(
        cls,
        snapshot: WifiLogicalSnapshot,
    ) -> set[str]:
        result: set[str] = set()
        for interfaces in cls.configured_logical_profile_interfaces(
            snapshot
        ).values():
            for interface_id in interfaces:
                result.add(interface_id.casefold())
        return result

    def _combined_interface_data(
        self,
        *,
        runtime: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        combined = copy.deepcopy(runtime)

        for interface_id, config_item in config.items():
            if not isinstance(config_item, dict):
                continue
            if not self._is_access_point_id(str(interface_id)):
                continue
            if not self.valid_ssid(
                self._first_text(config_item, ("ssid", "network-name"))
            ):
                continue

            runtime_item = runtime.get(interface_id)
            if isinstance(runtime_item, dict):
                merged = self._deep_merge(config_item, runtime_item)
            else:
                merged = copy.deepcopy(config_item)
                merged.setdefault("id", str(interface_id))
                merged.setdefault("type", "AccessPoint")
                merged["state"] = "down"
                merged["link"] = "down"
                merged["summary"] = {
                    "layer": {"conf": "disabled"}
                }

            combined[str(interface_id)] = merged

        return combined

    @classmethod
    def _deep_merge(
        cls,
        base: dict[str, Any],
        overlay: dict[str, Any],
    ) -> dict[str, Any]:
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            if (
                isinstance(value, dict)
                and isinstance(result.get(key), dict)
            ):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _enrich_network_metadata(
        self,
        networks: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        for network in networks:
            if network.get("network_type") != "wifi":
                continue

            interfaces = self._network_interfaces(network)
            band_states: dict[str, dict[str, Any]] = {}
            protected = False

            for interface_id in interfaces:
                index = self._access_point_index(interface_id)
                if index is not None and index < 2:
                    protected = True

                band = self.band_from_interface(interface_id)
                config_item = config.get(interface_id)
                if not isinstance(config_item, dict):
                    config_item = {}

                state = self._find_band_state(network, interface_id, band)
                state.update(
                    {
                        "interface_id": interface_id,
                        "band": band,
                        "hidden": self._config_hidden(config_item),
                        "security_mode": self._config_security_mode(
                            config_item
                        ),
                    }
                )
                band_states[band] = state

            network["interfaces"] = interfaces
            network["band_states"] = band_states
            network["protected_system_network"] = protected
            network["deletable"] = bool(interfaces) and not protected
            network["editable"] = bool(interfaces)
            network["hidden"] = any(
                bool(item.get("hidden"))
                for item in band_states.values()
            )
            security_modes = {
                str(item.get("security_mode") or "")
                for item in band_states.values()
                if item.get("security_mode")
            }
            network["security_mode"] = (
                security_modes.pop()
                if len(security_modes) == 1
                else "mixed"
                if security_modes
                else "unknown"
            )

        return networks

    @staticmethod
    def _find_band_state(
        network: dict[str, Any],
        interface_id: str,
        band: str,
    ) -> dict[str, Any]:
        existing = network.get("band_states")
        if isinstance(existing, dict):
            raw = existing.get(band)
            if isinstance(raw, dict):
                return dict(raw)

        enabled = bool(network.get("enabled", True))
        online = bool(network.get("online", enabled))
        return {
            "interface_id": interface_id,
            "band": band,
            "enabled": enabled,
            "online": online,
            "channel": network.get("channel") or "—",
        }

    # ------------------------------------------------------------------
    # Logical MWS Wi-Fi profiles
    # ------------------------------------------------------------------
    def read_logical_snapshot(
        self,
        *,
        force: bool = False,
    ) -> WifiLogicalSnapshot:
        now = time.monotonic()
        if (
            not force
            and self._logical_snapshot_time > 0
            and now - self._logical_snapshot_time
            < self.LOGICAL_CACHE_TTL_SECONDS
        ):
            return WifiLogicalSnapshot(
                config=copy.deepcopy(
                    self._logical_snapshot.config
                ),
                runtime=copy.deepcopy(
                    self._logical_snapshot.runtime
                ),
            )

        if not self._session_ready():
            return WifiLogicalSnapshot(config={}, runtime={})

        declared_support = self._declared_logical_mws_support()
        if declared_support is False:
            self._logical_management_supported = False
            self._logical_snapshot = WifiLogicalSnapshot(
                config={},
                runtime={},
            )
            self._logical_snapshot_time = now
            return WifiLogicalSnapshot(config={}, runtime={})

        # A failed probe is remembered for the current router/firmware
        # profile.  Repeating an unsupported RCI request only pollutes the
        # router journal and cannot make the endpoint appear later.
        if self._logical_management_supported is False:
            self._logical_snapshot_time = now
            return WifiLogicalSnapshot(config={}, runtime={})

        query = getattr(self.client, "post_rci_query", None)
        if not callable(query):
            return WifiLogicalSnapshot(config={}, runtime={})

        config: dict[str, Any] = {}
        runtime: dict[str, Any] = {}

        success, data, _error = query(
            [
                copy.deepcopy(self.LOGICAL_CONFIG_QUERY),
                copy.deepcopy(self.LOGICAL_RUNTIME_QUERY),
            ],
            timeout=20.0,
        )
        if success and isinstance(data, list):
            self._logical_management_supported = True
            if data:
                config = self._extract_logical_config(data[0])
            if len(data) > 1:
                runtime = self._extract_logical_runtime(data[1])

        elif not success:
            # One combined probe is sufficient for an unknown/new firmware.
            # Do not retry both paths separately: that produced four identical
            # `not found` records during every refresh cycle.
            self._logical_management_supported = False

        snapshot = WifiLogicalSnapshot(
            config=config,
            runtime=runtime,
        )
        self._record_logical_snapshot(snapshot)
        self._logical_snapshot = WifiLogicalSnapshot(
            config=copy.deepcopy(config),
            runtime=copy.deepcopy(runtime),
        )
        self._logical_snapshot_time = now
        return snapshot

    def _invalidate_logical_snapshot(self) -> None:
        self._logical_snapshot = WifiLogicalSnapshot(
            config={},
            runtime={},
        )
        self._logical_snapshot_time = 0.0

    def _record_logical_snapshot(
        self,
        snapshot: WifiLogicalSnapshot,
    ) -> None:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_snapshot = getattr(diagnostics, "record_snapshot", None)
        if not callable(record_snapshot):
            return
        record_snapshot(
            "show/sc/mws/wlan",
            copy.deepcopy(snapshot.config),
        )
        record_snapshot(
            "show/mws/wlan",
            copy.deepcopy(snapshot.runtime),
        )

    def _record_logical_event(
        self,
        message: str,
        *,
        profile_id: str,
        requested_enabled: bool,
        verified: bool,
        verification_message: str,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_event = getattr(diagnostics, "record_event", None)
        if not callable(record_event):
            return
        record_event(
            "wifi_mws_toggle",
            message,
            endpoint="mws/wlan",
            details={
                "profile_id": str(profile_id),
                "requested_enabled": bool(requested_enabled),
                "verified": bool(verified),
                "verification_message": str(verification_message),
                **dict(extra_details or {}),
            },
        )

    def _record_network_update_event(
        self,
        message: str,
        *,
        profile_id: str,
        interfaces: list[str],
        preserved_interface_states: dict[str, bool],
        verified: bool,
        verification_message: str,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_event = getattr(diagnostics, "record_event", None)
        if not callable(record_event):
            return
        record_event(
            "wifi_mws_update",
            message,
            endpoint="mws/wlan",
            details={
                "profile_id": str(profile_id),
                "interfaces": list(interfaces),
                "preserved_interface_states": dict(
                    preserved_interface_states
                ),
                "verified": bool(verified),
                "verification_message": str(verification_message),
                **dict(extra_details or {}),
            },
        )

    @staticmethod
    def _extract_logical_config(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        show = data.get("show")
        sc = show.get("sc") if isinstance(show, dict) else None
        mws = sc.get("mws") if isinstance(sc, dict) else None
        wlan = mws.get("wlan") if isinstance(mws, dict) else None
        if not isinstance(wlan, dict):
            return {}
        return {
            str(profile_id): copy.deepcopy(profile)
            for profile_id, profile in wlan.items()
            if isinstance(profile, dict)
        }

    @staticmethod
    def _extract_logical_runtime(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        show = data.get("show")
        mws = show.get("mws") if isinstance(show, dict) else None
        wlan = mws.get("wlan") if isinstance(mws, dict) else None
        if not isinstance(wlan, dict):
            return {}
        return {
            str(profile_id): copy.deepcopy(profile)
            for profile_id, profile in wlan.items()
            if isinstance(profile, dict)
        }

    @classmethod
    def logical_profile_interfaces(
        cls,
        runtime: dict[str, Any],
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}

        for profile_id, profile in runtime.items():
            if not isinstance(profile, dict):
                continue

            interfaces: list[str] = []
            bands = profile.get("band")
            if isinstance(bands, dict):
                for band_data in bands.values():
                    if not isinstance(band_data, dict):
                        continue
                    access_points = band_data.get("access-point")
                    if not isinstance(access_points, dict):
                        continue
                    for interface_id in access_points:
                        text = str(interface_id or "").strip()
                        if (
                            cls._is_access_point_id(text)
                            and text not in interfaces
                        ):
                            interfaces.append(text)

            if interfaces:
                result[str(profile_id)] = interfaces

        return result

    @classmethod
    def configured_logical_profile_interfaces(
        cls,
        snapshot: WifiLogicalSnapshot,
    ) -> dict[str, list[str]]:
        """Возвращает только AP диапазонов, сохранённых в MWS config."""
        runtime_mappings = cls.logical_profile_interfaces(
            snapshot.runtime
        )
        result: dict[str, list[str]] = {}
        for profile_id, profile in snapshot.config.items():
            if not isinstance(profile, dict):
                continue
            allowed_bands = set(cls._logical_profile_bands(profile))
            if not allowed_bands:
                continue
            interfaces = [
                interface_id
                for interface_id in runtime_mappings.get(profile_id, [])
                if cls.band_from_interface(interface_id) in allowed_bands
            ]
            if interfaces:
                result[str(profile_id)] = interfaces
        return result

    @classmethod
    def match_logical_profile(
        cls,
        interface_ids: list[str],
        snapshot: WifiLogicalSnapshot,
    ) -> tuple[str, list[str]]:
        targets = {
            value.casefold()
            for value in cls._normalize_interfaces(interface_ids)
        }
        if not targets:
            return "", []

        best_profile = ""
        best_interfaces: list[str] = []
        best_score = (-1, -1, -10_000)

        for profile_id, values in (
            cls.configured_logical_profile_interfaces(snapshot).items()
        ):
            profile_interfaces = cls._normalize_interfaces(values)
            normalized = {
                value.casefold()
                for value in profile_interfaces
            }
            intersection = targets.intersection(normalized)
            if not intersection:
                continue

            score = (
                1 if targets == normalized else 0,
                len(intersection),
                -abs(len(normalized) - len(targets)),
            )
            if score > best_score:
                best_score = score
                best_profile = profile_id
                best_interfaces = profile_interfaces

        return best_profile, best_interfaces

    def _reconcile_logical_networks(
        self,
        networks: list[dict[str, Any]],
        snapshot: WifiLogicalSnapshot,
        *,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Build one card per saved MWS profile.

        Physical AccessPoint configuration may retain an old SSID after
        a band is removed or a logical profile is deleted. Therefore it
        is used only as supplementary information (clients, channel and
        security details), never as the list of logical networks/bands.
        """
        non_wifi = [
            network
            for network in networks
            if isinstance(network, dict)
            and network.get("network_type") != "wifi"
        ]
        physical = [
            copy.deepcopy(network)
            for network in networks
            if isinstance(network, dict)
            and network.get("network_type") == "wifi"
        ]

        if not snapshot.config:
            if self._logical_management_supported is True:
                # A successful MWS query with an empty profile list means
                # there are no logical Wi-Fi networks.  Physical AP config can
                # retain old SSIDs and must not be exposed as ghost networks.
                return non_wifi
            legacy_supported = self._legacy_mode_supported()
            for network in physical:
                network["mws_managed"] = False
                network["mws_profile_id"] = ""
                network["legacy_managed"] = legacy_supported
                protected = bool(
                    network.get("protected_system_network", False)
                )
                network["mws_band_editable"] = bool(
                    legacy_supported and not protected
                )
            return non_wifi + physical

        mapped_by_profile = (
            self.configured_logical_profile_interfaces(snapshot)
        )
        reconciled: list[dict[str, Any]] = list(non_wifi)

        for profile_id, raw_profile in snapshot.config.items():
            if not isinstance(raw_profile, dict):
                continue

            profile = copy.deepcopy(raw_profile)
            runtime_profile = snapshot.runtime.get(profile_id)
            if not isinstance(runtime_profile, dict):
                runtime_profile = {}

            ssid_data = profile.get("ssid")
            logical_ssid = (
                str(ssid_data.get("name") or "").strip()
                if isinstance(ssid_data, dict)
                else ""
            )
            mapped_interfaces = self._normalize_interfaces(
                mapped_by_profile.get(profile_id, [])
            )
            mapped_folded = {
                value.casefold() for value in mapped_interfaces
            }

            candidates: list[dict[str, Any]] = []
            for network in physical:
                interfaces = {
                    value.casefold()
                    for value in self._network_interfaces(network)
                }
                same_interface = bool(
                    mapped_folded.intersection(interfaces)
                )
                same_ssid = bool(
                    logical_ssid
                    and logical_ssid.casefold()
                    == str(network.get("ssid") or "")
                    .strip()
                    .casefold()
                )
                if same_interface or (not mapped_interfaces and same_ssid):
                    candidates.append(network)

            base = self._merge_physical_candidates(candidates)
            if not base:
                base = {
                    "network_type": "wifi",
                    "clients": [],
                    "client_count": 0,
                    "unregistered_count": 0,
                }

            band_states, exact_interfaces = self._logical_band_model(
                runtime_profile=runtime_profile,
                profile=profile,
                interface_data=interface_data,
                interface_config_data=interface_config_data,
                physical_network=base,
            )
            if (
                not exact_interfaces
                and self._logical_profile_bands(profile)
            ):
                exact_interfaces = mapped_interfaces

            saved_enabled = self._logical_saved_enabled(profile)
            mws_runtime_enabled = any(
                bool(state.get("mws_enabled"))
                for state in band_states.values()
                if isinstance(state, dict)
            )
            physical_active = any(
                bool(state.get("online"))
                for state in band_states.values()
                if isinstance(state, dict)
            )
            # show/mws/wlan describes the manager's desired band state,
            # but a band can still be administratively down in
            # show/interface. The card's actual status must therefore
            # follow physical AccessPoint runtime. Keep the logical
            # switch state separately for diagnostics and recovery.
            logical_switch_enabled = (
                False
                if not self._logical_profile_bands(profile)
                else saved_enabled
                if saved_enabled is not None
                else physical_active
                if exact_interfaces
                else mws_runtime_enabled
                if band_states
                else False
            )
            actual_enabled = (
                physical_active
                if exact_interfaces
                else mws_runtime_enabled
                if band_states
                else bool(logical_switch_enabled)
            )
            enabled_band_count = sum(
                1
                for state in band_states.values()
                if isinstance(state, dict)
                and bool(state.get("online"))
            )
            # MWS owns the physical slot allocation.  A user-created profile
            # can legitimately receive AccessPoint0/1, so an AP index must not
            # make that logical profile undeletable.  Only the special router
            # default profile is protected at the logical layer.
            protected = profile_id == "DEFAULT__"

            base["network_type"] = "wifi"
            base["group"] = self._logical_group(profile, profile_id)
            base["ssid"] = logical_ssid or str(
                base.get("ssid") or profile_id
            )
            base["name"] = base["ssid"]
            base["interfaces"] = exact_interfaces
            base["interface_id"] = ", ".join(exact_interfaces)
            base["band_states"] = band_states
            base["band"] = ", ".join(
                self.BAND_TITLES.get(band, band)
                for band in band_states
            )
            base["enabled"] = bool(actual_enabled)
            base["online"] = bool(actual_enabled)
            base["all_enabled"] = bool(
                band_states
                and enabled_band_count == len(band_states)
            )
            base["partially_enabled"] = bool(
                band_states
                and 0 < enabled_band_count < len(band_states)
            )
            base["mws_managed"] = True
            base["mws_profile_id"] = str(profile_id)
            base["mws_profile_interfaces"] = exact_interfaces
            base["mws_profile_ssid"] = logical_ssid
            base["mws_profile_in_sync"] = True
            base["mws_enabled"] = bool(logical_switch_enabled)
            base["mws_saved_enabled"] = saved_enabled
            base["mws_runtime_active"] = mws_runtime_enabled
            base["physical_runtime_active"] = bool(physical_active)
            base["mws_state_mismatch"] = bool(
                bool(logical_switch_enabled) != bool(actual_enabled)
            )
            base["mws_requested_bands"] = list(band_states)
            base["mws_band_editable"] = bool(
                profile_id != "DEFAULT__"
            )
            base["hidden"] = (
                bool(ssid_data.get("hide"))
                if isinstance(ssid_data, dict)
                else False
            )
            base["security_mode"] = self._logical_security_mode(
                profile,
                str(base.get("security_mode") or "unknown"),
            )
            base["protected_system_network"] = protected
            base["deletable"] = not protected
            base["editable"] = True
            base["client_count"] = len(base.get("clients") or [])
            reconciled.append(base)

        return self.sort_networks(reconciled)

    def _logical_band_model(
        self,
        *,
        runtime_profile: dict[str, Any],
        profile: dict[str, Any],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
        physical_network: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        states: dict[str, dict[str, Any]] = {}
        interfaces: list[str] = []
        raw_bands = runtime_profile.get("band")
        configured_bands = set(
            self._logical_profile_bands(profile)
        )

        if isinstance(raw_bands, dict):
            for code, raw_state in raw_bands.items():
                if not isinstance(raw_state, dict):
                    continue
                configured_band = self.MWS_TO_BAND.get(str(code))
                if configured_band not in configured_bands:
                    # Runtime can retain a stale AP mapping after the saved
                    # MWS band was removed. Saved configuration is the
                    # authoritative membership list.
                    continue
                access_points = raw_state.get("access-point")
                ap_ids = (
                    self._normalize_interfaces(access_points.keys())
                    if isinstance(access_points, dict)
                    else []
                )
                grouped: dict[str, list[str]] = {}
                for interface_id in ap_ids:
                    band = self.band_from_interface(interface_id)
                    if band == "unknown":
                        band = self.MWS_TO_BAND.get(str(code), "unknown")
                    grouped.setdefault(band, []).append(interface_id)
                    if interface_id not in interfaces:
                        interfaces.append(interface_id)

                if not grouped:
                    band = self.MWS_TO_BAND.get(str(code), "unknown")
                    grouped[band] = []

                for band, band_interfaces in grouped.items():
                    template = self._physical_band_template(
                        physical_network,
                        band,
                        band_interfaces,
                    )
                    runtime_enabled = bool(raw_state.get("enabled", False))
                    physically_online = any(
                        self._interface_runtime_active(
                            interface_data.get(interface_id)
                        )
                        for interface_id in band_interfaces
                    )
                    template.update(
                        {
                            "band": band,
                            "interfaces": band_interfaces,
                            "interface_id": (
                                band_interfaces[0]
                                if band_interfaces
                                else ""
                            ),
                            # MWS runtime is the desired manager state;
                            # show/interface is the actual on-air state.
                            "mws_enabled": runtime_enabled,
                            "enabled": (
                                physically_online
                                if band_interfaces
                                else runtime_enabled
                            ),
                            "online": (
                                physically_online
                                if band_interfaces
                                else runtime_enabled
                            ),
                        }
                    )
                    states[band] = template

        if not states:
            for band in self._logical_profile_bands(profile):
                states[band] = {
                    "band": band,
                    "interfaces": [],
                    "interface_id": "",
                    "enabled": False,
                    "online": False,
                    "channel": "—",
                }

        ordered = {
            band: states[band]
            for band in ("2.4", "5", "6", "unknown")
            if band in states
        }
        for band, state in states.items():
            if band not in ordered:
                ordered[band] = state
        return ordered, interfaces

    @staticmethod
    def _merge_physical_candidates(
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not candidates:
            return {}
        base = copy.deepcopy(candidates[0])
        clients: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            raw_clients = candidate.get("clients")
            if not isinstance(raw_clients, list):
                continue
            for client in raw_clients:
                if not isinstance(client, dict):
                    continue
                key = str(client.get("mac") or repr(client)).casefold()
                if key in seen:
                    continue
                seen.add(key)
                clients.append(copy.deepcopy(client))
        base["clients"] = clients
        base["client_count"] = len(clients)
        base["unregistered_count"] = sum(
            1 for client in clients if not bool(client.get("registered", True))
        )
        return base

    @classmethod
    def _physical_band_template(
        cls,
        network: dict[str, Any],
        band: str,
        interfaces: list[str],
    ) -> dict[str, Any]:
        raw_states = network.get("band_states")
        if isinstance(raw_states, dict):
            state = raw_states.get(band)
            if isinstance(state, dict):
                return copy.deepcopy(state)
        return {"channel": network.get("channel") or "—"}

    @staticmethod
    def _interface_runtime_active(value: Any) -> bool:
        state = WiFiNetworkService._interface_runtime_state(value)
        return bool(state) if state is not None else False

    @staticmethod
    def _interface_runtime_state(value: Any) -> bool | None:
        if not isinstance(value, dict):
            return None
        state = str(value.get("state") or "").casefold()
        link = str(value.get("link") or "").casefold()
        if state in {"up", "running"} or link in {"up", "running"}:
            return True
        summary = value.get("summary")
        layer = summary.get("layer") if isinstance(summary, dict) else None
        if isinstance(layer, dict):
            values = {
                str(item).casefold()
                for item in layer.values()
            }
            if "running" in values:
                return True
            if "disabled" in values:
                return False
        if state in {"down", "disabled"} or link in {"down", "disabled"}:
            return False
        return None

    @staticmethod
    def _interface_config_state(value: Any) -> bool | None:
        if not isinstance(value, dict):
            return None
        if "up" in value:
            up = value.get("up")
            if isinstance(up, str):
                return up.strip().casefold() in {
                    "1",
                    "yes",
                    "true",
                    "on",
                    "enabled",
                    "up",
                }
            return bool(up)
        # In show/sc/interface Keenetic represents an administratively
        # disabled interface by omitting the `up` command entirely.
        return False

    @classmethod
    def _interface_observed_state(
        cls,
        runtime_value: Any,
        config_value: Any,
    ) -> bool | None:
        runtime_state = cls._interface_runtime_state(runtime_value)
        if runtime_state is not None:
            return runtime_state
        return cls._interface_config_state(config_value)

    @staticmethod
    def _logical_group(profile: dict[str, Any], profile_id: str) -> str:
        bind = profile.get("bind")
        bridge = (
            str(bind.get("interface") or "")
            if isinstance(bind, dict)
            else ""
        )
        if profile_id == "DEFAULT__" or bridge == "Bridge0":
            return "Основная сеть"
        if bridge == "Bridge1":
            return "Гостевая сеть"
        return "Другая Wi-Fi сеть"

    @staticmethod
    def _logical_security_mode(
        profile: dict[str, Any],
        fallback: str,
    ) -> str:
        encryption = str(profile.get("encryption") or "").casefold()
        if encryption in {"open", "none", "disabled"}:
            return "open"
        if encryption in {"wpa2+3", "wpa2+wpa3", "wpa2-3"}:
            return "WPA2-PSK + WPA3-PSK"
        if "wpa2" in encryption or isinstance(profile.get("wpa"), dict):
            return "wpa2"
        return fallback or "unknown"

    @classmethod
    def _logical_profile_bands(
        cls,
        profile: dict[str, Any],
    ) -> tuple[str, ...]:
        raw = profile.get("band")
        values = raw if isinstance(raw, list) else []
        result: list[str] = []
        for value in values:
            band = cls.MWS_TO_BAND.get(str(value))
            if band and band not in result:
                result.append(band)
        return tuple(result)

    @classmethod
    def _logical_band_states(
        cls,
        runtime_profile: dict[str, Any],
    ) -> dict[str, bool]:
        result: dict[str, bool] = {}
        bands = runtime_profile.get("band")
        if not isinstance(bands, dict):
            return result
        for code, data in bands.items():
            if not isinstance(data, dict):
                continue
            band = cls.MWS_TO_BAND.get(str(code))
            if band:
                result[band] = bool(data.get("enabled", False))
        return result

    @staticmethod
    def _logical_saved_enabled(
        profile: dict[str, Any],
    ) -> bool | None:
        explicit = profile.get("enable")
        return explicit if isinstance(explicit, bool) else None

    @classmethod
    def _logical_profile_enabled(
        cls,
        profile: dict[str, Any],
        runtime_profile: dict[str, Any],
    ) -> bool:
        # A profile without configured bands cannot be on air.  Runtime may
        # still expose stale band entries from its previous assignment.
        if not cls._logical_profile_bands(profile):
            return False
        # Runtime bands describe the actual on-air state. The saved
        # enable flag is only a fallback when runtime is unavailable:
        # Keenetic can save enable=true while legacy AP interfaces are
        # still administratively down.
        states = cls._logical_band_states(runtime_profile)
        if states:
            return any(states.values())
        explicit = cls._logical_saved_enabled(profile)
        if explicit is not None:
            return explicit
        return True

    # ------------------------------------------------------------------
    # Activity and disabled-network cache
    # ------------------------------------------------------------------
    def update_client_activity(
        self,
        networks: list[dict[str, Any]],
        *,
        fresh_sample: bool = True,
    ) -> None:
        traffic_service = self.client.traffic_service
        now = traffic_service.sample_time() if fresh_sample else 0.0
        seen_macs: set[str] = set()

        for network in networks:
            clients = network.get("clients")
            if not isinstance(clients, list):
                continue

            for client in clients:
                if not isinstance(client, dict):
                    continue

                mac = str(client.get("mac") or "").strip().lower()
                if not mac:
                    client["traffic_active"] = False
                    continue

                seen_macs.add(mac)
                rxbytes = traffic_service.first_optional_number(
                    client,
                    keys=("rxbytes",),
                )
                txbytes = traffic_service.first_optional_number(
                    client,
                    keys=("txbytes",),
                )
                previous = self.client_counters.get(mac)
                rx_bps = 0.0
                tx_bps = 0.0

                if fresh_sample:
                    rx_bps, tx_bps = (
                        traffic_service.calculate_counter_speeds(
                            interface_id=f"client:{mac}",
                            rxbytes=rxbytes,
                            txbytes=txbytes,
                            sampled_at=now,
                        )
                    )
                elif previous is not None:
                    rx_bps = previous[3]
                    tx_bps = previous[4]

                if fresh_sample:
                    self.client_counters[mac] = (
                        now,
                        float(rxbytes or 0.0),
                        float(txbytes or 0.0),
                        rx_bps,
                        tx_bps,
                    )
                client["rx_bps"] = rx_bps
                client["tx_bps"] = tx_bps
                client["to_client_bps"] = tx_bps
                client["from_client_bps"] = rx_bps
                client["traffic_active"] = (
                    rx_bps + tx_bps >= 512.0
                )

        if fresh_sample:
            for mac in set(self.client_counters) - seen_macs:
                self.client_counters.pop(mac, None)
                traffic_service.reset(f"client:{mac}")

    def _merge_client_port_bindings(
        self,
        clients: list[dict[str, Any]],
        *,
        runtime: dict[str, Any],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Keep a disabled physical port reachable from its last client."""
        active_ports: dict[str, str] = {}
        binding_fields = (
            "interface",
            "port_label",
            "connection_label",
            "port_speed",
            "duplex",
            "interface_enabled",
        )

        for client in clients:
            if not isinstance(client, dict):
                continue
            if (
                client.get("connection_kind") != "wired"
                or not bool(client.get("online", True))
            ):
                continue

            interface_id = str(client.get("interface") or "").strip()
            if not self._is_physical_ethernet_interface(interface_id):
                continue

            mac = str(client.get("mac") or "").strip().lower()
            if not mac:
                continue

            observed = self._physical_interface_admin_state(
                interface_id,
                runtime=runtime,
                config=config,
            )
            if observed is not None:
                client["interface_enabled"] = observed

            binding = {
                field: copy.deepcopy(client.get(field))
                for field in binding_fields
            }
            binding["interface"] = interface_id
            self.client_port_bindings[mac] = binding
            active_ports[interface_id.casefold()] = mac

        for client in clients:
            if not isinstance(client, dict):
                continue
            mac = str(client.get("mac") or "").strip().lower()
            binding = self.client_port_bindings.get(mac)
            if not mac or not isinstance(binding, dict):
                continue
            if client.get("connection_kind") == "wifi":
                continue

            interface_id = str(binding.get("interface") or "").strip()
            if not self._is_physical_ethernet_interface(interface_id):
                continue
            owner = active_ports.get(interface_id.casefold())
            if owner is not None and owner != mac:
                client["port_control_available"] = False
                continue

            client["connection_kind"] = "wired"
            for field in binding_fields:
                value = binding.get(field)
                if value not in (None, "", "-", "—"):
                    client[field] = copy.deepcopy(value)

            observed = self._physical_interface_admin_state(
                interface_id,
                runtime=runtime,
                config=config,
            )
            if observed is not None:
                client["interface_enabled"] = observed
                binding["interface_enabled"] = observed

            enabled = bool(client.get("interface_enabled", True))
            client["port_control_available"] = bool(
                client.get("online", True) or not enabled
            )

        return clients

    @staticmethod
    def _is_physical_ethernet_interface(value: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:gigabit|fast)?ethernet\d+(?:/\d+)?",
                str(value or "").strip(),
                re.IGNORECASE,
            )
        )

    @classmethod
    def _find_interface_record(
        cls,
        value: Any,
        interface_id: str,
    ) -> dict[str, Any] | None:
        target = str(interface_id or "").strip().casefold()
        if not target:
            return None

        if isinstance(value, dict):
            own_id = str(value.get("id") or "").strip().casefold()
            if own_id == target:
                return value
            for key, nested in value.items():
                if (
                    str(key).strip().casefold() == target
                    and isinstance(nested, dict)
                ):
                    return nested
                found = cls._find_interface_record(nested, interface_id)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_interface_record(nested, interface_id)
                if found is not None:
                    return found
        return None

    def _physical_interface_admin_state(
        self,
        interface_id: str,
        *,
        runtime: dict[str, Any],
        config: dict[str, Any],
    ) -> bool | None:
        manually_disabled = {
            str(value).strip().casefold()
            for value in getattr(
                self.client,
                "_manually_disabled_interfaces",
                set(),
            )
        }
        if interface_id.casefold() in manually_disabled:
            return False

        runtime_record = self._find_interface_record(runtime, interface_id)
        if runtime_record is not None:
            summary = runtime_record.get("summary")
            layer = summary.get("layer") if isinstance(summary, dict) else None
            conf = (
                str(layer.get("conf") or "").strip().casefold()
                if isinstance(layer, dict)
                else ""
            )
            state = str(runtime_record.get("state") or "").strip().casefold()
            if conf == "disabled" or state == "disabled":
                return False
            # link=down means only that the cable is absent. The port itself
            # remains administratively enabled unless conf says disabled.
            return True

        config_record = self._find_interface_record(config, interface_id)
        if config_record is None:
            return None
        return self._interface_config_state(config_record)

    def merge_known_networks(
        self,
        current_networks: list[dict[str, Any]],
        *,
        active_mws_profiles: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        current_keys: set[str] = set()

        for network in current_networks:
            if not isinstance(network, dict):
                continue
            if network.get("network_type") != "wifi":
                result.append(network)
                continue

            key = self.network_cache_key(network)
            if not key:
                result.append(network)
                continue

            interfaces = self._network_interfaces(network)
            self._forget_cached_interfaces(interfaces, keep_key=key)

            current_keys.add(key)
            cached = copy.deepcopy(network)
            cached["clients"] = []
            cached["client_count"] = 0
            cached["unregistered_count"] = 0
            self.known_networks[key] = cached
            result.append(network)

        active_folded = (
            {str(value).casefold() for value in active_mws_profiles}
            if active_mws_profiles is not None
            else None
        )
        for key, cached_network in list(self.known_networks.items()):
            if key in current_keys:
                continue

            profile_id = str(
                cached_network.get("mws_profile_id") or ""
            ).strip()
            if active_folded is not None and (
                not profile_id
                or profile_id.casefold() not in active_folded
            ):
                # In MWS mode only saved logical profiles are real networks.
                # Purge both removed profiles and legacy physical cards that
                # may have been cached during a transient empty response.
                self.known_networks.pop(key, None)
                continue

            restored = copy.deepcopy(cached_network)
            restored["enabled"] = False
            restored["online"] = False
            restored["clients"] = []
            restored["client_count"] = 0
            restored["unregistered_count"] = 0
            band_states = restored.get("band_states")
            if isinstance(band_states, dict):
                for state in band_states.values():
                    if isinstance(state, dict):
                        state["enabled"] = False
                        state["online"] = False
            result.append(restored)

        return self.sort_networks(result)

    @classmethod
    def network_cache_key(cls, network: dict[str, Any]) -> str:
        profile_id = str(network.get("mws_profile_id") or "").strip()
        if profile_id:
            return "mws|" + profile_id.casefold()

        interfaces = sorted(
            interface_id.casefold()
            for interface_id in cls._network_interfaces(network)
        )
        if interfaces:
            return "interfaces|" + "|".join(interfaces)

        group = str(
            network.get("group") or "Wi-Fi сеть"
        ).strip().casefold()
        ssid = str(network.get("ssid") or "").strip().casefold()
        return f"fallback|{group}|{ssid}" if ssid else ""

    def _forget_cached_interfaces(
        self,
        interface_ids: list[str],
        *,
        keep_key: str = "",
    ) -> None:
        targets = {
            interface_id.casefold()
            for interface_id in self._normalize_interfaces(interface_ids)
        }
        if not targets:
            return

        for key, cached_network in list(self.known_networks.items()):
            if key == keep_key:
                continue
            cached_interfaces = {
                interface_id.casefold()
                for interface_id in self._network_interfaces(cached_network)
            }
            if targets.intersection(cached_interfaces):
                self.known_networks.pop(key, None)

    @staticmethod
    def sort_networks(
        networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        group_order = {
            "Основная сеть": 0,
            "Гостевая сеть": 1,
            "Проводные подключения": 2,
            "Другая Wi-Fi сеть": 3,
        }
        return sorted(
            networks,
            key=lambda network: (
                group_order.get(str(network.get("group") or ""), 9),
                str(
                    network.get("ssid")
                    or network.get("name")
                    or ""
                ).lower(),
            ),
        )

    # ------------------------------------------------------------------
    # Validation and command planning
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_bands(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []

        result: list[str] = []
        for item in values:
            text = str(item or "").strip().lower().replace(",", ".")
            normalized = ""
            if text in {"2.4", "2.4ghz", "2.4 ггц", "2"}:
                normalized = "2.4"
            elif text in {"5", "5ghz", "5 ггц"}:
                normalized = "5"
            elif text in {"6", "6ghz", "6 ггц"}:
                normalized = "6"
            if normalized and normalized not in result:
                result.append(normalized)
        return tuple(result)

    @staticmethod
    def valid_ssid(value: Any) -> bool:
        text = str(value or "").strip()
        return bool(text) and len(text.encode("utf-8")) <= 32 and "\n" not in text

    @staticmethod
    def valid_password(value: Any, *, required: bool = True) -> bool:
        text = str(value or "")
        if not text:
            return not required
        if len(text) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", text):
            return True
        return 8 <= len(text) <= 63 and "\n" not in text

    def create_network(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if not self._session_ready():
            return False, "Нет активной сессии с роутером.", {}

        ssid = str(settings.get("ssid") or "").strip()
        bands = self.normalize_bands(settings.get("bands"))
        security = str(settings.get("security") or "wpa2").strip().lower()
        password = str(settings.get("password") or "")
        hidden = bool(settings.get("hidden", False))
        enabled = bool(settings.get("enabled", True)) and bool(bands)

        validation_error = self._validate_settings(
            ssid=ssid,
            bands=bands,
            security=security,
            password=password,
            password_required=(security != "open"),
            allow_empty_bands=True,
        )
        if validation_error:
            return False, validation_error, {"preflight_rejected": True}

        runtime = self.client.interface_service.read_runtime(required=False)
        config = self.client.interface_service.read_config(required=False)
        logical = self.read_logical_snapshot(force=True)
        capabilities = self.capabilities(
            interface_data=runtime,
            interface_config_data=config,
        )
        if self._logical_management_supported is False:
            if not capabilities.legacy_supported:
                return (
                    False,
                    "Этот роутер не предоставляет поддерживаемый "
                    "способ создания Wi-Fi сетей.",
                    {
                        "preflight_rejected": True,
                        "mws_unsupported": True,
                    },
                )
            return self._create_legacy_network(
                ssid=ssid,
                bands=bands,
                security=security,
                password=password,
                hidden=hidden,
                enabled=enabled,
                runtime=runtime,
                config=config,
                capabilities=capabilities,
            )

        unsupported = [
            band for band in bands
            if band not in capabilities.supported_bands
        ]
        if unsupported:
            return (
                False,
                "Роутер не поддерживает выбранные диапазоны: "
                + ", ".join(unsupported),
                {"preflight_rejected": True},
            )

        duplicate_interfaces = self._find_network_by_ssid(config, ssid)
        duplicate_profile = self._find_logical_profile_by_ssid(
            logical.config,
            ssid,
        )
        if duplicate_interfaces or duplicate_profile:
            return (
                False,
                f"Сеть с SSID «{ssid}» уже существует.",
                {
                    "preflight_rejected": True,
                    "interfaces": duplicate_interfaces,
                    "mws_profile_id": duplicate_profile,
                },
            )

        expected_interfaces = self._select_free_interfaces(
            bands=bands,
            available=capabilities.available_slots,
        )
        if len(expected_interfaces) != len(bands):
            return (
                False,
                "Нет свободной точки доступа для одного из диапазонов.",
                {
                    "preflight_rejected": True,
                    "available_slots": {
                        band: list(values)
                        for band, values
                        in capabilities.available_slots.items()
                    },
                },
            )

        profile_id = self._next_logical_profile_id(logical)
        commands = [
            self._build_logical_create_command(
                profile_id=profile_id,
                bridge_id=capabilities.home_bridge_id,
                bands=bands,
                ssid=ssid,
                password=password,
                security=security,
                hidden=hidden,
                enabled=enabled,
            ),
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось создать Wi-Fi сеть",
        )
        self._invalidate_logical_snapshot()
        if not success:
            rollback_ok, rollback_message = self._rollback_created_profile(
                profile_id
            )
            return (
                False,
                message,
                {
                    "mws_profile_id": profile_id,
                    "rolled_back": rollback_ok,
                    "rollback_message": rollback_message,
                },
            )

        verified, verify_message, actual_interfaces = (
            self.verify_logical_network(
                profile_id=profile_id,
                expected_ssid=ssid,
                expected_bands=bands,
                expected_enabled=enabled,
            )
        )
        if not verified:
            rollback_ok, rollback_message = self._rollback_created_profile(
                profile_id
            )
            return (
                False,
                "Проверка созданной сети не пройдена: "
                + verify_message
                + (
                    " Изменения автоматически отменены."
                    if rollback_ok
                    else " Автоматический откат не подтверждён: "
                    + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "expected_interfaces": expected_interfaces,
                    "interfaces": actual_interfaces,
                    "rolled_back": rollback_ok,
                },
            )

        self._forget_cached_interfaces(actual_interfaces)
        return (
            True,
            f"Wi-Fi сеть «{ssid}» создана.",
            {
                "mws_profile_id": profile_id,
                "interfaces": actual_interfaces,
                "expected_interfaces": expected_interfaces,
                "bands": list(bands),
                "mws_managed": True,
            },
        )

    def update_network(
        self,
        interface_ids: list[str],
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        interfaces = self._normalize_interfaces(interface_ids)
        requested_profile_id = str(
            settings.get("mws_profile_id") or ""
        ).strip()
        if not interfaces and not requested_profile_id:
            return False, "Не указаны интерфейсы Wi-Fi сети.", {}
        if not self._session_ready():
            return False, "Нет активной сессии с роутером.", {}

        ssid = str(settings.get("ssid") or "").strip()
        security = str(
            settings.get("security") or "wpa2"
        ).strip().lower()
        password = str(settings.get("password") or "")
        hidden = bool(settings.get("hidden", False))
        bands_provided = "bands" in settings
        requested_bands = self.normalize_bands(
            settings.get("bands")
        )
        existing_interface_bands = tuple(
            band
            for band in (
                self.band_from_interface(item)
                for item in interfaces
            )
            if band != "unknown"
        )
        change_password = bool(
            settings.get("change_password", bool(password))
        )
        change_security = bool(
            settings.get("change_security", False)
        )

        validation_error = self._validate_settings(
            ssid=ssid,
            bands=(
                requested_bands
                if bands_provided
                else existing_interface_bands
            ),
            security=security,
            password=password,
            password_required=(
                security != "open" and change_password
            ),
            allow_empty_bands=bands_provided,
        )
        if validation_error:
            return (
                False,
                validation_error,
                {"preflight_rejected": True},
            )

        config = self.client.interface_service.read_config(
            required=False
        )
        runtime = self.client.interface_service.read_runtime(
            required=False
        )
        if not isinstance(runtime, dict):
            runtime = {}
        missing_interfaces = [
            interface_id
            for interface_id in interfaces
            if not isinstance(config.get(interface_id), dict)
            or not self.valid_ssid(
                self._first_text(
                    config.get(interface_id) or {},
                    ("ssid", "network-name"),
                )
            )
        ]
        if missing_interfaces:
            return (
                False,
                "Интерфейс редактируемой сети больше не найден. "
                "Обновите данные роутера и повторите попытку.",
                {
                    "preflight_rejected": True,
                    "missing_interfaces": missing_interfaces,
                },
            )

        old_ssids = {
            self._first_text(
                config.get(interface_id) or {},
                ("ssid", "network-name"),
            )
            for interface_id in interfaces
        }
        old_ssids.discard("")
        old_ssid = (
            next(iter(old_ssids))
            if len(old_ssids) == 1
            else ""
        )

        logical_snapshot = self.read_logical_snapshot(force=True)
        if requested_profile_id and isinstance(
            logical_snapshot.config.get(requested_profile_id),
            dict,
        ):
            profile_id = requested_profile_id
            profile_interfaces = (
                self.configured_logical_profile_interfaces(
                    logical_snapshot
                ).get(profile_id, interfaces)
            )
        else:
            profile_id, profile_interfaces = self.match_logical_profile(
                interfaces,
                logical_snapshot,
            )
        same_profile_runtime_interfaces = (
            self.logical_profile_interfaces(
                logical_snapshot.runtime
            ).get(profile_id, [])
            if profile_id
            else []
        )
        own_interface_keys = {
            interface_id.casefold()
            for interface_id in self._normalize_interfaces(
                [*interfaces, *same_profile_runtime_interfaces]
            )
        }
        duplicates = [
            interface_id
            for interface_id in self._find_network_by_ssid(config, ssid)
            if interface_id.casefold() not in own_interface_keys
        ]
        duplicate_profiles = [
            other_profile_id
            for other_profile_id in self._logical_profiles_by_ssid(
                logical_snapshot.config,
                ssid,
            )
            if other_profile_id.casefold() != profile_id.casefold()
        ]
        if duplicates or duplicate_profiles:
            return (
                False,
                f"Сеть с SSID «{ssid}» уже существует.",
                {
                    "preflight_rejected": True,
                    "interfaces": duplicates,
                    "mws_profile_ids": duplicate_profiles,
                },
            )
        profile_config = logical_snapshot.config.get(profile_id)
        if not isinstance(profile_config, dict):
            profile_config = {}
        if not old_ssid:
            ssid_data = profile_config.get("ssid")
            if isinstance(ssid_data, dict):
                old_ssid = str(ssid_data.get("name") or "").strip()
        ssid_changed = bool(old_ssid and old_ssid != ssid)
        configured_profile_bands = self._logical_profile_bands(
            profile_config
        )
        current_bands = (
            configured_profile_bands
            if profile_id
            else tuple(
                dict.fromkeys(
                    band
                    for band in (
                        self.band_from_interface(item)
                        for item in interfaces
                    )
                    if band != "unknown"
                )
            )
        )
        effective_bands = (
            requested_bands
            if bands_provided
            else current_bands
        )
        bands_changed = bool(
            bands_provided
            and set(effective_bands) != set(current_bands)
        )

        legacy_mode = bool(
            self._legacy_mode_supported() and not profile_id
        )
        if legacy_mode:
            if not effective_bands:
                return (
                    False,
                    "Для KeeneticOS 4 выберите хотя бы один диапазон Wi-Fi.",
                    {
                        "preflight_rejected": True,
                        "band_missing": True,
                        "legacy_managed": True,
                    },
                )
            return self._update_legacy_network(
                interfaces=interfaces,
                current_bands=current_bands,
                requested_bands=effective_bands,
                ssid=ssid,
                security=security,
                password=password,
                hidden=hidden,
                change_password=change_password,
                change_security=change_security,
                config=config,
                runtime=runtime,
            )

        if bands_changed and not profile_id:
            return (
                False,
                "Изменение диапазонов доступно только для сети, "
                "которой управляет профиль MWS.",
                {
                    "preflight_rejected": True,
                    "logical_profile_missing": True,
                    "interfaces": interfaces,
                },
            )

        if bands_changed:
            capabilities = self.capabilities(
                interface_data=runtime,
                interface_config_data=config,
            )
            bind = profile_config.get("bind")
            profile_segment = (
                str(bind.get("interface") or "").strip()
                if isinstance(bind, dict)
                else ""
            ) or capabilities.home_bridge_id
            occupied_by_other_profiles = (
                self._logical_occupied_bands(
                    logical_snapshot.config,
                    segment=profile_segment,
                    exclude_profile_id=profile_id,
                )
            )
            unsupported = [
                band
                for band in effective_bands
                if band not in capabilities.supported_bands
            ]
            unavailable = [
                band
                for band in effective_bands
                if band not in current_bands
                and (
                    band in occupied_by_other_profiles
                    or not capabilities.physical_available_slots.get(band)
                )
            ]
            if unsupported or unavailable:
                unavailable_titles = [
                    self.BAND_TITLES.get(band, band)
                    for band in (*unsupported, *unavailable)
                ]
                return (
                    False,
                    "Нет свободной точки доступа для диапазонов: "
                    + ", ".join(dict.fromkeys(unavailable_titles)),
                    {
                        "preflight_rejected": True,
                        "unsupported_bands": unsupported,
                        "unavailable_bands": unavailable,
                        "available_slots": {
                            band: list(values)
                            for band, values
                            in capabilities.physical_available_slots.items()
                        },
                        "occupied_bands": sorted(
                            occupied_by_other_profiles
                        ),
                    },
                )
        mapped_profiles = self.configured_logical_profile_interfaces(
            logical_snapshot
        )
        preserved_interfaces = self._normalize_interfaces(
            [
                *interfaces,
                *[
                    interface_id
                    for mapped_interfaces in mapped_profiles.values()
                    for interface_id in mapped_interfaces
                ],
            ]
        )

        protected_system = any(
            (self._access_point_index(interface_id) or 0) < 2
            for interface_id in interfaces
        )
        if ssid_changed and protected_system and not profile_id:
            return (
                False,
                "Логический профиль системной Wi-Fi сети не найден. "
                "Изменение остановлено, чтобы имя в веб-интерфейсе "
                "и в эфире не разошлось.",
                {
                    "preflight_rejected": True,
                    "logical_profile_missing": True,
                    "interfaces": interfaces,
                },
            )

        existing_encryption = str(
            profile_config.get("encryption") or ""
        ).strip().lower()
        if (
            profile_id
            and change_security
            and security == "open"
            and existing_encryption not in {
                "",
                "open",
                "none",
                "disabled",
            }
        ):
            return (
                False,
                "Переключение логической MWS-сети в открытый режим "
                "пока заблокировано: точная команда удаления WPA "
                "должна быть проверена отдельно.",
                {
                    "preflight_rejected": True,
                    "unsupported_logical_security_change": True,
                    "mws_profile_id": profile_id,
                },
            )

        previous_state = self._capture_update_state(
            config=config,
            runtime=runtime,
            interfaces=interfaces,
            preserved_interfaces=preserved_interfaces,
            profile_id=profile_id,
            profile_config=profile_config,
            profile_interfaces=profile_interfaces,
            change_password=change_password,
            change_security=change_security,
        )
        runtime_profile = logical_snapshot.runtime.get(profile_id)
        if not isinstance(runtime_profile, dict):
            runtime_profile = {}
        logical_enabled = self._logical_saved_enabled(profile_config)
        if logical_enabled is None:
            logical_enabled = self._logical_profile_enabled(
                profile_config,
                runtime_profile,
            )
        previous_state.update(
            {
                "bands_changed": bands_changed,
                "logical_bands": list(current_bands),
                "logical_enabled": bool(logical_enabled),
            }
        )

        if bands_changed:
            return self._update_network_bands(
                ssid=ssid,
                bands=effective_bands,
                security=security,
                password=password,
                hidden=hidden,
                change_password=change_password,
                change_security=change_security,
                profile_id=profile_id,
                profile_config=profile_config,
                profile_interfaces=profile_interfaces or interfaces,
                previous_state=previous_state,
            )

        commands: list[dict[str, Any]] = []
        if profile_id:
            commands.append(
                self._build_logical_update_command(
                    profile_id=profile_id,
                    profile_config=profile_config,
                    ssid=ssid,
                    hidden=hidden,
                    password=password,
                    security=security,
                    change_password=change_password,
                    change_security=change_security,
                )
            )

        for interface_id in interfaces:
            interface_arg = self.client.quote_cli_argument(
                interface_id
            )
            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} ssid "
                        + self.client.quote_cli_argument(ssid)
                    )
                }
            )
            if change_password or change_security:
                commands.extend(
                    self._security_commands(
                        interface_arg=interface_arg,
                        security=security,
                        password=password,
                        change_password=change_password,
                    )
                )
            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} hide-ssid"
                        if hidden
                        else (
                            f"interface {interface_arg} "
                            "no hide-ssid"
                        )
                    )
                }
            )

        preserved_states = previous_state.get(
            "preserved_interface_states"
        )
        if not isinstance(preserved_states, dict):
            preserved_states = {}
        for interface_id, was_enabled in preserved_states.items():
            if not isinstance(was_enabled, bool):
                continue
            interface_arg = self.client.quote_cli_argument(
                interface_id
            )
            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} up"
                        if was_enabled
                        else f"interface {interface_arg} no up"
                    )
                }
            )

        commands.append(
            {"system": {"configuration": {"save": {}}}}
        )

        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось изменить Wi-Fi сеть",
        )
        self._invalidate_logical_snapshot()
        if not success:
            self._record_network_update_event(
                "Не удалось изменить Wi-Fi сеть.",
                profile_id=profile_id,
                interfaces=interfaces,
                preserved_interface_states=preserved_states,
                verified=False,
                verification_message=message,
                extra_details={"batch_failed": True},
            )
            return (
                False,
                message,
                {
                    "interfaces": interfaces,
                    "mws_profile_id": profile_id,
                },
            )

        verified, verify_message = self.verify_network(
            interfaces=interfaces,
            expected_ssid=ssid,
            expected_enabled=None,
            expected_interface_states=preserved_states,
            logical_profile_id=profile_id,
        )
        if not verified:
            rollback_ok, rollback_message = (
                self._rollback_network_update(previous_state)
            )
            self._record_network_update_event(
                "Проверка изменения Wi-Fi сети не пройдена.",
                profile_id=profile_id,
                interfaces=interfaces,
                preserved_interface_states=preserved_states,
                verified=False,
                verification_message=verify_message,
                extra_details={
                    "rolled_back": rollback_ok,
                    "rollback_message": rollback_message,
                },
            )
            return (
                False,
                "Параметры отправлены, но проверка не пройдена: "
                + verify_message
                + (
                    " Изменения автоматически отменены."
                    if rollback_ok
                    else (
                        " Автоматический откат не подтверждён: "
                        + rollback_message
                    )
                ),
                {
                    "interfaces": interfaces,
                    "mws_profile_id": profile_id,
                    "rolled_back": rollback_ok,
                },
            )

        self._record_network_update_event(
            "Изменение Wi-Fi сети подтверждено.",
            profile_id=profile_id,
            interfaces=interfaces,
            preserved_interface_states=preserved_states,
            verified=True,
            verification_message=verify_message,
        )
        self._forget_cached_interfaces(interfaces)
        return (
            True,
            f"Wi-Fi сеть «{ssid}» изменена.",
            {
                "interfaces": interfaces,
                "mws_profile_id": profile_id,
                "logical_profile_updated": bool(profile_id),
                "physical_state_preserved": True,
                "preserved_interfaces": list(preserved_states),
            },
        )

    def _update_network_bands(
        self,
        *,
        ssid: str,
        bands: tuple[str, ...],
        security: str,
        password: str,
        hidden: bool,
        change_password: bool,
        change_security: bool,
        profile_id: str,
        profile_config: dict[str, Any],
        profile_interfaces: list[str],
        previous_state: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Переносит MWS-профиль между диапазонами с проверкой новых AP."""
        preserved_states = previous_state.get(
            "preserved_interface_states"
        )
        if not isinstance(preserved_states, dict):
            preserved_states = {}

        old_targets = self._normalize_interfaces(profile_interfaces)
        old_target_keys = {
            interface_id.casefold()
            for interface_id in old_targets
        }
        old_target_states_by_band: dict[str, bool] = {}
        for interface_id in old_targets:
            state = preserved_states.get(interface_id)
            band = self.band_from_interface(interface_id)
            if isinstance(state, bool) and band != "unknown":
                old_target_states_by_band[band] = state

        logical_enabled = bool(
            previous_state.get("logical_enabled", True)
        )
        resulting_enabled = logical_enabled and bool(bands)
        logical_command = self._build_logical_update_command(
            profile_id=profile_id,
            profile_config=profile_config,
            ssid=ssid,
            hidden=hidden,
            password=password,
            security=security,
            change_password=change_password,
            change_security=change_security,
            bands=bands,
        )
        # A profile saved without bands often has no explicit ``enable``
        # field.  KeeneticOS then treats it as enabled by default as soon as a
        # band is assigned.  Preserve the observed state explicitly so that a
        # dormant profile remains off until the user presses "Enable".
        logical_command["mws"]["wlan"]["enable"] = resulting_enabled
        success, message = self.client.execute_rci_batch(
            [
                logical_command,
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось изменить диапазоны Wi-Fi сети",
        )
        self._invalidate_logical_snapshot()
        if not success:
            rollback_ok, rollback_message = (
                self._rollback_network_update(previous_state)
            )
            return (
                False,
                message
                + (
                    " Исходные параметры восстановлены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": old_targets,
                    "rolled_back": rollback_ok,
                },
            )

        actual_interfaces: list[str] = []
        mapping_confirmed = False
        latest_logical = WifiLogicalSnapshot(config={}, runtime={})
        mapping_message = "профиль не вернул новые точки доступа"
        expected_band_set = set(bands)
        for _ in range(8):
            latest_logical = self.read_logical_snapshot(force=True)
            profile = latest_logical.config.get(profile_id)
            mapped = self.configured_logical_profile_interfaces(
                latest_logical
            ).get(profile_id, [])
            configured_bands = (
                set(self._logical_profile_bands(profile))
                if isinstance(profile, dict)
                else set()
            )
            mapped_bands = {
                self.band_from_interface(interface_id)
                for interface_id in mapped
            }
            mapped_bands.discard("unknown")
            if (
                configured_bands == expected_band_set
                and mapped_bands == expected_band_set
            ):
                actual_interfaces = self._normalize_interfaces(mapped)
                mapping_confirmed = True
                break
            mapping_message = (
                "сохранены диапазоны "
                f"{sorted(configured_bands)}, назначены "
                f"{sorted(mapped_bands)}"
            )
            time.sleep(0.35)

        if not mapping_confirmed:
            rollback_ok, rollback_message = (
                self._rollback_network_update(previous_state)
            )
            return (
                False,
                "Роутер не подтвердил новые диапазоны: "
                + mapping_message
                + (
                    " Исходные параметры восстановлены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": old_targets,
                    "rolled_back": rollback_ok,
                },
            )

        target_states: dict[str, bool] = {}
        repair_commands: list[dict[str, Any]] = []
        for interface_id in actual_interfaces:
            interface_arg = self.client.quote_cli_argument(interface_id)
            band = self.band_from_interface(interface_id)
            desired_state = old_target_states_by_band.get(
                band,
                resulting_enabled,
            )
            target_states[interface_id] = bool(desired_state)
            repair_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} ssid "
                        + self.client.quote_cli_argument(ssid)
                    )
                }
            )
            if change_password or change_security:
                repair_commands.extend(
                    self._security_commands(
                        interface_arg=interface_arg,
                        security=security,
                        password=password,
                        change_password=change_password,
                    )
                )
            repair_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} hide-ssid"
                        if hidden
                        else f"interface {interface_arg} no hide-ssid"
                    )
                }
            )
            repair_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} up"
                        if desired_state
                        else f"interface {interface_arg} no up"
                    )
                }
            )

        neighbour_states = {
            interface_id: bool(state)
            for interface_id, state in preserved_states.items()
            if isinstance(state, bool)
            and interface_id.casefold() not in old_target_keys
        }
        actual_target_keys = {
            interface_id.casefold()
            for interface_id in actual_interfaces
        }
        for interface_id, was_enabled in neighbour_states.items():
            if interface_id.casefold() in actual_target_keys:
                continue
            interface_arg = self.client.quote_cli_argument(interface_id)
            repair_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} up"
                        if was_enabled
                        else f"interface {interface_arg} no up"
                    )
                }
            )

        repair_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            repair_commands,
            operation_name="Не удалось закрепить новые диапазоны Wi-Fi сети",
        )
        self._invalidate_logical_snapshot()
        if not success:
            rollback_ok, rollback_message = (
                self._rollback_network_update(previous_state)
            )
            return (
                False,
                message
                + (
                    " Исходные параметры восстановлены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": actual_interfaces,
                    "rolled_back": rollback_ok,
                },
            )

        logical_ok, logical_message, verified_interfaces = (
            self.verify_logical_network(
                profile_id=profile_id,
                expected_ssid=ssid,
                expected_bands=bands,
                expected_enabled=resulting_enabled,
            )
        )
        final_interfaces = (
            verified_interfaces or actual_interfaces
        )
        expected_states = {
            **neighbour_states,
            **{
                interface_id: target_states.get(
                    interface_id,
                    resulting_enabled,
                )
                for interface_id in final_interfaces
            },
        }
        physical_ok, physical_message = self.verify_network(
            interfaces=final_interfaces,
            expected_ssid=ssid,
            expected_enabled=None,
            expected_interface_states=expected_states,
            logical_profile_id=(
                profile_id if final_interfaces else ""
            ),
        )
        if not logical_ok or not physical_ok:
            verify_message = "; ".join(
                item
                for item in (logical_message, physical_message)
                if item
            )
            rollback_ok, rollback_message = (
                self._rollback_network_update(previous_state)
            )
            return (
                False,
                "Проверка новых диапазонов не пройдена: "
                + verify_message
                + (
                    " Исходные параметры восстановлены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": final_interfaces,
                    "rolled_back": rollback_ok,
                },
            )

        self._record_network_update_event(
            "Изменение диапазонов Wi-Fi сети подтверждено.",
            profile_id=profile_id,
            interfaces=final_interfaces,
            preserved_interface_states=expected_states,
            verified=True,
            verification_message=(
                logical_message + " " + physical_message
            ).strip(),
            extra_details={"bands": list(bands)},
        )
        self._forget_cached_interfaces(
            [*old_targets, *final_interfaces]
        )
        return (
            True,
            f"Wi-Fi сеть «{ssid}» изменена.",
            {
                "interfaces": final_interfaces,
                "previous_interfaces": old_targets,
                "mws_profile_id": profile_id,
                "bands": list(bands),
                "logical_profile_updated": True,
                "physical_state_preserved": True,
            },
        )

    def _create_legacy_network(
        self,
        *,
        ssid: str,
        bands: tuple[str, ...],
        security: str,
        password: str,
        hidden: bool,
        enabled: bool,
        runtime: dict[str, Any],
        config: dict[str, Any],
        capabilities: WifiNetworkCapabilities,
    ) -> tuple[bool, str, dict[str, Any]]:
        if not bands:
            return (
                False,
                "Для KeeneticOS 4 выберите хотя бы один диапазон Wi-Fi.",
                {
                    "preflight_rejected": True,
                    "band_missing": True,
                    "legacy_managed": True,
                },
            )
        unsupported = [
            band for band in bands
            if band not in capabilities.supported_bands
        ]
        if unsupported:
            return (
                False,
                "Роутер не поддерживает выбранные диапазоны: "
                + ", ".join(unsupported),
                {"preflight_rejected": True, "legacy_managed": True},
            )
        duplicates = self._find_network_by_ssid(config, ssid)
        if duplicates:
            return (
                False,
                f"Сеть с SSID «{ssid}» уже существует.",
                {
                    "preflight_rejected": True,
                    "interfaces": duplicates,
                    "legacy_managed": True,
                },
            )
        interfaces = self._select_free_interfaces(
            bands=bands,
            available=capabilities.physical_available_slots,
        )
        if len(interfaces) != len(bands):
            return (
                False,
                "Нет свободной физической точки доступа для одного "
                "из выбранных диапазонов.",
                {
                    "preflight_rejected": True,
                    "legacy_managed": True,
                    "available_slots": {
                        band: list(values)
                        for band, values
                        in capabilities.physical_available_slots.items()
                    },
                },
            )

        bridge_id = capabilities.home_bridge_id
        target_by_band = {
            self.band_from_interface(interface_id): interface_id
            for interface_id in interfaces
        }
        follow_target = target_by_band.get("2.4", "")
        commands: list[dict[str, Any]] = []
        for interface_id in interfaces:
            band = self.band_from_interface(interface_id)
            commands.extend(
                self._legacy_configure_interface_commands(
                    interface_id=interface_id,
                    ssid=ssid,
                    security=security,
                    password=password,
                    hidden=hidden,
                    enabled=enabled,
                    change_password=(security != "open"),
                    follow_target=(
                        follow_target if band == "5" else ""
                    ),
                )
            )
            commands.append(
                self._legacy_bridge_command(
                    bridge_id,
                    interface_id,
                    include=True,
                )
            )
        commands.append({"system": {"configuration": {"save": {}}}})

        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось создать legacy Wi-Fi сеть",
        )
        if not success:
            self._cleanup_legacy_interfaces(
                interfaces,
                config=config,
                operation_name="Откат создания legacy Wi-Fi сети",
            )
            return False, message, {
                "interfaces": interfaces,
                "legacy_managed": True,
            }

        verified, verify_message = self.verify_network(
            interfaces=interfaces,
            expected_ssid=ssid,
            expected_enabled=enabled,
        )
        if not verified:
            rollback_ok, rollback_message = self._cleanup_legacy_interfaces(
                interfaces,
                config=config,
                operation_name="Откат создания legacy Wi-Fi сети",
            )
            return (
                False,
                "Проверка созданной legacy-сети не пройдена: "
                + verify_message
                + (
                    " Изменения отменены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "interfaces": interfaces,
                    "legacy_managed": True,
                    "rolled_back": rollback_ok,
                },
            )

        self._forget_cached_interfaces(interfaces)
        return (
            True,
            f"Wi-Fi сеть «{ssid}» создана в legacy-режиме.",
            {
                "interfaces": interfaces,
                "bands": list(bands),
                "legacy_managed": True,
                "mws_managed": False,
            },
        )

    def _update_legacy_network(
        self,
        *,
        interfaces: list[str],
        current_bands: tuple[str, ...],
        requested_bands: tuple[str, ...],
        ssid: str,
        security: str,
        password: str,
        hidden: bool,
        change_password: bool,
        change_security: bool,
        config: dict[str, Any],
        runtime: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        protected = any(
            (self._access_point_index(interface_id) or 0) < 2
            for interface_id in interfaces
        )
        bands_changed = set(current_bands) != set(requested_bands)
        if protected and bands_changed:
            return (
                False,
                "У системной сети KeeneticOS 4 можно изменить имя, "
                "безопасность и состояние, но не состав диапазонов.",
                {
                    "preflight_rejected": True,
                    "protected": interfaces,
                    "legacy_managed": True,
                },
            )

        capabilities = self.capabilities(
            interface_data=runtime,
            interface_config_data=config,
        )
        unsupported = [
            band for band in requested_bands
            if band not in capabilities.supported_bands
        ]
        if unsupported:
            return (
                False,
                "Роутер не поддерживает выбранные диапазоны: "
                + ", ".join(unsupported),
                {"preflight_rejected": True, "legacy_managed": True},
            )

        current_by_band: dict[str, str] = {}
        for interface_id in interfaces:
            band = self.band_from_interface(interface_id)
            if band != "unknown" and band not in current_by_band:
                current_by_band[band] = interface_id

        target_by_band: dict[str, str] = {}
        preferred_indices = {
            index
            for index in (
                self._access_point_index(interface_id)
                for interface_id in interfaces
            )
            if index is not None
        }
        for band in requested_bands:
            existing = current_by_band.get(band)
            if existing:
                target_by_band[band] = existing
                continue
            candidates = list(
                capabilities.physical_available_slots.get(band, ())
            )
            preferred = next(
                (
                    candidate
                    for candidate in candidates
                    if self._access_point_index(candidate)
                    in preferred_indices
                ),
                "",
            )
            selected = preferred or (candidates[0] if candidates else "")
            if not selected:
                return (
                    False,
                    "Нет свободной физической точки доступа для "
                    f"диапазона {self.BAND_TITLES.get(band, band)}.",
                    {
                        "preflight_rejected": True,
                        "legacy_managed": True,
                        "unavailable_band": band,
                    },
                )
            target_by_band[band] = selected
            index = self._access_point_index(selected)
            if index is not None:
                preferred_indices.add(index)

        targets = [target_by_band[band] for band in requested_bands]
        removed = [
            interface_id for interface_id in interfaces
            if interface_id not in targets
        ]
        affected = self._normalize_interfaces([*interfaces, *targets])
        previous_state = self._capture_legacy_state(
            affected,
            config=config,
            runtime=runtime,
        )
        bridge_id = self._legacy_bridge_for_interfaces(
            config,
            interfaces,
        ) or capabilities.home_bridge_id
        network_enabled = any(
            self._interface_observed_state(
                runtime.get(interface_id),
                config.get(interface_id),
            ) is True
            for interface_id in interfaces
        )
        existing_password = next(
            (
                self._config_password(config.get(interface_id) or {})
                for interface_id in interfaces
                if self._config_password(config.get(interface_id) or {})
            ),
            "",
        )
        effective_password = password or existing_password
        added = [item for item in targets if item not in interfaces]
        if security != "open" and added and not effective_password:
            return (
                False,
                "Для добавления нового диапазона в legacy-режиме "
                "введите пароль WPA2 ещё раз.",
                {
                    "preflight_rejected": True,
                    "password_required": True,
                    "legacy_managed": True,
                },
            )

        follow_target = target_by_band.get("2.4", "")
        commands: list[dict[str, Any]] = []
        expected_states: dict[str, bool] = {}
        for band in requested_bands:
            interface_id = target_by_band[band]
            old_state = self._interface_observed_state(
                runtime.get(interface_id),
                config.get(interface_id),
            )
            desired_state = (
                bool(old_state)
                if interface_id in interfaces and old_state is not None
                else network_enabled
            )
            expected_states[interface_id] = desired_state
            commands.extend(
                self._legacy_configure_interface_commands(
                    interface_id=interface_id,
                    ssid=ssid,
                    security=security,
                    password=effective_password,
                    hidden=hidden,
                    enabled=desired_state,
                    change_password=(
                        change_password
                        or change_security
                        or interface_id in added
                    ),
                    follow_target=(
                        follow_target if band == "5" else ""
                    ),
                )
            )
            commands.append(
                self._legacy_bridge_command(
                    bridge_id,
                    interface_id,
                    include=True,
                )
            )

        for interface_id in removed:
            commands.extend(
                self._legacy_cleanup_interface_commands(
                    interface_id,
                    bridge_ids=self._legacy_interface_bridges(
                        config,
                        interface_id,
                    ),
                )
            )
        commands.append({"system": {"configuration": {"save": {}}}})

        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось изменить legacy Wi-Fi сеть",
        )
        if not success:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return False, message, {
                "interfaces": interfaces,
                "legacy_managed": True,
                "rolled_back": rollback_ok,
                "rollback_message": rollback_message,
            }

        verified, verify_message = self.verify_network(
            interfaces=targets,
            expected_ssid=ssid,
            expected_enabled=None,
            expected_interface_states=expected_states,
        )
        removed_verified, removed_message = self.verify_deleted(removed)
        if not verified or not removed_verified:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return (
                False,
                "Проверка legacy-сети не пройдена: "
                + "; ".join(
                    part for part in (verify_message, removed_message)
                    if part and part != "Сеть удалена."
                )
                + (
                    " Исходные параметры восстановлены."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                ),
                {
                    "interfaces": targets,
                    "legacy_managed": True,
                    "rolled_back": rollback_ok,
                },
            )

        self._forget_cached_interfaces(affected)
        return (
            True,
            f"Wi-Fi сеть «{ssid}» изменена в legacy-режиме.",
            {
                "interfaces": targets,
                "previous_interfaces": interfaces,
                "bands": list(requested_bands),
                "legacy_managed": True,
                "mws_managed": False,
            },
        )

    def _set_legacy_network_enabled(
        self,
        interfaces: list[str],
        enabled: bool,
    ) -> tuple[bool, str, dict[str, Any]]:
        config = self.client.interface_service.read_config(required=False)
        runtime = self.client.interface_service.read_runtime(required=False)
        previous_state = self._capture_legacy_state(
            interfaces,
            config=config,
            runtime=runtime,
        )
        ordered = sorted(
            interfaces,
            key=lambda interface_id: bool(
                self._legacy_follow_target(config.get(interface_id) or {})
            ),
            reverse=not enabled,
        )
        commands = [
            {
                "parse": (
                    "interface "
                    + self.client.quote_cli_argument(interface_id)
                    + (" up" if enabled else " no up")
                )
            }
            for interface_id in ordered
        ]
        commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name=(
                "Не удалось включить legacy Wi-Fi сеть"
                if enabled
                else "Не удалось выключить legacy Wi-Fi сеть"
            ),
        )
        if not success:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return False, message, {
                "interfaces": interfaces,
                "legacy_managed": True,
                "rolled_back": rollback_ok,
                "rollback_message": rollback_message,
            }
        ssid = self._first_text(
            config.get(interfaces[0]) or {},
            ("ssid", "network-name"),
        )
        verified, verify_message = self.verify_network(
            interfaces=interfaces,
            expected_ssid=ssid,
            expected_enabled=enabled,
        )
        if not verified:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return False, (
                "Команда принята, но состояние legacy-сети не подтверждено: "
                + verify_message
                + (
                    " Исходное состояние восстановлено."
                    if rollback_ok
                    else " Откат не подтверждён: " + rollback_message
                )
            ), {
                "interfaces": interfaces,
                "legacy_managed": True,
                "rolled_back": rollback_ok,
            }
        return True, (
            "Legacy Wi-Fi сеть включена."
            if enabled
            else "Legacy Wi-Fi сеть выключена."
        ), {
            "interfaces": interfaces,
            "enabled": enabled,
            "legacy_managed": True,
        }

    def _delete_legacy_network(
        self,
        interfaces: list[str],
    ) -> tuple[bool, str, dict[str, Any]]:
        if any(
            (self._access_point_index(interface_id) or 0) < 2
            for interface_id in interfaces
        ):
            return (
                False,
                "Основную и гостевую системные сети удалять нельзя.",
                {
                    "preflight_rejected": True,
                    "protected": interfaces,
                    "legacy_managed": True,
                },
            )
        config = self.client.interface_service.read_config(required=False)
        runtime = self.client.interface_service.read_runtime(required=False)
        previous_state = self._capture_legacy_state(
            interfaces,
            config=config,
            runtime=runtime,
        )
        success, message = self._cleanup_legacy_interfaces(
            interfaces,
            config=config,
            operation_name="Не удалось удалить legacy Wi-Fi сеть",
        )
        if not success:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return False, message, {
                "interfaces": interfaces,
                "legacy_managed": True,
                "rolled_back": rollback_ok,
                "rollback_message": rollback_message,
            }
        verified, verify_message = self.verify_deleted(interfaces)
        if not verified:
            rollback_ok, rollback_message = self._rollback_legacy_state(
                previous_state
            )
            return False, (
                "Удаление legacy-сети не подтверждено: "
                + verify_message
                + (
                    " Сеть восстановлена."
                    if rollback_ok
                    else " Восстановление не подтверждено: "
                    + rollback_message
                )
            ), {
                "interfaces": interfaces,
                "legacy_managed": True,
                "rolled_back": rollback_ok,
            }
        self._forget_cached_interfaces(interfaces)
        return True, "Legacy Wi-Fi сеть удалена.", {
            "interfaces": interfaces,
            "legacy_managed": True,
        }

    def set_network_enabled(
        self,
        network_reference: Any,
        enabled: bool,
    ) -> tuple[bool, str, dict[str, Any]]:
        if not self._session_ready():
            return False, "Нет активной сессии с роутером.", {}

        logical = self.read_logical_snapshot(force=True)
        profile_id, interfaces, profile = self._resolve_logical_reference(
            network_reference,
            logical,
        )
        if not profile_id:
            legacy_requested = bool(
                isinstance(network_reference, dict)
                and network_reference.get("legacy_managed", False)
            )
            if (
                interfaces
                and self._legacy_mode_supported()
                and legacy_requested
            ):
                return self._set_legacy_network_enabled(
                    interfaces,
                    bool(enabled),
                )
            return (
                False,
                "Логический профиль Wi-Fi сети не найден. "
                "Управление остановлено, чтобы ползунок Keenetic "
                "и физические точки доступа не разошлись.",
                {
                    "preflight_rejected": True,
                    "logical_profile_missing": True,
                    "interfaces": interfaces,
                },
            )

        if enabled and not self._logical_profile_bands(profile):
            return (
                False,
                "Сначала назначьте сети диапазон 2,4 или 5 ГГц.",
                {
                    "preflight_rejected": True,
                    "band_missing": True,
                    "mws_profile_id": profile_id,
                    "interfaces": interfaces,
                },
            )

        commands = self._logical_toggle_commands(
            profile_id,
            bool(enabled),
        )
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name=(
                "Не удалось включить Wi-Fi сеть"
                if enabled
                else "Не удалось выключить Wi-Fi сеть"
            ),
        )
        self._invalidate_logical_snapshot()
        if not success:
            return (
                False,
                message,
                {
                    "mws_profile_id": profile_id,
                    "interfaces": interfaces,
                },
            )

        saved_verified, saved_message = (
            self.verify_saved_logical_enabled(
                profile_id,
                bool(enabled),
            )
        )
        full_profile_fallback = False
        fallback_message = ""

        # On some recovered profiles the short MWS toggle is accepted
        # but does not materialize in show/sc/mws/wlan. Re-send the
        # existing saved profile as a full patch with only enable changed.
        # This mirrors the web editor and preserves every other setting.
        if not saved_verified and isinstance(profile, dict):
            fallback_commands = self._logical_full_toggle_commands(
                profile_id,
                profile,
                bool(enabled),
            )
            fallback_ok, fallback_message = (
                self.client.execute_rci_batch(
                    fallback_commands,
                    operation_name=(
                        "Не удалось закрепить включение Wi-Fi сети"
                        if enabled
                        else "Не удалось закрепить выключение Wi-Fi сети"
                    ),
                )
            )
            full_profile_fallback = bool(fallback_ok)
            self._invalidate_logical_snapshot()
            if fallback_ok:
                saved_verified, saved_message = (
                    self.verify_saved_logical_enabled(
                        profile_id,
                        bool(enabled),
                    )
                )

        physical_changed, physical_failures = (
            self._set_mapped_access_points_enabled(
                profile_id,
                interfaces,
                bool(enabled),
            )
        )
        self._invalidate_logical_snapshot()

        verified, verify_message = self.verify_logical_enabled(
            profile_id,
            bool(enabled),
            interfaces=interfaces,
        )

        self._record_logical_event(
            (
                "Состояние Wi-Fi сети подтверждено."
                if verified
                else "Команда принята, полная синхронизация ожидается."
            ),
            profile_id=profile_id,
            requested_enabled=bool(enabled),
            verified=verified,
            verification_message=verify_message,
            extra_details={
                "saved_verified": bool(saved_verified),
                "saved_verification_message": saved_message,
                "full_profile_fallback": bool(full_profile_fallback),
                "fallback_message": fallback_message,
                "physical_changed": physical_changed,
                "physical_failures": physical_failures,
            },
        )

        self._forget_cached_interfaces(interfaces)

        if physical_failures:
            return (
                False,
                (
                    "Логическая команда отправлена, но не удалось "
                    "изменить состояние связанных точек доступа: "
                    + "; ".join(physical_failures)
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": interfaces,
                    "enabled": bool(enabled),
                    "mws_managed": True,
                    "verified": bool(verified),
                    "verification_pending": not verified,
                    "full_profile_fallback": bool(full_profile_fallback),
                    "physical_changed": physical_changed,
                    "physical_failures": physical_failures,
                },
            )

        message_text = (
            "Wi-Fi сеть включена."
            if enabled
            else "Wi-Fi сеть выключена."
        )
        if full_profile_fallback:
            message_text += " Состояние MWS закреплено полным профилем."
        if physical_changed:
            message_text += (
                " Состояние связанных точек доступа синхронизировано."
            )
        if not verified:
            message_text = (
                "Команда принята роутером, но итоговое состояние Wi-Fi "
                f"не подтверждено: {verify_message}"
            )

        details = {
            "mws_profile_id": profile_id,
            "interfaces": interfaces,
            "enabled": bool(enabled),
            "mws_managed": True,
            "verified": bool(verified),
            "verification_pending": not verified,
            "verification_message": verify_message,
            "saved_verified": bool(saved_verified),
            "saved_verification_message": saved_message,
            "full_profile_fallback": bool(full_profile_fallback),
            "physical_changed": physical_changed,
            "physical_failures": physical_failures,
        }
        if not verified:
            return False, message_text, details
        return True, message_text, details

    @staticmethod
    def _logical_toggle_commands(
        profile_id: str,
        enabled: bool,
    ) -> list[dict[str, Any]]:
        return [
            {
                "mws": {
                    "wlan": {
                        "id": str(profile_id),
                        "enable": bool(enabled),
                    }
                }
            },
            {"system": {"configuration": {"save": {}}}},
        ]

    @staticmethod
    def _logical_full_toggle_commands(
        profile_id: str,
        profile: dict[str, Any],
        enabled: bool,
    ) -> list[dict[str, Any]]:
        patch = copy.deepcopy(profile)
        patch["id"] = str(profile_id)
        patch["enable"] = bool(enabled)
        return [
            {"mws": {"wlan": patch}},
            {"system": {"configuration": {"save": {}}}},
        ]

    def verify_saved_logical_enabled(
        self,
        profile_id: str,
        expected_enabled: bool,
    ) -> tuple[bool, str]:
        last_value: bool | None = None
        for _ in range(self.LOGICAL_SAVED_VERIFY_ATTEMPTS):
            logical = self.read_logical_snapshot(force=True)
            profile = logical.config.get(profile_id)
            if isinstance(profile, dict):
                last_value = self._logical_saved_enabled(profile)
                # show/sc contains configured commands: after `enable=false`
                # Keenetic removes the enable command instead of serializing
                # a false value. Runtime MWS bands can remain stale even when
                # the mapped AccessPoint interfaces have already switched.
                actual = False if last_value is None else bool(last_value)
                if actual == bool(expected_enabled):
                    return True, "Сохранённое состояние MWS подтверждено."
            time.sleep(self.LOGICAL_SAVED_VERIFY_DELAY_SECONDS)

        text = (
            "не указано"
            if last_value is None
            else "включено"
            if last_value
            else "выключено"
        )
        return (
            False,
            f"{profile_id}: сохранённое состояние MWS={text}",
        )

    def _set_mapped_access_points_enabled(
        self,
        profile_id: str,
        interfaces: list[str],
        enabled: bool,
    ) -> tuple[list[str], list[str]]:
        mapped = self._normalize_interfaces(interfaces)
        if not mapped:
            logical = self.read_logical_snapshot(force=True)
            mapped = self.configured_logical_profile_interfaces(
                logical
            ).get(profile_id, [])
        if not mapped:
            return [], []

        interface_service = getattr(
            self.client,
            "interface_service",
            None,
        )
        set_enabled = getattr(interface_service, "set_enabled", None)
        read_runtime = getattr(interface_service, "read_runtime", None)
        if not callable(set_enabled):
            return [], ["сервис физических интерфейсов недоступен"]

        runtime = (
            read_runtime(required=False)
            if callable(read_runtime)
            else {}
        )
        if not isinstance(runtime, dict):
            runtime = {}
        read_config = getattr(interface_service, "read_config", None)
        config = (
            read_config(required=False)
            if callable(read_config)
            else {}
        )
        if not isinstance(config, dict):
            config = {}

        changed: list[str] = []
        failures: list[str] = []
        for interface_id in mapped:
            active = self._interface_observed_state(
                runtime.get(interface_id),
                config.get(interface_id),
            )
            if active is not None and active == bool(enabled):
                continue

            success, message = set_enabled(
                interface_id,
                bool(enabled),
            )
            if success:
                changed.append(interface_id)
            else:
                failures.append(
                    f"{interface_id}: {message}"
                )

        return changed, failures

    def delete_network(
        self,
        network_reference: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        if not self._session_ready():
            return False, "Нет активной сессии с роутером.", {}

        logical = self.read_logical_snapshot(force=True)
        profile_id, interfaces, profile = self._resolve_logical_reference(
            network_reference,
            logical,
        )
        if not profile_id:
            legacy_requested = bool(
                isinstance(network_reference, dict)
                and network_reference.get("legacy_managed", False)
            )
            if (
                interfaces
                and self._legacy_mode_supported()
                and legacy_requested
            ):
                return self._delete_legacy_network(interfaces)
            return (
                False,
                "Логический профиль Wi-Fi сети не найден. "
                "Физические интерфейсы отдельно удаляться не будут.",
                {
                    "preflight_rejected": True,
                    "logical_profile_missing": True,
                    "interfaces": interfaces,
                },
            )

        # For an MWS-managed network the profile is the deletion target.
        # Physical AP indices are assigned by KeeneticOS and may include 0/1
        # even for user-created profiles.
        protected = profile_id == "DEFAULT__"
        if protected:
            return (
                False,
                "Основную и гостевую системные сети удалять нельзя. "
                "Их можно выключить или изменить.",
                {
                    "preflight_rejected": True,
                    "protected": interfaces,
                    "mws_profile_id": profile_id,
                },
            )

        commands = [
            {
                "mws": {
                    "wlan": {
                        "id": profile_id,
                        "no": True,
                    }
                }
            },
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось удалить Wi-Fi сеть",
        )
        self._invalidate_logical_snapshot()
        if not success:
            rollback_ok, rollback_message = self._restore_logical_profile(
                profile_id,
                profile,
            )
            return (
                False,
                message,
                {
                    "mws_profile_id": profile_id,
                    "interfaces": interfaces,
                    "rolled_back": rollback_ok,
                    "rollback_message": rollback_message,
                },
            )

        verified, verify_message = self.verify_logical_deleted(profile_id)
        if not verified:
            rollback_ok, rollback_message = self._restore_logical_profile(
                profile_id,
                profile,
            )
            return (
                False,
                "Команда удаления отправлена, но проверка не пройдена: "
                + verify_message
                + (
                    " Профиль восстановлен."
                    if rollback_ok
                    else " Восстановление не подтверждено: "
                    + rollback_message
                ),
                {
                    "mws_profile_id": profile_id,
                    "interfaces": interfaces,
                    "rolled_back": rollback_ok,
                },
            )

        self._forget_cached_interfaces(interfaces)
        return (
            True,
            "Wi-Fi сеть удалена.",
            {
                "mws_profile_id": profile_id,
                "interfaces": interfaces,
                "mws_managed": True,
            },
        )

    def verify_logical_network(
        self,
        *,
        profile_id: str,
        expected_ssid: str,
        expected_bands: tuple[str, ...],
        expected_enabled: bool,
    ) -> tuple[bool, str, list[str]]:
        mismatches: list[str] = []
        interfaces: list[str] = []

        for _ in range(6):
            logical = self.read_logical_snapshot(force=True)
            profile = logical.config.get(profile_id)
            runtime_profile = logical.runtime.get(profile_id)
            if not isinstance(profile, dict):
                mismatches = [f"{profile_id}: профиль MWS не найден"]
                time.sleep(0.35)
                continue
            if not isinstance(runtime_profile, dict):
                runtime_profile = {}

            mismatches = []
            ssid_data = profile.get("ssid")
            actual_ssid = (
                str(ssid_data.get("name") or "").strip()
                if isinstance(ssid_data, dict)
                else ""
            )
            if actual_ssid != expected_ssid:
                mismatches.append(
                    f"{profile_id}: логическое имя не сохранено"
                )

            actual_bands = set(self._logical_profile_bands(profile))
            if set(expected_bands) != actual_bands:
                mismatches.append(
                    f"{profile_id}: диапазоны не сохранены"
                )

            interfaces = self.configured_logical_profile_interfaces(
                logical
            ).get(profile_id, [])
            mapped_bands = {
                self.band_from_interface(interface_id)
                for interface_id in interfaces
            }
            if set(expected_bands) != mapped_bands:
                mismatches.append(
                    f"{profile_id}: точки доступа не назначены"
                )

            config = self.client.interface_service.read_config(
                required=False
            )
            runtime = self.client.interface_service.read_runtime(
                required=False
            )
            physical_state_confirmed = True
            for interface_id in interfaces:
                item = config.get(interface_id)
                if not isinstance(item, dict):
                    physical_state_confirmed = False
                    continue
                actual_interface_enabled = self._interface_observed_state(
                    runtime.get(interface_id),
                    item,
                )
                if actual_interface_enabled != expected_enabled:
                    physical_state_confirmed = False
                    mismatches.append(
                        f"{interface_id}: состояние точки доступа "
                        "не подтверждено"
                    )

            saved_enabled = self._logical_saved_enabled(profile)
            if saved_enabled is not None:
                actual_enabled = saved_enabled
            elif not expected_bands:
                actual_enabled = False
            elif physical_state_confirmed:
                # KeeneticOS may omit enable=false from show/sc/mws/wlan and
                # keep stale MWS runtime flags.  The physical AP state is the
                # authoritative confirmation in that case.
                actual_enabled = expected_enabled
            else:
                actual_enabled = self._logical_profile_enabled(
                    profile,
                    runtime_profile,
                )
            if actual_enabled != expected_enabled:
                mismatches.append(
                    f"{profile_id}: сохранённое состояние не подтверждено"
                )

            for interface_id in interfaces:
                item = config.get(interface_id)
                if not isinstance(item, dict):
                    mismatches.append(
                        f"{interface_id}: конфигурация не найдена"
                    )
                    continue
                physical_ssid = self._first_text(
                    item,
                    ("ssid", "network-name"),
                )
                if physical_ssid != expected_ssid:
                    mismatches.append(
                        f"{interface_id}: физический SSID не сохранён"
                    )

            if not mismatches:
                return True, "Логическая сеть подтверждена.", interfaces
            time.sleep(0.35)

        return False, "; ".join(mismatches), interfaces

    def verify_logical_enabled(
        self,
        profile_id: str,
        expected_enabled: bool,
        *,
        interfaces: list[str] | None = None,
    ) -> tuple[bool, str]:
        last_saved: bool | None = None
        last_mws_states: dict[str, bool] = {}
        last_physical_states: dict[str, bool | None] = {}
        expected_interfaces = self._normalize_interfaces(interfaces or [])

        for _ in range(self.LOGICAL_TOGGLE_VERIFY_ATTEMPTS):
            logical = self.read_logical_snapshot(force=True)
            profile = logical.config.get(profile_id)
            runtime_profile = logical.runtime.get(profile_id)

            if isinstance(profile, dict):
                last_saved = self._logical_saved_enabled(profile)

            if not isinstance(runtime_profile, dict):
                runtime_profile = {}
            last_mws_states = self._logical_band_states(
                runtime_profile
            )

            observed_interfaces = (
                expected_interfaces
                or self.configured_logical_profile_interfaces(
                    logical
                ).get(profile_id, [])
            )
            interface_service = getattr(
                self.client,
                "interface_service",
                None,
            )
            read_runtime = getattr(
                interface_service,
                "read_runtime",
                None,
            )
            runtime = (
                read_runtime(required=False)
                if callable(read_runtime)
                else {}
            )
            if not isinstance(runtime, dict):
                runtime = {}
            read_config = getattr(
                interface_service,
                "read_config",
                None,
            )
            config = (
                read_config(required=False)
                if callable(read_config)
                else {}
            )
            if not isinstance(config, dict):
                config = {}
            last_physical_states = {
                interface_id: self._interface_observed_state(
                    runtime.get(interface_id),
                    config.get(interface_id),
                )
                for interface_id in observed_interfaces
            }

            saved_actual = (
                True if last_saved is None else bool(last_saved)
            )
            if last_mws_states:
                logical_ok = (
                    all(last_mws_states.values())
                    if expected_enabled
                    else not any(last_mws_states.values())
                )
            else:
                logical_ok = saved_actual == bool(expected_enabled)

            if last_physical_states:
                known_physical = all(
                    value is not None
                    for value in last_physical_states.values()
                )
                if not known_physical and not expected_interfaces:
                    # Compatibility callers that do not provide a captured
                    # interface list only ask about the logical MWS switch.
                    physical_ok = logical_ok
                else:
                    physical_ok = bool(known_physical) and (
                        all(
                            value is True
                            for value in last_physical_states.values()
                        )
                        if expected_enabled
                        else all(
                            value is False
                            for value in last_physical_states.values()
                        )
                    )
            else:
                physical_ok = logical_ok

            if physical_ok and expected_interfaces:
                if logical_ok:
                    return True, "Логическое и физическое состояние подтверждено."
                return (
                    True,
                    "Физическое состояние Wi-Fi подтверждено; "
                    "runtime MWS ещё содержит предыдущее значение.",
                )

            if logical_ok and physical_ok:
                return True, "Логическое состояние Wi-Fi подтверждено."

            time.sleep(self.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS)

        saved_text = (
            "не указано"
            if last_saved is None
            else "включено"
            if last_saved
            else "выключено"
        )
        mws_text = (
            ", ".join(
                f"{band}={'on' if value else 'off'}"
                for band, value in last_mws_states.items()
            )
            or "нет данных"
        )
        physical_text = (
            ", ".join(
                f"{name}={('unknown' if value is None else 'up' if value else 'down')}"
                for name, value in last_physical_states.items()
            )
            or "нет данных"
        )
        return (
            False,
            f"{profile_id}: saved={saved_text}; "
            f"mws={mws_text}; physical={physical_text}",
        )

    def verify_logical_deleted(
        self,
        profile_id: str,
    ) -> tuple[bool, str]:
        for _ in range(6):
            logical = self.read_logical_snapshot(force=True)
            if (
                profile_id not in logical.config
                and profile_id not in logical.runtime
            ):
                return True, "Логический профиль удалён."
            time.sleep(0.35)
        return False, f"{profile_id}: профиль MWS всё ещё существует"

    def verify_network(
        self,
        *,
        interfaces: list[str],
        expected_ssid: str,
        expected_enabled: bool | None,
        expected_interface_states: dict[str, bool] | None = None,
        logical_profile_id: str = "",
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(4):
            config = self.client.interface_service.read_config(
                required=False
            )
            runtime = self.client.interface_service.read_runtime(
                required=False
            )
            mismatches = []

            for interface_id in interfaces:
                item = config.get(interface_id)
                if not isinstance(item, dict):
                    mismatches.append(
                        f"{interface_id}: конфигурация не найдена"
                    )
                    continue

                actual_ssid = self._first_text(
                    item,
                    ("ssid", "network-name"),
                )
                if actual_ssid != expected_ssid:
                    mismatches.append(
                        f"{interface_id}: SSID не сохранён"
                    )

                interface_expected = (
                    expected_interface_states.get(interface_id)
                    if isinstance(expected_interface_states, dict)
                    and interface_id in expected_interface_states
                    else expected_enabled
                )
                if interface_expected is not None:
                    actual_enabled = self._interface_observed_state(
                        runtime.get(interface_id),
                        item,
                    )
                    if actual_enabled != bool(interface_expected):
                        mismatches.append(
                            f"{interface_id}: состояние "
                            "не подтверждено"
                        )

            target_keys = {
                interface_id.casefold()
                for interface_id in interfaces
            }
            for interface_id, interface_expected in (
                expected_interface_states or {}
            ).items():
                if interface_id.casefold() in target_keys:
                    continue
                item = config.get(interface_id)
                if not isinstance(item, dict):
                    mismatches.append(
                        f"{interface_id}: конфигурация соседней "
                        "Wi-Fi сети не найдена"
                    )
                    continue
                actual_enabled = self._interface_observed_state(
                    runtime.get(interface_id),
                    item,
                )
                if actual_enabled != bool(interface_expected):
                    mismatches.append(
                        f"{interface_id}: состояние соседней "
                        "Wi-Fi сети изменилось"
                    )

            if logical_profile_id:
                logical = self.read_logical_snapshot(force=True)
                profile = logical.config.get(
                    logical_profile_id
                )
                if not isinstance(profile, dict):
                    mismatches.append(
                        f"{logical_profile_id}: профиль MWS "
                        "не найден"
                    )
                else:
                    ssid_data = profile.get("ssid")
                    logical_ssid = (
                        str(ssid_data.get("name") or "").strip()
                        if isinstance(ssid_data, dict)
                        else ""
                    )
                    if logical_ssid != expected_ssid:
                        mismatches.append(
                            f"{logical_profile_id}: логическое "
                            "имя не сохранено"
                        )

                matched_id, mapped_interfaces = (
                    self.match_logical_profile(
                        interfaces,
                        logical,
                    )
                )
                mapped = {
                    value.casefold()
                    for value in mapped_interfaces
                }
                expected = {
                    value.casefold()
                    for value in interfaces
                }
                if (
                    matched_id != logical_profile_id
                    or not expected.issubset(mapped)
                ):
                    mismatches.append(
                        f"{logical_profile_id}: связь с точками "
                        "доступа не подтверждена"
                    )

            if not mismatches:
                return True, "Сеть подтверждена."

            time.sleep(0.35)

        return False, "; ".join(mismatches)

    def verify_deleted(
        self,
        interfaces: list[str],
    ) -> tuple[bool, str]:
        for _ in range(4):
            config = self.client.interface_service.read_config(required=False)
            remaining = []
            for interface_id in interfaces:
                item = config.get(interface_id)
                if isinstance(item, dict) and self.valid_ssid(
                    self._first_text(item, ("ssid", "network-name"))
                ):
                    remaining.append(interface_id)
            if not remaining:
                return True, "Сеть удалена."
            time.sleep(0.35)
        return False, "SSID остаётся у: " + ", ".join(remaining)

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------
    def _build_logical_update_command(
        self,
        *,
        profile_id: str,
        profile_config: dict[str, Any],
        ssid: str,
        hidden: bool,
        password: str,
        security: str,
        change_password: bool,
        change_security: bool,
        bands: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {
            "id": profile_id,
            "ssid": {
                "name": ssid,
                "hide": hidden,
            },
        }

        if bands is not None:
            patch["band"] = self._logical_band_command_value(
                bands
            )

        if change_password and security != "open":
            patch["wpa"] = {"psk": password}

        if change_security and security == "wpa2":
            current = str(
                profile_config.get("encryption") or ""
            ).strip()
            patch["encryption"] = (
                current
                if "wpa" in current.lower()
                else "wpa2"
            )

        return {"mws": {"wlan": patch}}

    def _capture_update_state(
        self,
        *,
        config: dict[str, Any],
        runtime: dict[str, Any],
        interfaces: list[str],
        preserved_interfaces: list[str],
        profile_id: str,
        profile_config: dict[str, Any],
        profile_interfaces: list[str],
        change_password: bool,
        change_security: bool,
    ) -> dict[str, Any]:
        physical: dict[str, dict[str, Any]] = {}
        preserved_interface_states: dict[str, bool] = {}

        for interface_id in self._normalize_interfaces(
            preserved_interfaces
        ):
            item = config.get(interface_id)
            if not isinstance(item, dict):
                continue
            enabled = self._interface_observed_state(
                runtime.get(interface_id),
                item,
            )
            if isinstance(enabled, bool):
                preserved_interface_states[interface_id] = enabled

        for interface_id in interfaces:
            item = config.get(interface_id)
            if not isinstance(item, dict):
                continue
            physical[interface_id] = {
                "ssid": self._first_text(
                    item,
                    ("ssid", "network-name"),
                ),
                "hidden": self._config_hidden(item),
                "security": self._config_security_mode(item),
                "password": self._config_password(item),
                "enabled": preserved_interface_states.get(
                    interface_id
                ),
            }

        ssid_data = profile_config.get("ssid")
        logical_ssid = (
            str(ssid_data.get("name") or "").strip()
            if isinstance(ssid_data, dict)
            else ""
        )
        logical_hidden = (
            bool(ssid_data.get("hide"))
            if isinstance(ssid_data, dict)
            else False
        )
        wpa = profile_config.get("wpa")
        logical_password = (
            str(wpa.get("psk") or "")
            if isinstance(wpa, dict)
            else ""
        )

        return {
            "interfaces": list(interfaces),
            "physical": physical,
            "preserved_interface_states": preserved_interface_states,
            "profile_id": profile_id,
            "profile_config": copy.deepcopy(profile_config),
            "profile_interfaces": list(profile_interfaces),
            "logical_ssid": logical_ssid,
            "logical_hidden": logical_hidden,
            "logical_password": logical_password,
            "change_password": change_password,
            "change_security": change_security,
        }

    def _rollback_network_update(
        self,
        state: dict[str, Any],
    ) -> tuple[bool, str]:
        commands: list[dict[str, Any]] = []
        profile_id = str(state.get("profile_id") or "")
        profile_config = state.get("profile_config")
        if not isinstance(profile_config, dict):
            profile_config = {}

        if profile_id:
            logical_patch: dict[str, Any] = {
                "id": profile_id,
                "ssid": {
                    "name": str(
                        state.get("logical_ssid") or ""
                    ),
                    "hide": bool(
                        state.get("logical_hidden", False)
                    ),
                },
            }
            if state.get("bands_changed"):
                old_bands = self.normalize_bands(
                    state.get("logical_bands")
                )
                logical_patch["band"] = (
                    self._logical_band_command_value(old_bands)
                )
                logical_patch["enable"] = bool(
                    state.get("logical_enabled", False)
                )
            if (
                state.get("change_password")
                and state.get("logical_password")
            ):
                logical_patch["wpa"] = {
                    "psk": str(state["logical_password"])
                }
            if state.get("change_security"):
                encryption = str(
                    profile_config.get("encryption") or ""
                ).strip()
                if encryption:
                    logical_patch["encryption"] = encryption

            commands.append(
                {"mws": {"wlan": logical_patch}}
            )

        physical = state.get("physical")
        if not isinstance(physical, dict):
            physical = {}

        for interface_id, old in physical.items():
            if not isinstance(old, dict):
                continue

            interface_arg = self.client.quote_cli_argument(
                interface_id
            )
            old_ssid = str(old.get("ssid") or "")
            if old_ssid:
                commands.append(
                    {
                        "parse": (
                            f"interface {interface_arg} ssid "
                            + self.client.quote_cli_argument(
                                old_ssid
                            )
                        )
                    }
                )

            if state.get("change_password"):
                old_password = str(
                    old.get("password") or ""
                )
                if old_password:
                    commands.append(
                        {
                            "parse": (
                                f"interface {interface_arg} "
                                "authentication wpa-psk "
                                + self.client.quote_cli_argument(
                                    old_password
                                )
                            )
                        }
                    )

            if state.get("change_security"):
                commands.extend(
                    self._security_commands(
                        interface_arg=interface_arg,
                        security=str(
                            old.get("security") or "wpa2"
                        ),
                        password=str(
                            old.get("password") or ""
                        ),
                        change_password=bool(
                            old.get("password")
                        ),
                    )
                )

            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} hide-ssid"
                        if bool(old.get("hidden"))
                        else (
                            f"interface {interface_arg} "
                            "no hide-ssid"
                        )
                    )
                }
            )


        preserved_states = state.get("preserved_interface_states")
        if not isinstance(preserved_states, dict):
            preserved_states = {
                interface_id: bool(item["enabled"])
                for interface_id, item in physical.items()
                if isinstance(item, dict)
                and isinstance(item.get("enabled"), bool)
            }
        for interface_id, was_enabled in preserved_states.items():
            if not isinstance(was_enabled, bool):
                continue
            interface_arg = self.client.quote_cli_argument(
                interface_id
            )
            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} up"
                        if was_enabled
                        else f"interface {interface_arg} no up"
                    )
                }
            )

        commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Откат изменения Wi-Fi сети",
        )
        self._invalidate_logical_snapshot()
        if not success:
            return False, message

        old_ssid = str(state.get("logical_ssid") or "")
        for old in physical.values():
            if not old_ssid and isinstance(old, dict) and old.get("ssid"):
                old_ssid = str(old["ssid"])
                break

        physical_verified, physical_message = self.verify_network(
            interfaces=self._normalize_interfaces(
                state.get("interfaces")
            ),
            expected_ssid=old_ssid,
            expected_enabled=None,
            expected_interface_states=preserved_states,
            # Bandless profiles intentionally have no current AP linkage.
            # Their logical mapping is verified separately below.
            logical_profile_id="",
        )
        if not profile_id:
            return physical_verified, physical_message

        old_bands = self.normalize_bands(state.get("logical_bands"))
        logical_verified, logical_message, _ = self.verify_logical_network(
            profile_id=profile_id,
            expected_ssid=str(state.get("logical_ssid") or old_ssid),
            expected_bands=old_bands,
            expected_enabled=(
                bool(state.get("logical_enabled", False))
                if old_bands
                else False
            ),
        )
        return (
            physical_verified and logical_verified,
            f"{logical_message} {physical_message}".strip(),
        )

    def _build_logical_create_command(
        self,
        *,
        profile_id: str,
        bridge_id: str,
        bands: tuple[str, ...],
        ssid: str,
        password: str,
        security: str,
        hidden: bool,
        enabled: bool,
    ) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "id": profile_id,
            "bind": {"interface": bridge_id},
            "band": self._logical_band_command_value(bands),
            "encryption": "open" if security == "open" else "wpa2",
            "ssid": {
                "name": ssid,
                "hide": hidden,
            },
            "schedule": {"no": True},
            "wps": {"enable": False},
            "peer-isolation": False,
            "rrm": {"enable": False},
            "ft": {
                "enable": False,
                "skip-band": {"no": True},
                "mdid": {"no": True},
                "iapp": {"key": {"no": True}},
            },
            "band-steering": {
                "enable": False,
                "prefer-band": {"no": True},
            },
            "mlo": {"enable": False},
            # Keenetic's web UI toggle reads the saved MWS field. Always
            # write it explicitly, including the enabled=True case.
            "enable": bool(enabled),
        }
        if security != "open":
            profile["wpa"] = {"psk": password}
        return {"mws": {"wlan": profile}}

    def _logical_band_command_value(
        self,
        bands: tuple[str, ...],
    ) -> list[str] | dict[str, bool]:
        values = [
            self.BAND_TO_MWS[band]
            for band in bands
        ]
        # This is the same deletion marker used by Keenetic's own web UI.
        # An empty JSON list is not the canonical RCI representation for
        # removing the saved `band` command.
        return values if values else {"no": True}

    def _rollback_created_profile(
        self,
        profile_id: str,
    ) -> tuple[bool, str]:
        success, message = self.client.execute_rci_batch(
            [
                {"mws": {"wlan": {"id": profile_id, "no": True}}},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Откат создания Wi-Fi сети",
        )
        self._invalidate_logical_snapshot()
        if not success:
            return False, message
        return self.verify_logical_deleted(profile_id)

    def _restore_logical_profile(
        self,
        profile_id: str,
        profile: dict[str, Any],
    ) -> tuple[bool, str]:
        if not isinstance(profile, dict) or not profile:
            return False, "Исходный профиль MWS не сохранён."
        restored = copy.deepcopy(profile)
        restored["id"] = profile_id
        success, message = self.client.execute_rci_batch(
            [
                {"mws": {"wlan": restored}},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Восстановление Wi-Fi сети",
        )
        self._invalidate_logical_snapshot()
        if not success:
            return False, message
        for _ in range(6):
            logical = self.read_logical_snapshot(force=True)
            current = logical.config.get(profile_id)
            if isinstance(current, dict):
                return True, "Профиль MWS восстановлен."
            time.sleep(0.35)
        return False, f"{profile_id}: профиль MWS не восстановлен"

    def _legacy_configure_interface_commands(
        self,
        *,
        interface_id: str,
        ssid: str,
        security: str,
        password: str,
        hidden: bool,
        enabled: bool,
        change_password: bool,
        follow_target: str = "",
    ) -> list[dict[str, Any]]:
        interface_arg = self.client.quote_cli_argument(interface_id)
        commands: list[dict[str, Any]] = [
            {
                "parse": (
                    f"interface {interface_arg} ssid "
                    + self.client.quote_cli_argument(ssid)
                )
            }
        ]
        commands.extend(
            self._security_commands(
                interface_arg=interface_arg,
                security=security,
                password=password,
                change_password=change_password,
            )
        )
        commands.append(
            {
                "parse": (
                    f"interface {interface_arg} hide-ssid"
                    if hidden
                    else f"interface {interface_arg} no hide-ssid"
                )
            }
        )
        if self.band_from_interface(interface_id) == "5":
            commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} follow access-point "
                        + self.client.quote_cli_argument(follow_target)
                        if follow_target
                        else (
                            f"interface {interface_arg} "
                            "no follow access-point"
                        )
                    )
                }
            )
        commands.append(
            {
                "parse": (
                    f"interface {interface_arg} up"
                    if enabled
                    else f"interface {interface_arg} no up"
                )
            }
        )
        return commands

    def _legacy_cleanup_interface_commands(
        self,
        interface_id: str,
        *,
        bridge_ids: list[str],
    ) -> list[dict[str, Any]]:
        interface_arg = self.client.quote_cli_argument(interface_id)
        commands: list[dict[str, Any]] = []
        for bridge_id in bridge_ids:
            commands.append(
                self._legacy_bridge_command(
                    bridge_id,
                    interface_id,
                    include=False,
                )
            )
        commands.extend(
            [
                {"parse": f"interface {interface_arg} no up"},
                {
                    "parse": (
                        f"interface {interface_arg} no follow access-point"
                    )
                },
                {"parse": f"interface {interface_arg} no hide-ssid"},
                {
                    "parse": (
                        f"interface {interface_arg} "
                        "no authentication wpa-psk"
                    )
                },
                {"parse": f"interface {interface_arg} encryption disable"},
                {"parse": f"interface {interface_arg} no ssid"},
            ]
        )
        return commands

    def _cleanup_legacy_interfaces(
        self,
        interfaces: list[str],
        *,
        config: dict[str, Any],
        operation_name: str,
    ) -> tuple[bool, str]:
        commands: list[dict[str, Any]] = []
        for interface_id in interfaces:
            commands.extend(
                self._legacy_cleanup_interface_commands(
                    interface_id,
                    bridge_ids=self._legacy_interface_bridges(
                        config,
                        interface_id,
                    ),
                )
            )
        commands.append({"system": {"configuration": {"save": {}}}})
        return self.client.execute_rci_batch(
            commands,
            operation_name=operation_name,
        )

    def _capture_legacy_state(
        self,
        interfaces: list[str],
        *,
        config: dict[str, Any],
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "interfaces": {
                interface_id: {
                    "config": copy.deepcopy(
                        config.get(interface_id)
                        if isinstance(config.get(interface_id), dict)
                        else {}
                    ),
                    "enabled": self._interface_observed_state(
                        runtime.get(interface_id),
                        config.get(interface_id),
                    ),
                    "bridges": self._legacy_interface_bridges(
                        config,
                        interface_id,
                    ),
                }
                for interface_id in interfaces
            }
        }

    def _rollback_legacy_state(
        self,
        state: dict[str, Any],
    ) -> tuple[bool, str]:
        raw_interfaces = state.get("interfaces")
        if not isinstance(raw_interfaces, dict):
            return False, "Исходное состояние legacy-сети отсутствует."
        current_config = self.client.interface_service.read_config(
            required=False
        )
        commands: list[dict[str, Any]] = []
        for interface_id, raw_state in raw_interfaces.items():
            if not isinstance(raw_state, dict):
                continue
            bridge_ids = set(
                self._legacy_interface_bridges(
                    current_config,
                    str(interface_id),
                )
            )
            bridge_ids.update(
                str(value)
                for value in raw_state.get("bridges") or []
                if str(value).strip()
            )
            commands.extend(
                self._legacy_cleanup_interface_commands(
                    str(interface_id),
                    bridge_ids=sorted(bridge_ids),
                )
            )

        for interface_id, raw_state in raw_interfaces.items():
            if not isinstance(raw_state, dict):
                continue
            old_config = raw_state.get("config")
            if not isinstance(old_config, dict):
                continue
            old_ssid = self._first_text(
                old_config,
                ("ssid", "network-name"),
            )
            if not self.valid_ssid(old_ssid):
                continue
            old_security = self._config_security_mode(old_config)
            old_password = self._config_password(old_config)
            enabled = raw_state.get("enabled")
            commands.extend(
                self._legacy_configure_interface_commands(
                    interface_id=str(interface_id),
                    ssid=old_ssid,
                    security=(
                        "open" if old_security == "open" else "wpa2"
                    ),
                    password=old_password,
                    hidden=self._config_hidden(old_config),
                    enabled=bool(enabled),
                    change_password=bool(old_password),
                    follow_target=self._legacy_follow_target(old_config),
                )
            )
            for bridge_id in raw_state.get("bridges") or []:
                commands.append(
                    self._legacy_bridge_command(
                        str(bridge_id),
                        str(interface_id),
                        include=True,
                    )
                )
        commands.append({"system": {"configuration": {"save": {}}}})
        return self.client.execute_rci_batch(
            commands,
            operation_name="Восстановление legacy Wi-Fi сети",
        )

    def _legacy_bridge_command(
        self,
        bridge_id: str,
        interface_id: str,
        *,
        include: bool,
    ) -> dict[str, Any]:
        bridge_arg = self.client.quote_cli_argument(bridge_id)
        interface_arg = self.client.quote_cli_argument(interface_id)
        return {
            "parse": (
                ("interface " if include else "no interface ")
                + bridge_arg
                + " include "
                + interface_arg
            )
        }

    @classmethod
    def _legacy_include_values(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, dict):
            direct = value.get("interface")
            if isinstance(direct, str) and direct.strip():
                return [direct.strip()]
            result: list[str] = []
            for nested in value.values():
                result.extend(cls._legacy_include_values(nested))
            return list(dict.fromkeys(result))
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for nested in value:
                result.extend(cls._legacy_include_values(nested))
            return list(dict.fromkeys(result))
        return []

    @classmethod
    def _legacy_interface_bridges(
        cls,
        config: dict[str, Any],
        interface_id: str,
    ) -> list[str]:
        result: list[str] = []
        target = str(interface_id).casefold()
        for bridge_id, item in config.items():
            if (
                not str(bridge_id).casefold().startswith("bridge")
                or not isinstance(item, dict)
            ):
                continue
            included = {
                value.casefold()
                for value in cls._legacy_include_values(item.get("include"))
            }
            if target in included:
                result.append(str(bridge_id))
        return result

    @classmethod
    def _legacy_bridge_for_interfaces(
        cls,
        config: dict[str, Any],
        interfaces: list[str],
    ) -> str:
        for interface_id in interfaces:
            bridges = cls._legacy_interface_bridges(config, interface_id)
            if bridges:
                return bridges[0]
        return ""

    @staticmethod
    def _legacy_follow_target(config_item: dict[str, Any]) -> str:
        follow = config_item.get("follow")
        if isinstance(follow, dict):
            return str(follow.get("access-point") or "").strip()
        return ""


    def _security_commands(
        self,
        *,
        interface_arg: str,
        security: str,
        password: str,
        change_password: bool,
    ) -> list[dict[str, Any]]:
        if security == "open":
            return [
                {"parse": f"interface {interface_arg} no authentication wpa-psk"},
                {"parse": f"interface {interface_arg} encryption disable"},
            ]

        commands = [
            {"parse": f"interface {interface_arg} encryption enable"},
            {"parse": f"interface {interface_arg} encryption wpa2"},
        ]
        if change_password:
            commands.insert(
                0,
                {
                    "parse": (
                        f"interface {interface_arg} authentication wpa-psk "
                        + self.client.quote_cli_argument(password)
                    )
                },
            )
        return commands


    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    @classmethod
    def _find_logical_profile_by_ssid(
        cls,
        profiles: dict[str, Any],
        ssid: str,
    ) -> str:
        matches = cls._logical_profiles_by_ssid(profiles, ssid)
        return matches[0] if matches else ""

    @classmethod
    def _logical_profiles_by_ssid(
        cls,
        profiles: dict[str, Any],
        ssid: str,
    ) -> list[str]:
        target = str(ssid or "").strip().casefold()
        if not target:
            return []
        matches: list[str] = []
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            ssid_data = profile.get("ssid")
            actual = (
                str(ssid_data.get("name") or "").strip()
                if isinstance(ssid_data, dict)
                else ""
            )
            if actual.casefold() == target:
                matches.append(str(profile_id))
        return matches

    @staticmethod
    def _next_logical_profile_id(
        snapshot: WifiLogicalSnapshot,
    ) -> str:
        used = {
            str(profile_id).casefold()
            for profile_id in set(snapshot.config) | set(snapshot.runtime)
        }
        index = 0
        while f"wlan{index}".casefold() in used:
            index += 1
        return f"wlan{index}"

    def _resolve_logical_reference(
        self,
        reference: Any,
        snapshot: WifiLogicalSnapshot,
    ) -> tuple[str, list[str], dict[str, Any]]:
        profile_id = ""
        interfaces: list[str] = []

        if isinstance(reference, dict):
            profile_id = str(
                reference.get("mws_profile_id")
                or reference.get("profile_id")
                or ""
            ).strip()
            interfaces = self._network_interfaces(reference)
        elif isinstance(reference, str):
            text = reference.strip()
            if text == "DEFAULT__" or re.fullmatch(r"wlan\d+", text, re.I):
                profile_id = text
            else:
                interfaces = self._normalize_interfaces(text)
        else:
            interfaces = self._normalize_interfaces(reference)

        mapped = self.configured_logical_profile_interfaces(snapshot)
        if profile_id and isinstance(snapshot.config.get(profile_id), dict):
            profile = copy.deepcopy(snapshot.config[profile_id])
            return profile_id, mapped.get(profile_id, interfaces), profile

        if interfaces:
            matched_id, matched_interfaces = self.match_logical_profile(
                interfaces,
                snapshot,
            )
            if matched_id:
                profile = snapshot.config.get(matched_id)
                if not isinstance(profile, dict):
                    profile = {}
                return (
                    matched_id,
                    matched_interfaces or interfaces,
                    copy.deepcopy(profile),
                )

        return "", interfaces, {}

    def _validate_settings(
        self,
        *,
        ssid: str,
        bands: tuple[str, ...],
        security: str,
        password: str,
        password_required: bool,
        allow_empty_bands: bool = False,
    ) -> str:
        if not self.valid_ssid(ssid):
            return "SSID должен содержать от 1 до 32 байт."
        if not bands and not allow_empty_bands:
            return "Выберите хотя бы один диапазон Wi-Fi."
        if security not in {"wpa2", "open"}:
            return "Неподдерживаемый режим безопасности."
        if not self.valid_password(password, required=password_required):
            return "Пароль WPA2 должен содержать от 8 до 63 символов."
        return ""

    def _session_ready(self) -> bool:
        return self.client.session is not None and self.client.base_url is not None

    def _declared_logical_mws_support(self) -> bool | None:
        """Return safe version-based support, or None for unknown clients."""
        raw_capabilities = getattr(self.client, "capabilities", None)
        version_data = getattr(self.client, "version_data", None)
        capabilities = raw_capabilities
        if (
            not isinstance(capabilities, RouterCapabilities)
            or (
                capabilities.keeneticos_major <= 0
                and isinstance(version_data, dict)
                and bool(version_data)
            )
        ):
            if not isinstance(version_data, dict) or not version_data:
                return None
            capabilities = RouterCapabilities.from_version_data(version_data)

        major = int(capabilities.keeneticos_major or 0)
        minor = int(capabilities.keeneticos_minor or 0)
        signature = (
            major,
            minor,
            bool(capabilities.mws_component),
            bool(capabilities.logical_mws),
            str(
                version_data.get("release", "")
                if isinstance(version_data, dict)
                else ""
            ),
        )
        if signature != self._logical_capability_signature:
            self._logical_capability_signature = signature
            self._logical_management_supported = None
            self._invalidate_logical_snapshot()

        if major <= 0:
            return None
        return bool(capabilities.logical_mws)

    def _wifi_management_mode(self) -> str:
        capabilities = getattr(self.client, "capabilities", None)
        if isinstance(capabilities, RouterCapabilities):
            if capabilities.logical_mws:
                return "mws"
            if capabilities.legacy_wifi:
                return "legacy"
        if self._logical_management_supported is True:
            return "mws"
        if self._legacy_mode_supported():
            return "legacy"
        return "monitoring"

    def _legacy_mode_supported(self) -> bool:
        """Legacy physical AP management is limited to KeeneticOS 4."""
        if self._logical_management_supported is not False:
            return False
        version_data = getattr(self.client, "version_data", {})
        extract_version = getattr(self.client, "extract_version", None)
        version = ""
        if callable(extract_version) and isinstance(version_data, dict):
            try:
                version = str(extract_version(version_data) or "").strip()
            except (TypeError, ValueError):
                version = ""
        if not version:
            version = str(
                getattr(self.client, "keeneticos_version", "") or ""
            ).strip()
        match = re.search(r"(?:^|\D)(\d+)(?:\.|$)", version)
        return bool(match and int(match.group(1)) == 4)

    @classmethod
    def band_from_interface(cls, interface_id: str) -> str:
        text = str(interface_id or "").lower()
        if "wifimaster0" in text:
            return "2.4"
        if "wifimaster1" in text:
            return "5"
        if "wifimaster2" in text:
            return "6"
        return "unknown"

    @staticmethod
    def _access_point_index(interface_id: str) -> int | None:
        match = re.search(r"/AccessPoint(\d+)$", str(interface_id), re.I)
        return int(match.group(1)) if match else None

    @staticmethod
    def _is_access_point_id(interface_id: str) -> bool:
        return bool(re.search(r"/AccessPoint\d+$", interface_id, re.I))

    @classmethod
    def _network_interfaces(cls, network: dict[str, Any]) -> list[str]:
        raw = network.get("interfaces")
        if isinstance(raw, list):
            values = raw
        else:
            values = str(network.get("interface_id") or "").split(",")
        return cls._normalize_interfaces(values)

    @staticmethod
    def _normalize_interfaces(values: Any) -> list[str]:
        if isinstance(values, str):
            values = values.split(",")
        elif isinstance(values, dict):
            values = values.keys()
        elif not isinstance(values, (list, tuple, set)):
            try:
                values = list(values)
            except TypeError:
                return []
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
        lowered = {str(key).lower(): value for key, value in data.items()}
        for key in keys:
            value = lowered.get(key.lower())
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        for value in data.values():
            if isinstance(value, dict):
                nested = WiFiNetworkService._first_text(value, keys)
                if nested:
                    return nested
        return ""

    @staticmethod
    def _config_hidden(item: dict[str, Any]) -> bool:
        raw = item.get("hide-ssid")
        if isinstance(raw, dict):
            return True
        if raw is not None:
            return bool(raw)
        return False

    @staticmethod
    def _config_security_mode(item: dict[str, Any]) -> str:
        authentication = item.get("authentication")
        encryption = item.get("encryption")
        text = f"{authentication} {encryption}".lower()
        if "wpa" in text:
            return "wpa2"
        if "disable" in text or not text.strip("{} "):
            return "open"
        return "unknown"

    @staticmethod
    def _config_password(item: dict[str, Any]) -> str:
        authentication = item.get("authentication")
        if not isinstance(authentication, dict):
            return ""
        wpa_psk = authentication.get("wpa-psk")
        if isinstance(wpa_psk, dict):
            return str(
                wpa_psk.get("psk")
                or wpa_psk.get("key")
                or ""
            )
        if isinstance(wpa_psk, str):
            return wpa_psk
        return ""

    @classmethod
    def _home_bridge_id(cls, config: dict[str, Any]) -> str:
        if isinstance(config.get("Bridge0"), dict):
            return "Bridge0"
        for interface_id, item in config.items():
            if not isinstance(item, dict):
                continue
            if str(interface_id).lower().startswith("bridge"):
                return str(interface_id)
        return "Bridge0"

    @classmethod
    def _select_free_interfaces(
        cls,
        *,
        bands: tuple[str, ...],
        available: dict[str, tuple[str, ...]],
    ) -> list[str]:
        if not bands:
            return []

        by_band: dict[str, dict[int, str]] = {}
        for band in bands:
            index_map: dict[int, str] = {}
            for interface_id in available.get(band, ()):
                index = cls._access_point_index(interface_id)
                if index is not None:
                    index_map[index] = interface_id
            by_band[band] = index_map

        common_indices: set[int] | None = None
        for index_map in by_band.values():
            indices = set(index_map)
            common_indices = (
                indices
                if common_indices is None
                else common_indices & indices
            )

        if common_indices:
            index = min(common_indices)
            return [by_band[band][index] for band in bands]

        selected: list[str] = []
        for band in bands:
            index_map = by_band.get(band) or {}
            if not index_map:
                return []
            selected.append(index_map[min(index_map)])
        return selected

    @classmethod
    def _find_network_by_ssid(
        cls,
        config: dict[str, Any],
        ssid: str,
    ) -> list[str]:
        target = ssid.casefold()
        result = []
        for interface_id, item in config.items():
            if not isinstance(item, dict) or not cls._is_access_point_id(str(interface_id)):
                continue
            actual = cls._first_text(item, ("ssid", "network-name"))
            if actual.casefold() == target:
                result.append(str(interface_id))
        return result
