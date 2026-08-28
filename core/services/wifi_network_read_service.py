from __future__ import annotations

import copy
import re
import time
from typing import Any

from core.services.wifi_network_models import (
    WifiLogicalSnapshot,
    WifiNetworkCapabilities,
)
from core.wifi_engine import WifiEngine


class WifiNetworkReadMixin:
    """Build and reconcile the Wi-Fi read model independently of mutations."""

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
            if self._logical_profiles_are_authoritative()
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
            return self._cached_logical_snapshot()

        if not self._session_ready():
            return self._cached_logical_snapshot()

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
            return self._cached_logical_snapshot()

        config: dict[str, Any] = {}
        runtime: dict[str, Any] = {}

        success, data, error = query(
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

        else:
            if not success and self._logical_query_is_unsupported(error):
                # One combined probe is sufficient for an unsupported
                # firmware. Repeating a confirmed `not found` request only
                # pollutes the router journal.
                self._logical_management_supported = False
            else:
                # A timeout, disconnect or malformed response does not change
                # router capabilities. Keep the last confirmed snapshot and
                # leave its timestamp untouched so the next refresh retries.
                return self._cached_logical_snapshot()

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

    def _cached_logical_snapshot(self) -> WifiLogicalSnapshot:
        return WifiLogicalSnapshot(
            config=copy.deepcopy(self._logical_snapshot.config),
            runtime=copy.deepcopy(self._logical_snapshot.runtime),
        )

    @staticmethod
    def _logical_query_is_unsupported(error: Any) -> bool:
        text = str(error or "").strip().casefold()
        return any(
            marker in text
            for marker in (
                "not found",
                "unknown command",
                "unsupported",
                "не найден",
                "не поддерж",
            )
        )

    def _logical_profiles_are_authoritative(self) -> bool:
        return bool(
            self._logical_management_supported is True
            or self._wifi_management_mode() == "mws"
        )

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
            if self._logical_profiles_are_authoritative():
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
        state = WifiNetworkReadMixin._interface_runtime_state(value)
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
