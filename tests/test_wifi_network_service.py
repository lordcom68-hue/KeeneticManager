from __future__ import annotations

import copy
import shlex
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from core.services.wifi_network_service import (
    WiFiNetworkService,
    WifiLogicalSnapshot,
    WifiNetworkCapabilities,
)
from core.services.traffic_service import TrafficService


class FakeInterfaceService:
    def __init__(
        self,
        runtime: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.runtime = copy.deepcopy(runtime or {})
        self.config = copy.deepcopy(config or {})
        self.state_calls: list[tuple[str, bool]] = []

    def read_runtime(self, *, required: bool = False) -> dict[str, Any]:
        return self.runtime

    def read_config(self, *, required: bool = False) -> dict[str, Any]:
        return self.config

    def set_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        name = str(interface_id)
        self.state_calls.append((name, bool(enabled)))
        item = self.config.setdefault(
            name,
            {"id": name, "type": "AccessPoint"},
        )
        if enabled:
            item["up"] = True
            self.runtime[name] = {
                "id": name,
                "type": "AccessPoint",
                "ssid": item.get("ssid", ""),
                "state": "up",
                "link": "up",
                "summary": {"layer": {"conf": "running"}},
            }
        else:
            item.pop("up", None)
            self.runtime[name] = {
                "id": name,
                "type": "AccessPoint",
                "ssid": item.get("ssid", ""),
                "state": "down",
                "link": "down",
                "summary": {"layer": {"conf": "disabled"}},
            }
        return True, (
            "Интерфейс включён."
            if enabled
            else "Интерфейс выключен."
        )


class FakeDiagnostics:
    def __init__(self) -> None:
        self.snapshots: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def record_snapshot(self, endpoint: str, data: Any) -> None:
        self.snapshots[str(endpoint)] = copy.deepcopy(data)

    def record_event(
        self,
        event_type: str,
        message: str,
        *,
        endpoint: str | None = None,
        details: Any = None,
    ) -> None:
        self.events.append(
            {
                "type": event_type,
                "message": message,
                "endpoint": endpoint,
                "details": copy.deepcopy(details),
            }
        )


class FakeClient:
    def __init__(
        self,
        runtime: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        mws_config: dict[str, Any] | None = None,
        mws_runtime: dict[str, Any] | None = None,
    ) -> None:
        self.session = object()
        self.base_url = "http://router.test"
        self.interface_service = FakeInterfaceService(runtime, config)
        self.traffic_service = TrafficService(self)
        self.mws_config = copy.deepcopy(mws_config or {})
        self.mws_runtime = copy.deepcopy(mws_runtime or {})
        self.batches: list[tuple[str, list[dict[str, Any]]]] = []
        self.query_payloads: list[Any] = []
        self.diagnostics = FakeDiagnostics()

    @staticmethod
    def quote_cli_argument(value: str) -> str:
        text = str(value)
        if text and all(ch.isalnum() or ch in "_./:@+-" for ch in text):
            return text
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    def post_rci_query(
        self,
        payload: Any,
        *,
        timeout: float = 25.0,
    ) -> tuple[bool, Any, str]:
        self.query_payloads.append(payload)

        def response_for(item: Any) -> dict[str, Any]:
            if item == WiFiNetworkService.LOGICAL_CONFIG_QUERY:
                return {
                    "show": {
                        "sc": {
                            "mws": {
                                "wlan": self.mws_config
                            }
                        }
                    }
                }
            if item == WiFiNetworkService.LOGICAL_RUNTIME_QUERY:
                return {
                    "show": {
                        "mws": {
                            "wlan": self.mws_runtime
                        }
                    }
                }
            return {}

        if isinstance(payload, list):
            return True, [response_for(item) for item in payload], ""
        return True, response_for(payload), ""

    def execute_rci_batch(
        self,
        commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        self.batches.append((operation_name, commands))
        self._apply(commands)
        return True, ""

    def _apply(self, commands: list[dict[str, Any]]) -> None:
        config = self.interface_service.config
        runtime = self.interface_service.runtime

        for command in commands:
            if not isinstance(command, dict):
                continue

            mws = command.get("mws")
            wlan = mws.get("wlan") if isinstance(mws, dict) else None
            if isinstance(wlan, dict):
                self._apply_mws_wlan(wlan)
                continue

            line = command.get("parse")
            if not isinstance(line, str):
                continue

            tokens = shlex.split(line)
            if (
                len(tokens) >= 5
                and tokens[0:2] == ["no", "interface"]
                and tokens[3] == "include"
            ):
                bridge = config.setdefault(tokens[2], {})
                included = bridge.setdefault("include", [])
                if isinstance(included, list):
                    bridge["include"] = [
                        value for value in included
                        if not (
                            isinstance(value, dict)
                            and value.get("interface") == tokens[4]
                        )
                    ]
                continue
            if len(tokens) < 3 or tokens[0] != "interface":
                continue

            interface_id = tokens[1]
            item = config.setdefault(
                interface_id,
                {
                    "id": interface_id,
                    "type": (
                        "Bridge"
                        if interface_id.startswith("Bridge")
                        else "AccessPoint"
                    ),
                },
            )

            if len(tokens) >= 4 and tokens[2] == "include":
                included = item.setdefault("include", [])
                if (
                    isinstance(included, list)
                    and not any(
                        isinstance(value, dict)
                        and value.get("interface") == tokens[3]
                        for value in included
                    )
                ):
                    included.append({"interface": tokens[3]})
            elif len(tokens) >= 4 and tokens[2] == "ssid":
                item["ssid"] = tokens[3]
            elif tokens[2:4] == ["no", "ssid"]:
                item.pop("ssid", None)
                runtime.pop(interface_id, None)
            elif tokens[2] == "up":
                item["up"] = True
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": item.get("ssid", ""),
                    "state": "up",
                    "link": "up",
                }
                self._set_mws_interface_enabled(interface_id, True)
            elif tokens[2:4] == ["no", "up"] or tokens[2] == "down":
                item.pop("up", None)
                runtime.pop(interface_id, None)
                self._set_mws_interface_enabled(interface_id, False)
            elif tokens[2:4] == ["hide-ssid"]:
                item["hide-ssid"] = True
            elif tokens[2:4] == ["no", "hide-ssid"]:
                item.pop("hide-ssid", None)
            elif len(tokens) >= 5 and tokens[2:4] == ["authentication", "wpa-psk"]:
                item["authentication"] = {
                    "wpa-psk": {"psk": tokens[4]}
                }
            elif tokens[2:5] == ["no", "authentication", "wpa-psk"]:
                item.pop("authentication", None)
            elif len(tokens) >= 5 and tokens[2:4] == ["follow", "access-point"]:
                item["follow"] = {"access-point": tokens[4]}
            elif tokens[2:5] == ["no", "follow", "access-point"]:
                item.pop("follow", None)
            elif len(tokens) >= 4 and tokens[2] == "encryption":
                encryption = item.setdefault("encryption", {})
                if tokens[3] == "disable":
                    encryption.clear()
                    encryption["enable"] = False
                elif tokens[3] == "enable":
                    encryption["enable"] = True
                else:
                    encryption[tokens[3]] = True


    def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
        profile_id = str(wlan.get("id") or "")
        if not profile_id:
            return

        if wlan.get("no") is True:
            runtime_profile = self.mws_runtime.pop(profile_id, {})
            self.mws_config.pop(profile_id, None)
            self._clear_profile_interfaces(runtime_profile)
            return

        creating = profile_id not in self.mws_config
        profile = self.mws_config.setdefault(profile_id, {})
        old_band_codes = [
            str(value)
            for value in profile.get("band") or []
        ]
        old_runtime_profile = copy.deepcopy(
            self.mws_runtime.get(profile_id, {})
        )
        for key, value in wlan.items():
            if key == "id":
                continue
            if (
                key == "band"
                and isinstance(value, dict)
                and value.get("no") is True
            ):
                profile["band"] = []
                continue
            if (
                isinstance(value, dict)
                and isinstance(profile.get(key), dict)
            ):
                profile[key].update(copy.deepcopy(value))
            else:
                profile[key] = copy.deepcopy(value)

        new_band_codes = [
            str(value)
            for value in profile.get("band") or []
        ]
        bands_changed = (
            not creating
            and "band" in wlan
            and old_band_codes != new_band_codes
        )
        if bands_changed:
            self._clear_profile_interfaces(old_runtime_profile)
            self.mws_runtime.pop(profile_id, None)

        if creating or bands_changed:
            self._assign_profile_interfaces(profile_id, profile)

        runtime_profile = self.mws_runtime.setdefault(
            profile_id,
            {"disabled-by-schedule": False, "band": {}},
        )
        enabled = bool(profile.get("enable", True))
        if "enable" in wlan:
            enabled = bool(wlan["enable"])

        bands = runtime_profile.get("band")
        if isinstance(bands, dict):
            for band_data in bands.values():
                if not isinstance(band_data, dict):
                    continue
                band_data["enabled"] = enabled
                access_points = band_data.get("access-point")
                if not isinstance(access_points, dict):
                    continue
                for interface_id in access_points:
                    item = self.interface_service.config.setdefault(
                        interface_id,
                        {"id": interface_id, "type": "AccessPoint"},
                    )
                    ssid_data = profile.get("ssid")
                    ssid = (
                        str(ssid_data.get("name") or "")
                        if isinstance(ssid_data, dict)
                        else ""
                    )
                    if ssid:
                        item["ssid"] = ssid
                    if isinstance(profile.get("wpa"), dict):
                        item["authentication"] = {
                            "wpa-psk": {
                                "psk": str(profile["wpa"].get("psk") or "")
                            }
                        }
                    self.interface_service.runtime[interface_id] = {
                        "id": interface_id,
                        "type": "AccessPoint",
                        "ssid": ssid,
                        "state": "up" if enabled else "down",
                        "link": "up" if enabled else "down",
                    }

    def _set_mws_interface_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> None:
        for runtime_profile in self.mws_runtime.values():
            if not isinstance(runtime_profile, dict):
                continue
            bands = runtime_profile.get("band")
            if not isinstance(bands, dict):
                continue
            for band_data in bands.values():
                if not isinstance(band_data, dict):
                    continue
                access_points = band_data.get("access-point")
                if (
                    isinstance(access_points, dict)
                    and interface_id in access_points
                ):
                    band_data["enabled"] = bool(enabled)

    def _assign_profile_interfaces(
        self,
        profile_id: str,
        profile: dict[str, Any],
    ) -> None:
        band_codes = [str(value) for value in profile.get("band") or []]
        code_to_master = {
            "0": "WifiMaster0",
            "1": "WifiMaster1",
            "2": "WifiMaster2",
        }
        used = {
            interface_id.casefold()
            for profile_interfaces in WiFiNetworkService.logical_profile_interfaces(
                self.mws_runtime
            ).values()
            for interface_id in profile_interfaces
        }
        by_code: dict[str, dict[int, str]] = {}
        for code in band_codes:
            master = code_to_master.get(code)
            if not master:
                continue
            slots: dict[int, str] = {}
            prefix = master + "/AccessPoint"
            for interface_id, item in self.interface_service.config.items():
                if not str(interface_id).startswith(prefix):
                    continue
                index = WiFiNetworkService._access_point_index(str(interface_id))
                if index is None or index < 2:
                    continue
                if str(interface_id).casefold() in used:
                    continue
                if isinstance(item, dict) and WiFiNetworkService.valid_ssid(
                    WiFiNetworkService._first_text(
                        item,
                        ("ssid", "network-name"),
                    )
                ):
                    continue
                slots[index] = str(interface_id)
            by_code[code] = slots

        common: set[int] | None = None
        for slots in by_code.values():
            indices = set(slots)
            common = indices if common is None else common & indices
        if not common:
            return
        index = min(common)
        runtime_bands: dict[str, Any] = {}
        enabled = bool(profile.get("enable", True))
        for code in band_codes:
            interface_id = by_code[code][index]
            runtime_bands[code] = {
                "enabled": enabled,
                "access-point": {interface_id: {}},
            }
        self.mws_runtime[profile_id] = {
            "disabled-by-schedule": False,
            "band": runtime_bands,
        }

    def _clear_profile_interfaces(
        self,
        runtime_profile: Any,
    ) -> None:
        if not isinstance(runtime_profile, dict):
            return
        bands = runtime_profile.get("band")
        if not isinstance(bands, dict):
            return
        for band_data in bands.values():
            if not isinstance(band_data, dict):
                continue
            access_points = band_data.get("access-point")
            if not isinstance(access_points, dict):
                continue
            for interface_id in access_points:
                item = self.interface_service.config.get(interface_id)
                if isinstance(item, dict):
                    item.pop("ssid", None)
                    item.pop("authentication", None)
                runtime = self.interface_service.runtime.get(interface_id)
                if isinstance(runtime, dict):
                    runtime.pop("ssid", None)
                    runtime["state"] = "down"
                    runtime["link"] = "down"


class FailFirstLogicalClient(FakeClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.logical_write_count = 0

    def _apply(self, commands: list[dict[str, Any]]) -> None:
        filtered: list[dict[str, Any]] = []
        for command in commands:
            if (
                isinstance(command, dict)
                and isinstance(command.get("mws"), dict)
            ):
                self.logical_write_count += 1
                if self.logical_write_count == 1:
                    continue
            filtered.append(command)
        super()._apply(filtered)


class NoMwsClient(FakeClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.version_data = {"release": "4.03.C.8.0-0"}

    @staticmethod
    def extract_version(data: dict[str, Any]) -> str:
        return str(data.get("release") or "")

    def post_rci_query(
        self,
        payload: Any,
        *,
        timeout: float = 25.0,
    ) -> tuple[bool, Any, str]:
        self.query_payloads.append(payload)
        return False, {}, "not found: mws/wlan"


class WiFiNetworkServiceTests(unittest.TestCase):
    def test_cached_client_sample_preserves_measurement_interval(self) -> None:
        service = WiFiNetworkService(FakeClient())

        def networks(rxbytes: int, txbytes: int) -> list[dict[str, Any]]:
            return [
                {
                    "clients": [
                        {
                            "mac": "aa:bb:cc:dd:ee:ff",
                            "rxbytes": rxbytes,
                            "txbytes": txbytes,
                        }
                    ]
                }
            ]

        first = networks(100, 200)
        second = networks(200, 300)
        cached = networks(200, 300)
        fourth = networks(300, 400)
        service.client.traffic_service.clock = Mock(
            side_effect=(100.0, 105.0, 110.0, 115.0)
        )
        service.update_client_activity(first, fresh_sample=True)
        service.update_client_activity(second, fresh_sample=True)
        service.update_client_activity(cached, fresh_sample=False)
        service.update_client_activity(fourth, fresh_sample=True)

        self.assertAlmostEqual(second[0]["clients"][0]["rx_bps"], 160.0)
        self.assertAlmostEqual(cached[0]["clients"][0]["rx_bps"], 160.0)
        self.assertAlmostEqual(fourth[0]["clients"][0]["rx_bps"], 160.0)
        self.assertEqual(
            service.client_counters["aa:bb:cc:dd:ee:ff"][0],
            110.0,
        )
        service.update_client_activity([], fresh_sample=True)
        self.assertNotIn(
            "client:aa:bb:cc:dd:ee:ff",
            service.client.traffic_service.counters,
        )

    def test_validation_and_band_normalization(self) -> None:
        self.assertTrue(WiFiNetworkService.valid_ssid("Home"))
        self.assertFalse(WiFiNetworkService.valid_ssid(""))
        self.assertTrue(WiFiNetworkService.valid_password("12345678"))
        self.assertFalse(WiFiNetworkService.valid_password("short"))
        self.assertEqual(
            WiFiNetworkService.normalize_bands(["2,4 ГГц", "5GHz"]),
            ("2.4", "5"),
        )

    @staticmethod
    def _logical_fixture() -> tuple[
        dict[str, Any],
        dict[str, Any],
        list[str],
    ]:
        interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster0/AccessPoint1",
            "WifiMaster1/AccessPoint0",
            "WifiMaster1/AccessPoint1",
        ]
        mws_config = {
            "DEFAULT__": {
                "band": ["0", "1"],
                "bind": {"interface": "Bridge0"},
                "ssid": {"name": "Keenetic8"},
                "encryption": "wpa2+3",
                "wpa": {"psk": "old-password"},
                "wps": {"enable": True},
                "rrm": {"enable": True},
                "ft": {"mdid": "8d", "enable": True},
                "enable": True,
            },
            "wlan0": {
                "band": ["1"],
                "bind": {"interface": "Bridge1"},
                "ssid": {"name": "Temporary"},
                "encryption": "wpa2",
                "wpa": {"psk": "temporary-password"},
                "enable": True,
            },
        }
        mws_runtime = {
            "DEFAULT__": {
                "disabled-by-schedule": False,
                "band": {
                    "0": {
                        "enabled": True,
                        "access-point": {
                            interfaces[0]: {},
                            interfaces[1]: {},
                        },
                    },
                    "1": {
                        "enabled": False,
                        "access-point": {
                            interfaces[2]: {},
                            interfaces[3]: {},
                        },
                    },
                },
            },
            "wlan0": {
                "disabled-by-schedule": False,
                "band": {
                    "1": {
                        "enabled": True,
                        "access-point": {
                            "WifiMaster1/AccessPoint2": {},
                        },
                    }
                },
            },
        }
        return mws_config, mws_runtime, interfaces

    def test_logical_snapshot_matches_profile_by_access_points(self) -> None:
        mws_config, mws_runtime, interfaces = self._logical_fixture()
        client = FakeClient(
            mws_config=mws_config,
            mws_runtime=mws_runtime,
        )
        service = WiFiNetworkService(client)

        snapshot = service.read_logical_snapshot(force=True)
        self.assertIsInstance(snapshot, WifiLogicalSnapshot)
        self.assertEqual(
            snapshot.config["DEFAULT__"]["ssid"]["name"],
            "Keenetic8",
        )
        profile_id, mapped = service.match_logical_profile(
            interfaces,
            snapshot,
        )
        self.assertEqual(profile_id, "DEFAULT__")
        self.assertEqual(set(mapped), set(interfaces))
        self.assertEqual(len(client.query_payloads), 1)

    def test_known_unsupported_firmware_skips_mws_queries(self) -> None:
        client = NoMwsClient()
        service = WiFiNetworkService(client)

        first = service.read_logical_snapshot(force=True)
        second = service.read_logical_snapshot(force=True)

        self.assertEqual(first, WifiLogicalSnapshot({}, {}))
        self.assertEqual(second, WifiLogicalSnapshot({}, {}))
        self.assertEqual(client.query_payloads, [])
        self.assertFalse(service._logical_management_supported)

    def test_failed_unknown_mws_probe_is_not_repeated(self) -> None:
        client = FakeClient()

        def fail_query(
            payload: Any,
            *,
            timeout: float = 25.0,
        ) -> tuple[bool, Any, str]:
            client.query_payloads.append(payload)
            return False, {}, "not found: mws/wlan"

        client.post_rci_query = fail_query  # type: ignore[method-assign]
        service = WiFiNetworkService(client)

        service.read_logical_snapshot(force=True)
        service.read_logical_snapshot(force=True)

        self.assertEqual(len(client.query_payloads), 1)
        self.assertFalse(service._logical_management_supported)

    def test_update_syncs_logical_profile_and_physical_access_points(
        self,
    ) -> None:
        mws_config, mws_runtime, interfaces = self._logical_fixture()
        client = FakeClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic8",
                    "state": "up",
                    "link": "up",
                }
                for interface_id in interfaces
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic8",
                    "authentication": {
                        "wpa-psk": {"psk": "old-password"}
                    },
                    "encryption": {
                        "enable": True,
                        "wpa2": True,
                        "wpa3": {"enabled": True},
                    },
                }
                for interface_id in interfaces
            },
            mws_config=mws_config,
            mws_runtime=mws_runtime,
        )
        service = WiFiNetworkService(client)

        success, message, details = service.update_network(
            interfaces,
            {
                "ssid": "Keenetic9",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(details["mws_profile_id"], "DEFAULT__")
        self.assertTrue(details["logical_profile_updated"])
        self.assertEqual(
            client.mws_config["DEFAULT__"]["ssid"]["name"],
            "Keenetic9",
        )
        self.assertEqual(
            client.mws_config["DEFAULT__"]["encryption"],
            "wpa2+3",
        )
        self.assertEqual(
            client.mws_config["DEFAULT__"]["wps"],
            {"enable": True},
        )
        for interface_id in interfaces:
            self.assertEqual(
                client.interface_service.config[
                    interface_id
                ]["ssid"],
                "Keenetic9",
            )

        logical_command = client.batches[0][1][0]
        self.assertEqual(
            logical_command,
            {
                "mws": {
                    "wlan": {
                        "id": "DEFAULT__",
                        "ssid": {
                            "name": "Keenetic9",
                            "hide": False,
                        },
                    }
                }
            },
        )

    def test_rename_restores_access_points_dropped_by_mws_update(
        self,
    ) -> None:
        interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint1",
        ]

        class RenameDropsAccessPointsClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                profile_id = str(wlan.get("id") or "")
                updating_ssid = (
                    profile_id in self.mws_config
                    and isinstance(wlan.get("ssid"), dict)
                )
                super()._apply_mws_wlan(wlan)
                if not updating_ssid:
                    return

                runtime_profile = self.mws_runtime.get(profile_id)
                bands = (
                    runtime_profile.get("band")
                    if isinstance(runtime_profile, dict)
                    else None
                )
                if not isinstance(bands, dict):
                    return
                for band_data in bands.values():
                    if not isinstance(band_data, dict):
                        continue
                    band_data["enabled"] = False
                    access_points = band_data.get("access-point")
                    if not isinstance(access_points, dict):
                        continue
                    for interface_id in access_points:
                        item = self.interface_service.config[interface_id]
                        item.pop("up", None)
                        self.interface_service.runtime[interface_id] = {
                            "id": interface_id,
                            "type": "AccessPoint",
                            "ssid": item.get("ssid", ""),
                            "state": "down",
                            "link": "down",
                            "summary": {"layer": {"conf": "disabled"}},
                        }

        client = RenameDropsAccessPointsClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic1",
                    "state": "up",
                    "link": "up",
                }
                for interface_id in interfaces
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic1",
                    "up": True,
                }
                for interface_id in interfaces
            },
            mws_config={
                "wlan0": {
                    "band": ["0", "1"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Keenetic1"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {interfaces[1]: {}},
                        },
                    }
                }
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            interfaces,
            {
                "ssid": "Keenetic12",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertTrue(success, message)
        self.assertTrue(details["physical_state_preserved"])
        parse_commands = [
            command["parse"]
            for command in client.batches[0][1]
            if isinstance(command.get("parse"), str)
        ]
        for interface_id in interfaces:
            self.assertIn(f"interface {interface_id} up", parse_commands)
            self.assertTrue(
                client.interface_service.config[interface_id]["up"]
            )
            self.assertEqual(
                client.interface_service.runtime[interface_id]["state"],
                "up",
            )

    def test_rename_preserves_disabled_access_points(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Disabled",
                    "state": "down",
                    "link": "down",
                }
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Disabled",
                }
            },
            mws_config={
                "wlan0": {
                    "band": ["0"],
                    "ssid": {"name": "Disabled"},
                    "enable": False,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )

        success, message, _details = WiFiNetworkService(
            client
        ).update_network(
            [interface_id],
            {
                "ssid": "Still-Disabled",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertTrue(success, message)
        self.assertIn(
            f"interface {interface_id} no up",
            [
                command["parse"]
                for command in client.batches[0][1]
                if isinstance(command.get("parse"), str)
            ],
        )
        self.assertNotIn(
            "up",
            client.interface_service.config[interface_id],
        )
        runtime_item = client.interface_service.runtime.get(interface_id)
        self.assertTrue(
            runtime_item is None or runtime_item.get("state") == "down"
        )

    def test_renaming_second_network_restores_first_network(self) -> None:
        first_interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint1",
        ]
        second_interfaces = [
            "WifiMaster0/AccessPoint1",
            "WifiMaster1/AccessPoint2",
        ]
        all_interfaces = first_interfaces + second_interfaces

        class MwsUpdateDropsAllProfilesClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                profile_id = str(wlan.get("id") or "")
                updating_ssid = (
                    profile_id in self.mws_config
                    and isinstance(wlan.get("ssid"), dict)
                )
                super()._apply_mws_wlan(wlan)
                if not updating_ssid:
                    return

                for runtime_profile in self.mws_runtime.values():
                    if not isinstance(runtime_profile, dict):
                        continue
                    bands = runtime_profile.get("band")
                    if not isinstance(bands, dict):
                        continue
                    for band_data in bands.values():
                        if not isinstance(band_data, dict):
                            continue
                        band_data["enabled"] = False
                        access_points = band_data.get("access-point")
                        if not isinstance(access_points, dict):
                            continue
                        for interface_id in access_points:
                            item = self.interface_service.config[
                                interface_id
                            ]
                            item.pop("up", None)
                            self.interface_service.runtime[interface_id] = {
                                "id": interface_id,
                                "type": "AccessPoint",
                                "ssid": item.get("ssid", ""),
                                "state": "down",
                                "link": "down",
                                "summary": {
                                    "layer": {"conf": "disabled"}
                                },
                            }

        client = MwsUpdateDropsAllProfilesClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": (
                        "Keenetic1"
                        if interface_id in first_interfaces
                        else "test2"
                    ),
                    "state": "up",
                    "link": "up",
                }
                for interface_id in all_interfaces
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": (
                        "Keenetic1"
                        if interface_id in first_interfaces
                        else "test2"
                    ),
                    "up": True,
                }
                for interface_id in all_interfaces
            },
            mws_config={
                "wlan0": {
                    "band": ["0", "1"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Keenetic1"},
                    "enable": True,
                },
                "wlan1": {
                    "band": ["0", "1"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "test2"},
                    "enable": True,
                },
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {first_interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {first_interfaces[1]: {}},
                        },
                    }
                },
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {second_interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {second_interfaces[1]: {}},
                        },
                    }
                },
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            second_interfaces,
            {
                "ssid": "123",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(
            set(details["preserved_interfaces"]),
            set(all_interfaces),
        )
        self.assertEqual(
            client.mws_config["wlan0"]["ssid"]["name"],
            "Keenetic1",
        )
        self.assertEqual(
            client.mws_config["wlan1"]["ssid"]["name"],
            "123",
        )
        update_event = client.diagnostics.events[-1]
        self.assertEqual(update_event["type"], "wifi_mws_update")
        self.assertTrue(update_event["details"]["verified"])
        self.assertEqual(
            set(
                update_event["details"][
                    "preserved_interface_states"
                ]
            ),
            set(all_interfaces),
        )
        for interface_id in all_interfaces:
            self.assertTrue(
                client.interface_service.config[interface_id]["up"]
            )
            self.assertEqual(
                client.interface_service.runtime[interface_id]["state"],
                "up",
            )

    def test_system_rename_stops_when_logical_profile_is_missing(
        self,
    ) -> None:
        interface_id = "WifiMaster0/AccessPoint0"
        client = FakeClient(
            runtime={
                interface_id: {
                    "ssid": "Old",
                    "state": "up",
                    "link": "up",
                }
            },
            config={
                interface_id: {
                    "ssid": "Old",
                    "type": "AccessPoint",
                }
            },
        )

        success, _message, details = WiFiNetworkService(
            client
        ).update_network(
            [interface_id],
            {
                "ssid": "New",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertTrue(details["logical_profile_missing"])
        self.assertEqual(client.batches, [])

    def test_verification_failure_rolls_back_both_layers(self) -> None:
        mws_config, mws_runtime, interfaces = self._logical_fixture()
        client = FailFirstLogicalClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic8",
                    "state": "up",
                    "link": "up",
                }
                for interface_id in interfaces
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic8",
                }
                for interface_id in interfaces
            },
            mws_config=mws_config,
            mws_runtime=mws_runtime,
        )

        success, _message, details = WiFiNetworkService(
            client
        ).update_network(
            interfaces,
            {
                "ssid": "Broken-Rename",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
            },
        )

        self.assertFalse(success)
        self.assertTrue(details["rolled_back"])
        self.assertEqual(
            client.mws_config["DEFAULT__"]["ssid"]["name"],
            "Keenetic8",
        )
        for interface_id in interfaces:
            self.assertEqual(
                client.interface_service.config[
                    interface_id
                ]["ssid"],
                "Keenetic8",
            )
        self.assertEqual(len(client.batches), 2)

    def test_capabilities_find_supported_bands_and_free_slots(self) -> None:
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }

        config["WifiMaster0/AccessPoint2"]["ssid"] = "Used"

        client = FakeClient(runtime=runtime, config=config)
        result = WiFiNetworkService(client).capabilities(
            interface_data=client.interface_service.runtime,
            interface_config_data=client.interface_service.config,
        )
        self.assertIsInstance(result, WifiNetworkCapabilities)
        self.assertEqual(result.supported_bands, ("2.4", "5"))
        self.assertNotIn(
            "WifiMaster0/AccessPoint2",
            result.available_slots["2.4"],
        )
        self.assertEqual(
            result.available_slots["2.4"][0],
            "WifiMaster0/AccessPoint3",
        )
        self.assertEqual(
            result.available_slots["5"][0],
            "WifiMaster1/AccessPoint2",
        )
        self.assertFalse(
            any(
                value.endswith("AccessPoint7")
                for values in result.available_slots.values()
                for value in values
            )
        )

    def test_capabilities_hide_bands_owned_in_home_segment(self) -> None:
        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }
        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan0": {
                    "bind": {"interface": "Bridge0"},
                    "band": ["0"],
                    "ssid": {"name": "First"},
                },
                "wlan1": {
                    "bind": {"interface": "Bridge0"},
                    "band": ["1"],
                    "ssid": {"name": "Second"},
                },
            },
        )

        result = WiFiNetworkService(client).capabilities(
            interface_data=runtime,
            interface_config_data=config,
        )

        self.assertEqual(result.available_slots["2.4"], ())
        self.assertEqual(result.available_slots["5"], ())
        self.assertTrue(result.physical_available_slots["2.4"])
        self.assertTrue(result.physical_available_slots["5"])

    def test_disabled_configured_network_survives_without_runtime(self) -> None:
        client = NoMwsClient()
        service = WiFiNetworkService(client)
        networks = service.build_networks(
            interface_data={},
            interface_config_data={
                "WifiMaster0/AccessPoint2": {
                    "id": "WifiMaster0/AccessPoint2",
                    "type": "AccessPoint",
                    "ssid": "Sleeping",
                    "authentication": {"wpa-psk": {}},
                    "encryption": {"wpa2": {}},
                }
            },
            associations_data={},
            hotspot_data={},
        )
        wifi = next(item for item in networks if item.get("ssid") == "Sleeping")
        self.assertFalse(wifi["enabled"])
        self.assertFalse(wifi["online"])
        self.assertEqual(wifi["client_count"], 0)
        self.assertEqual(
            wifi["band_states"]["2.4"]["interface_id"],
            "WifiMaster0/AccessPoint2",
        )

    def test_same_ssid_is_merged_with_independent_band_states(self) -> None:
        client = NoMwsClient()
        service = WiFiNetworkService(client)
        runtime = {
            "WifiMaster0/AccessPoint2": {
                "id": "WifiMaster0/AccessPoint2",
                "type": "AccessPoint",
                "ssid": "Home",
                "state": "up",
                "link": "up",
            }
        }
        config = {
            "WifiMaster0/AccessPoint2": {
                "id": "WifiMaster0/AccessPoint2",
                "type": "AccessPoint",
                "ssid": "Home",
            },
            "WifiMaster1/AccessPoint2": {
                "id": "WifiMaster1/AccessPoint2",
                "type": "AccessPoint",
                "ssid": "Home",
            },
        }
        networks = service.build_networks(
            interface_data=runtime,
            interface_config_data=config,
            associations_data={},
            hotspot_data={},
        )
        home = next(item for item in networks if item.get("ssid") == "Home")
        self.assertEqual(set(home["band_states"]), {"2.4", "5"})
        self.assertTrue(home["partially_enabled"])
        self.assertTrue(home["enabled"])
        self.assertFalse(home["all_enabled"])

    def test_rename_replaces_cached_ssid_without_ghost_network(self) -> None:
        service = WiFiNetworkService(FakeClient())
        interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint0",
        ]
        old_network = {
            "network_type": "wifi",
            "group": "Основная сеть",
            "ssid": "Keenetic1",
            "interfaces": interfaces,
            "interface_id": ", ".join(interfaces),
            "enabled": True,
            "online": True,
            "clients": [],
        }
        new_network = {
            **old_network,
            "ssid": "Keenetic12",
        }

        first = service.merge_known_networks([old_network])
        second = service.merge_known_networks([new_network])

        self.assertEqual(
            [item["ssid"] for item in first],
            ["Keenetic1"],
        )
        self.assertEqual(
            [item["ssid"] for item in second],
            ["Keenetic12"],
        )
        self.assertEqual(len(service.known_networks), 1)
        self.assertEqual(
            WiFiNetworkService.network_cache_key(old_network),
            WiFiNetworkService.network_cache_key(new_network),
        )

    def test_update_rejects_missing_interface_instead_of_creating_ap(self) -> None:
        client = FakeClient(
            runtime={"WifiMaster0": {"id": "WifiMaster0"}},
            config={"Bridge0": {}},
        )
        success, _message, details = WiFiNetworkService(
            client
        ).update_network(
            ["WifiMaster0/AccessPoint2"],
            {
                "ssid": "Renamed",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "hidden": False,
            },
        )

        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(
            details["missing_interfaces"],
            ["WifiMaster0/AccessPoint2"],
        )
        self.assertEqual(client.batches, [])

    def test_create_uses_same_free_slot_on_both_bands(self) -> None:
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }

        client = FakeClient(runtime=runtime, config=config)
        success, message, details = WiFiNetworkService(client).create_network(
            {
                "ssid": "Family",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4", "5"],
                "hidden": False,
                "enabled": True,
            }
        )
        self.assertTrue(success, message)
        self.assertEqual(
            details["interfaces"],
            [
                "WifiMaster0/AccessPoint2",
                "WifiMaster1/AccessPoint2",
            ],
        )
        commands = client.batches[0][1]
        self.assertEqual(
            commands[0],
            {
                "mws": {
                    "wlan": {
                        "id": "wlan0",
                        "bind": {"interface": "Bridge0"},
                        "band": ["0", "1"],
                        "encryption": "wpa2",
                        "ssid": {"name": "Family", "hide": False},
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
                        "enable": True,
                        "wpa": {"psk": "12345678"},
                    }
                }
            },
        )
        self.assertFalse(
            any("parse" in item for item in commands if isinstance(item, dict))
        )

    def test_create_uses_legacy_mode_when_router_has_no_mws_api(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = NoMwsClient(
            runtime={
                "WifiMaster0": {"id": "WifiMaster0"},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                },
            },
            config={
                "Bridge0": {},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                },
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "Unsupported",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4"],
            }
        )

        self.assertTrue(success, message)
        self.assertTrue(details["legacy_managed"])
        self.assertEqual(details["interfaces"], [interface_id])
        self.assertEqual(
            client.interface_service.config[interface_id]["ssid"],
            "Unsupported",
        )
        self.assertTrue(
            any(
                command.get("parse")
                == f"interface Bridge0 include {interface_id}"
                for command in client.batches[0][1]
            )
        )

    def test_keeneticos5_without_mws_does_not_use_legacy_mode(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = NoMwsClient(
            runtime={
                "WifiMaster0": {"id": "WifiMaster0"},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                },
            },
            config={
                "Bridge0": {},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                },
            },
        )
        client.version_data = {"release": "5.00.C.12.0-0"}

        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "Unsupported",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4"],
            }
        )

        self.assertFalse(success)
        self.assertIn("не предоставляет", message)
        self.assertTrue(details["mws_unsupported"])
        self.assertEqual(client.batches, [])

    def test_create_allows_dormant_profile_without_band(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient(
            runtime={
                "WifiMaster0": {"id": "WifiMaster0"},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                },
            },
            config={
                "Bridge0": {},
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                },
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "Dormant",
                "password": "12345678",
                "security": "wpa2",
                "bands": [],
                "enabled": True,
            }
        )

        self.assertTrue(success, message)
        self.assertEqual(details["interfaces"], [])
        self.assertEqual(details["bands"], [])
        self.assertEqual(client.mws_config["wlan0"]["band"], [])
        self.assertFalse(client.mws_config["wlan0"]["enable"])
        self.assertEqual(
            client.batches[0][1][0]["mws"]["wlan"]["band"],
            {"no": True},
        )

    def test_update_can_assign_band_to_dormant_profile(self) -> None:
        runtime: dict[str, Any] = {
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for index in range(7):
            interface_id = f"WifiMaster1/AccessPoint{index}"
            runtime[interface_id] = {
                "id": interface_id,
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            }
            config[interface_id] = {
                "id": interface_id,
                "type": "AccessPoint",
            }
        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan2": {
                    "band": [],
                    "ssid": {"name": "Dormant", "hide": False},
                    "encryption": "wpa2",
                }
            },
            mws_runtime={
                "wlan2": {"band": {}}
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [],
            {
                "mws_profile_id": "wlan2",
                "ssid": "Dormant",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "hidden": False,
                "bands": ["5"],
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(client.mws_config["wlan2"]["band"], ["1"])
        self.assertEqual(details["bands"], ["5"])
        self.assertEqual(len(details["interfaces"]), 1)
        self.assertTrue(
            details["interfaces"][0].startswith(
                "WifiMaster1/AccessPoint"
            )
        )
        self.assertFalse(client.mws_config["wlan2"]["enable"])
        self.assertFalse(
            client.batches[0][1][0]["mws"]["wlan"]["enable"]
        )

    def test_assign_band_accepts_omitted_false_and_stale_mws_state(
        self,
    ) -> None:
        class KeeneticOmittedFalseClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                super()._apply_mws_wlan(wlan)
                profile_id = str(wlan.get("id") or "")
                if "band" not in wlan or wlan.get("enable") is not False:
                    return
                self.mws_config[profile_id].pop("enable", None)
                runtime_profile = self.mws_runtime.get(profile_id, {})
                for band_data in (runtime_profile.get("band") or {}).values():
                    if isinstance(band_data, dict):
                        band_data["enabled"] = True

            def _set_mws_interface_enabled(
                self,
                interface_id: str,
                enabled: bool,
            ) -> None:
                # Some KeeneticOS builds keep the MWS runtime flag stale even
                # after the physical AP is administratively disabled.
                return

        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for index in range(7):
            interface_id = f"WifiMaster0/AccessPoint{index}"
            runtime[interface_id] = {
                "id": interface_id,
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            }
            config[interface_id] = {
                "id": interface_id,
                "type": "AccessPoint",
            }

        stale_interface = "WifiMaster0/AccessPoint0"
        config[stale_interface]["ssid"] = "Keenetic12"
        client = KeeneticOmittedFalseClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan0": {
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Keenetic12"},
                    "encryption": "wpa2",
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {stale_interface: {}},
                        }
                    }
                }
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [],
            {
                "mws_profile_id": "wlan0",
                "ssid": "Keenetic12",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
                "bands": ["2.4"],
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(details["bands"], ["2.4"])
        self.assertNotIn("enable", client.mws_config["wlan0"])
        assigned = details["interfaces"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(
            client.interface_service.runtime.get(assigned[0], {}).get(
                "state",
                "down",
            ),
            "down",
        )

    def test_bandless_rollback_does_not_require_ap_linkage(self) -> None:
        stale_interface = "WifiMaster0/AccessPoint0"
        client = FakeClient(
            runtime={
                stale_interface: {
                    "id": stale_interface,
                    "state": "down",
                    "link": "down",
                }
            },
            config={
                stale_interface: {
                    "id": stale_interface,
                    "type": "AccessPoint",
                    "ssid": "Changed",
                }
            },
            mws_config={
                "wlan0": {
                    "band": ["0"],
                    "ssid": {"name": "Changed"},
                    "enable": False,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {stale_interface: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        state = {
            "profile_id": "wlan0",
            "profile_config": {
                "ssid": {"name": "Keenetic12"},
            },
            "logical_ssid": "Keenetic12",
            "logical_hidden": False,
            "logical_bands": [],
            "logical_enabled": False,
            "bands_changed": True,
            "interfaces": [stale_interface],
            "physical": {
                stale_interface: {
                    "ssid": "Keenetic12",
                    "hidden": False,
                    "enabled": False,
                }
            },
            "preserved_interface_states": {stale_interface: False},
            "change_password": False,
            "change_security": False,
        }

        success, message = service._rollback_network_update(state)

        self.assertTrue(success, message)
        self.assertEqual(
            client.mws_config["wlan0"]["ssid"]["name"],
            "Keenetic12",
        )
        self.assertEqual(client.mws_config["wlan0"]["band"], [])

    def test_returning_mws_band_ignores_own_stale_runtime_ssid(self) -> None:
        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {
            "Bridge0": {},
        }
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }

        stale_24 = "WifiMaster0/AccessPoint1"
        active_5 = "WifiMaster1/AccessPoint2"
        for interface_id in (stale_24, active_5):
            config[interface_id]["ssid"] = "1234"
        config[active_5]["up"] = True
        runtime[active_5].update(
            {"ssid": "1234", "state": "up", "link": "up"}
        )

        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan1": {
                    "band": ["1"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "1234", "hide": False},
                    "enable": True,
                    "encryption": "wpa2",
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {stale_24: {}},
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {active_5: {}},
                        },
                    }
                }
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [active_5],
            {
                "mws_profile_id": "wlan1",
                "ssid": "1234",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
                "bands": ["2.4", "5"],
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(client.mws_config["wlan1"]["band"], ["0", "1"])
        self.assertEqual(set(details["bands"]), {"2.4", "5"})

    def test_legacy_dual_band_create_uses_follow_and_bridge(self) -> None:
        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {"include": []}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(2, 4):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }
        client = NoMwsClient(runtime=runtime, config=config)

        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "Legacy",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4", "5"],
                "enabled": True,
            }
        )

        self.assertTrue(success, message)
        self.assertTrue(details["legacy_managed"])
        ap24 = "WifiMaster0/AccessPoint2"
        ap5 = "WifiMaster1/AccessPoint2"
        self.assertEqual(
            client.interface_service.config[ap5]["follow"],
            {"access-point": ap24},
        )
        included = client.interface_service.config["Bridge0"]["include"]
        self.assertEqual(
            {item["interface"] for item in included},
            {ap24, ap5},
        )

    def test_legacy_network_can_add_band_toggle_and_delete(self) -> None:
        ap24 = "WifiMaster0/AccessPoint2"
        ap5 = "WifiMaster1/AccessPoint2"
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
            ap24: {
                "id": ap24,
                "type": "AccessPoint",
                "ssid": "Legacy",
                "state": "up",
                "link": "up",
            },
            ap5: {
                "id": ap5,
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
        }
        config = {
            "Bridge0": {"include": [{"interface": ap24}]},
            ap24: {
                "id": ap24,
                "type": "AccessPoint",
                "ssid": "Legacy",
                "authentication": {"wpa-psk": {"psk": "12345678"}},
                "encryption": {"enable": True, "wpa2": True},
                "up": True,
            },
            ap5: {"id": ap5, "type": "AccessPoint"},
        }
        client = NoMwsClient(runtime=runtime, config=config)
        service = WiFiNetworkService(client)

        success, message, details = service.update_network(
            [ap24],
            {
                "ssid": "Legacy",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
                "bands": ["2.4", "5"],
            },
        )
        self.assertTrue(success, message)
        self.assertEqual(details["interfaces"], [ap24, ap5])
        self.assertEqual(
            client.interface_service.config[ap5]["follow"],
            {"access-point": ap24},
        )

        reference = {
            "interfaces": [ap24, ap5],
            "legacy_managed": True,
        }
        success, message, details = service.set_network_enabled(
            reference,
            False,
        )
        self.assertTrue(success, message)
        self.assertFalse(details["enabled"])

        success, message, details = service.delete_network(reference)
        self.assertTrue(success, message)
        self.assertTrue(details["legacy_managed"])
        self.assertNotIn("ssid", client.interface_service.config[ap24])
        self.assertNotIn("ssid", client.interface_service.config[ap5])

    def test_legacy_system_network_rename_preserves_follow_and_state(self) -> None:
        ap24 = "WifiMaster0/AccessPoint0"
        ap5 = "WifiMaster1/AccessPoint0"
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
            ap24: {
                "id": ap24,
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
            ap5: {
                "id": ap5,
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
        }
        config = {
            "Bridge0": {
                "include": [
                    {"interface": ap24},
                    {"interface": ap5},
                ]
            },
            ap24: {
                "ssid": "Old",
                "authentication": {"wpa-psk": {"psk": "12345678"}},
                "encryption": {"enable": True, "wpa2": True},
            },
            ap5: {
                "ssid": "Old",
                "authentication": {"wpa-psk": {"psk": "12345678"}},
                "encryption": {"enable": True, "wpa2": True},
                "follow": {"access-point": ap24},
            },
        }
        client = NoMwsClient(runtime=runtime, config=config)

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [ap24, ap5],
            {
                "ssid": "New",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
                "bands": ["2.4", "5"],
            },
        )

        self.assertTrue(success, message)
        self.assertTrue(details["legacy_managed"])
        self.assertEqual(client.interface_service.config[ap24]["ssid"], "New")
        self.assertEqual(client.interface_service.config[ap5]["ssid"], "New")
        self.assertEqual(
            client.interface_service.config[ap5]["follow"],
            {"access-point": ap24},
        )
        self.assertNotIn("up", client.interface_service.config[ap24])
        self.assertNotIn("up", client.interface_service.config[ap5])

    def test_update_can_move_mws_network_to_another_band(self) -> None:
        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                }

        neighbour_interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint0",
        ]
        old_target = "WifiMaster0/AccessPoint2"
        for interface_id in neighbour_interfaces:
            config[interface_id]["ssid"] = "Main"
            config[interface_id]["up"] = True
            runtime[interface_id].update(
                {"ssid": "Main", "state": "up", "link": "up"}
            )
        config[old_target]["ssid"] = "Old"
        config[old_target]["up"] = True
        runtime[old_target].update(
            {"ssid": "Old", "state": "up", "link": "up"}
        )

        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan0": {
                    "band": ["0", "1"],
                    "bind": {"interface": "Bridge1"},
                    "ssid": {"name": "Main", "hide": False},
                    "enable": True,
                },
                "wlan1": {
                    "band": ["0"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Old", "hide": False},
                    "enable": True,
                    "encryption": "wpa2",
                },
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                neighbour_interfaces[0]: {}
                            },
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {
                                neighbour_interfaces[1]: {}
                            },
                        },
                    }
                },
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {old_target: {}},
                        }
                    }
                },
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [old_target],
            {
                "ssid": "Moved",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "change_security": False,
                "hidden": False,
                "bands": ["5"],
            },
        )

        self.assertTrue(success, message)
        self.assertEqual(client.mws_config["wlan1"]["band"], ["1"])
        self.assertEqual(details["bands"], ["5"])
        self.assertEqual(len(details["interfaces"]), 1)
        new_target = details["interfaces"][0]
        self.assertTrue(new_target.startswith("WifiMaster1/AccessPoint"))
        self.assertEqual(
            client.interface_service.config[new_target]["ssid"],
            "Moved",
        )
        self.assertEqual(
            client.interface_service.runtime[new_target]["state"],
            "up",
        )
        for interface_id in neighbour_interfaces:
            self.assertEqual(
                client.interface_service.runtime[interface_id]["state"],
                "up",
            )

    def test_update_rejects_band_occupied_by_other_networks(self) -> None:
        current = "WifiMaster0/AccessPoint2"
        runtime: dict[str, Any] = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                used = master == "WifiMaster1" and index >= 2
                ssid = f"Used-{index}" if used else ""
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    **({"ssid": ssid} if ssid else {}),
                }
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": ssid,
                    "state": "up" if used else "down",
                    "link": "up" if used else "down",
                }
        config[current]["ssid"] = "Current"
        config[current]["up"] = True
        runtime[current].update(
            {"ssid": "Current", "state": "up", "link": "up"}
        )
        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan1": {
                    "band": ["0"],
                    "ssid": {"name": "Current"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {current: {}},
                        }
                    }
                }
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).update_network(
            [current],
            {
                "ssid": "Current",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "hidden": False,
                "bands": ["2.4", "5"],
            },
        )

        self.assertFalse(success)
        self.assertIn("5 ГГц", message)
        self.assertEqual(details["unavailable_bands"], ["5"])
        self.assertEqual(client.batches, [])

    def test_create_never_invents_access_point7_when_real_slots_are_full(
        self,
    ) -> None:
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster1": {"id": "WifiMaster1"},
        }
        config: dict[str, Any] = {"Bridge0": {}}
        for master in ("WifiMaster0", "WifiMaster1"):
            for index in range(7):
                interface_id = f"{master}/AccessPoint{index}"
                runtime[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": f"Used-{master}-{index}",
                    "state": "up",
                    "link": "up",
                }
                config[interface_id] = {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": f"Used-{master}-{index}",
                }

        client = FakeClient(runtime=runtime, config=config)
        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "NoRoom",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4", "5"],
            }
        )

        self.assertFalse(success)
        self.assertIn("Нет свободной точки доступа", message)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(client.batches, [])
        self.assertNotIn("AccessPoint7", repr(details))

    def test_create_does_not_fabricate_slots_without_inventory(
        self,
    ) -> None:
        client = FakeClient(
            runtime={
                "WifiMaster0": {"id": "WifiMaster0"},
                "WifiMaster1": {"id": "WifiMaster1"},
            },
            config={"Bridge0": {}},
        )
        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "UnknownInventory",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4", "5"],
            }
        )

        self.assertFalse(success)
        self.assertIn("Нет свободной точки доступа", message)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(client.batches, [])

    def test_duplicate_ssid_is_rejected_before_write(self) -> None:
        client = FakeClient(
            runtime={"WifiMaster0": {}},
            config={
                "Bridge0": {},
                "WifiMaster0/AccessPoint2": {
                    "ssid": "Home",
                    "type": "AccessPoint",
                },
            },
        )
        success, _message, details = WiFiNetworkService(client).create_network(
            {
                "ssid": "home",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4"],
            }
        )
        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(client.batches, [])

    def test_update_applies_ssid_to_all_interfaces(self) -> None:
        interfaces = [
            "WifiMaster0/AccessPoint2",
            "WifiMaster1/AccessPoint2",
        ]
        client = FakeClient(
            runtime={
                item: {
                    "id": item,
                    "type": "AccessPoint",
                    "ssid": "Old",
                    "state": "up",
                    "link": "up",
                }
                for item in interfaces
            },
            config={
                item: {"id": item, "type": "AccessPoint", "ssid": "Old"}
                for item in interfaces
            },
        )
        success, message, _details = WiFiNetworkService(client).update_network(
            interfaces,
            {
                "ssid": "New",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "hidden": True,
            },
        )
        self.assertTrue(success, message)
        self.assertEqual(
            client.interface_service.config[interfaces[0]]["ssid"],
            "New",
        )
        self.assertEqual(
            client.interface_service.config[interfaces[1]]["ssid"],
            "New",
        )

    def test_update_rejects_ssid_owned_by_another_network(self) -> None:
        current = "WifiMaster0/AccessPoint2"
        other = "WifiMaster1/AccessPoint3"
        client = FakeClient(
            runtime={
                current: {"state": "up", "ssid": "Current"},
                other: {"state": "up", "ssid": "Existing"},
            },
            config={
                current: {"ssid": "Current", "type": "AccessPoint"},
                other: {"ssid": "Existing", "type": "AccessPoint"},
            },
        )
        success, _message, details = WiFiNetworkService(client).update_network(
            [current],
            {
                "ssid": "Existing",
                "security": "wpa2",
                "password": "",
                "change_password": False,
                "hidden": False,
            },
        )
        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(details["interfaces"], [other])
        self.assertEqual(client.batches, [])

    def test_system_access_points_cannot_be_deleted(self) -> None:
        client = FakeClient(config={"Bridge0": {}})
        success, _message, details = WiFiNetworkService(client).delete_network(
            ["WifiMaster0/AccessPoint0"]
        )
        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertEqual(client.batches, [])

    def test_user_mws_profile_on_low_ap_index_is_deleted(self) -> None:
        interface_id = "WifiMaster0/AccessPoint1"
        client = FakeClient(
            runtime={interface_id: {"state": "up", "ssid": "Temp"}},
            config={
                "Bridge0": {},
                interface_id: {"ssid": "Temp", "type": "AccessPoint"},
            },
            mws_config={
                "wlan1": {
                    "band": ["0"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Temp"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        success, message, details = WiFiNetworkService(client).delete_network(
            {
                "mws_profile_id": "wlan1",
                "interfaces": [interface_id],
            }
        )
        self.assertTrue(success, message)
        self.assertEqual(details["mws_profile_id"], "wlan1")
        self.assertNotIn("wlan1", client.mws_config)
        self.assertNotIn("wlan1", client.mws_runtime)
        self.assertNotIn(
            "ssid",
            client.interface_service.config[interface_id],
        )
        self.assertEqual(
            client.batches[0][1][0],
            {"mws": {"wlan": {"id": "wlan1", "no": True}}},
        )

    def test_user_mws_profile_on_low_ap_index_is_deletable_in_ui(self) -> None:
        interface_id = "WifiMaster0/AccessPoint1"
        client = FakeClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "state": "up",
                    "link": "up",
                    "ssid": "1234",
                }
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "1234",
                    "up": True,
                }
            },
            mws_config={
                "wlan1": {
                    "band": ["0"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "1234"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        snapshot = WifiLogicalSnapshot(
            config=copy.deepcopy(client.mws_config),
            runtime=copy.deepcopy(client.mws_runtime),
        )

        networks = WiFiNetworkService(client)._reconcile_logical_networks(
            [],
            snapshot,
            interface_data=client.interface_service.runtime,
            interface_config_data=client.interface_service.config,
        )

        self.assertEqual(len(networks), 1)
        self.assertFalse(networks[0]["protected_system_network"])
        self.assertTrue(networks[0]["deletable"])

    def test_bandless_mws_profile_is_deleted_without_interfaces(self) -> None:
        client = FakeClient(
            mws_config={
                "wlan0": {
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Keenetic1"},
                    "encryption": "wpa2",
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster0/AccessPoint0": {}
                            },
                        }
                    }
                }
            },
        )

        success, message, details = WiFiNetworkService(
            client
        ).delete_network(
            {"mws_profile_id": "wlan0", "interfaces": []}
        )

        self.assertTrue(success, message)
        self.assertEqual(details["interfaces"], [])
        self.assertNotIn("wlan0", client.mws_config)
        self.assertNotIn("wlan0", client.mws_runtime)


    def test_create_uses_next_free_mws_profile_id(self) -> None:
        runtime = {
            "WifiMaster0": {"id": "WifiMaster0"},
            "WifiMaster0/AccessPoint2": {
                "id": "WifiMaster0/AccessPoint2",
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
            "WifiMaster0/AccessPoint3": {
                "id": "WifiMaster0/AccessPoint3",
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
        }
        config = {
            "Bridge0": {},
            "WifiMaster0/AccessPoint2": {
                "id": "WifiMaster0/AccessPoint2",
                "type": "AccessPoint",
                "ssid": "Existing",
            },
            "WifiMaster0/AccessPoint3": {
                "id": "WifiMaster0/AccessPoint3",
                "type": "AccessPoint",
            },
        }
        client = FakeClient(
            runtime=runtime,
            config=config,
            mws_config={
                "wlan0": {
                    "band": ["0"],
                    "ssid": {"name": "Existing"},
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        success, message, details = WiFiNetworkService(
            client
        ).create_network(
            {
                "ssid": "Second",
                "password": "12345678",
                "security": "wpa2",
                "bands": ["2.4"],
            }
        )
        self.assertTrue(success, message)
        self.assertEqual(details["mws_profile_id"], "wlan1")
        self.assertEqual(
            details["interfaces"],
            ["WifiMaster0/AccessPoint3"],
        )

    def test_mws_enable_disable_uses_exact_captured_payload(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient(
            runtime={interface_id: {"state": "up", "ssid": "Temp"}},
            config={interface_id: {"ssid": "Temp", "type": "AccessPoint"}},
            mws_config={
                "wlan1": {
                    "band": ["0"],
                    "ssid": {"name": "Temp"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        reference = {
            "mws_profile_id": "wlan1",
            "interfaces": [interface_id],
        }

        success, message, details = service.set_network_enabled(
            reference,
            False,
        )
        self.assertTrue(success, message)
        self.assertFalse(details["enabled"])
        self.assertEqual(
            client.batches[0][1],
            [
                {"mws": {"wlan": {"id": "wlan1", "enable": False}}},
                {"system": {"configuration": {"save": {}}}},
            ],
        )

        success, message, details = service.set_network_enabled(
            reference,
            True,
        )
        self.assertTrue(success, message)
        self.assertTrue(details["enabled"])
        self.assertEqual(
            client.batches[1][1],
            [
                {"mws": {"wlan": {"id": "wlan1", "enable": True}}},
                {"system": {"configuration": {"save": {}}}},
            ],
        )

    def test_bandless_mws_profile_cannot_be_enabled(self) -> None:
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Dormant"},
                    "enable": False,
                }
            },
            mws_runtime={"wlan1": {"band": {}}},
        )

        success, message, details = WiFiNetworkService(
            client
        ).set_network_enabled(
            {"mws_profile_id": "wlan1", "interfaces": []},
            True,
        )

        self.assertFalse(success)
        self.assertIn("назначьте", message)
        self.assertTrue(details["preflight_rejected"])
        self.assertTrue(details["band_missing"])
        self.assertEqual(client.batches, [])

    def test_runtime_mws_has_priority_for_actual_card_state(self) -> None:
        service = WiFiNetworkService(FakeClient())
        runtime = {
            "band": {
                "0": {
                    "enabled": True,
                    "access-point": {
                        "WifiMaster0/AccessPoint2": {}
                    },
                }
            }
        }
        self.assertTrue(
            service._logical_profile_enabled(
                {"band": ["0"], "enable": False},
                runtime,
            )
        )
        runtime["band"]["0"]["enabled"] = False
        self.assertFalse(
            service._logical_profile_enabled(
                {"band": ["0"], "enable": True},
                runtime,
            )
        )
        runtime["band"]["0"]["enabled"] = True
        self.assertFalse(
            service._logical_profile_enabled(
                {"ssid": {"name": "Dormant"}},
                runtime,
            )
        )

    def test_verify_enable_rejects_saved_true_when_runtime_is_off(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Keenetic12"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster0/AccessPoint0": {}
                            },
                        },
                        "1": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster1/AccessPoint1": {}
                            },
                        },
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0
        success, message = service.verify_logical_enabled(
            "wlan0",
            True,
        )
        self.assertFalse(success)
        self.assertIn("mws=2.4=off, 5=off", message)

    def test_enable_accepts_physical_state_when_mws_runtime_is_stale(self) -> None:
        interfaces = [
            "WifiMaster0/AccessPoint0",
            "WifiMaster1/AccessPoint1",
        ]

        class LegacyDownClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                # Keenetic accepts enable=true in MWS but legacy APs
                # remain physically down until explicit interface up.
                profile_id = str(wlan.get("id") or "")
                if "enable" in wlan and profile_id:
                    profile = self.mws_config.setdefault(profile_id, {})
                    profile["enable"] = bool(wlan["enable"])
                    return
                super()._apply_mws_wlan(wlan)

        client = LegacyDownClient(
            config={
                interfaces[0]: {
                    "id": interfaces[0],
                    "type": "AccessPoint",
                    "ssid": "Keenetic12",
                },
                interfaces[1]: {
                    "id": interfaces[1],
                    "type": "AccessPoint",
                    "ssid": "Keenetic12",
                },
            },
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Keenetic12"},
                    "band": ["0", "1"],
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": False,
                            "access-point": {interfaces[1]: {}},
                        },
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message, details = service.set_network_enabled(
            {
                "mws_profile_id": "wlan0",
                "interfaces": interfaces,
            },
            True,
        )
        self.assertTrue(success, message)
        self.assertTrue(details["verified"])
        self.assertIn("runtime MWS", details["verification_message"])
        self.assertTrue(details["physical_changed"])
        self.assertEqual(details["physical_changed"], interfaces)
        self.assertEqual(len(client.batches), 1)
        self.assertEqual(
            client.interface_service.state_calls,
            [
                (interfaces[0], True),
                (interfaces[1], True),
            ],
        )
        self.assertTrue(
            client.interface_service.runtime[interfaces[0]]["state"]
            == "up"
        )
        self.assertTrue(
            client.interface_service.runtime[interfaces[1]]["state"]
            == "up"
        )

    def test_disable_never_repairs_legacy_access_points(self) -> None:
        interface_id = "WifiMaster0/AccessPoint0"
        client = FakeClient(
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Keenetic12",
                }
            },
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Keenetic12"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        success, message, details = service.set_network_enabled(
            {
                "mws_profile_id": "wlan0",
                "interfaces": [interface_id],
            },
            False,
        )
        self.assertTrue(success, message)
        self.assertEqual(details["physical_changed"], [])
        self.assertEqual(details["physical_failures"], [])
        self.assertEqual(len(client.batches), 1)

    def test_reconciliation_uses_runtime_when_saved_true_but_off(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Keenetic12"},
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster0/AccessPoint0": {}
                            },
                        }
                    }
                }
            },
        )
        networks = WiFiNetworkService(client)._reconcile_logical_networks(
            [],
            WifiLogicalSnapshot(
                config=copy.deepcopy(client.mws_config),
                runtime=copy.deepcopy(client.mws_runtime),
            ),
            interface_data={},
            interface_config_data={},
        )
        self.assertEqual(len(networks), 1)
        self.assertFalse(networks[0]["enabled"])
        self.assertTrue(networks[0]["mws_saved_enabled"])
        self.assertFalse(networks[0]["mws_runtime_active"])

    def test_disable_accepts_missing_saved_enable_as_disabled(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"

        class IgnoreShortToggleClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                if set(wlan) == {"id", "enable"}:
                    return
                profile_id = str(wlan.get("id") or "")
                if profile_id:
                    profile = self.mws_config.setdefault(profile_id, {})
                    for key, value in wlan.items():
                        if key != "id":
                            profile[key] = copy.deepcopy(value)
                    runtime_profile = self.mws_runtime.get(profile_id, {})
                    bands = runtime_profile.get("band")
                    if isinstance(bands, dict):
                        for state in bands.values():
                            if isinstance(state, dict):
                                state["enabled"] = bool(wlan.get("enable"))
                    return
                super()._apply_mws_wlan(wlan)

        client = IgnoreShortToggleClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "state": "up",
                    "link": "up",
                    "ssid": "Temp",
                }
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Temp",
                    "up": True,
                }
            },
            mws_config={
                "wlan1": {
                    "band": ["0"],
                    "bind": {"interface": "Bridge0"},
                    "ssid": {"name": "Temp"},
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_SAVED_VERIFY_ATTEMPTS = 1
        service.LOGICAL_SAVED_VERIFY_DELAY_SECONDS = 0.0
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message, details = service.set_network_enabled(
            {
                "mws_profile_id": "wlan1",
                "interfaces": [interface_id],
            },
            False,
        )
        self.assertTrue(success, message)
        self.assertFalse(details["full_profile_fallback"])
        self.assertNotIn("enable", client.mws_config["wlan1"])
        self.assertEqual(
            client.interface_service.state_calls,
            [(interface_id, False)],
        )
        self.assertEqual(len(client.batches), 1)

    def test_reconciliation_uses_physical_ap_state_not_mws_flag(
        self,
    ) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "state": "down",
                    "link": "down",
                    "ssid": "Temp",
                }
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Temp",
                }
            },
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        networks = WiFiNetworkService(client)._reconcile_logical_networks(
            [],
            WifiLogicalSnapshot(
                config=copy.deepcopy(client.mws_config),
                runtime=copy.deepcopy(client.mws_runtime),
            ),
            interface_data=client.interface_service.runtime,
            interface_config_data=client.interface_service.config,
        )
        self.assertEqual(len(networks), 1)
        network = networks[0]
        self.assertFalse(network["enabled"])
        self.assertFalse(network["online"])
        self.assertFalse(network["mws_enabled"])
        self.assertTrue(network["mws_runtime_active"])
        self.assertFalse(network["physical_runtime_active"])
        self.assertFalse(network["mws_state_mismatch"])
        self.assertFalse(network["band_states"]["2.4"]["enabled"])
        self.assertTrue(network["band_states"]["2.4"]["mws_enabled"])

    def test_enable_uses_interface_service_not_parse_commands(self) -> None:
        interface_id = "WifiMaster0/AccessPoint0"

        class LegacyDownClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                profile_id = str(wlan.get("id") or "")
                if "enable" in wlan and profile_id:
                    profile = self.mws_config.setdefault(profile_id, {})
                    profile["enable"] = bool(wlan["enable"])
                    return
                super()._apply_mws_wlan(wlan)

        client = LegacyDownClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "state": "down",
                    "link": "down",
                }
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "Main",
                }
            },
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Main"},
                    "enable": True,
                    "band": ["0"],
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message, details = service.set_network_enabled(
            {
                "mws_profile_id": "wlan0",
                "interfaces": [interface_id],
            },
            True,
        )
        self.assertTrue(success, message)
        self.assertTrue(details["verified"])
        self.assertIn("runtime MWS", details["verification_message"])
        self.assertEqual(
            client.interface_service.state_calls,
            [(interface_id, True)],
        )
        self.assertFalse(
            any(
                "parse" in command
                for _name, commands in client.batches
                for command in commands
            )
        )
        self.assertEqual(details["physical_changed"], [interface_id])

    def test_verify_enable_accepts_saved_true_without_runtime_bands(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Keenetic12"},
                    "enable": True,
                }
            },
            mws_runtime={"wlan0": {}},
        )
        success, message = WiFiNetworkService(
            client
        ).verify_logical_enabled("wlan0", True)
        self.assertTrue(success, message)

    def test_verify_toggle_rejects_saved_state_while_runtime_disagrees(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                    "enable": False,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        success, message = WiFiNetworkService(
            client
        ).verify_logical_enabled("wlan1", False)
        self.assertFalse(success)
        self.assertIn("mws=2.4=on", message)

    def test_verify_toggle_accepts_runtime_when_saved_value_is_absent(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0
        success, message = service.verify_logical_enabled(
            "wlan1",
            False,
        )
        self.assertTrue(success, message)

    def test_physical_target_is_success_when_mws_read_is_stale(
        self,
    ) -> None:
        class InconclusiveClient(FakeClient):
            def _apply_mws_wlan(self, wlan: dict[str, Any]) -> None:
                # Simulate firmware that accepts the command but exposes
                # the previous saved/runtime state for a few seconds.
                if "enable" in wlan:
                    return
                super()._apply_mws_wlan(wlan)

        client = InconclusiveClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0
        success, message, details = service.set_network_enabled(
            {"mws_profile_id": "wlan1"},
            False,
        )
        self.assertTrue(success, message)
        self.assertFalse(details["verification_pending"])
        self.assertTrue(details["verified"])
        self.assertEqual(len(client.batches), 2)
        self.assertTrue(details["full_profile_fallback"])
        self.assertNotIn("Откат", message)

    def test_unknown_interface_state_still_sends_disable(self) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient()
        service = WiFiNetworkService(client)

        changed, failures = service._set_mapped_access_points_enabled(
            "wlan1",
            [interface_id],
            False,
        )

        self.assertEqual(changed, [interface_id])
        self.assertEqual(failures, [])
        self.assertEqual(
            client.interface_service.state_calls,
            [(interface_id, False)],
        )

    def test_verify_uses_captured_interfaces_after_runtime_disappears(
        self,
    ) -> None:
        interface_id = "WifiMaster0/AccessPoint2"
        client = FakeClient(
            runtime={
                interface_id: {
                    "state": "up",
                    "link": "up",
                }
            },
            config={
                interface_id: {
                    "type": "AccessPoint",
                    "up": True,
                }
            },
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "enable": False,
                }
            },
            mws_runtime={},
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message = service.verify_logical_enabled(
            "wlan1",
            False,
            interfaces=[interface_id],
        )

        self.assertFalse(success)
        self.assertIn(f"{interface_id}=up", message)

    def test_physical_state_overrides_stale_mws_band(self) -> None:
        interfaces = [
            "WifiMaster0/AccessPoint2",
            "WifiMaster1/AccessPoint2",
        ]
        client = FakeClient(
            runtime={
                interface_id: {"state": "up", "link": "up"}
                for interface_id in interfaces
            },
            config={
                interface_id: {"type": "AccessPoint", "up": True}
                for interface_id in interfaces
            },
            mws_config={"wlan1": {"enable": True}},
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": False,
                            "access-point": {interfaces[1]: {}},
                        },
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message = service.verify_logical_enabled(
            "wlan1",
            True,
            interfaces=interfaces,
        )

        self.assertTrue(success, message)
        self.assertIn("runtime MWS", message)

    def test_verify_enable_requires_every_physical_access_point(
        self,
    ) -> None:
        interfaces = [
            "WifiMaster0/AccessPoint2",
            "WifiMaster1/AccessPoint2",
        ]
        client = FakeClient(
            runtime={
                interfaces[0]: {"state": "up", "link": "up"},
                interfaces[1]: {"state": "down", "link": "down"},
            },
            config={
                interfaces[0]: {"type": "AccessPoint", "up": True},
                interfaces[1]: {"type": "AccessPoint"},
            },
            mws_config={"wlan1": {"enable": True}},
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interfaces[0]: {}},
                        },
                        "1": {
                            "enabled": True,
                            "access-point": {interfaces[1]: {}},
                        },
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0

        success, message = service.verify_logical_enabled(
            "wlan1",
            True,
            interfaces=interfaces,
        )

        self.assertFalse(success)
        self.assertIn(f"{interfaces[1]}=down", message)

    def test_toggle_records_safe_mws_diagnostics(self) -> None:
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        success, message, _details = service.set_network_enabled(
            {"mws_profile_id": "wlan1"},
            False,
        )
        self.assertTrue(success, message)
        self.assertIn(
            "show/sc/mws/wlan",
            client.diagnostics.snapshots,
        )
        self.assertIn(
            "show/mws/wlan",
            client.diagnostics.snapshots,
        )
        self.assertTrue(client.diagnostics.events)
        event = client.diagnostics.events[-1]
        self.assertEqual(event["type"], "wifi_mws_toggle")
        self.assertEqual(event["details"]["profile_id"], "wlan1")
        self.assertFalse(event["details"]["requested_enabled"])

    def test_toggle_verification_reports_saved_and_runtime_evidence(
        self,
    ) -> None:
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["0"],
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        service.LOGICAL_TOGGLE_VERIFY_ATTEMPTS = 1
        service.LOGICAL_TOGGLE_VERIFY_DELAY_SECONDS = 0.0
        success, message = service.verify_logical_enabled(
            "wlan1",
            False,
        )
        self.assertFalse(success)
        self.assertIn("saved=включено", message)
        self.assertIn("2.4=on", message)

    def test_mws_reconciliation_removes_stale_physical_band(self) -> None:
        client = FakeClient(
            mws_config={
                "DEFAULT__": {
                    "ssid": {"name": "Keenetic1"},
                    "band": ["0"],
                    "bind": {"interface": "Bridge0"},
                    "enable": True,
                }
            },
            mws_runtime={
                "DEFAULT__": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {
                                "WifiMaster0/AccessPoint0": {}
                            },
                        }
                    }
                }
            },
        )
        runtime = {
            "WifiMaster0/AccessPoint0": {
                "id": "WifiMaster0/AccessPoint0",
                "type": "AccessPoint",
                "ssid": "Keenetic1",
                "state": "up",
                "link": "up",
            },
            "WifiMaster1/AccessPoint0": {
                "id": "WifiMaster1/AccessPoint0",
                "type": "AccessPoint",
                "state": "down",
                "link": "down",
            },
        }
        config = {
            "WifiMaster0/AccessPoint0": {
                "id": "WifiMaster0/AccessPoint0",
                "type": "AccessPoint",
                "ssid": "Keenetic1",
            },
            # Stale physical configuration from the removed 5 GHz band.
            "WifiMaster1/AccessPoint0": {
                "id": "WifiMaster1/AccessPoint0",
                "type": "AccessPoint",
                "ssid": "Keenetic1",
            },
        }
        network = WiFiNetworkService(client).build_networks(
            interface_data=runtime,
            interface_config_data=config,
            associations_data={},
            hotspot_data={},
        )[0]
        self.assertEqual(network["mws_profile_id"], "DEFAULT__")
        self.assertEqual(
            network["interfaces"],
            ["WifiMaster0/AccessPoint0"],
        )
        self.assertEqual(set(network["band_states"]), {"2.4"})
        self.assertTrue(network["band_states"]["2.4"]["enabled"])

    def test_saved_empty_band_list_ignores_stale_runtime_mapping(self) -> None:
        interface_id = "WifiMaster0/AccessPoint0"
        client = FakeClient(
            mws_config={
                "wlan0": {
                    "ssid": {"name": "Dormant"},
                    "bind": {"interface": "Bridge0"},
                }
            },
            mws_runtime={
                "wlan0": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        runtime = {
            interface_id: {
                "id": interface_id,
                "type": "AccessPoint",
                "ssid": "Dormant",
                "state": "down",
                "link": "down",
            }
        }
        config = {
            "Bridge0": {},
            interface_id: {
                "id": interface_id,
                "type": "AccessPoint",
                "ssid": "Dormant",
            },
        }

        network = WiFiNetworkService(client).build_networks(
            interface_data=runtime,
            interface_config_data=config,
            associations_data={},
            hotspot_data={},
        )[0]

        self.assertEqual(network["mws_profile_id"], "wlan0")
        self.assertEqual(network["interfaces"], [])
        self.assertEqual(network["band_states"], {})
        self.assertFalse(network["enabled"])

    def test_deleted_web_profile_is_not_restored_from_cache(self) -> None:
        main_interface = "WifiMaster0/AccessPoint0"
        temp_interface = "WifiMaster1/AccessPoint2"
        client = FakeClient(
            mws_config={
                "DEFAULT__": {
                    "ssid": {"name": "Main"},
                    "band": ["0"],
                    "enable": True,
                },
                "wlan1": {
                    "ssid": {"name": "Temp"},
                    "band": ["1"],
                    "enable": True,
                },
            },
            mws_runtime={
                "DEFAULT__": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {main_interface: {}},
                        }
                    }
                },
                "wlan1": {
                    "band": {
                        "1": {
                            "enabled": True,
                            "access-point": {temp_interface: {}},
                        }
                    }
                },
            },
        )
        runtime = {
            main_interface: {
                "id": main_interface,
                "type": "AccessPoint",
                "ssid": "Main",
                "state": "up",
                "link": "up",
            },
            temp_interface: {
                "id": temp_interface,
                "type": "AccessPoint",
                "ssid": "Temp",
                "state": "up",
                "link": "up",
            },
        }
        config = copy.deepcopy(runtime)
        service = WiFiNetworkService(client)
        first = service.build_networks(
            interface_data=runtime,
            interface_config_data=config,
            associations_data={},
            hotspot_data={},
        )
        first_wifi = [
            item for item in first
            if item.get("network_type") == "wifi"
        ]
        self.assertEqual(
            {item["ssid"] for item in first_wifi},
            {"Main", "Temp"},
        )

        client.mws_config.pop("wlan1")
        client.mws_runtime.pop("wlan1")
        # Keenetic may retain stale physical SSID data briefly.
        second = service.build_networks(
            interface_data=runtime,
            interface_config_data=config,
            associations_data={},
            hotspot_data={},
        )
        second_wifi = [
            item for item in second
            if item.get("network_type") == "wifi"
        ]
        self.assertEqual([item["ssid"] for item in second_wifi], ["Main"])
        self.assertFalse(
            any(
                item.get("mws_profile_id") == "wlan1"
                for item in service.known_networks.values()
            )
        )

    def test_empty_supported_mws_does_not_show_physical_ghosts(self) -> None:
        ghost = "WifiMaster1/AccessPoint0"
        client = FakeClient(
            runtime={
                ghost: {
                    "id": ghost,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
            },
            config={
                ghost: {
                    "id": ghost,
                    "type": "AccessPoint",
                    "ssid": "Keenetic-6256",
                }
            },
            mws_config={},
            mws_runtime={},
        )
        service = WiFiNetworkService(client)

        networks = service.build_networks(
            interface_data=client.interface_service.runtime,
            interface_config_data=client.interface_service.config,
            associations_data={},
            hotspot_data={},
        )

        self.assertTrue(service._logical_management_supported)
        self.assertEqual(
            [item for item in networks if item.get("network_type") == "wifi"],
            [],
        )

    def test_mws_mode_purges_cached_unmapped_physical_network(self) -> None:
        interface_id = "WifiMaster0/AccessPoint1"
        ghost_interface = "WifiMaster1/AccessPoint0"
        client = FakeClient(
            runtime={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "1234",
                    "state": "up",
                    "link": "up",
                },
                ghost_interface: {
                    "id": ghost_interface,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                },
            },
            config={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "ssid": "1234",
                    "up": True,
                },
                ghost_interface: {
                    "id": ghost_interface,
                    "type": "AccessPoint",
                    "ssid": "Keenetic-6256",
                },
            },
            mws_config={
                "wlan1": {
                    "ssid": {"name": "1234"},
                    "band": ["0"],
                    "enable": True,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": True,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        service = WiFiNetworkService(client)
        ghost_card = {
            "network_type": "wifi",
            "ssid": "Keenetic-6256",
            "interfaces": [ghost_interface],
            "enabled": False,
        }
        ghost_key = service.network_cache_key(ghost_card)
        service.known_networks[ghost_key] = ghost_card

        networks = service.build_networks(
            interface_data=client.interface_service.runtime,
            interface_config_data=client.interface_service.config,
            associations_data={},
            hotspot_data={},
        )

        wifi_names = {
            item.get("ssid")
            for item in networks
            if item.get("network_type") == "wifi"
        }
        self.assertEqual(wifi_names, {"1234"})
        self.assertNotIn(ghost_key, service.known_networks)

    def test_disabled_mws_profile_is_shown_without_physical_ssid(
        self,
    ) -> None:
        interface_id = "WifiMaster1/AccessPoint2"
        client = FakeClient(
            mws_config={
                "wlan1": {
                    "ssid": {"name": "Sleeping"},
                    "band": ["1"],
                    "bind": {"interface": "Bridge0"},
                    "enable": False,
                }
            },
            mws_runtime={
                "wlan1": {
                    "band": {
                        "1": {
                            "enabled": False,
                            "access-point": {interface_id: {}},
                        }
                    }
                }
            },
        )
        networks = WiFiNetworkService(client).build_networks(
            interface_data={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                    "state": "down",
                    "link": "down",
                }
            },
            interface_config_data={
                interface_id: {
                    "id": interface_id,
                    "type": "AccessPoint",
                }
            },
            associations_data={},
            hotspot_data={},
        )
        wifi_networks = [
            item for item in networks
            if item.get("network_type") == "wifi"
        ]
        self.assertEqual(len(wifi_networks), 1)
        network = wifi_networks[0]
        self.assertEqual(network["ssid"], "Sleeping")
        self.assertFalse(network["enabled"])
        self.assertEqual(set(network["band_states"]), {"5"})
        self.assertFalse(network["band_states"]["5"]["enabled"])

    def test_mws_state_rejects_missing_profile_before_write(self) -> None:
        client = FakeClient()
        success, _message, details = WiFiNetworkService(
            client
        ).set_network_enabled(
            {"interfaces": ["WifiMaster0/AccessPoint2"]},
            False,
        )
        self.assertFalse(success)
        self.assertTrue(details["preflight_rejected"])
        self.assertTrue(details["logical_profile_missing"])
        self.assertEqual(client.batches, [])

    def test_logical_runtime_controls_card_enabled_state(self) -> None:
        service = WiFiNetworkService(FakeClient())
        network = {
            "network_type": "wifi",
            "ssid": "Temp",
            "interfaces": ["WifiMaster0/AccessPoint2"],
            "band_states": {
                "2.4": {
                    "enabled": True,
                    "online": True,
                    "interface_id": "WifiMaster0/AccessPoint2",
                }
            },
            "enabled": True,
            "online": True,
        }
        snapshot = WifiLogicalSnapshot(
            config={
                "wlan1": {
                    "band": ["0"],
                    "ssid": {"name": "Temp"},
                }
            },
            runtime={
                "wlan1": {
                    "band": {
                        "0": {
                            "enabled": False,
                            "access-point": {
                                "WifiMaster0/AccessPoint2": {}
                            },
                        }
                    }
                }
            },
        )
        result = service._reconcile_logical_networks(
            [network],
            snapshot,
            interface_data={},
            interface_config_data={},
        )[0]
        self.assertFalse(result["enabled"])
        self.assertFalse(result["online"])
        self.assertFalse(result["band_states"]["2.4"]["enabled"])
        self.assertEqual(result["mws_profile_id"], "wlan1")


    def test_disabled_port_binding_survives_client_going_offline(self) -> None:
        client = FakeClient()
        client._manually_disabled_interfaces = set()
        service = WiFiNetworkService(client)
        interface_id = "GigabitEthernet0/2"
        runtime = {
            "GigabitEthernet0": {
                "id": "GigabitEthernet0",
                "port": {
                    "2": {
                        "id": interface_id,
                        "type": "Port",
                        "state": "up",
                        "link": "up",
                        "summary": {"layer": {"conf": "running"}},
                    }
                },
            }
        }
        active = [
            {
                "mac": "00:00:00:00:00:61",
                "name": "Desktop",
                "connection_kind": "wired",
                "online": True,
                "interface": interface_id,
                "port_label": "Порт 2",
                "connection_label": "Ethernet, порт 2",
                "interface_enabled": True,
            }
        ]
        service._merge_client_port_bindings(
            active,
            runtime=runtime,
            config={},
        )

        client._manually_disabled_interfaces.add(interface_id)
        offline = [
            {
                "mac": "00:00:00:00:00:61",
                "name": "Desktop",
                "connection_kind": "unknown",
                "online": False,
            }
        ]
        restored = service._merge_client_port_bindings(
            offline,
            runtime={},
            config={},
        )[0]

        self.assertEqual(restored["connection_kind"], "wired")
        self.assertEqual(restored["interface"], interface_id)
        self.assertFalse(restored["interface_enabled"])
        self.assertTrue(restored["port_control_available"])


class SourceBoundaryTests(unittest.TestCase):
    def test_client_constructs_service_and_uses_it_for_refresh(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "self.wifi_network_service = WiFiNetworkService(self)",
            source,
        )
        self.assertIn(
            "self.wifi_network_service.build_networks(",
            source,
        )
        self.assertNotIn("current_wifi_networks = WifiEngine(", source)

    def test_worker_routes_writes_directly_to_service(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "connection_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "client.wifi_network_service.create_network(",
            source,
        )
        self.assertIn(
            "client.wifi_network_service.update_network(",
            source,
        )
        self.assertIn(
            "client.wifi_network_service.set_network_enabled(",
            source,
        )
        self.assertIn(
            "client.wifi_network_service.delete_network(",
            source,
        )

    def test_top_wifi_tab_is_real_widget_after_internet(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.wifi_tab = WiFiSettingsTab()", source)
        titles_start = source.index("TAB_TITLES =")
        titles_end = source.index("def __init__", titles_start)
        titles_source = source[titles_start:titles_end]
        self.assertLess(
            titles_source.index('"Интернет"'),
            titles_source.index('"Wi-Fi"'),
        )
        self.assertLess(
            titles_source.index('"Wi-Fi"'),
            titles_source.index('"Клиенты"'),
        )

    def test_wifi_cards_emit_full_network_for_mws_state(self) -> None:
        settings_source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "wifi_settings_tab.py"
        ).read_text(encoding="utf-8")
        monitoring_source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "wifi_network_card.py"
        ).read_text(encoding="utf-8")
        self.assertIn("dict(self.network)", settings_source)
        self.assertIn("dict(self.network)", monitoring_source)

    def test_wifi_card_identity_prefers_mws_profile_id(self) -> None:
        settings_source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "wifi_settings_tab.py"
        ).read_text(encoding="utf-8")
        monitoring_source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "wifi_network_card.py"
        ).read_text(encoding="utf-8")
        self.assertIn('network.get("mws_profile_id")', settings_source)
        self.assertIn('"mws:" + profile_id.casefold()', settings_source)
        self.assertIn('network.get("mws_profile_id")', monitoring_source)
        self.assertIn('"mws:" + profile_id.casefold()', monitoring_source)

    def test_main_window_routes_wifi_state_and_delete_to_mws(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "main_window.py"
        ).read_text(encoding="utf-8")
        self.assertIn("request_set_wifi_network_enabled(", source)
        self.assertIn("request_delete_wifi_network(dict(network))", source)
        self.assertIn('network.get("mws_profile_id")', source)
        self.assertIn("if not interfaces and not profile_id:", source)
        self.assertIn("Профиль MWS:", source)

    def test_monitoring_card_exports_delete_signal(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "widgets"
            / "wifi_network_card.py"
        ).read_text(encoding="utf-8")
        self.assertIn("delete_requested = Signal(object)", source)
        self.assertIn('QPushButton("Удалить")', source)


class ClientFacadeTests(unittest.TestCase):
    def test_client_facades_preserve_public_contract(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient("admin", "secret")
        self.assertIsInstance(client.wifi_network_service, WiFiNetworkService)
        for name in (
            "create_wifi_network",
            "update_wifi_network",
            "set_wifi_network_enabled",
            "delete_wifi_network",
        ):
            self.assertTrue(callable(getattr(client, name)))


if __name__ == "__main__":
    unittest.main()
