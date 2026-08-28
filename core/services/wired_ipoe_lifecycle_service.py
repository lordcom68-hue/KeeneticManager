from __future__ import annotations

import time
from typing import Any


class WiredIpoeLifecycleService:
    """Verification and rollback logic for wired IPoE lifecycle operations."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def verify_wired_ipoe_skeleton(
        self,
        *,
        interface_id: str,
        port_id: str,
        vlan_id: int,
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(8):
            time.sleep(0.6)

            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            interface_item = config.get(
                interface_id
            )
            port_item = config.get(
                port_id
            )
            mismatches = []

            if not isinstance(
                interface_item,
                dict,
            ):
                mismatches.append(
                    "новый интерфейс не найден"
                )
            elif self.client.truthy(
                interface_item.get("up")
            ):
                mismatches.append(
                    "интерфейс включился раньше проверки"
                )

            if not isinstance(port_item, dict):
                mismatches.append(
                    "конфигурация Ethernet-порта не найдена"
                )
            else:
                actual_vid = (
                    self.client.extract_port_access_vid(
                        port_item
                    )
                )

                if actual_vid != vlan_id:
                    mismatches.append(
                        (
                            "VLAN порта: ожидалось "
                            f"{vlan_id}, получено {actual_vid}"
                        )
                    )

                roles = self.client.extract_port_roles(
                    port_item
                )

                has_role = any(
                    role.get("role") == "inet"
                    and role.get("ifor")
                    == interface_id
                    for role in roles
                )

                if not has_role:
                    mismatches.append(
                        "роль Provider не подтверждена"
                    )

            if not mismatches:
                return (
                    True,
                    "Каркас подключения подтверждён.",
                )

        return (
            False,
            (
                "Проверка каркаса подключения не пройдена: "
                + "; ".join(mismatches)
            ),
        )

    def verify_created_wired_ipoe(
        self,
        *,
        interface_id: str,
        port_id: str,
        vlan_id: int,
        expected_order: int,
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(10):
            time.sleep(0.75)

            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            item = config.get(interface_id)
            port_item = config.get(port_id)
            mismatches = []

            if not isinstance(item, dict):
                mismatches.append(
                    "интерфейс не найден"
                )
            else:
                if not self.client.truthy(
                    item.get("up")
                ):
                    mismatches.append(
                        "интерфейс не включён"
                    )

                global_config = (
                    item.get("ip", {})
                    .get("global", {})
                )

                try:
                    actual_order = int(
                        global_config.get("order")
                    )
                except (
                    TypeError,
                    ValueError,
                    AttributeError,
                ):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        (
                            "приоритет: ожидалось "
                            f"{expected_order + 1}, получено "
                            f"{actual_order}"
                        )
                    )

            if not isinstance(port_item, dict):
                mismatches.append(
                    "Ethernet-порт не найден"
                )
            elif (
                self.client.extract_port_access_vid(
                    port_item
                )
                != vlan_id
            ):
                mismatches.append(
                    "назначение Ethernet-порта изменилось"
                )

            if not mismatches:
                return (
                    True,
                    "Созданное подключение подтверждено.",
                )

        return (
            False,
            (
                "Роутер принял команды, но итоговая "
                "проверка не пройдена: "
                + "; ".join(mismatches)
            ),
        )

    def rollback_wired_ipoe_creation(
        self,
        *,
        interface_id: str,
        port_id: str,
        old_access_vid: int | None,
        old_switch_mode: str,
        old_roles: list[dict[str, str]],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        errors: list[str] = []
        port_arg = self.client.quote_cli_argument(
            port_id
        )
        interface_arg = self.client.quote_cli_argument(
            interface_id
        )
        manager_profile = self.client.ping_check_profile_name(
            interface_id
        )
        profile_config = self.client.get_rci_data(
            endpoint="show/sc/ping-check",
            required=False,
        )
        manager_profile_exists = bool(
            self.client.find_ping_check_profile(
                profile_config,
                manager_profile,
            )
        )

        commands: list[str] = [
            f"interface {port_arg} no role",
        ]

        mode = (
            old_switch_mode
            if old_switch_mode
            in {"access", "trunk"}
            else "access"
        )
        commands.append(
            (
                f"interface {port_arg} "
                f"switchport mode {mode}"
            )
        )

        if old_access_vid is None:
            commands.append(
                (
                    f"interface {port_arg} "
                    "no switchport access vlan"
                )
            )
        else:
            commands.append(
                (
                    f"interface {port_arg} "
                    "switchport access vlan "
                    f"{old_access_vid}"
                )
            )

        for role in old_roles:
            role_name = self.client.quote_cli_argument(
                role.get("role", "")
            )
            ifor = str(
                role.get("ifor")
                or ""
            ).strip()

            if not role_name:
                continue

            command = (
                f"interface {port_arg} "
                f"role {role_name}"
            )

            if ifor:
                command += (
                    " for "
                    + self.client.quote_cli_argument(ifor)
                )

            commands.append(command)

        for command in commands:
            success, message = (
                self.client.execute_parse_command(command)
            )

            if not success:
                errors.append(
                    f"{command}: {message}"
                )

        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if (
            isinstance(current_config, dict)
            and interface_id in current_config
        ):
            if manager_profile_exists:
                detach_success, detach_message = (
                    self.client.execute_parse_command(
                        (
                            f"interface {interface_arg} "
                            "no ping-check profile"
                        )
                    )
                )

                if not detach_success:
                    errors.append(
                        (
                            "Не удалось отсоединить профиль "
                            f"Ping Check: {detach_message}"
                        )
                    )

            success, message = (
                self.client.execute_parse_command(
                    f"no interface {interface_arg}"
                )
            )

            if not success:
                errors.append(
                    (
                        "Не удалось удалить незавершённый "
                        f"интерфейс: {message}"
                    )
                )

        if manager_profile_exists:
            delete_profile_success, delete_profile_message = (
                self.client.execute_parse_command(
                    (
                        "no ping-check profile "
                        + self.client.quote_cli_argument(
                            manager_profile
                        )
                    )
                )
            )

            if not delete_profile_success:
                errors.append(
                    (
                        "Не удалось удалить временный профиль "
                        f"Ping Check: {delete_profile_message}"
                    )
                )

        save_success, save_message = (
            self.client.execute_parse_command(
                "system configuration save"
            )
        )

        if not save_success:
            errors.append(
                (
                    "Не удалось сохранить откат: "
                    f"{save_message}"
                )
            )

        if old_priorities:
            priority_success, priority_message = (
                self.client.set_internet_priorities(
                    old_priorities
                )
            )

            if not priority_success:
                errors.append(
                    (
                        "Не удалось восстановить приоритеты: "
                        f"{priority_message}"
                    )
                )

        if errors:
            return (
                False,
                "; ".join(errors),
            )

        return (
            True,
            "Исходная конфигурация порта восстановлена.",
        )

    def verify_wired_ipoe_delete_preparation(
        self,
        *,
        interface_id: str,
        port_id: str,
        home_vlan_id: int,
        manager_profile_detached: bool,
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(8):
            time.sleep(0.6)
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            interface_item = config.get(interface_id)
            port_item = config.get(port_id)
            mismatches = []

            if not isinstance(interface_item, dict):
                mismatches.append(
                    "подключение исчезло до этапа удаления"
                )
            else:
                if self.client.truthy(interface_item.get("up")):
                    mismatches.append(
                        "подключение не выключено"
                    )

                if manager_profile_detached:
                    assignment = interface_item.get(
                        "ping-check"
                    )

                    if assignment:
                        mismatches.append(
                            "профиль Ping Check не отсоединён"
                        )

            if not isinstance(port_item, dict):
                mismatches.append(
                    "конфигурация LAN-порта не найдена"
                )
            else:
                actual_vid = self.client.extract_port_access_vid(
                    port_item
                )

                if actual_vid != home_vlan_id:
                    mismatches.append(
                        (
                            "LAN-порт не возвращён в домашний "
                            f"VLAN {home_vlan_id}"
                        )
                    )

                roles = self.client.extract_port_roles(port_item)

                if any(
                    str(role.get("role") or "")
                    .strip()
                    .lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == interface_id
                    for role in roles
                ):
                    mismatches.append(
                        "с LAN-порта не снята роль Provider"
                    )

            if not mismatches:
                return (
                    True,
                    "LAN-порт подготовлен к удалению подключения.",
                )

        return (
            False,
            (
                "Подготовка к удалению не подтверждена: "
                + "; ".join(mismatches)
            ),
        )

    def rollback_wired_ipoe_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        errors: list[str] = []
        interface_id = str(context["interface_id"])
        port_id = str(context["port_id"])
        interface_arg = self.client.quote_cli_argument(
            interface_id
        )
        port_arg = self.client.quote_cli_argument(port_id)

        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not (
            isinstance(current_config, dict)
            and interface_id in current_config
        ):
            return (
                False,
                (
                    "Подключение уже удалено; автоматическое "
                    "восстановление его параметров невозможно."
                ),
            )

        commands: list[str] = [
            f"interface {port_arg} no role",
            (
                f"interface {port_arg} switchport mode "
                f"{context['old_switch_mode']}"
            ),
            (
                f"interface {port_arg} switchport access vlan "
                f"{context['old_access_vid']}"
            ),
        ]

        for role in context.get("old_roles", []):
            role_name = str(
                role.get("role")
                or ""
            ).strip()

            if not role_name:
                continue

            command = (
                f"interface {port_arg} role "
                + self.client.quote_cli_argument(role_name)
            )
            ifor = str(
                role.get("ifor")
                or ""
            ).strip()

            if ifor:
                command += (
                    " for "
                    + self.client.quote_cli_argument(ifor)
                )

            commands.append(command)

        ping_profile = str(
            context.get("ping_profile")
            or ""
        ).strip()

        if ping_profile.startswith("_KM_"):
            commands.append(
                (
                    f"interface {interface_arg} "
                    "ping-check profile "
                    + self.client.quote_cli_argument(
                        ping_profile
                    )
                )
            )

        commands.append(
            (
                f"interface {interface_arg} up"
                if context.get("was_up")
                else f"interface {interface_arg} no up"
            )
        )
        commands.append("system configuration save")

        for command in commands:
            success, message = self.client.execute_parse_command(
                command
            )

            if not success:
                errors.append(
                    f"{command}: {message}"
                )

        if old_priorities:
            priority_success, priority_message = (
                self.client.set_internet_priorities(
                    old_priorities
                )
            )

            if not priority_success:
                errors.append(
                    (
                        "Не удалось восстановить приоритеты: "
                        f"{priority_message}"
                    )
                )

        if errors:
            return False, "; ".join(errors)

        return (
            True,
            "Исходная конфигурация подключения восстановлена.",
        )

    def verify_deleted_wired_ipoe(
        self,
        *,
        interface_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(10):
            time.sleep(0.65)
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            mismatches = []

            if interface_id in config:
                mismatches.append(
                    "интерфейс всё ещё присутствует"
                )

            port_item = config.get(port_id)

            if not isinstance(port_item, dict):
                mismatches.append(
                    "LAN-порт не найден"
                )
            else:
                actual_vid = self.client.extract_port_access_vid(
                    port_item
                )

                if actual_vid != home_vlan_id:
                    mismatches.append(
                        (
                            "VLAN LAN-порта: ожидалось "
                            f"{home_vlan_id}, получено {actual_vid}"
                        )
                    )

                if any(
                    str(role.get("role") or "")
                    .strip()
                    .lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == interface_id
                    for role in self.client.extract_port_roles(
                        port_item
                    )
                ):
                    mismatches.append(
                        "на LAN-порту осталась роль Provider"
                    )

            for remaining_id, expected_order in (
                remaining_priorities.items()
            ):
                item = config.get(remaining_id)

                if not isinstance(item, dict):
                    mismatches.append(
                        f"{remaining_id}: интерфейс не найден"
                    )
                    continue

                try:
                    actual_order = int(
                        item.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (
                    TypeError,
                    ValueError,
                    AttributeError,
                ):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        (
                            f"{remaining_id}: ожидался порядок "
                            f"{expected_order}, получен "
                            f"{actual_order}"
                        )
                    )

            if not mismatches:
                return (
                    True,
                    "Удаление подтверждено роутером.",
                )

        return (
            False,
            (
                "Итоговая проверка удаления не пройдена: "
                + "; ".join(mismatches)
            ),
        )

