from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.debug_log import write_priority_debug
from core.services.runtime_paths import LOGS_DIR


SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class PrioritySnapshot:
    """Сохранённый глобальный порядок интернет-интерфейсов."""

    config: dict[str, Any]
    orders: dict[str, int]
    ordered_interfaces: tuple[str, ...]

    @property
    def primary_interface(self) -> str | None:
        return self.ordered_interfaces[0] if self.ordered_interfaces else None

    @property
    def backup_interfaces(self) -> tuple[str, ...]:
        return self.ordered_interfaces[1:]


class PriorityService:
    """
    Чтение, нормализация и изменение порядка интернет-каналов.

    ``ip.global.order`` является единственным источником основного и
    резервных каналов. Значение 0 означает основной канал, 1 и далее —
    резервные каналы. KeeneticClient сохраняет совместимые фасады.
    """

    CONFIG_ENDPOINT = "show/sc/interface"

    def __init__(
        self,
        client: Any,
        *,
        sleep: SleepFunction = time.sleep,
    ) -> None:
        self.client = client
        self._sleep = sleep

    def log_path(self) -> Path:
        router_suffix = (
            str(self.client.router_id)
            if self.client.router_id is not None
            else "unknown"
        )
        return LOGS_DIR / f"priority_debug_router_{router_suffix}.log"

    def write_log(self, message: str) -> None:
        router_suffix = (
            str(self.client.router_id)
            if self.client.router_id is not None
            else "unknown"
        )
        write_priority_debug(
            self.log_path(),
            f"router_id={router_suffix} | {message}",
        )

    def read_config(
        self,
        *,
        required: bool = False,
    ) -> dict[str, Any]:
        data = self.client.get_rci_data(
            endpoint=self.CONFIG_ENDPOINT,
            required=required,
        )
        return data if isinstance(data, dict) else {}

    def read_snapshot(
        self,
        *,
        required: bool = False,
    ) -> PrioritySnapshot:
        config = self.read_config(required=required)
        orders = self.current_global_orders(config)
        ordered = tuple(self.ordered_interface_ids(orders))
        return PrioritySnapshot(
            config=config,
            orders=orders,
            ordered_interfaces=ordered,
        )

    @staticmethod
    def interface_priority(item: dict[str, Any]) -> int:
        value = item.get("priority")
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return 999999

    @staticmethod
    def connection_role_order(interface_type: str) -> int:
        text = str(interface_type or "").lower()
        if any(
            marker in text
            for marker in (
                "gigabitethernet",
                "ethernet",
                "vlan",
                "pppoe",
            )
        ):
            return 0
        if "wifistation" in text:
            return 1
        if "lte" in text or "modem" in text:
            return 2
        return 3

    @staticmethod
    def extract_valid_priority(item: dict[str, Any]) -> int | None:
        try:
            value = int(float(item.get("priority")))
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 65534 else None

    @staticmethod
    def current_global_orders(
        interface_config: dict[str, Any],
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for interface_id, item in interface_config.items():
            if not isinstance(item, dict):
                continue
            ip_config = item.get("ip")
            if not isinstance(ip_config, dict):
                continue
            global_config = ip_config.get("global")
            if not isinstance(global_config, dict):
                continue
            try:
                order = int(global_config.get("order"))
            except (TypeError, ValueError):
                continue
            if order >= 0:
                result[str(interface_id)] = order
        return result

    @staticmethod
    def ordered_interface_ids(
        priorities: dict[str, int],
    ) -> list[str]:
        return [
            interface_id
            for interface_id, _order in sorted(
                priorities.items(),
                key=lambda item: (int(item[1]), str(item[0])),
            )
        ]

    @staticmethod
    def role_for_order(order: Any) -> str:
        try:
            normalized = int(order)
        except (TypeError, ValueError):
            return ""
        if normalized < 0:
            return ""
        return "primary" if normalized == 0 else "backup"

    @classmethod
    def normalize_order_map(
        cls,
        priorities: dict[str, Any],
    ) -> dict[str, int]:
        """Устраняет пропуски и дубликаты, сохраняя стабильный порядок."""
        valid: list[tuple[str, int, int]] = []
        for position, (interface_id, raw_order) in enumerate(priorities.items()):
            name = str(interface_id or "").strip()
            if not name:
                continue
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                continue
            if order < 0:
                continue
            valid.append((name, order, position))
        valid.sort(key=lambda item: (item[1], item[2], item[0]))
        return {
            interface_id: index
            for index, (interface_id, _order, _position) in enumerate(valid)
        }

    @classmethod
    def priorities_with_inserted_interface(
        cls,
        old_priorities: dict[str, int],
        *,
        interface_id: str,
        requested_order: int,
    ) -> dict[str, int]:
        ordered = [
            current_id
            for current_id in cls.ordered_interface_ids(old_priorities)
            if current_id != interface_id
        ]
        order = max(0, min(len(ordered), int(requested_order)))
        ordered.insert(order, interface_id)
        return {
            current_id: index
            for index, current_id in enumerate(ordered)
        }

    @classmethod
    def priorities_without_interface(
        cls,
        old_priorities: dict[str, int],
        *,
        interface_id: str,
    ) -> dict[str, int]:
        ordered = [
            current_id
            for current_id in cls.ordered_interface_ids(old_priorities)
            if current_id != interface_id
        ]
        return {
            current_id: index
            for index, current_id in enumerate(ordered)
        }

    def merge_configured_global_connections(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Добавляет настроенные глобальные интерфейсы, отсутствующие runtime."""
        result = [
            dict(connection)
            for connection in connections
            if isinstance(connection, dict)
        ]
        known = {
            str(
                connection.get("interface_id")
                or connection.get("id")
                or ""
            ).strip()
            for connection in result
        }

        for interface_id, config_item in interface_config_data.items():
            name = str(interface_id or "").strip()
            if not name or name in known or not isinstance(config_item, dict):
                continue
            ip_config = config_item.get("ip")
            global_config = (
                ip_config.get("global")
                if isinstance(ip_config, dict)
                else None
            )
            if not isinstance(global_config, dict):
                continue
            try:
                order = int(global_config.get("order"))
            except (TypeError, ValueError):
                continue
            if order < 0:
                continue

            runtime = interface_data.get(name)
            if not isinstance(runtime, dict):
                runtime = {}
            runtime_type = str(runtime.get("type") or "").strip()
            if not runtime_type:
                if re.fullmatch(r"PPPoE\d+", name):
                    runtime_type = "PPPoE"
                elif re.fullmatch(r"PPTP\d+", name):
                    runtime_type = "PPTP"
                elif re.fullmatch(r"L2TP\d+", name):
                    runtime_type = "L2TP"
                else:
                    runtime_type = "Интернет-интерфейс"

            description = str(
                config_item.get("description")
                or runtime.get("description")
                or name
            ).strip()
            link_text = str(runtime.get("link") or "").strip().lower()
            connected_text = str(
                runtime.get("connected") or ""
            ).strip().lower()
            connected = (
                connected_text in {"yes", "true", "up", "connected"}
                or link_text in {"up", "running", "connected"}
            )
            enabled = self.client.truthy(config_item.get("up", True))
            address = runtime.get("address")

            connection: dict[str, Any] = {
                "interface_id": name,
                "id": name,
                "name": description,
                "description": description,
                "interface_name": name,
                "type": runtime_type,
                "connection_type": runtime_type,
                "connected": connected,
                "enabled": enabled,
                "global_order": order,
                "role": self.role_for_order(order),
                "state": runtime.get("state") or (
                    "up" if enabled else "disabled"
                ),
            }
            if address not in (None, ""):
                connection["address"] = address
            elif enabled:
                connection["address"] = "pending"
            via = str(runtime.get("via") or "").strip()
            if via:
                connection["gateway"] = via
            try:
                connection["priority"] = int(
                    float(
                        runtime.get("priority")
                        if runtime.get("priority") is not None
                        else global_config.get("priority")
                    )
                )
            except (TypeError, ValueError):
                pass
            result.append(connection)
            known.add(name)

        return self.sort_connections(result)

    @classmethod
    def sort_connections(
        cls,
        connections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        connections.sort(
            key=lambda item: (
                int(item.get("global_order"))
                if str(item.get("global_order", "")).lstrip("-").isdigit()
                else 9999,
                str(item.get("interface_id") or ""),
            )
        )
        return connections

    @staticmethod
    def validate_requested_orders(
        priorities: dict[str, Any],
    ) -> tuple[bool, str, dict[str, int]]:
        requested: dict[str, int] = {}
        for interface_id, raw_order in priorities.items():
            name = str(interface_id or "").strip()
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                return False, f"Некорректный порядок для {name}.", {}
            if not name:
                return False, "Не указано имя интерфейса.", {}
            if order < 0:
                return (
                    False,
                    f"Порядок интерфейса {name} не может быть отрицательным.",
                    {},
                )
            requested[name] = order
        if not requested:
            return False, "Список интернет-каналов пуст.", {}
        actual_orders = sorted(requested.values())
        expected_orders = list(range(len(requested)))
        if actual_orders != expected_orders:
            return (
                False,
                "Порядок каналов должен быть непрерывным: "
                + ", ".join(str(value) for value in expected_orders),
                {},
            )
        return True, "", requested

    @staticmethod
    def build_batch_commands(
        requested: dict[str, int],
    ) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        for interface_id, order in sorted(
            requested.items(),
            key=lambda item: item[1],
        ):
            commands.append(
                {
                    "interface": {
                        "ip": {
                            "global": {
                                "enabled": True,
                                "order": order,
                            }
                        },
                        "name": interface_id,
                    }
                }
            )
        commands.append(
            {"system": {"configuration": {"save": {}}}}
        )
        return commands

    def set_internet_priorities(
        self,
        priorities: dict[str, int],
    ) -> tuple[bool, str]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером."

        valid, message, requested = self.validate_requested_orders(priorities)
        if not valid:
            return False, message
        commands = self.build_batch_commands(requested)
        self.write_log(
            "SEND priorities="
            + json.dumps(requested, ensure_ascii=False, sort_keys=True)
        )
        applied, apply_message = self.client.execute_rci_batch(
            commands,
            operation_name="Изменение порядка интернет-каналов",
        )
        self.write_log(
            f"RESULT success={applied} message={apply_message}"
        )
        if not applied:
            return False, apply_message
        verified, verification_message = self.verify_global_interface_orders(
            requested
        )
        if not verified:
            return False, verification_message
        return True, "Порядок интернет-каналов изменён и сохранён."

    def verify_global_interface_orders(
        self,
        expected: dict[str, int],
    ) -> tuple[bool, str]:
        mismatches: list[str] = []
        for _ in range(8):
            self._sleep(0.75)
            config = self.read_config(required=False)
            if not config:
                continue
            mismatches = []
            for interface_id, expected_order in expected.items():
                item = config.get(interface_id)
                if not isinstance(item, dict):
                    mismatches.append(f"{interface_id}: интерфейс не найден")
                    continue
                global_config = item.get("ip", {}).get("global", {})
                try:
                    actual_order = int(global_config.get("order"))
                except (TypeError, ValueError, AttributeError):
                    actual_order = None
                if actual_order != expected_order:
                    mismatches.append(
                        f"{interface_id}: ожидалось {expected_order}, "
                        f"получено {actual_order}"
                    )
            if not mismatches:
                return True, "Порядок подтверждён роутером."
        return (
            False,
            "Роутер принял запрос, но проверка порядка не пройдена: "
            + "; ".join(mismatches),
        )
