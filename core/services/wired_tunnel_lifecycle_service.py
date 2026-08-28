from __future__ import annotations

import time
from typing import Any


class WiredTunnelLifecycleService:
    """Verification and rollback logic shared by PPPoE, PPTP and L2TP."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def rollback_wired_pppoe_creation(
        self,
        *,
        pppoe_id: str,
        transport_id: str,
        port_id: str,
        old_access_vid: int | None,
        old_switch_mode: str,
        old_roles: list[dict[str, str]],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        errors: list[str] = []
        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )
        pppoe_arg = self.client.quote_cli_argument(pppoe_id)
        profile_name = self.client.ping_check_profile_name(pppoe_id)

        if isinstance(config, dict) and pppoe_id in config:
            item = config.get(pppoe_id)
            assignment = (
                item.get("ping-check")
                if isinstance(item, dict)
                else None
            )
            assigned_profile = ""

            if isinstance(assignment, dict):
                assigned_profile = str(
                    assignment.get("profile") or ""
                ).strip()
            elif isinstance(assignment, str):
                assigned_profile = assignment.strip()

            if assigned_profile:
                success, message = self.client.execute_parse_command(
                    f"interface {pppoe_arg} no ping-check profile"
                )

                if not success:
                    errors.append(
                        "Не удалось отсоединить Ping Check PPPoE: "
                        + message
                    )

            success, message = self.client.execute_parse_command(
                f"no interface {pppoe_arg}"
            )

            if not success:
                errors.append(
                    "Не удалось удалить незавершённый PPPoE: "
                    + message
                )

            if assigned_profile.startswith("_KM_"):
                success, message = self.client.execute_parse_command(
                    "no ping-check profile "
                    + self.client.quote_cli_argument(assigned_profile)
                )

                if not success:
                    errors.append(
                        "Не удалось удалить профиль Ping Check: "
                        + message
                    )
        else:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.find_ping_check_profile(
                profile_config,
                profile_name,
            ):
                success, message = self.client.execute_parse_command(
                    "no ping-check profile "
                    + self.client.quote_cli_argument(profile_name)
                )

                if not success:
                    errors.append(
                        "Не удалось удалить профиль Ping Check: "
                        + message
                    )

        save_success, save_message = self.client.execute_parse_command(
            "system configuration save"
        )

        if not save_success:
            errors.append(
                "Не удалось сохранить удаление PPPoE: "
                + save_message
            )

        transport_success, transport_message = (
            self.client.rollback_wired_ipoe_creation(
                interface_id=transport_id,
                port_id=port_id,
                old_access_vid=old_access_vid,
                old_switch_mode=old_switch_mode,
                old_roles=old_roles,
                old_priorities=old_priorities,
            )
        )

        if not transport_success:
            errors.append(transport_message)

        if errors:
            return False, "; ".join(errors)

        return (
            True,
            "PPPoE и транспорт удалены; исходный LAN-порт восстановлен.",
        )

    def rollback_wired_pptp_creation(
        self,
        *,
        pptp_id: str,
        transport_id: str,
        port_id: str,
        old_access_vid: int | None,
        old_switch_mode: str,
        old_roles: list[dict[str, str]],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.rollback_wired_pppoe_creation(
            pppoe_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
        )
        return success, message.replace("PPPoE", "PPTP")

    def rollback_wired_l2tp_creation(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        old_access_vid: int | None,
        old_switch_mode: str,
        old_roles: list[dict[str, str]],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.rollback_wired_pppoe_creation(
            pppoe_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
        )
        return success, message.replace("PPPoE", "L2TP")

    def verify_created_wired_pppoe(
        self,
        *,
        pppoe_id: str,
        transport_id: str,
        port_id: str,
        vlan_id: int,
        expected_order: int,
        expected_identity: str,
        expected_service: str,
        expected_mtu: int,
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
            pppoe_item = config.get(pppoe_id)
            transport_item = config.get(transport_id)
            port_item = config.get(port_id)

            if not isinstance(pppoe_item, dict):
                mismatches.append("PPPoE-интерфейс не найден")
            else:
                connect = pppoe_item.get("connect")
                via = (
                    str(connect.get("via") or "").strip()
                    if isinstance(connect, dict)
                    else ""
                )

                if via != transport_id:
                    mismatches.append(
                        "неверный транспорт PPPoE"
                    )

                authentication = pppoe_item.get("authentication")
                identity = (
                    str(authentication.get("identity") or "").strip()
                    if isinstance(authentication, dict)
                    else ""
                )

                if identity != expected_identity:
                    mismatches.append(
                        "имя пользователя PPPoE не сохранено"
                    )

                if not (
                    isinstance(authentication, dict)
                    and authentication.get("password") is not None
                ):
                    mismatches.append("пароль PPPoE не сохранён")

                pppoe_config = pppoe_item.get("pppoe")
                service = (
                    str(pppoe_config.get("service") or "").strip()
                    if isinstance(pppoe_config, dict)
                    else ""
                )

                if service != expected_service:
                    mismatches.append(
                        "имя сервиса PPPoE не совпадает"
                    )

                try:
                    actual_mtu = int(
                        pppoe_item.get("ip", {}).get("mtu")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_mtu = None

                if actual_mtu != expected_mtu:
                    mismatches.append(
                        f"MTU PPPoE: ожидалось {expected_mtu}, "
                        f"получено {actual_mtu}"
                    )

                try:
                    actual_order = int(
                        pppoe_item.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        f"приоритет PPPoE: ожидалось "
                        f"{expected_order + 1}, получено "
                        f"{actual_order}"
                    )

                if not self.client.truthy(pppoe_item.get("up")):
                    mismatches.append("PPPoE-интерфейс выключен")

            if not isinstance(transport_item, dict):
                mismatches.append("транспорт IPoE не найден")
            else:
                ip_config = transport_item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    mismatches.append(
                        "транспорт ошибочно назначен интернет-каналом"
                    )

            if not isinstance(port_item, dict):
                mismatches.append("Ethernet-порт не найден")
            else:
                if self.client.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.client.extract_port_roles(port_item)

                if not any(
                    str(role.get("role") or "").lower() == "inet"
                    and str(role.get("ifor") or "") == transport_id
                    for role in roles
                ):
                    mismatches.append(
                        "роль Provider не привязана к транспорту"
                    )

            if not mismatches:
                return True, "Созданное PPPoE подтверждено."

        return (
            False,
            "Итоговая проверка PPPoE не пройдена: "
            + "; ".join(mismatches),
        )

    def verify_created_wired_pptp(
        self,
        *,
        pptp_id: str,
        transport_id: str,
        port_id: str,
        vlan_id: int,
        expected_order: int,
        expected_identity: str,
        expected_server: str,
        expected_mtu: int,
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
            pptp_item = config.get(pptp_id)
            transport_item = config.get(transport_id)
            port_item = config.get(port_id)

            if not isinstance(pptp_item, dict):
                mismatches.append("PPTP-интерфейс не найден")
            else:
                connect = pptp_item.get("connect")
                via = (
                    str(connect.get("via") or "").strip()
                    if isinstance(connect, dict)
                    else ""
                )

                if via != transport_id:
                    mismatches.append(
                        "неверный транспорт PPTP"
                    )

                authentication = pptp_item.get("authentication")
                identity = (
                    str(authentication.get("identity") or "").strip()
                    if isinstance(authentication, dict)
                    else ""
                )

                if identity != expected_identity:
                    mismatches.append(
                        "имя пользователя PPTP не сохранено"
                    )

                if not (
                    isinstance(authentication, dict)
                    and authentication.get("password") is not None
                ):
                    mismatches.append("пароль PPTP не сохранён")

                actual_server = str(
                    pptp_item.get("peer") or ""
                ).strip()

                if actual_server != expected_server:
                    mismatches.append(
                        "адрес PPTP-сервера не совпадает"
                    )

                try:
                    actual_mtu = int(
                        pptp_item.get("ip", {}).get("mtu")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_mtu = None

                if actual_mtu != expected_mtu:
                    mismatches.append(
                        f"MTU PPTP: ожидалось {expected_mtu}, "
                        f"получено {actual_mtu}"
                    )

                try:
                    actual_order = int(
                        pptp_item.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        f"приоритет PPTP: ожидалось "
                        f"{expected_order + 1}, получено "
                        f"{actual_order}"
                    )

                if not self.client.truthy(pptp_item.get("up")):
                    mismatches.append("PPTP-интерфейс выключен")

            if not isinstance(transport_item, dict):
                mismatches.append("транспорт IPoE не найден")
            else:
                ip_config = transport_item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    mismatches.append(
                        "транспорт ошибочно назначен интернет-каналом"
                    )

            if not isinstance(port_item, dict):
                mismatches.append("Ethernet-порт не найден")
            else:
                if self.client.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.client.extract_port_roles(port_item)

                if not any(
                    str(role.get("role") or "").lower() == "inet"
                    and str(role.get("ifor") or "") == transport_id
                    for role in roles
                ):
                    mismatches.append(
                        "роль Provider не привязана к транспорту"
                    )

            if not mismatches:
                return True, "Созданное PPTP подтверждено."

        return (
            False,
            "Итоговая проверка PPTP не пройдена: "
            + "; ".join(mismatches),
        )

    def verify_created_wired_l2tp(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        vlan_id: int,
        expected_order: int,
        expected_identity: str,
        expected_server: str,
        expected_mtu: int,
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
            l2tp_item = config.get(l2tp_id)
            transport_item = config.get(transport_id)
            port_item = config.get(port_id)

            if not isinstance(l2tp_item, dict):
                mismatches.append("L2TP-интерфейс не найден")
            else:
                connect = l2tp_item.get("connect")
                via = (
                    str(connect.get("via") or "").strip()
                    if isinstance(connect, dict)
                    else ""
                )

                if via != transport_id:
                    mismatches.append(
                        "неверный транспорт L2TP"
                    )

                authentication = l2tp_item.get("authentication")
                identity = (
                    str(authentication.get("identity") or "").strip()
                    if isinstance(authentication, dict)
                    else ""
                )

                if identity != expected_identity:
                    mismatches.append(
                        "имя пользователя L2TP не сохранено"
                    )

                if not (
                    isinstance(authentication, dict)
                    and authentication.get("password") is not None
                ):
                    mismatches.append("пароль L2TP не сохранён")

                actual_server = str(
                    l2tp_item.get("peer") or ""
                ).strip()

                if actual_server != expected_server:
                    mismatches.append(
                        "адрес L2TP-сервера не совпадает"
                    )

                try:
                    actual_mtu = int(
                        l2tp_item.get("ip", {}).get("mtu")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_mtu = None

                if actual_mtu != expected_mtu:
                    mismatches.append(
                        f"MTU L2TP: ожидалось {expected_mtu}, "
                        f"получено {actual_mtu}"
                    )

                try:
                    actual_order = int(
                        l2tp_item.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        f"приоритет L2TP: ожидалось "
                        f"{expected_order + 1}, получено "
                        f"{actual_order}"
                    )

                if not self.client.truthy(l2tp_item.get("up")):
                    mismatches.append("L2TP-интерфейс выключен")

            if not isinstance(transport_item, dict):
                mismatches.append("транспорт IPoE не найден")
            else:
                ip_config = transport_item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    mismatches.append(
                        "транспорт ошибочно назначен интернет-каналом"
                    )

            if not isinstance(port_item, dict):
                mismatches.append("Ethernet-порт не найден")
            else:
                if self.client.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.client.extract_port_roles(port_item)

                if not any(
                    str(role.get("role") or "").lower() == "inet"
                    and str(role.get("ifor") or "") == transport_id
                    for role in roles
                ):
                    mismatches.append(
                        "роль Provider не привязана к транспорту"
                    )

            if not mismatches:
                return True, "Созданное L2TP подтверждено."

        return (
            False,
            "Итоговая проверка L2TP не пройдена: "
            + "; ".join(mismatches),
        )

    def verify_wired_pppoe_delete_preparation(
        self,
        *,
        pppoe_id: str,
        transport_id: str,
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

            pppoe_item = config.get(pppoe_id)
            transport_item = config.get(transport_id)
            port_item = config.get(port_id)
            mismatches = []

            if not isinstance(pppoe_item, dict):
                mismatches.append(
                    "PPPoE исчезло до этапа удаления"
                )
            else:
                if self.client.truthy(pppoe_item.get("up")):
                    mismatches.append("PPPoE не выключено")

                if manager_profile_detached:
                    assignment = pppoe_item.get("ping-check")

                    if assignment:
                        mismatches.append(
                            "профиль Ping Check не отсоединён"
                        )

            if not isinstance(transport_item, dict):
                mismatches.append(
                    "транспорт исчез до этапа удаления"
                )
            elif self.client.truthy(transport_item.get("up")):
                mismatches.append("транспорт PPPoE не выключен")

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
                        "LAN-порт не возвращён в домашний "
                        f"VLAN {home_vlan_id}"
                    )

                roles = self.client.extract_port_roles(port_item)

                if any(
                    str(role.get("role") or "")
                    .strip()
                    .lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                    for role in roles
                ):
                    mismatches.append(
                        "с LAN-порта не снята роль Provider"
                    )

            if not mismatches:
                return (
                    True,
                    "PPPoE и LAN-порт подготовлены к удалению.",
                )

        return (
            False,
            "Подготовка PPPoE к удалению не подтверждена: "
            + "; ".join(mismatches),
        )

    def verify_wired_pptp_delete_preparation(
        self,
        *,
        pptp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        manager_profile_detached: bool,
    ) -> tuple[bool, str]:
        success, message = self.verify_wired_pppoe_delete_preparation(
            pppoe_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
        )
        return success, message.replace("PPPoE", "PPTP")

    def verify_wired_l2tp_delete_preparation(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        manager_profile_detached: bool,
    ) -> tuple[bool, str]:
        success, message = self.verify_wired_pppoe_delete_preparation(
            pppoe_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
        )
        return success, message.replace("PPPoE", "L2TP")

    def rollback_wired_pppoe_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        errors: list[str] = []
        pppoe_id = str(context["interface_id"])
        transport_id = str(context["transport_id"])
        port_id = str(context["port_id"])
        pppoe_arg = self.client.quote_cli_argument(pppoe_id)
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(port_id)

        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not (
            isinstance(current_config, dict)
            and pppoe_id in current_config
            and transport_id in current_config
        ):
            return (
                False,
                "PPPoE или его транспорт уже удалены; автоматическое "
                "восстановление учётных данных невозможно.",
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
            role_name = str(role.get("role") or "").strip()

            if not role_name:
                continue

            command = (
                f"interface {port_arg} role "
                + self.client.quote_cli_argument(role_name)
            )
            ifor = str(role.get("ifor") or "").strip()

            if ifor:
                command += (
                    " for " + self.client.quote_cli_argument(ifor)
                )

            commands.append(command)

        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()

        if ping_profile.startswith("_KM_"):
            commands.append(
                f"interface {pppoe_arg} ping-check profile "
                + self.client.quote_cli_argument(ping_profile)
            )

        commands.extend(
            (
                (
                    f"interface {transport_arg} up"
                    if context.get("transport_was_up")
                    else f"interface {transport_arg} no up"
                ),
                (
                    f"interface {pppoe_arg} up"
                    if context.get("was_up")
                    else f"interface {pppoe_arg} no up"
                ),
                "system configuration save",
            )
        )

        for command in commands:
            success, message = self.client.execute_parse_command(command)

            if not success:
                errors.append(f"{command}: {message}")

        if old_priorities:
            success, message = self.client.set_internet_priorities(
                old_priorities
            )

            if not success:
                errors.append(
                    "Не удалось восстановить приоритеты: "
                    + message
                )

        if errors:
            return False, "; ".join(errors)

        return (
            True,
            "Исходная конфигурация PPPoE и транспорта восстановлена.",
        )

    def rollback_wired_pptp_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.rollback_wired_pppoe_deletion(
            context=context,
            old_priorities=old_priorities,
        )
        return success, message.replace("PPPoE", "PPTP")

    def rollback_wired_l2tp_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.rollback_wired_pppoe_deletion(
            context=context,
            old_priorities=old_priorities,
        )
        return success, message.replace("PPPoE", "L2TP")

    def verify_deleted_wired_pppoe(
        self,
        *,
        pppoe_id: str,
        transport_id: str,
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

            if pppoe_id in config:
                mismatches.append("PPPoE-интерфейс всё ещё существует")

            if transport_id in config:
                mismatches.append("транспорт PPPoE всё ещё существует")

            port_item = config.get(port_id)

            if not isinstance(port_item, dict):
                mismatches.append("LAN-порт не найден")
            else:
                if self.client.extract_port_access_vid(port_item) != home_vlan_id:
                    mismatches.append(
                        f"LAN-порт не находится в VLAN {home_vlan_id}"
                    )

                roles = self.client.extract_port_roles(port_item)

                if any(
                    str(role.get("role") or "")
                    .strip()
                    .lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                    for role in roles
                ):
                    mismatches.append(
                        "на LAN-порту осталась роль Provider"
                    )

            actual_priorities = self.client.current_global_orders(config)

            for interface_id, expected_order in (
                remaining_priorities.items()
            ):
                actual_order = actual_priorities.get(interface_id)

                if actual_order != expected_order:
                    mismatches.append(
                        f"{interface_id}: ожидался порядок "
                        f"{expected_order}, получен {actual_order}"
                    )

            unexpected = set(actual_priorities) - set(
                remaining_priorities
            )

            if unexpected:
                mismatches.append(
                    "обнаружены неожиданные глобальные каналы: "
                    + ", ".join(sorted(unexpected))
                )

            if not mismatches:
                return True, "Удаление PPPoE подтверждено роутером."

        return (
            False,
            "Итоговая проверка удаления PPPoE не пройдена: "
            + "; ".join(mismatches),
        )

    def verify_deleted_wired_pptp(
        self,
        *,
        pptp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.verify_deleted_wired_pppoe(
            pppoe_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
        )
        return success, message.replace("PPPoE", "PPTP")

    def verify_deleted_wired_l2tp(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        success, message = self.verify_deleted_wired_pppoe(
            pppoe_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
        )
        return success, message.replace("PPPoE", "L2TP")

