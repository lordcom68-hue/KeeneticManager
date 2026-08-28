from __future__ import annotations

import ipaddress
import time
from typing import Any


class InterfaceConfigurationService:
    """
    Applies and verifies configurable Internet-interface parameters.

    KeeneticClient remains the public compatibility facade. All router
    access goes through its shared RCI transport and specialized services.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    def remove_interface_dns_record(
        self,
        interface_id: str,
        address: str,
    ) -> tuple[bool, str]:
        """
        Удаляет одну DNS-запись через точную CLI-команду.

        Проверка выполняется только после общего сохранения
        конфигурации, потому что show/sc/interface отражает
        сохранённое состояние.
        """
        try:
            parsed_address = ipaddress.ip_address(
                address
            )
        except ValueError:
            return (
                False,
                f"Некорректный DNS-адрес: {address}",
            )

        family = (
            "ipv6"
            if isinstance(
                parsed_address,
                ipaddress.IPv6Address,
            )
            else "ip"
        )

        interface_name = (
            self.client.get_interface_cli_name(
                interface_id
            )
        )

        command = (
            f"no {family} name-server "
            f"{self.client.quote_cli_argument(str(parsed_address))} "
            f"\"\" on "
            f"{self.client.quote_cli_argument(interface_name)}"
        )

        success, message = self.client.execute_parse_command(
            command
        )

        if not success:
            return (
                False,
                (
                    f"Не удалось удалить старый DNS "
                    f"{parsed_address}: {message}"
                ),
            )

        return True, ""

    def add_interface_dns_record(
        self,
        interface_id: str,
        address: str,
    ) -> tuple[bool, str]:
        """
        Добавляет одну DNS-запись через точную CLI-команду.

        Фактическая проверка выполняется после сохранения.
        """
        try:
            parsed_address = ipaddress.ip_address(
                address
            )
        except ValueError:
            return (
                False,
                f"Некорректный DNS-адрес: {address}",
            )

        family = (
            "ipv6"
            if isinstance(
                parsed_address,
                ipaddress.IPv6Address,
            )
            else "ip"
        )

        interface_name = (
            self.client.get_interface_cli_name(
                interface_id
            )
        )

        command = (
            f"{family} name-server "
            f"{self.client.quote_cli_argument(str(parsed_address))} "
            f"\"\" on "
            f"{self.client.quote_cli_argument(interface_name)}"
        )

        success, message = self.client.execute_parse_command(
            command
        )

        if not success:
            return (
                False,
                (
                    f"Не удалось добавить DNS "
                    f"{parsed_address}: {message}"
                ),
            )

        return True, ""

    def replace_interface_dns_records(
        self,
        interface_id: str,
        addresses: list[str],
    ) -> tuple[bool, str]:
        """
        Формирует новое состояние ручных DNS.

        Здесь команды только применяются к текущей конфигурации.
        Сохранение и итоговая проверка выполняются вызывающим
        методом одним общим этапом.
        """
        current_dns = (
            self.client.get_configured_interface_dns(
                interface_id
            )
        )

        for address in current_dns:
            removed, message = (
                self.remove_interface_dns_record(
                    interface_id=interface_id,
                    address=address,
                )
            )

            if not removed:
                return False, message

        for address in addresses[:2]:
            added, message = (
                self.add_interface_dns_record(
                    interface_id=interface_id,
                    address=address,
                )
            )

            if not added:
                return False, message

        return True, ""

    @staticmethod
    def route_missing_message(
        message: str,
    ) -> bool:
        text = str(message or "").lower()

        return any(
            marker in text
            for marker in (
                "no such route",
                "route not found",
                "маршрут не найден",
                "такого маршрута",
                "route does not exist",
                "not found in routing table",
            )
        )

    def get_configured_interface_ipv4(
        self,
        interface_id: str,
    ) -> dict[str, Any]:
        """
        Возвращает сохранённый режим IPv4 интерфейса.
        """
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        item = (
            config.get(interface_id)
            if isinstance(config, dict)
            else None
        )

        if not isinstance(item, dict):
            return {
                "dhcp": False,
                "address": "",
                "prefix": None,
            }

        ip_config = item.get("ip")

        if not isinstance(ip_config, dict):
            return {
                "dhcp": False,
                "address": "",
                "prefix": None,
            }

        dhcp, address, prefix = (
            self.client.extract_configured_ipv4(
                ip_config.get("address")
            )
        )

        return {
            "dhcp": dhcp,
            "address": address,
            "prefix": prefix,
        }

    def remove_interface_default_route(
        self,
        interface_id: str,
        gateway: str = "",
    ) -> tuple[bool, str]:
        """
        Удаляет статический маршрут по умолчанию выбранного
        интерфейса.

        В команде удаления Keenetic принимает либо интерфейс,
        либо шлюз. Одновременная передача обоих аргументов
        для команды `no` не используется.
        """
        cli_name = self.client.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.client.quote_cli_argument(
            cli_name
        )

        commands = [
            (
                "no ip route default "
                f"{interface_arg}"
            )
        ]

        normalized_gateway = (
            self.client.normalize_ipv4_text(
                gateway
            )
        )

        if normalized_gateway:
            commands.append(
                (
                    "no ip route default "
                    f"{normalized_gateway}"
                )
            )

        last_message = ""

        for command in commands:
            success, message = (
                self.client.execute_parse_command(
                    command
                )
            )

            if success:
                return True, ""

            if self.route_missing_message(
                message
            ):
                return True, ""

            last_message = message

        return False, last_message

    def apply_interface_ipv4_settings(
        self,
        interface_id: str,
        ipv4_settings: dict[str, Any],
        current_gateway: str = "",
    ) -> tuple[bool, str]:
        """
        Переключает DHCP или задаёт статический IPv4.

        Динамический маршрут DHCP не удаляется вручную:
        команда `no ip address dhcp` сама удаляет полученные
        по DHCP адрес, маску и шлюз.
        """
        cli_name = self.client.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.client.quote_cli_argument(
            cli_name
        )

        current = self.get_configured_interface_ipv4(
            interface_id
        )
        current_uses_dhcp = bool(
            current.get("dhcp")
        )
        target_uses_dhcp = bool(
            ipv4_settings.get("dhcp")
        )

        if target_uses_dhcp:
            if not current_uses_dhcp:
                route_removed, route_message = (
                    self.remove_interface_default_route(
                        interface_id=interface_id,
                        gateway=current_gateway,
                    )
                )

                if not route_removed:
                    return (
                        False,
                        (
                            "Не удалось удалить прежний "
                            "статический маршрут по умолчанию: "
                            f"{route_message}"
                        ),
                    )

            command = (
                f"interface {interface_arg} "
                "ip address dhcp"
            )

            success, message = (
                self.client.execute_parse_command(
                    command
                )
            )

            if not success:
                return (
                    False,
                    (
                        "Не удалось включить DHCP: "
                        f"{message}"
                    ),
                )

            return True, ""

        address = self.client.normalize_ipv4_text(
            ipv4_settings.get("address")
        )
        gateway = self.client.normalize_ipv4_text(
            ipv4_settings.get("gateway")
        )

        try:
            prefix = int(
                ipv4_settings.get("prefix")
            )
        except (TypeError, ValueError):
            return (
                False,
                "Некорректная длина префикса IPv4.",
            )

        if (
            not address
            or not gateway
            or not 1 <= prefix <= 32
        ):
            return (
                False,
                "Некорректные ручные параметры IPv4.",
            )

        # При DHCP текущий маршрут динамический. Остановка
        # клиента сама удаляет его, поэтому отдельная команда
        # no ip route здесь не нужна.
        if current_uses_dhcp:
            success, message = (
                self.client.execute_parse_command(
                    (
                        f"interface {interface_arg} "
                        "no ip address dhcp"
                    )
                )
            )

            if not success:
                return (
                    False,
                    (
                        "Не удалось остановить DHCP: "
                        f"{message}"
                    ),
                )
        else:
            route_removed, route_message = (
                self.remove_interface_default_route(
                    interface_id=interface_id,
                    gateway=current_gateway,
                )
            )

            if not route_removed:
                return (
                    False,
                    (
                        "Не удалось удалить прежний "
                        "статический маршрут по умолчанию: "
                        f"{route_message}"
                    ),
                )

        commands = (
            (
                f"interface {interface_arg} "
                f"ip address {address}/{prefix}"
            ),
            (
                "ip route default "
                f"{gateway} {interface_arg}"
            ),
        )

        for command in commands:
            success, message = (
                self.client.execute_parse_command(
                    command
                )
            )

            if not success:
                return (
                    False,
                    (
                        "Не удалось применить IPv4: "
                        f"{message}"
                    ),
                )

        return True, ""

    def find_interface_default_gateway(
        self,
        interface_id: str,
    ) -> str:
        """
        Ищет активный шлюз выбранного интерфейса.
        """
        interface_names = {
            str(interface_id or "").strip().lower(),
            self.client.get_interface_cli_name(
                interface_id
            ).lower(),
        }

        route_data = self.client.get_rci_data(
            endpoint="show/ip/route",
            required=False,
        )

        result = ""

        def walk(value: Any) -> None:
            nonlocal result

            if result:
                return

            if isinstance(value, list):
                for item in value:
                    walk(item)
                return

            if not isinstance(value, dict):
                return

            destination = str(
                value.get("destination")
                or value.get("network")
                or value.get("prefix")
                or ""
            ).strip().lower()
            interface_name = str(
                value.get("interface")
                or value.get("ifname")
                or value.get("via")
                or ""
            ).strip().lower()
            gateway = self.client.normalize_ipv4_text(
                value.get("gateway")
                or value.get("next-hop")
            )

            if (
                destination
                in {
                    "default",
                    "0.0.0.0/0",
                    "0.0.0.0",
                }
                and interface_name
                in interface_names
                and gateway
            ):
                result = gateway
                return

            for nested in value.values():
                if isinstance(
                    nested,
                    (dict, list),
                ):
                    walk(nested)

        walk(route_data)

        if result:
            return result

        interface_data = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        item = (
            interface_data.get(interface_id)
            if isinstance(
                interface_data,
                dict,
            )
            else None
        )

        if isinstance(item, dict):
            return self.client.normalize_ipv4_text(
                self.client.find_first_value(
                    item,
                    (
                        "gateway",
                        "default-gateway",
                        "default_gateway",
                    ),
                )
            )

        return ""

    def set_interface_settings(
        self,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Изменяет описание и/или MTU интерфейса, сохраняет
        конфигурацию и проверяет результат.
        """
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером."

        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указано имя интерфейса."

        settings = dict(settings)
        wireless_settings = settings.pop(
            "wireless_isp",
            None,
        )
        wireless_changed = False
        wireless_message = ""

        if wireless_settings is not None:
            if not isinstance(wireless_settings, dict):
                return (
                    False,
                    "Параметры Wireless ISP переданы в неверном формате.",
                )

            wireless_success, wireless_message = (
                self.client.wireless_isp_service.update_wireless_isp_settings(
                    interface_id=name,
                    settings=dict(wireless_settings),
                )
            )

            if not wireless_success:
                return False, wireless_message

            wireless_changed = bool(wireless_settings)

            if not settings:
                return (
                    True,
                    wireless_message
                    or "Параметры Wireless ISP изменены.",
                )

        changes: dict[str, Any] = {}

        if "description" in settings:
            description = str(
                settings.get("description")
                or ""
            ).strip()

            if not description:
                return (
                    False,
                    "Пользовательское имя не может быть пустым.",
                )

            if len(description) > 64:
                return (
                    False,
                    "Пользовательское имя слишком длинное.",
                )

            changes["description"] = description

        if "mtu" in settings:
            raw_mtu = settings.get("mtu")

            if raw_mtu is None:
                return (
                    False,
                    "Пустое значение MTU пока не поддерживается.",
                )

            try:
                mtu = int(raw_mtu)
            except (TypeError, ValueError):
                return False, "MTU должен быть целым числом."

            if not 576 <= mtu <= 9000:
                return (
                    False,
                    "MTU должен быть от 576 до 9000.",
                )

            changes["mtu"] = mtu

        if "use_provider_dns" in settings:
            changes["use_provider_dns"] = bool(
                settings.get(
                    "use_provider_dns"
                )
            )

        if "manual_dns" in settings:
            raw_dns = settings.get("manual_dns")

            if raw_dns is None:
                raw_dns = []

            if not isinstance(raw_dns, list):
                return (
                    False,
                    "Ручные DNS должны быть переданы списком.",
                )

            manual_dns: list[str] = []

            for raw_value in raw_dns[:2]:
                value = str(raw_value or "").strip()

                if not value:
                    continue

                try:
                    address = ipaddress.ip_address(
                        value
                    )
                except ValueError:
                    return (
                        False,
                        f"Некорректный DNS-адрес: {value}",
                    )

                normalized = str(address)

                if normalized in manual_dns:
                    return (
                        False,
                        "DNS-серверы не должны совпадать.",
                    )

                manual_dns.append(normalized)

            changes["manual_dns"] = manual_dns

        if "ipv4" in settings:
            raw_ipv4 = settings.get("ipv4")

            if not isinstance(raw_ipv4, dict):
                return (
                    False,
                    "Параметры IPv4 переданы в неверном формате.",
                )

            use_dhcp = bool(
                raw_ipv4.get("dhcp")
            )

            if use_dhcp:
                changes["ipv4"] = {
                    "dhcp": True,
                }
            else:
                address = self.client.normalize_ipv4_text(
                    raw_ipv4.get("address")
                )
                gateway = self.client.normalize_ipv4_text(
                    raw_ipv4.get("gateway")
                )

                try:
                    prefix = int(
                        raw_ipv4.get("prefix")
                    )
                except (TypeError, ValueError):
                    return (
                        False,
                        "Длина префикса IPv4 должна быть числом.",
                    )

                if not address:
                    return (
                        False,
                        "Некорректный ручной IPv4-адрес.",
                    )

                if not gateway:
                    return (
                        False,
                        "Некорректный IPv4-адрес шлюза.",
                    )

                if not 1 <= prefix <= 32:
                    return (
                        False,
                        "Длина префикса должна быть от 1 до 32.",
                    )

                changes["ipv4"] = {
                    "dhcp": False,
                    "address": address,
                    "prefix": prefix,
                    "gateway": gateway,
                }

        if "dhcp_client" in settings:
            raw_dhcp_client = settings.get(
                "dhcp_client"
            )

            if not isinstance(
                raw_dhcp_client,
                dict,
            ):
                return (
                    False,
                    "Параметры DHCP-клиента переданы "
                    "в неверном формате.",
                )

            hostname = str(
                raw_dhcp_client.get("hostname")
                or ""
            ).strip()

            if not hostname:
                return (
                    False,
                    "Имя DHCP-клиента не может быть пустым.",
                )

            if len(hostname) > 63:
                return (
                    False,
                    "Имя DHCP-клиента слишком длинное.",
                )

            allowed = set(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789-."
            )

            if (
                any(
                    character not in allowed
                    for character in hostname
                )
                or hostname.startswith(("-", "."))
                or hostname.endswith(("-", "."))
            ):
                return (
                    False,
                    "Имя DHCP-клиента содержит "
                    "недопустимые символы.",
                )

            changes["dhcp_client"] = {
                "hostname": hostname,
                "routes": bool(
                    raw_dhcp_client.get(
                        "routes"
                    )
                ),
                "dns_routes": bool(
                    raw_dhcp_client.get(
                        "dns_routes"
                    )
                ),
            }

        if "ping_check" in settings:
            raw_ping_check = settings.get(
                "ping_check"
            )

            if not isinstance(
                raw_ping_check,
                dict,
            ):
                return (
                    False,
                    "Параметры Ping Check переданы "
                    "в неверном формате.",
                )

            mode = str(
                raw_ping_check.get("mode")
                or (
                    "icmp"
                    if raw_ping_check.get("enabled")
                    else "disabled"
                )
            ).strip().lower()

            if mode == "disabled":
                changes["ping_check"] = {
                    "mode": "disabled",
                }
            elif mode == "automatic":
                changes["ping_check"] = {
                    "mode": "automatic",
                }
            elif mode in {
                "icmp",
                "tls",
                "connect",
            }:
                host = str(
                    raw_ping_check.get("host")
                    or ""
                ).strip()

                if (
                    not host
                    or len(host) > 253
                    or any(
                        character.isspace()
                        for character in host
                    )
                    or "://" in host
                    or "/" in host
                ):
                    return (
                        False,
                        "Некорректный узел Ping Check.",
                    )

                try:
                    interval = int(
                        raw_ping_check.get(
                            "interval"
                        )
                    )
                    max_fails = int(
                        raw_ping_check.get(
                            "max_fails"
                        )
                    )
                except (TypeError, ValueError):
                    return (
                        False,
                        "Интервал и число ошибок Ping Check "
                        "должны быть целыми числами.",
                    )

                if not 3 <= interval <= 3600:
                    return (
                        False,
                        "Интервал Ping Check должен быть "
                        "от 3 до 3600 секунд.",
                    )

                if not 1 <= max_fails <= 10:
                    return (
                        False,
                        "Число неудачных попыток должно "
                        "быть от 1 до 10.",
                    )

                normalized = {
                    "mode": mode,
                    "host": host,
                    "interval": interval,
                    "max_fails": max_fails,
                }

                if mode in {
                    "tls",
                    "connect",
                }:
                    try:
                        port = int(
                            raw_ping_check.get(
                                "port"
                            )
                        )
                    except (TypeError, ValueError):
                        return (
                            False,
                            "TCP-порт Ping Check должен "
                            "быть целым числом.",
                        )

                    if not 1 <= port <= 65535:
                        return (
                            False,
                            "TCP-порт должен находиться "
                            "в диапазоне от 1 до 65535.",
                        )

                    normalized["port"] = port

                changes["ping_check"] = normalized
            else:
                return (
                    False,
                    "Неподдерживаемый режим Ping Check.",
                )

        if "mac" in settings:
            raw_mac = settings.get("mac")

            if not isinstance(raw_mac, dict):
                return (
                    False,
                    "Параметры MAC переданы "
                    "в неверном формате.",
                )

            mode = str(
                raw_mac.get("mode")
                or "factory"
            ).strip().lower()

            if mode == "factory":
                changes["mac"] = {
                    "mode": "factory",
                }
            elif mode == "manual":
                valid, normalized = (
                    self.client.validate_manual_mac(
                        raw_mac.get("address")
                    )
                )

                if not valid:
                    return (
                        False,
                        "Некорректный ручной MAC-адрес.",
                    )

                changes["mac"] = {
                    "mode": "manual",
                    "address": normalized,
                }
            else:
                return (
                    False,
                    "Неизвестный режим MAC-адреса.",
                )

        if not changes:
            if wireless_changed:
                return (
                    True,
                    wireless_message
                    or "Параметры Wireless ISP изменены.",
                )
            return False, "Нет изменённых параметров."

        interface_command: dict[str, Any] = {
            "name": name,
        }

        if "description" in changes:
            interface_command[
                "description"
            ] = changes["description"]

        ip_command: dict[str, Any] = {}

        if "mtu" in changes:
            ip_command["mtu"] = changes["mtu"]

        if "use_provider_dns" in changes:
            ip_command["name-servers"] = (
                changes["use_provider_dns"]
            )

        if "dhcp_client" in changes:
            dhcp_client = changes[
                "dhcp_client"
            ]
            ip_command["dhcp"] = {
                "client": {
                    "hostname": dhcp_client[
                        "hostname"
                    ],
                    "routes": dhcp_client[
                        "routes"
                    ],
                    "dns-routes": dhcp_client[
                        "dns_routes"
                    ],
                }
            }

        if ip_command:
            interface_command["ip"] = ip_command

        if "manual_dns" in changes:
            dns_success, dns_message = (
                self.replace_interface_dns_records(
                    interface_id=name,
                    addresses=changes["manual_dns"],
                )
            )

            if not dns_success:
                return False, dns_message

        if "ipv4" in changes:
            current_gateway = (
                self.find_interface_default_gateway(
                    name
                )
            )
            ipv4_success, ipv4_message = (
                self.apply_interface_ipv4_settings(
                    interface_id=name,
                    ipv4_settings=changes["ipv4"],
                    current_gateway=current_gateway,
                )
            )

            if not ipv4_success:
                return False, ipv4_message

        commands: list[dict[str, Any]] = []

        if len(interface_command) > 1:
            commands.append(
                {
                    "interface": interface_command,
                }
            )

        if "mac" in changes:
            commands.append(
                {
                    "parse": (
                        self.client.build_interface_mac_command(
                            interface_id=name,
                            mac_settings=changes["mac"],
                        )
                    )
                }
            )

        if "ping_check" in changes:
            current_config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            current_item = (
                current_config.get(name)
                if isinstance(
                    current_config,
                    dict,
                )
                else None
            )
            current_profile = ""

            if isinstance(current_item, dict):
                assignment = current_item.get(
                    "ping-check"
                )

                if isinstance(
                    assignment,
                    dict,
                ):
                    current_profile = str(
                        assignment.get("profile")
                        or ""
                    ).strip()

            (
                ping_commands,
                expected_profile,
            ) = self.client.ping_check_service.build_ping_check_commands(
                interface_id=name,
                settings=changes["ping_check"],
                current_profile=current_profile,
            )

            changes[
                "ping_check_profile"
            ] = expected_profile

            for command in ping_commands:
                commands.append(
                    {
                        "parse": command,
                    }
                )

        commands.append(
            {
                "system": {
                    "configuration": {
                        "save": {},
                    }
                }
            }
        )

        applied, apply_message = self.client.execute_rci_batch(
            commands,
            operation_name="Изменение параметров подключения",
        )
        if not applied:
            return False, apply_message

        verified, message = (
            self.verify_interface_settings(
                interface_id=name,
                expected=changes,
            )
        )

        if not verified:
            return False, message

        return (
            True,
            (
                "Параметры Wireless ISP и подключения изменены "
                "и сохранены."
                if wireless_changed
                else "Параметры подключения изменены и сохранены."
            ),
        )

    def verify_interface_settings(
        self,
        interface_id: str,
        expected: dict[str, Any],
    ) -> tuple[bool, str]:
        last_details = "нет данных"

        slow_reconfiguration = (
            "ipv4" in expected
            or "mac" in expected
            or "ping_check" in expected
        )
        attempts = (
            10
            if slow_reconfiguration
            else 5
        )
        delay = (
            0.75
            if slow_reconfiguration
            else 0.5
        )

        for _ in range(attempts):
            time.sleep(delay)

            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            item = (
                config.get(interface_id)
                if isinstance(config, dict)
                else None
            )

            if not isinstance(item, dict):
                last_details = "интерфейс не найден"
                continue

            mismatches: list[str] = []

            if "description" in expected:
                actual_description = str(
                    item.get("description")
                    or ""
                ).strip()

                if (
                    actual_description
                    != expected["description"]
                ):
                    mismatches.append(
                        "имя не подтверждено"
                    )

            ip_config = item.get("ip")

            if not isinstance(ip_config, dict):
                ip_config = {}

            if "mtu" in expected:
                actual_mtu: int | None = None

                try:
                    actual_mtu = int(
                        ip_config.get("mtu")
                    )
                except (TypeError, ValueError):
                    actual_mtu = None

                if actual_mtu != expected["mtu"]:
                    mismatches.append(
                        (
                            "MTU: ожидалось "
                            f"{expected['mtu']}, "
                            f"получено {actual_mtu}"
                        )
                    )

            if "use_provider_dns" in expected:
                actual_provider_dns = self.client.truthy(
                    ip_config.get(
                        "name-servers",
                        False,
                    )
                )

                if (
                    actual_provider_dns
                    != expected["use_provider_dns"]
                ):
                    mismatches.append(
                        (
                            "DNS провайдера: ожидалось "
                            f"{expected['use_provider_dns']}, "
                            f"получено {actual_provider_dns}"
                        )
                    )

            if "manual_dns" in expected:
                actual_dns: list[str] = []
                dns_items = ip_config.get(
                    "name-server"
                )

                if isinstance(dns_items, dict):
                    dns_items = [dns_items]

                if isinstance(dns_items, list):
                    for dns_item in dns_items:
                        if isinstance(dns_item, dict):
                            raw_value = (
                                dns_item.get("name-server")
                                or dns_item.get("address")
                            )
                        else:
                            raw_value = dns_item

                        value = str(
                            raw_value or ""
                        ).strip()

                        if not value:
                            continue

                        try:
                            normalized = str(
                                ipaddress.ip_address(value)
                            )
                        except ValueError:
                            normalized = value

                        if normalized not in actual_dns:
                            actual_dns.append(normalized)

                if actual_dns != expected["manual_dns"]:
                    mismatches.append(
                        (
                            "Ручные DNS: ожидалось "
                            f"{expected['manual_dns']}, "
                            f"получено {actual_dns}"
                        )
                    )

            if "mac" in expected:
                (
                    actual_mac_supported,
                    actual_mac_mode,
                    actual_manual_mac,
                ) = self.client.extract_configured_mac_settings(
                    item
                )
                expected_mac = expected["mac"]
                expected_mode = expected_mac[
                    "mode"
                ]

                if not actual_mac_supported:
                    mismatches.append(
                        "Настройка MAC не подтверждена"
                    )
                elif actual_mac_mode != expected_mode:
                    mismatches.append(
                        (
                            "Режим MAC: ожидалось "
                            f"{expected_mode}, "
                            f"получено {actual_mac_mode}"
                        )
                    )
                elif (
                    expected_mode == "manual"
                    and actual_manual_mac
                    != expected_mac["address"]
                ):
                    mismatches.append(
                        (
                            "MAC: ожидалось "
                            f"{expected_mac['address']}, "
                            f"получено "
                            f"{actual_manual_mac or '—'}"
                        )
                    )

            if "ping_check" in expected:
                expected_ping = expected[
                    "ping_check"
                ]
                expected_mode = str(
                    expected_ping.get("mode")
                    or "disabled"
                )
                assignment = item.get(
                    "ping-check"
                )
                actual_profile = ""

                if isinstance(assignment, dict):
                    actual_profile = str(
                        assignment.get("profile")
                        or ""
                    ).strip()
                elif isinstance(assignment, str):
                    actual_profile = assignment.strip()

                if expected_mode == "disabled":
                    if actual_profile:
                        mismatches.append(
                            (
                                "Ping Check: ожидалось "
                                "выключено, но назначен профиль "
                                f"{actual_profile}"
                            )
                        )
                else:
                    expected_profile = str(
                        expected.get(
                            "ping_check_profile"
                        )
                        or self.client.ping_check_profile_name(
                            interface_id
                        )
                    )

                    if actual_profile != expected_profile:
                        mismatches.append(
                            (
                                "Профиль Ping Check: ожидалось "
                                f"{expected_profile}, "
                                f"получено {actual_profile or '—'}"
                            )
                        )
                    else:
                        profile_config = self.client.get_rci_data(
                            endpoint="show/sc/ping-check",
                            required=False,
                        )
                        profile_status = self.client.get_rci_data(
                            endpoint="show/ping-check",
                            required=False,
                        )
                        profile_details = (
                            self.client.find_ping_check_profile(
                                profile_config,
                                expected_profile,
                            )
                        )

                        if not profile_details:
                            profile_details = (
                                self.client.find_ping_check_profile(
                                    profile_status,
                                    expected_profile,
                                )
                            )

                        if not profile_details:
                            mismatches.append(
                                "Профиль Ping Check не найден"
                            )
                        elif expected_mode == "automatic":
                            if expected_profile == "default":
                                # The built-in profile is shown
                                # differently in show/ping-check
                                # and has no editable sc/profile
                                # object. The interface assignment
                                # verified above is authoritative.
                                pass
                            elif not self.client.is_automatic_ping_check_profile(
                                profile_details
                            ):
                                mismatches.append(
                                    (
                                        "Автоматический Ping Check "
                                        "не подтверждён"
                                    )
                                )
                        else:
                            actual_mode = str(
                                self.client.ping_check_scalar(
                                    profile_details,
                                    ("mode",),
                                    "icmp",
                                )
                                or "icmp"
                            ).strip().lower()
                            expected_manual_mode = str(
                                expected_ping.get("mode")
                                or "icmp"
                            )

                            if (
                                actual_mode
                                != expected_manual_mode
                            ):
                                mismatches.append(
                                    (
                                        "Режим Ping Check: "
                                        f"ожидалось "
                                        f"{expected_manual_mode}, "
                                        f"получено {actual_mode}"
                                    )
                                )

                            actual_hosts = {
                                value.rstrip(".").lower()
                                for value in self.client.ping_check_hosts(
                                    profile_details
                                )
                            }
                            expected_host = str(
                                expected_ping["host"]
                            ).rstrip(".").lower()

                            if expected_host not in actual_hosts:
                                mismatches.append(
                                    (
                                        "Узел Ping Check: ожидалось "
                                        f"{expected_ping['host']}, "
                                        "получено "
                                        f"{sorted(actual_hosts) or '—'}"
                                    )
                                )

                            if expected_manual_mode in {
                                "tls",
                                "connect",
                            }:
                                try:
                                    actual_port = int(
                                        self.client.ping_check_scalar(
                                            profile_details,
                                            ("port",),
                                            -1,
                                        )
                                    )
                                except (
                                    TypeError,
                                    ValueError,
                                ):
                                    actual_port = -1

                                if (
                                    actual_port
                                    != expected_ping["port"]
                                ):
                                    mismatches.append(
                                        (
                                            "TCP-порт Ping Check: "
                                            f"ожидалось "
                                            f"{expected_ping['port']}, "
                                            f"получено {actual_port}"
                                        )
                                    )

                            try:
                                actual_interval = int(
                                    self.client.ping_check_scalar(
                                        profile_details,
                                        (
                                            "update-interval",
                                            "update_interval",
                                            "interval",
                                        ),
                                        -1,
                                    )
                                )
                            except (
                                TypeError,
                                ValueError,
                            ):
                                actual_interval = -1

                            if (
                                actual_interval
                                != expected_ping["interval"]
                            ):
                                mismatches.append(
                                    (
                                        "Интервал Ping Check: "
                                        f"ожидалось "
                                        f"{expected_ping['interval']}, "
                                        f"получено {actual_interval}"
                                    )
                                )

                            try:
                                actual_max_fails = int(
                                    self.client.ping_check_scalar(
                                        profile_details,
                                        (
                                            "max-fails",
                                            "max_fails",
                                            "fail-count",
                                        ),
                                        -1,
                                    )
                                )
                            except (
                                TypeError,
                                ValueError,
                            ):
                                actual_max_fails = -1

                            if (
                                actual_max_fails
                                != expected_ping["max_fails"]
                            ):
                                mismatches.append(
                                    (
                                        "Ошибки Ping Check: "
                                        f"ожидалось "
                                        f"{expected_ping['max_fails']}, "
                                        f"получено "
                                        f"{actual_max_fails}"
                                    )
                                )

            if "dhcp_client" in expected:
                expected_dhcp_client = expected[
                    "dhcp_client"
                ]
                dhcp_config = ip_config.get(
                    "dhcp"
                )
                actual_dhcp_client: dict[str, Any] = {}

                if isinstance(dhcp_config, dict):
                    candidate = dhcp_config.get(
                        "client"
                    )

                    if isinstance(candidate, dict):
                        actual_dhcp_client = candidate

                actual_hostname = str(
                    actual_dhcp_client.get(
                        "hostname"
                    )
                    or ""
                ).strip()

                if (
                    actual_hostname
                    != expected_dhcp_client["hostname"]
                ):
                    mismatches.append(
                        (
                            "Имя DHCP-клиента: ожидалось "
                            f"{expected_dhcp_client['hostname']}, "
                            f"получено {actual_hostname or '—'}"
                        )
                    )

                actual_routes = self.client.truthy(
                    actual_dhcp_client.get(
                        "routes",
                        False,
                    )
                )

                if (
                    actual_routes
                    != expected_dhcp_client["routes"]
                ):
                    mismatches.append(
                        (
                            "Маршруты DHCP: ожидалось "
                            f"{expected_dhcp_client['routes']}, "
                            f"получено {actual_routes}"
                        )
                    )

                actual_dns_routes = self.client.truthy(
                    actual_dhcp_client.get(
                        "dns-routes",
                        False,
                    )
                )

                if (
                    actual_dns_routes
                    != expected_dhcp_client[
                        "dns_routes"
                    ]
                ):
                    mismatches.append(
                        (
                            "Маршруты DNS: ожидалось "
                            f"{expected_dhcp_client['dns_routes']}, "
                            f"получено {actual_dns_routes}"
                        )
                    )

            if "ipv4" in expected:
                expected_ipv4 = expected["ipv4"]
                (
                    actual_dhcp,
                    actual_address,
                    actual_prefix,
                ) = self.client.extract_configured_ipv4(
                    ip_config.get("address")
                )

                if actual_dhcp != bool(
                    expected_ipv4.get("dhcp")
                ):
                    mismatches.append(
                        (
                            "Режим IPv4: ожидалось "
                            + (
                                "DHCP"
                                if expected_ipv4.get("dhcp")
                                else "вручную"
                            )
                        )
                    )
                elif not actual_dhcp:
                    if (
                        actual_address
                        != expected_ipv4.get("address")
                    ):
                        mismatches.append(
                            (
                                "IPv4: ожидалось "
                                f"{expected_ipv4.get('address')}, "
                                f"получено {actual_address or '—'}"
                            )
                        )

                    if (
                        actual_prefix
                        != expected_ipv4.get("prefix")
                    ):
                        mismatches.append(
                            (
                                "Префикс: ожидалось "
                                f"{expected_ipv4.get('prefix')}, "
                                f"получено {actual_prefix}"
                            )
                        )

                    actual_gateway = (
                        self.find_interface_default_gateway(
                            interface_id
                        )
                    )

                    if (
                        actual_gateway
                        and actual_gateway
                        != expected_ipv4.get("gateway")
                    ):
                        mismatches.append(
                            (
                                "Шлюз: ожидалось "
                                f"{expected_ipv4.get('gateway')}, "
                                f"получено {actual_gateway}"
                            )
                        )

            if not mismatches:
                return True, "Настройки подтверждены роутером."

            last_details = "; ".join(mismatches)

        return (
            False,
            (
                "Роутер принял запрос, но проверка "
                f"настроек не пройдена: {last_details}"
            ),
        )

