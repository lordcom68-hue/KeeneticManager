from __future__ import annotations

import copy
import re
import time
from typing import Any

from core.rci.capabilities import RouterCapabilities
from core.services.wifi_network_models import (
    WifiLogicalSnapshot,
    WifiNetworkCapabilities,
)
from core.services.wifi_network_read_service import WifiNetworkReadMixin



class WiFiNetworkService(WifiNetworkReadMixin):
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
