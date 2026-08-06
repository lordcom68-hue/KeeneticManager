from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from core.services.interface_service import InterfaceService


class WirelessIspService:
    """Чтение, распознавание и управление Wireless ISP 2.4/5 ГГц."""

    truthy = staticmethod(InterfaceService.truthy)

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def wireless_isp_band_from_interface(
        cls,
        interface_id: str,
    ) -> str:
        name = str(interface_id or "").strip().lower()

        if name.startswith("wifimaster0/"):
            return "2.4"

        if name.startswith("wifimaster1/"):
            return "5"

        return ""

    @classmethod
    def wireless_isp_configured_item(
        cls,
        item: dict[str, Any],
        runtime_item: dict[str, Any] | None = None,
    ) -> bool:
        """Определяет, содержит ли WifiStation реальное WISP-подключение.

        Само состояние ``up`` не означает, что подключение настроено:
        официальный интерфейс Keenetic позволяет включить пустой
        WifiStation без SSID и без выбранного приоритета. Такой диапазон
        должен оставаться свободным в мастере создания.
        """
        runtime = (
            runtime_item
            if isinstance(runtime_item, dict)
            else {}
        )
        ssid = str(
            item.get("ssid")
            or runtime.get("ssid")
            or ""
        ).strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )
        has_global_order = bool(
            isinstance(global_config, dict)
            and global_config.get("order") is not None
        )
        runtime_global = cls.truthy(
            runtime.get("global")
        )

        return bool(
            ssid
            or has_global_order
            or runtime_global
        )

    def enrich_wireless_isp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        """Добавляет настройки и безопасное удаление Wireless ISP."""
        for connection in connections:
            interface_id = str(
                connection.get("interface_id")
                or ""
            ).strip()
            runtime_item = interface_data.get(interface_id)
            config_item = interface_config_data.get(interface_id)

            runtime_type = (
                str(runtime_item.get("type") or "")
                if isinstance(runtime_item, dict)
                else ""
            )
            runtime_name = (
                str(runtime_item.get("interface-name") or "")
                if isinstance(runtime_item, dict)
                else ""
            )

            if not self.is_wireless_isp_interface(
                runtime_type,
                interface_id,
                runtime_name,
            ):
                continue

            if not isinstance(config_item, dict):
                config_item = {}

            band = self.wireless_isp_band_from_interface(
                interface_id
            )
            band_title = (
                "2,4 ГГц"
                if band == "2.4"
                else "5 ГГц"
                if band == "5"
                else "не определён"
            )
            ssid = str(
                config_item.get("ssid")
                or connection.get("ssid")
                or (
                    runtime_item.get("ssid")
                    if isinstance(runtime_item, dict)
                    else ""
                )
                or ""
            ).strip()
            authentication = config_item.get("authentication")
            psk_configured = bool(
                isinstance(authentication, dict)
                and isinstance(
                    authentication.get("wpa-psk"),
                    dict,
                )
            )
            encryption = config_item.get("encryption")
            encrypted = bool(
                psk_configured
                or (
                    isinstance(encryption, dict)
                    and self.truthy(encryption.get("enable"))
                )
            )
            bssid = ""
            mac_config = config_item.get("mac")

            if isinstance(mac_config, dict):
                bssid = str(
                    mac_config.get("bssid")
                    or ""
                ).strip().upper()

            if not bssid and isinstance(runtime_item, dict):
                bssid = str(
                    runtime_item.get("ap")
                    or ""
                ).strip().upper()

            connection["provider_protocol"] = "Wireless ISP"
            connection["wireless_isp_settings_supported"] = True
            connection["wireless_isp_band"] = band
            connection["wireless_isp_band_title"] = band_title
            connection["wireless_isp_ssid"] = ssid
            connection["wireless_isp_bssid"] = bssid
            connection["wireless_isp_open_network"] = not encrypted
            connection["wireless_isp_password_configured"] = psk_configured
            connection["mac_settings_supported"] = False
            connection["provider_port_label"] = band_title
            connection["provider_port_id"] = interface_id

            if ssid:
                connection["ssid"] = ssid

            if isinstance(runtime_item, dict):
                for source_key, target_key in (
                    ("rssi", "wireless_isp_rssi"),
                    ("channel", "wireless_isp_channel"),
                    ("mode", "wireless_isp_mode"),
                    ("rate", "wireless_isp_rate"),
                    ("ap", "wireless_isp_connected_bssid"),
                ):
                    value = runtime_item.get(source_key)
                    if value not in (None, ""):
                        connection[target_key] = value

            if self.wireless_isp_configured_item(
                config_item,
                runtime_item if isinstance(runtime_item, dict) else None,
            ):
                connection["delete_supported"] = True
                connection["delete_kind"] = "wireless.isp"
                connection["delete_reason"] = ""
                connection["delete_port_display_name"] = (
                    f"Wireless ISP {band_title}"
                )
                connection["delete_port_link_known"] = True
                connection["delete_port_link_active"] = bool(
                    isinstance(runtime_item, dict)
                    and str(
                        runtime_item.get("link")
                        or ""
                    ).strip().lower()
                    == "up"
                )
                connection["delete_port_link"] = str(
                    runtime_item.get("link")
                    if isinstance(runtime_item, dict)
                    else "unknown"
                ).strip().lower()
            else:
                self.client.clear_connection_delete_context(
                    connection,
                    "Wireless ISP в этом диапазоне не настроен.",
                )

    @staticmethod
    def wireless_isp_display_name(
        interface_id: str,
        interface_name: str,
        item: dict[str, Any],
    ) -> str:
        """
        Определяет диапазон Wireless ISP.

        Обычно WifiMaster0 соответствует 2.4 ГГц,
        а WifiMaster1 — 5 ГГц.
        """
        text = " ".join(
            [
                interface_id,
                interface_name,
                str(item.get("description") or ""),
            ]
        ).lower()

        if (
            "wifimaster0" in text
            or "2.4" in text
            or "2g" in text
            or "2ghz" in text
        ):
            return "Wireless ISP 2.4 ГГц"

        if (
            "wifimaster1" in text
            or "5ghz" in text
            or "5 ггц" in text
            or "5g" in text
        ):
            return "Wireless ISP 5 ГГц"

        return interface_name or "Wireless ISP"

    @staticmethod
    def is_wireless_isp_interface(
        interface_type: str,
        interface_id: str,
        interface_name: str,
    ) -> bool:
        text = " ".join(
            [
                interface_type,
                interface_id,
                interface_name,
            ]
        ).lower()

        return any(
            marker in text
            for marker in (
                "wifistation",
                "wirelessisp",
                "wireless isp",
            )
        )

    def is_configured_wireless_isp(
        self,
        item: dict[str, Any],
        connection: dict[str, Any],
    ) -> bool:
        """
        WifiStation считается Wireless ISP только при наличии
        хотя бы одного реального признака настроенного канала.
        """
        priority = self.client.priority_service.extract_valid_priority(item)
        address = str(
            item.get("address")
            or connection.get("address")
            or connection.get("ip")
            or ""
        ).strip()

        connected = str(
            item.get("connected")
            or connection.get("connected")
            or ""
        ).strip().lower()

        link = str(
            item.get("link")
            or connection.get("link")
            or ""
        ).strip().lower()

        state = str(
            item.get("state")
            or connection.get("state")
            or ""
        ).strip().lower()

        return bool(
            self.truthy(item.get("global"))
            or self.truthy(item.get("defaultgw"))
            or priority is not None
            or (
                address
                and address not in {
                    "0.0.0.0",
                    "::",
                    "—",
                }
            )
            or connected in {
                "yes",
                "true",
                "1",
            }
            or (
                link == "up"
                and state == "up"
            )
        )

    @staticmethod
    def normalize_wireless_isp_band(value: Any) -> str:
        text = str(value or "").strip().lower().replace("ghz", "")
        text = text.replace("ггц", "").replace(",", ".").strip()

        if text in {"2.4", "2", "24", "wifimaster0"}:
            return "2.4"

        if text in {"5", "5.0", "50", "wifimaster1"}:
            return "5"

        return ""

    @classmethod
    def wireless_isp_interface_for_band(
        cls,
        band: Any,
    ) -> tuple[str, str]:
        normalized = cls.normalize_wireless_isp_band(band)

        if normalized == "2.4":
            return "WifiMaster0", "WifiMaster0/WifiStation0"

        if normalized == "5":
            return "WifiMaster1", "WifiMaster1/WifiStation0"

        return "", ""

    @classmethod
    def parse_wireless_site_survey(
        cls,
        data: Any,
    ) -> list[dict[str, Any]]:
        """Нормализует разные ответы show site-survey."""
        result: list[dict[str, Any]] = []

        def normalize_mac(value: Any) -> str:
            text = str(value or "").strip().upper().replace("-", ":")
            return text if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", text) else ""

        def first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
            lowered = {str(key).lower().replace("_", "-"): value for key, value in mapping.items()}
            for key in keys:
                normalized = key.lower().replace("_", "-")
                if normalized in lowered:
                    return lowered[normalized]
            return None

        def add(mapping: dict[str, Any], fallback_ssid: str = "") -> None:
            ssid = str(
                first(mapping, ("ssid", "essid", "network", "name"))
                or fallback_ssid
                or ""
            ).strip()
            bssid = normalize_mac(
                first(mapping, ("bssid", "mac", "address", "ap"))
            )
            channel = first(mapping, ("channel", "ch"))
            rssi = first(mapping, ("rssi", "signal", "signal-level", "level"))
            quality = first(mapping, ("quality", "q"))
            rate = first(mapping, ("rate", "max-rate", "speed"))
            security = str(
                first(mapping, ("security", "encryption", "auth", "authentication"))
                or ""
            ).strip()

            if not ssid:
                return

            item: dict[str, Any] = {
                "ssid": ssid,
                "bssid": bssid,
            }

            if channel not in (None, ""):
                try:
                    item["channel"] = int(float(channel))
                except (TypeError, ValueError):
                    item["channel"] = str(channel)

            if rssi not in (None, ""):
                try:
                    item["signal"] = int(float(rssi))
                except (TypeError, ValueError):
                    item["signal"] = str(rssi)

            if quality not in (None, ""):
                try:
                    item["quality"] = int(float(quality))
                except (TypeError, ValueError):
                    item["quality"] = str(quality)

            if rate not in (None, ""):
                item["rate"] = rate

            if security:
                item["security"] = security
                item["open"] = security.lower() in {
                    "none",
                    "open",
                    "off",
                    "disabled",
                }

            result.append(item)

        def parse_table(text: str) -> None:
            for raw_line in str(text or "").splitlines():
                line = raw_line.strip()

                if not line or set(line) <= {"=", "-", " ", "\t"}:
                    continue

                if re.search(r"\b(?:SSID|ESSID)\b", line, flags=re.IGNORECASE) and re.search(
                    r"\b(?:MAC|BSSID)\b",
                    line,
                    flags=re.IGNORECASE,
                ):
                    continue

                match = re.match(
                    r"^(.*?)\s+((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})"
                    r"(?:\s+(\d+))?(?:\s+(.+?))?(?:\s+(-?\d+))?$",
                    line,
                )

                if match is None:
                    continue

                ssid, bssid, channel, rate, quality = match.groups()
                mapping: dict[str, Any] = {
                    "ssid": ssid.strip(),
                    "bssid": bssid,
                }

                if channel:
                    mapping["channel"] = channel

                if rate:
                    mapping["rate"] = rate.strip()

                if quality:
                    mapping["quality"] = quality

                add(mapping)

        def walk(value: Any, fallback_ssid: str = "") -> None:
            if isinstance(value, str):
                parse_table(value)
                return

            if isinstance(value, list):
                for nested in value:
                    walk(nested)
                return

            if not isinstance(value, dict):
                return

            key_text = {str(key).lower() for key in value}
            if key_text.intersection({"ssid", "essid", "bssid", "mac"}):
                add(value, fallback_ssid)

            for key, nested in value.items():
                nested_fallback = (
                    str(key)
                    if isinstance(nested, dict)
                    and re.fullmatch(r"[^/:]{1,32}", str(key))
                    else ""
                )
                if isinstance(nested, (dict, list, str)):
                    walk(nested, nested_fallback)

        walk(data)

        unique: dict[tuple[str, str], dict[str, Any]] = {}

        for item in result:
            ssid = str(item.get("ssid") or "").strip()
            bssid = str(item.get("bssid") or "").strip().upper()

            if not ssid:
                continue

            key = (ssid, bssid)
            previous = unique.get(key)

            if previous is None:
                unique[key] = item
                continue

            previous_signal = previous.get("signal")
            current_signal = item.get("signal")

            try:
                if float(current_signal) > float(previous_signal):
                    unique[key] = item
            except (TypeError, ValueError):
                pass

        networks = list(unique.values())

        def sort_key(item: dict[str, Any]) -> tuple[float, float, str]:
            try:
                signal = float(item.get("signal"))
            except (TypeError, ValueError):
                signal = -999.0

            try:
                quality = float(item.get("quality"))
            except (TypeError, ValueError):
                quality = -1.0

            return (-signal, -quality, str(item.get("ssid") or "").lower())

        networks.sort(key=sort_key)
        return networks

    def scan_wireless_isp(
        self,
        band: Any,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        master_id, _station_id = self.wireless_isp_interface_for_band(band)

        if not master_id:
            return False, "Неизвестный диапазон Wireless ISP.", []

        attempts: list[tuple[str, Any]] = []

        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", []

        response = self.client.transport.request(
            method="GET",
            path=(
                "/rci/show/site-survey/"
                f"{quote(master_id, safe='')}"
            ),
            endpoint="show/site-survey",
            required=False,
            timeout=max(self.client.timeout, 30),
            allow_reauthentication=True,
            retry_transient=True,
        )
        if response is None:
            attempts.append(
                (
                    "GET",
                    self.client.transport.last_error
                    or "Пустой HTTP-ответ.",
                )
            )
        else:
            try:
                data: Any = response.json()
            except ValueError:
                data = response.text

            networks = self.parse_wireless_site_survey(data)
            if networks:
                return True, f"Найдено сетей: {len(networks)}.", networks
            attempts.append(("GET", data))

        for payload in (
            {"show": {"site-survey": {"name": master_id}}},
            {"parse": f"show site-survey {master_id}"},
        ):
            success, data, message = self.client.post_rci_query(
                payload,
                timeout=30,
            )
            networks = self.parse_wireless_site_survey(data)

            if success and networks:
                return True, f"Найдено сетей: {len(networks)}.", networks

            attempts.append(("POST", message or data))

        # Пустой, но корректный результат означает, что в эфире
        # сейчас нет доступных точек. Ручной SSID остаётся доступен.
        if any(not isinstance(value, str) or "not found" not in value.lower() for _kind, value in attempts):
            return True, "Доступные сети не найдены. Можно указать скрытый SSID вручную.", []

        return False, "Роутер не поддержал сканирование выбранного диапазона.", []

    @staticmethod
    def valid_wireless_isp_ssid(value: Any) -> bool:
        text = str(value or "")

        if not text or any(character in "\r\n\x00" for character in text):
            return False

        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return False

        return 1 <= size <= 32

    @staticmethod
    def valid_wireless_isp_password(value: Any) -> bool:
        text = str(value or "")

        if any(character in "\r\n\x00" for character in text):
            return False

        if re.fullmatch(r"[0-9A-Fa-f]{64}", text):
            return True

        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return False

        return 8 <= size <= 63

    def rollback_wireless_isp_creation(
        self,
        *,
        interface_id: str,
        ipv4_settings: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        interface_arg = self.client.quote_cli_argument(interface_id)
        errors: list[str] = []

        commands = [
            f"interface {interface_arg} no up",
            f"interface {interface_arg} no ping-check profile",
            f"interface {interface_arg} no ip global",
            f"interface {interface_arg} no mac bssid",
            f"interface {interface_arg} no authentication wpa-psk",
            f"interface {interface_arg} encryption disable",
            f"interface {interface_arg} no ssid",
            f"interface {interface_arg} no description",
        ]

        if bool(ipv4_settings.get("dhcp")):
            commands.append(
                f"interface {interface_arg} no ip address dhcp"
            )
        else:
            address = self.client.normalize_ipv4_text(ipv4_settings.get("address"))
            try:
                prefix = int(ipv4_settings.get("prefix"))
            except (TypeError, ValueError):
                prefix = None
            if address and prefix is not None:
                commands.append(
                    f"interface {interface_arg} no ip address {address}/{prefix}"
                )

        for command in commands:
            success, message = self.client.execute_parse_command(command)

            if not success:
                lowered = str(message or "").lower()
                if not any(
                    marker in lowered
                    for marker in (
                        "not found",
                        "not configured",
                        "does not exist",
                        "не найден",
                        "не настро",
                    )
                ):
                    errors.append(message)

        self.client.replace_interface_dns_records(
            interface_id=interface_id,
            addresses=[],
        )
        self.client.execute_parse_command("system configuration save")

        remaining_priorities = {
            name: order
            for name, order in old_priorities.items()
            if name != interface_id
        }

        if remaining_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                remaining_priorities
            )
            if not success:
                errors.append(message)

        if errors:
            return False, "; ".join(dict.fromkeys(errors))

        return True, "Настройки Wireless ISP очищены."

    def verify_wireless_isp_configuration(
        self,
        *,
        interface_id: str,
        ssid: str,
        open_network: bool,
        bssid: str,
        expected_order: int,
        expected_up: bool = True,
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(12):
            time.sleep(0.65)
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            item = config.get(interface_id)
            mismatches = []

            if not isinstance(item, dict):
                mismatches.append("интерфейс Wireless ISP не найден")
            else:
                if str(item.get("ssid") or "") != ssid:
                    mismatches.append("SSID не сохранён")

                authentication = item.get("authentication")
                encryption = item.get("encryption")
                encrypted = bool(
                    isinstance(authentication, dict)
                    and authentication.get("wpa-psk")
                ) or bool(
                    isinstance(encryption, dict)
                    and self.truthy(encryption.get("enable"))
                )

                if open_network == encrypted:
                    mismatches.append("режим защиты не совпадает")

                ip_config = item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                try:
                    actual_order = int(global_config.get("order"))
                except (TypeError, ValueError, AttributeError):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append("приоритет не применён")

                if expected_up and not self.truthy(item.get("up")):
                    mismatches.append("интерфейс не включён")

                if bssid:
                    mac_config = item.get("mac")
                    actual_bssid = (
                        str(mac_config.get("bssid") or "").strip().upper()
                        if isinstance(mac_config, dict)
                        else ""
                    )
                    if actual_bssid != bssid.upper():
                        mismatches.append("BSSID не сохранён")

            if not mismatches:
                return True, ""

        return False, "Проверка Wireless ISP не пройдена: " + ", ".join(mismatches or ["нет данных"])

    def create_wireless_isp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        band = self.normalize_wireless_isp_band(settings.get("band"))
        _master_id, interface_id = self.wireless_isp_interface_for_band(band)
        ssid = str(settings.get("ssid") or "")
        description = str(
            settings.get("description")
            or (
                "Wireless ISP 2,4 ГГц"
                if band == "2.4"
                else "Wireless ISP 5 ГГц"
            )
        ).strip()
        open_network = bool(settings.get("open_network"))
        password = str(settings.get("password") or "")
        bssid = str(settings.get("bssid") or "").strip().upper()
        ipv4_settings = dict(settings.get("ipv4") or {"dhcp": True})

        if not interface_id:
            return False, "Не выбран диапазон Wireless ISP.", {"preflight_rejected": True}

        if not self.valid_wireless_isp_ssid(ssid):
            return False, "Некорректный SSID Wireless ISP.", {"preflight_rejected": True}

        if not description or len(description) > 64:
            return False, "Некорректное название подключения.", {"preflight_rejected": True}

        if not open_network and not self.valid_wireless_isp_password(password):
            return False, "Некорректный пароль Wireless ISP.", {"preflight_rejected": True}

        if bssid and not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", bssid):
            return False, "Некорректный BSSID точки доступа.", {"preflight_rejected": True}

        runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return False, "Не удалось прочитать конфигурацию интерфейсов.", {}

        options = self.client.build_connection_creation_options(
            interface_data=runtime if isinstance(runtime, dict) else {},
            interface_config_data=config,
        )
        bands = options.get("wireless_isp", {}).get("bands", [])
        current_band = next(
            (
                item
                for item in bands
                if str(item.get("id") or "") == band
            ),
            None,
        )

        if not isinstance(current_band, dict) or not current_band.get("available"):
            return False, "Этот диапазон Wireless ISP уже занят или недоступен.", {"preflight_rejected": True}

        conflict = self.client.find_static_ipv4_network_conflict(
            ipv4_settings=ipv4_settings,
            interface_config=config,
            interface_runtime=runtime if isinstance(runtime, dict) else {},
        )

        if conflict:
            return (
                False,
                "Статическая сеть "
                f"{conflict['target_network']} уже используется "
                f"интерфейсом {conflict['interface_id']} "
                f"({conflict['network']}).",
                {"preflight_rejected": True},
            )

        old_priorities = self.client.priority_service.current_global_orders(config)
        try:
            requested_order = int(
                settings.get("priority_order", len(old_priorities))
            )
        except (TypeError, ValueError):
            return False, "Некорректный приоритет подключения.", {"preflight_rejected": True}

        requested_order = max(0, min(len(old_priorities), requested_order))
        interface_arg = self.client.quote_cli_argument(interface_id)
        ssid_arg = self.client.quote_cli_argument(ssid)
        description_arg = self.client.quote_cli_argument(description)

        base_commands: list[dict[str, Any]] = [
            {"parse": f"interface {interface_arg} no up"},
            {"parse": f"interface {interface_arg} description {description_arg}"},
            {"parse": f"interface {interface_arg} security-level public"},
            {"parse": f"interface {interface_arg} ssid {ssid_arg}"},
        ]

        if bssid:
            base_commands.append(
                {"parse": f"interface {interface_arg} mac bssid {bssid}"}
            )
        else:
            base_commands.append(
                {"parse": f"interface {interface_arg} no mac bssid"}
            )

        if open_network:
            base_commands.extend(
                [
                    {"parse": f"interface {interface_arg} no authentication wpa-psk"},
                    {"parse": f"interface {interface_arg} encryption disable"},
                ]
            )
        else:
            password_arg = self.client.quote_cli_argument(password)
            base_commands.extend(
                [
                    {"parse": f"interface {interface_arg} authentication wpa-psk {password_arg}"},
                    {"parse": f"interface {interface_arg} encryption enable"},
                    {"parse": f"interface {interface_arg} encryption wpa2"},
                ]
            )

        base_commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.client.execute_rci_batch(
            base_commands,
            operation_name="Не удалось подготовить Wireless ISP",
        )
        message = self.client.redact_secrets_from_text(message, password)

        if not success:
            rollback_success, rollback_message = self.rollback_wireless_isp_creation(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                old_priorities=old_priorities,
            )
            return False, message + " Откат: " + rollback_message, {"rollback_success": rollback_success}

        interface_settings: dict[str, Any] = {
            "mtu": settings.get("mtu", 1500),
            "ipv4": ipv4_settings,
            "use_provider_dns": bool(settings.get("use_provider_dns", True)),
            "manual_dns": list(settings.get("manual_dns") or []),
            "ping_check": dict(settings.get("ping_check") or {"mode": "disabled"}),
        }

        if ipv4_settings.get("dhcp"):
            interface_settings["dhcp_client"] = dict(
                settings.get("dhcp_client")
                or {
                    "hostname": "Keenetic",
                    "routes": True,
                    "dns_routes": True,
                }
            )

        settings_success, settings_message = self.client.set_interface_settings(
            interface_id=interface_id,
            settings=interface_settings,
        )
        settings_message = self.client.redact_secrets_from_text(settings_message, password)

        if not settings_success:
            rollback_success, rollback_message = self.rollback_wireless_isp_creation(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                old_priorities=old_priorities,
            )
            return False, settings_message + " Откат: " + rollback_message, {"rollback_success": rollback_success}

        priorities = self.client.priority_service.priorities_with_inserted_interface(
            old_priorities,
            interface_id=interface_id,
            requested_order=requested_order,
        )
        priority_success, priority_message = self.client.priority_service.set_internet_priorities(priorities)

        if not priority_success:
            rollback_success, rollback_message = self.rollback_wireless_isp_creation(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                old_priorities=old_priorities,
            )
            return False, priority_message + " Откат: " + rollback_message, {"rollback_success": rollback_success}

        enable_success, enable_message = self.client.execute_rci_batch(
            [
                {"parse": f"interface {interface_arg} up"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось включить Wireless ISP",
        )
        enable_message = self.client.redact_secrets_from_text(enable_message, password)

        if not enable_success:
            rollback_success, rollback_message = self.rollback_wireless_isp_creation(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                old_priorities=old_priorities,
            )
            return False, enable_message + " Откат: " + rollback_message, {"rollback_success": rollback_success}

        verified, verify_message = self.verify_wireless_isp_configuration(
            interface_id=interface_id,
            ssid=ssid,
            open_network=open_network,
            bssid=bssid,
            expected_order=requested_order,
        )

        if not verified:
            rollback_success, rollback_message = self.rollback_wireless_isp_creation(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                old_priorities=old_priorities,
            )
            return False, verify_message + " Откат: " + rollback_message, {"rollback_success": rollback_success}

        self.client._traffic_counters.pop(interface_id, None)
        return (
            True,
            f"Wireless ISP {('2,4' if band == '2.4' else '5')} ГГц создан и сохранён.",
            {
                "interface_id": interface_id,
                "band": band,
                "ssid": ssid,
                "bssid": bssid,
                "priority_order": requested_order,
                "password_stored": not open_network,
            },
        )

    def update_wireless_isp_settings(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )
        item = config.get(interface_id) if isinstance(config, dict) else None

        if not isinstance(item, dict) or not self.wireless_isp_configured_item(item):
            return False, "Подключение Wireless ISP не найдено."

        interface_arg = self.client.quote_cli_argument(interface_id)
        commands: list[dict[str, Any]] = []
        password = str(settings.get("password") or "")

        if "ssid" in settings:
            ssid = str(settings.get("ssid") or "")
            if not self.valid_wireless_isp_ssid(ssid):
                return False, "Некорректный SSID Wireless ISP."
            commands.append(
                {"parse": f"interface {interface_arg} ssid {self.client.quote_cli_argument(ssid)}"}
            )

        if "bssid" in settings:
            bssid = str(settings.get("bssid") or "").strip().upper()
            if bssid:
                if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", bssid):
                    return False, "Некорректный BSSID точки доступа."
                commands.append(
                    {"parse": f"interface {interface_arg} mac bssid {bssid}"}
                )
            else:
                commands.append(
                    {"parse": f"interface {interface_arg} no mac bssid"}
                )

        if "open_network" in settings:
            open_network = bool(settings.get("open_network"))
            if open_network:
                commands.extend(
                    [
                        {"parse": f"interface {interface_arg} no authentication wpa-psk"},
                        {"parse": f"interface {interface_arg} encryption disable"},
                    ]
                )
            else:
                if not self.valid_wireless_isp_password(password):
                    return False, "Для защищённой сети укажите новый пароль WPA/WPA2."
                commands.extend(
                    [
                        {"parse": f"interface {interface_arg} authentication wpa-psk {self.client.quote_cli_argument(password)}"},
                        {"parse": f"interface {interface_arg} encryption enable"},
                        {"parse": f"interface {interface_arg} encryption wpa2"},
                    ]
                )
        elif password:
            if not self.valid_wireless_isp_password(password):
                return False, "Некорректный пароль WPA/WPA2."
            commands.extend(
                [
                    {"parse": f"interface {interface_arg} authentication wpa-psk {self.client.quote_cli_argument(password)}"},
                    {"parse": f"interface {interface_arg} encryption enable"},
                    {"parse": f"interface {interface_arg} encryption wpa2"},
                ]
            )

        if not commands:
            return True, "Параметры Wi-Fi не изменялись."

        commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name="Не удалось изменить Wireless ISP",
        )
        message = self.client.redact_secrets_from_text(message, password)

        if not success:
            return False, message

        return True, "Параметры Wi-Fi Wireless ISP изменены."

    def delete_wireless_isp(
        self,
        *,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        name = str(interface_id or "").strip()
        band = self.wireless_isp_band_from_interface(name)

        if not band:
            return False, "Указан неподдерживаемый интерфейс Wireless ISP.", {"preflight_rejected": True}

        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(config, dict):
            return False, "Не удалось прочитать конфигурацию интерфейсов.", {}

        item = config.get(name)

        if not isinstance(item, dict) or not self.wireless_isp_configured_item(item):
            return False, "Подключение Wireless ISP уже отсутствует.", {"preflight_rejected": True}

        old_priorities = self.client.priority_service.current_global_orders(config)
        assignment = item.get("ping-check")
        ping_profile = (
            str(assignment.get("profile") or "").strip()
            if isinstance(assignment, dict)
            else ""
        )
        interface_arg = self.client.quote_cli_argument(name)

        def restore_before_cleanup() -> tuple[bool, str]:
            errors: list[str] = []

            if old_priorities:
                restored, restore_message = (
                    self.client.priority_service.set_internet_priorities(
                        dict(old_priorities)
                    )
                )
                if not restored:
                    errors.append(restore_message)

            if ping_profile:
                restored, restore_message = (
                    self.client.execute_parse_command(
                        f"interface {interface_arg} ping-check profile "
                        + self.client.quote_cli_argument(ping_profile)
                    )
                )
                if not restored:
                    errors.append(restore_message)

            restored, restore_message = (
                self.client.execute_parse_command(
                    f"interface {interface_arg} up"
                )
            )
            if not restored:
                errors.append(restore_message)

            saved, save_message = (
                self.client.execute_parse_command(
                    "system configuration save"
                )
            )
            if not saved:
                errors.append(save_message)

            if errors:
                return (
                    False,
                    "; ".join(dict.fromkeys(errors)),
                )

            return True, "Канал Wireless ISP восстановлен."

        phase_one = [
            {"parse": f"interface {interface_arg} no up"},
            {"parse": f"interface {interface_arg} no ping-check profile"},
            {"parse": f"interface {interface_arg} no ip global"},
            {"system": {"configuration": {"save": {}}}},
        ]
        success, message = self.client.execute_rci_batch(
            phase_one,
            operation_name="Не удалось отключить Wireless ISP",
        )

        if not success:
            rollback_success, rollback_message = (
                restore_before_cleanup()
            )
            return (
                False,
                message + " Откат: " + rollback_message,
                {"rollback_success": rollback_success},
            )

        remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda pair: pair[1],
            )
            if current_id != name
        ]
        priorities = {
            current_id: index
            for index, current_id in enumerate(remaining)
        }

        if priorities:
            priority_success, priority_message = self.client.priority_service.set_internet_priorities(priorities)
            if not priority_success:
                # До очистки SSID и пароля канал можно полностью вернуть.
                rollback_success, rollback_message = (
                    restore_before_cleanup()
                )
                return (
                    False,
                    priority_message + " Откат: " + rollback_message,
                    {"rollback_success": rollback_success},
                )

        ip_config = item.get("ip")
        address_config = (
            ip_config.get("address")
            if isinstance(ip_config, dict)
            else None
        )
        uses_dhcp, static_address, static_prefix = (
            self.client.extract_configured_ipv4(address_config)
        )
        current_gateway = self.client.find_interface_default_gateway(
            name
        )

        cleanup_commands = [
            {"parse": f"interface {interface_arg} no mac bssid"},
            {"parse": f"interface {interface_arg} no authentication wpa-psk"},
            {"parse": f"interface {interface_arg} encryption disable"},
            {"parse": f"interface {interface_arg} no ssid"},
            {"parse": f"interface {interface_arg} no description"},
        ]

        if uses_dhcp:
            cleanup_commands.append(
                {"parse": f"interface {interface_arg} no ip address dhcp"}
            )
        elif static_address and static_prefix is not None:
            if current_gateway:
                cleanup_commands.append(
                    {
                        "parse": (
                            "no ip route default "
                            + self.client.quote_cli_argument(current_gateway)
                        )
                    }
                )
            cleanup_commands.append(
                {
                    "parse": (
                        f"interface {interface_arg} no ip address "
                        f"{static_address}/{static_prefix}"
                    )
                }
            )

        cleanup_commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        cleanup_success, cleanup_message = self.client.execute_rci_batch(
            cleanup_commands,
            operation_name="Не удалось очистить Wireless ISP",
        )

        if not cleanup_success:
            return False, cleanup_message, {"partial_success": True}

        self.client.replace_interface_dns_records(
            interface_id=name,
            addresses=[],
        )

        if ping_profile.startswith("_KM_"):
            self.client.execute_parse_command(
                "no ping-check profile "
                + self.client.quote_cli_argument(ping_profile)
            )

        self.client.execute_parse_command("system configuration save")

        last_reason = "нет данных"
        for _ in range(10):
            time.sleep(0.55)
            current = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            current_item = current.get(name) if isinstance(current, dict) else None

            if not isinstance(current_item, dict):
                return True, "Wireless ISP удалён.", {"interface_id": name, "band": band}

            ip_config = current_item.get("ip")
            global_config = (
                ip_config.get("global")
                if isinstance(ip_config, dict)
                else None
            )
            configured = self.wireless_isp_configured_item(current_item)

            if not configured and not isinstance(global_config, dict):
                self.client._traffic_counters.pop(name, None)
                return True, "Wireless ISP удалён, диапазон снова свободен.", {"interface_id": name, "band": band}

            last_reason = "SSID или глобальная роль всё ещё присутствуют"

        return False, "Удаление Wireless ISP не подтверждено: " + last_reason, {"partial_success": True}
