from __future__ import annotations

import re
from typing import Any


class WiFiClientService:
    """Управляет доступом отдельного клиента к Wi-Fi без выключения AP."""

    MAC_PATTERN = re.compile(
        r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$",
        re.IGNORECASE,
    )
    ACCESS_POINT_PATTERN = re.compile(
        r"^WifiMaster\d+/AccessPoint\d+$",
        re.IGNORECASE,
    )
    SUPPORTED_ACL_TYPES = {"none", "deny", "permit"}

    def __init__(self, client: Any) -> None:
        self.client = client
        self._acl_modes_enabled_by_app: set[str] = set()

    def enrich_clients(
        self,
        clients: list[dict[str, Any]],
        *,
        interface_config: dict[str, Any],
    ) -> None:
        """Добавляет клиентам сегмент и фактическое состояние Wi-Fi ACL."""
        config = (
            interface_config
            if isinstance(interface_config, dict)
            else {}
        )

        for item in clients:
            if not isinstance(item, dict):
                continue
            if str(item.get("connection_kind") or "").lower() != "wifi":
                item.setdefault("wifi_blocked", False)
                continue

            mac = self.normalize_mac(item.get("mac"))
            access_point = str(
                item.get("ap") or item.get("interface") or ""
            ).strip()
            requested_segment = str(
                item.get("segment_id") or item.get("segment") or ""
            ).strip()
            segment_id = self.resolve_segment_id(
                config,
                access_point=access_point,
                requested_segment=requested_segment,
            )
            if segment_id:
                item["segment_id"] = segment_id

            acl_type, addresses = self.read_acl(
                config.get(segment_id)
                if segment_id
                else None
            )
            wifi_blocked = self.is_blocked(
                mac,
                acl_type=acl_type,
                addresses=addresses,
            )
            item["wifi_acl_type"] = acl_type
            item["wifi_blocked"] = wifi_blocked
            item["blocked"] = bool(item.get("blocked") or wifi_blocked)

    def set_access(
        self,
        client_reference: dict[str, Any],
        *,
        allowed: bool,
    ) -> tuple[bool, str, dict[str, Any]]:
        reference = (
            dict(client_reference)
            if isinstance(client_reference, dict)
            else {}
        )
        mac = self.normalize_mac(reference.get("mac"))
        if not mac:
            return False, "Некорректный MAC-адрес Wi-Fi клиента.", {}

        if bool(reference.get("management_client")) and not allowed:
            return (
                False,
                "Нельзя отключить Wi-Fi клиента, через которого открыто управление роутером.",
                {"mac": mac},
            )

        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=True,
        )
        if not isinstance(config, dict):
            return False, "Роутер не вернул конфигурацию интерфейсов.", {"mac": mac}

        access_point = str(
            reference.get("ap") or reference.get("interface") or ""
        ).strip()
        requested_segment = str(
            reference.get("segment_id") or reference.get("segment") or ""
        ).strip()
        segment_id = self.resolve_segment_id(
            config,
            access_point=access_point,
            requested_segment=requested_segment,
        )
        if not segment_id:
            return (
                False,
                "Не удалось определить Wi-Fi сегмент клиента.",
                {"mac": mac, "access_point": access_point},
            )

        acl_type, addresses = self.read_acl(config.get(segment_id))
        if acl_type not in self.SUPPORTED_ACL_TYPES:
            return (
                False,
                f"Роутер вернул неподдерживаемый режим Wi-Fi ACL: {acl_type or 'не определён'}.",
                {"mac": mac, "segment_id": segment_id},
            )

        currently_blocked = self.is_blocked(
            mac,
            acl_type=acl_type,
            addresses=addresses,
        )
        target_blocked = not bool(allowed)
        if currently_blocked == target_blocked:
            return (
                True,
                "Wi-Fi доступ клиента уже разрешён."
                if allowed
                else "Wi-Fi доступ клиента уже отключён.",
                {
                    "mac": mac,
                    "segment_id": segment_id,
                    "allowed": bool(allowed),
                    "unchanged": True,
                },
            )

        segment_arg = self.client.quote_cli_argument(segment_id)
        mac_arg = self.client.quote_cli_argument(mac)
        commands: list[dict[str, Any]] = []
        adds_mac_to_acl = bool(
            (acl_type == "permit" and allowed)
            or (acl_type in {"none", "deny"} and not allowed)
        )
        register_client = bool(
            adds_mac_to_acl
            and reference.get("registered") is False
            and mac not in addresses
        )
        if register_client:
            # KeeneticOS accepts a MAC in a segment ACL only after the host
            # has been registered.  Clients learned dynamically by the
            # hotspot otherwise fail with: "MAC address ... is unknown".
            # A deterministic unique name avoids collisions between devices
            # with identical hostnames (for example, several iPhones).
            host_name = "KM-" + mac.replace(":", "")
            host_arg = self.client.quote_cli_argument(host_name)
            commands.append(
                {"parse": f"known host {host_arg} {mac_arg}"}
            )
        restore_acl_none = bool(
            allowed
            and acl_type == "deny"
            and segment_id in self._acl_modes_enabled_by_app
            and addresses == {mac}
        )

        if acl_type == "permit":
            command = (
                f"interface {segment_arg} mac access-list address {mac_arg}"
                if allowed
                else f"interface {segment_arg} no mac access-list address {mac_arg}"
            )
            commands.append({"parse": command})
        else:
            command = (
                f"interface {segment_arg} no mac access-list address {mac_arg}"
                if allowed
                else f"interface {segment_arg} mac access-list address {mac_arg}"
            )
            commands.append({"parse": command})
            if acl_type == "none" and not allowed:
                commands.append(
                    {
                        "parse": (
                            f"interface {segment_arg} mac access-list type deny"
                        )
                    }
                )
            elif restore_acl_none:
                commands.append(
                    {
                        "parse": (
                            f"interface {segment_arg} mac access-list type none"
                        )
                    }
                )

        commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name=(
                "Не удалось разрешить Wi-Fi доступ клиента"
                if allowed
                else "Не удалось отключить Wi-Fi клиента"
            ),
        )
        details = {
            "mac": mac,
            "segment_id": segment_id,
            "access_point": access_point,
            "allowed": bool(allowed),
            "acl_type": acl_type,
            "registered_automatically": register_client,
        }
        if not success:
            return False, message, details

        if acl_type == "none" and not allowed:
            self._acl_modes_enabled_by_app.add(segment_id)
        elif restore_acl_none:
            self._acl_modes_enabled_by_app.discard(segment_id)

        verified, verification = self.verify_access(
            mac=mac,
            segment_id=segment_id,
            expected_allowed=bool(allowed),
        )
        if not verified:
            return (
                False,
                "Команда принята, но новое состояние Wi-Fi доступа не подтверждено: "
                + verification,
                details,
            )

        return (
            True,
            "Wi-Fi доступ клиента разрешён."
            if allowed
            else (
                "Клиент отключён от Wi-Fi и не сможет подключиться "
                "повторно до разрешения доступа."
            ),
            details,
        )

    def verify_access(
        self,
        *,
        mac: str,
        segment_id: str,
        expected_allowed: bool,
    ) -> tuple[bool, str]:
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )
        if not isinstance(config, dict):
            return False, "нет конфигурации интерфейсов"

        acl_type, addresses = self.read_acl(config.get(segment_id))
        if acl_type not in self.SUPPORTED_ACL_TYPES:
            return False, f"режим ACL: {acl_type or 'не определён'}"

        actual_allowed = not self.is_blocked(
            mac,
            acl_type=acl_type,
            addresses=addresses,
        )
        if actual_allowed == expected_allowed:
            return True, "состояние подтверждено"
        return False, f"ожидалось {'разрешено' if expected_allowed else 'отключено'}"

    @classmethod
    def resolve_segment_id(
        cls,
        interface_config: dict[str, Any],
        *,
        access_point: str,
        requested_segment: str,
    ) -> str:
        config = (
            interface_config
            if isinstance(interface_config, dict)
            else {}
        )
        ap = str(access_point or "").strip().casefold()
        requested = str(requested_segment or "").strip()

        if requested in config and isinstance(config.get(requested), dict):
            included = cls.included_interfaces(config[requested])
            if (
                not ap
                or ap in {value.casefold() for value in included}
                or requested.casefold().startswith("bridge")
            ):
                return requested

        if ap:
            for interface_id, item in config.items():
                if not isinstance(item, dict):
                    continue
                included = cls.included_interfaces(item)
                if ap in {value.casefold() for value in included}:
                    return str(interface_id)

        return ""

    @classmethod
    def included_interfaces(cls, item: dict[str, Any]) -> list[str]:
        raw = item.get("include") if isinstance(item, dict) else None
        values = raw if isinstance(raw, list) else [raw]
        result: list[str] = []
        for value in values:
            if isinstance(value, dict):
                text = str(
                    value.get("interface") or value.get("name") or ""
                ).strip()
                candidates = [text]
            else:
                candidates = str(value or "").split(",")
            for candidate in candidates:
                normalized = str(candidate or "").strip()
                if normalized and normalized not in result:
                    result.append(normalized)
        return result

    @classmethod
    def read_acl(
        cls,
        item: Any,
    ) -> tuple[str, set[str]]:
        if not isinstance(item, dict):
            return "", set()
        mac_config = item.get("mac")
        if not isinstance(mac_config, dict):
            return "none", set()
        acl = mac_config.get("access-list")
        if not isinstance(acl, dict):
            return "none", set()
        acl_type = str(acl.get("type") or "none").strip().lower()
        addresses: set[str] = set()
        cls._collect_macs(acl.get("address"), addresses)
        return acl_type, addresses

    @classmethod
    def _collect_macs(cls, value: Any, output: set[str]) -> None:
        normalized = cls.normalize_mac(value)
        if normalized:
            output.add(normalized)
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                key_mac = cls.normalize_mac(key)
                if key_mac:
                    output.add(key_mac)
                cls._collect_macs(nested, output)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                cls._collect_macs(nested, output)

    @classmethod
    def normalize_mac(cls, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", ":")
        return text if cls.MAC_PATTERN.fullmatch(text) else ""

    @staticmethod
    def is_blocked(
        mac: str,
        *,
        acl_type: str,
        addresses: set[str],
    ) -> bool:
        if not mac:
            return False
        normalized_type = str(acl_type or "").strip().lower()
        if normalized_type == "deny":
            return mac in addresses
        if normalized_type == "permit":
            return mac not in addresses
        return False
