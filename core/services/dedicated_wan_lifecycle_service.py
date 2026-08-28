from __future__ import annotations

import time
from typing import Any


class DedicatedWanLifecycleService:
    """Проверяет и откатывает создание и удаление выделенного WAN."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def verify_dedicated_wan_active(
        self,
        *,
        transport_id: str,
        bridge_id: str,
        expected_order: int,
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(10):
            time.sleep(0.55)
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            mismatches = []
            bridge_item = config.get(bridge_id)
            included = (
                self.client.collect_included_interfaces(
                    bridge_item.get("include")
                )
                if isinstance(bridge_item, dict)
                else []
            )

            if transport_id in included:
                mismatches.append(
                    "аппаратный WAN всё ещё включён в домашний Bridge"
                )

            transport = config.get(transport_id)

            if not isinstance(transport, dict):
                mismatches.append("аппаратный WAN не найден")
            else:
                try:
                    order = int(
                        transport.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (TypeError, ValueError, AttributeError):
                    order = None

                if order != expected_order:
                    mismatches.append(
                        f"ожидался приоритет {expected_order}, получен {order}"
                    )

                security = transport.get("security-level")
                is_public = (
                    self.client.truthy(security.get("public"))
                    if isinstance(security, dict)
                    else str(security or "").strip().lower() == "public"
                )

                if not is_public:
                    mismatches.append(
                        "аппаратный WAN не получил публичный уровень безопасности"
                    )

            if not mismatches:
                return True, "Аппаратный WAN восстановлен и активирован."

        return (
            False,
            "Проверка аппаратного WAN не пройдена: "
            + "; ".join(mismatches or ["нет актуальных данных"]),
        )

    def rollback_dedicated_wan_creation(
        self,
        *,
        bridge_id: str,
        transport_id: str,
        old_priorities: dict[str, int],
        target_interface_id: str = "",
    ) -> tuple[bool, str]:
        errors: list[str] = []
        target = str(target_interface_id or "").strip()

        if target and target != transport_id:
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if isinstance(config, dict) and target in config:
                success, message = self.client.execute_rci_batch(
                    [
                        {
                            "parse": (
                                "interface "
                                + self.client.quote_cli_argument(target)
                                + " no up"
                            )
                        },
                        {
                            "parse": (
                                "no interface "
                                + self.client.quote_cli_argument(target)
                            )
                        },
                        {"system": {"configuration": {"save": {}}}},
                    ],
                    operation_name="Не удалось удалить новый протокол",
                )

                if not success:
                    errors.append(message)

        success, message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "interface "
                        + self.client.quote_cli_argument(bridge_id)
                        + " include "
                        + self.client.quote_cli_argument(transport_id)
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name=(
                "Не удалось вернуть аппаратный WAN в домашнюю сеть"
            ),
        )

        if not success:
            errors.append(message)

        if old_priorities:
            success, message = self.client.set_internet_priorities(
                old_priorities
            )

            if not success:
                errors.append(
                    "Не удалось восстановить приоритеты: " + message
                )

        if errors:
            return False, "; ".join(errors)

        return True, "Аппаратный WAN возвращён в домашнюю сеть."

    def verify_dedicated_wan_deleted(
        self,
        *,
        source_id: str,
        transport_id: str,
        bridge_id: str,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        mismatches: list[str] = []

        for _ in range(10):
            time.sleep(0.6)
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            mismatches = []
            bridge = config.get(bridge_id)
            included = (
                self.client.collect_included_interfaces(
                    bridge.get("include")
                )
                if isinstance(bridge, dict)
                else []
            )

            if transport_id not in included:
                mismatches.append("WAN-порт не включён в домашний Bridge")

            transport = config.get(transport_id)

            if not isinstance(transport, dict):
                mismatches.append("физический Ethernet-интерфейс исчез")
            else:
                ip_config = transport.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    mismatches.append("физический порт остался интернет-каналом")

                security = transport.get("security-level")
                is_public = (
                    self.client.truthy(security.get("public"))
                    if isinstance(security, dict)
                    else str(security or "").strip().lower() == "public"
                )

                if is_public:
                    mismatches.append(
                        "у LAN0 остался публичный уровень безопасности"
                    )

                if isinstance(transport.get("ipoe"), dict):
                    mismatches.append(
                        "у LAN0 осталась служебная конфигурация IPoE"
                    )

            if source_id != transport_id and source_id in config:
                source = config.get(source_id)
                source_ip = (
                    source.get("ip")
                    if isinstance(source, dict)
                    else None
                )
                source_global = (
                    source_ip.get("global")
                    if isinstance(source_ip, dict)
                    else None
                )

                if isinstance(source_global, dict):
                    mismatches.append("протокол провайдера остался активным")

            for interface_id, expected_order in remaining_priorities.items():
                item = config.get(interface_id)

                try:
                    actual_order = int(
                        item.get("ip", {})
                        .get("global", {})
                        .get("order")
                    )
                except (TypeError, ValueError, AttributeError):
                    actual_order = None

                if actual_order != expected_order:
                    mismatches.append(
                        f"{interface_id}: ожидался порядок "
                        f"{expected_order}, получен {actual_order}"
                    )

            if not mismatches:
                return True, "Аппаратный WAN включён в домашнюю сеть."

        return (
            False,
            "Проверка удаления аппаратного WAN не пройдена: "
            + "; ".join(mismatches or ["нет актуальных данных"]),
        )

    def rollback_dedicated_wan_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        source_id = str(context["interface_id"])
        transport_id = str(context["transport_id"])
        bridge_id = str(context["bridge_id"])
        errors: list[str] = []

        config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if (
            source_id != transport_id
            and isinstance(config, dict)
            and source_id not in config
        ):
            return (
                False,
                "Протокольный интерфейс уже удалён; автоматический "
                "откат его учётных данных невозможен.",
            )

        success, message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "no interface "
                        + self.client.quote_cli_argument(bridge_id)
                        + " include "
                        + self.client.quote_cli_argument(transport_id)
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Не удалось восстановить аппаратный WAN",
        )

        if not success:
            errors.append(message)

        ping_profile = str(context.get("ping_profile") or "").strip()

        if ping_profile and source_id != transport_id:
            success, message = self.client.execute_parse_command(
                "interface "
                + self.client.quote_cli_argument(source_id)
                + " ping-check profile "
                + self.client.quote_cli_argument(ping_profile)
            )

            if not success:
                errors.append("Не удалось восстановить Ping Check: " + message)

        if old_priorities:
            success, message = self.client.set_internet_priorities(old_priorities)

            if not success:
                errors.append("Не удалось восстановить приоритеты: " + message)

        self.client.execute_parse_command(
            "interface "
            + self.client.quote_cli_argument(source_id)
            + (" up" if context.get("was_up") else " no up")
        )
        self.client.execute_parse_command("system configuration save")

        if errors:
            return False, "; ".join(errors)

        return True, "Исходное назначение аппаратного WAN восстановлено."
