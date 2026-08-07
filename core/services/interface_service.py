from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from core.services.operation_result import OperationResult


SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class InterfaceSnapshot:
    """Оперативное состояние и сохранённая конфигурация интерфейсов."""

    runtime: dict[str, Any]
    config: dict[str, Any]


class InterfaceService:
    """
    Чтение и базовое управление интерфейсами Keenetic.

    Сервис получает клиент как совместимый транспортный фасад:
    session/base_url/timeout/verify_ssl/get_rci_data и набор
    _manually_disabled_interfaces. Это сохраняет существующий
    публичный API KeeneticClient, но отделяет интерфейсную логику.
    """

    RUNTIME_ENDPOINT = "show/interface"
    CONFIG_ENDPOINT = "show/sc/interface"

    def __init__(
        self,
        client: Any,
        *,
        sleep: SleepFunction = time.sleep,
    ) -> None:
        self.client = client
        self._sleep = sleep

    def read_runtime(
        self,
        *,
        required: bool = False,
    ) -> dict[str, Any]:
        data = self.client.get_rci_data(
            endpoint=self.RUNTIME_ENDPOINT,
            required=required,
        )
        return data if isinstance(data, dict) else {}

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
        runtime_required: bool = False,
        config_required: bool = False,
    ) -> InterfaceSnapshot:
        return InterfaceSnapshot(
            runtime=self.read_runtime(
                required=runtime_required,
            ),
            config=self.read_config(
                required=config_required,
            ),
        )

    @staticmethod
    def truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value != 0

        if isinstance(value, str):
            return value.strip().lower() in {
                "yes",
                "true",
                "1",
                "on",
                "enabled",
            }

        return False

    @staticmethod
    def interface_is_enabled(
        item: dict[str, Any],
    ) -> bool:
        state = str(
            item.get("state")
            or ""
        ).strip().lower()

        summary = item.get("summary")
        conf = ""

        if isinstance(summary, dict):
            layer = summary.get("layer")

            if isinstance(layer, dict):
                conf = str(
                    layer.get("conf")
                    or ""
                ).strip().lower()

        if state == "down" and conf == "disabled":
            return False

        if conf == "disabled":
            return False

        return True

    @staticmethod
    def find_interface_item(
        data: Any,
        interface_id: str,
    ) -> dict[str, Any] | None:
        """Find an interface in both flat and nested RCI payloads."""
        name = str(interface_id or "").strip().casefold()
        if not name:
            return None

        candidates: list[tuple[int, int, dict[str, Any]]] = []
        sequence = 0

        def richness(item: dict[str, Any]) -> int:
            score = 0
            summary = item.get("summary")
            if isinstance(summary, dict):
                layer = summary.get("layer")
                if isinstance(layer, dict) and "conf" in layer:
                    score += 30
            if "up" in item or "down" in item:
                score += 20
            if "state" in item:
                score += 5
            if str(item.get("type") or "").lower() == "port":
                score += 1
            return score

        def add(item: dict[str, Any], base_score: int, depth: int) -> None:
            nonlocal sequence
            sequence += 1
            candidates.append(
                (base_score + richness(item) - depth, sequence, item)
            )

        def walk(value: Any, depth: int = 0) -> None:
            if isinstance(value, dict):
                item_id = str(value.get("id") or "").strip().casefold()
                if item_id == name:
                    add(value, 100, depth)

                for key, child in value.items():
                    if (
                        str(key).strip().casefold() == name
                        and isinstance(child, dict)
                    ):
                        add(child, 200, depth)

                for child in value.values():
                    walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)

        walk(data)
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate[:2])[2]

    @staticmethod
    def runtime_admin_state(
        item: dict[str, Any] | None,
    ) -> bool | None:
        """Return the administrative state without confusing it with link."""
        if not isinstance(item, dict):
            return None

        summary = item.get("summary")
        conf = ""
        if isinstance(summary, dict):
            layer = summary.get("layer")
            if isinstance(layer, dict):
                conf = str(layer.get("conf") or "").strip().lower()

        if conf == "disabled":
            return False
        if conf:
            return True

        if "up" in item:
            return InterfaceService.truthy(item.get("up"))
        if "down" in item:
            return not InterfaceService.truthy(item.get("down"))

        # state=down can mean that no cable is connected.  It is not an
        # administrative disable signal by itself.
        if str(item.get("state") or "").strip().lower() == "up":
            return True
        return None

    @staticmethod
    def config_admin_state(
        item: dict[str, Any] | None,
    ) -> bool | None:
        if not isinstance(item, dict):
            return None
        if "up" in item:
            return InterfaceService.truthy(item.get("up"))
        if "down" in item:
            return not InterfaceService.truthy(item.get("down"))
        return None

    @staticmethod
    def interface_is_up(
        item: dict[str, Any],
    ) -> bool:
        link = str(
            item.get("link")
            or ""
        ).lower()
        connected = str(
            item.get("connected")
            or ""
        ).lower()
        state = str(
            item.get("state")
            or ""
        ).lower()

        summary = item.get("summary")
        summary_link = ""

        if isinstance(summary, dict):
            layer = summary.get("layer")

            if isinstance(layer, dict):
                summary_link = str(
                    layer.get("link")
                    or ""
                ).lower()

        return (
            link == "up"
            and connected in {"yes", "true", "1"}
            and state == "up"
            and summary_link in {"", "running"}
        )

    @staticmethod
    def extract_rci_error(
        data: Any,
    ) -> str | None:
        if isinstance(data, dict):
            for key in (
                "error",
                "message",
                "warning",
                "failed",
            ):
                value = data.get(key)

                if isinstance(value, str) and value:
                    return value

                if isinstance(value, dict):
                    nested = InterfaceService.extract_rci_error(
                        value
                    )

                    if nested:
                        return nested

            for value in data.values():
                nested = InterfaceService.extract_rci_error(
                    value
                )

                if nested:
                    return nested

        elif isinstance(data, list):
            for value in data:
                nested = InterfaceService.extract_rci_error(
                    value
                )

                if nested:
                    return nested

        return None

    def set_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        return self.set_enabled_result(
            interface_id,
            enabled,
        ).as_legacy_tuple()

    def set_enabled_result(
        self,
        interface_id: str,
        enabled: bool,
    ) -> OperationResult:
        name = str(
            interface_id
            or ""
        ).strip()

        if not name:
            return OperationResult.rejected_result(
                "Не указано имя интерфейса."
            )

        if (
            self.client.session is None
            or self.client.base_url is None
        ):
            return OperationResult.rejected_result(
                "Нет активной сессии с роутером."
            )

        action = "up" if enabled else "down"
        payload = {"name": name}

        response = self.client.transport.request(
            method="POST",
            path=f"/rci/interface/{action}",
            endpoint=f"interface/{action}",
            required=False,
            timeout=self.client.timeout,
            json_payload=payload,
            allow_reauthentication=True,
            retry_transient=False,
            return_error_response=True,
        )

        if response is None:
            # При выключении текущего WAN связь может оборваться
            # до получения ответа, хотя команда уже применена.
            if not enabled:
                self.client._manually_disabled_interfaces.add(name)
                return OperationResult.unknown_result(
                    "Команда выключения отправлена; состояние будет проверено после переподключения.",
                    interface_id=name,
                    requested_enabled=False,
                )
            return OperationResult.rejected_result(
                "Роутер не подтвердил выполнение команды.",
                interface_id=name,
                requested_enabled=True,
            )

        if response.status_code in (401, 403):
            return OperationResult.rejected_result(
                "Недостаточно прав для изменения интерфейса.",
                http_status=response.status_code,
            )

        response_data: Any = None

        try:
            response_data = response.json()
        except ValueError:
            response_data = None

        response_text = str(
            response.text
            or ""
        ).strip()
        response_text_lower = response_text.lower()

        error_text = self.extract_rci_error(
            response_data
        )

        success_messages = (
            "interface is up",
            "interface is down",
            "already up",
            "already down",
        )
        textual_success = any(
            message in response_text_lower
            for message in success_messages
        )

        if response.ok and (
            not error_text
            or textual_success
        ):
            verified, state_text = (
                self.verify_admin_state(
                    interface_id=name,
                    expected_enabled=enabled,
                )
            )

            if verified or textual_success:
                if enabled:
                    self.client._manually_disabled_interfaces.discard(
                        name
                    )
                else:
                    self.client._manually_disabled_interfaces.add(
                        name
                    )

                return OperationResult.confirmed_result(
                    (
                        "Интерфейс включён."
                        if enabled
                        else "Интерфейс выключен."
                    ),
                    interface_id=name,
                    enabled=enabled,
                    verification=state_text,
                    verified=verified,
                )

            return OperationResult.rejected_result(
                (
                    "Команда принята, но состояние "
                    "интерфейса не подтверждено: "
                    f"{state_text}"
                ),
                interface_id=name,
                requested_enabled=enabled,
                mutation_may_have_applied=True,
            )

        details = (
            error_text
            or response_text[:300]
            or "неизвестная ошибка"
        )

        return OperationResult.rejected_result(
            (
                "Роутер отклонил команду. "
                f"HTTP {response.status_code}: "
                f"{details}"
            ),
            interface_id=name,
            requested_enabled=enabled,
            http_status=response.status_code,
        )

    def reconnect(
        self,
        interface_id: str,
    ) -> tuple[bool, str]:
        name = str(
            interface_id
            or ""
        ).strip()

        if not name:
            return False, "Не указано имя интерфейса."

        if (
            self.client.session is None
            or self.client.base_url is None
        ):
            return False, "Нет активной сессии с роутером."

        down_success, down_message = self.set_enabled(
            interface_id=name,
            enabled=False,
        )

        if not down_success:
            return (
                False,
                (
                    "Не удалось отключить интерфейс: "
                    f"{down_message}"
                ),
            )

        self._sleep(1.2)

        up_success, up_message = self.set_enabled(
            interface_id=name,
            enabled=True,
        )

        if not up_success:
            return (
                False,
                (
                    "Интерфейс был отключён, но повторное "
                    f"включение не выполнено: {up_message}"
                ),
            )

        return (
            True,
            "Интернет-подключение переподключено.",
        )

    def verify_admin_state(
        self,
        interface_id: str,
        expected_enabled: bool,
    ) -> tuple[bool, str]:
        name = str(
            interface_id
            or ""
        ).strip()

        if not name:
            return False, "не указано имя интерфейса"

        last_state = "нет данных"

        for _ in range(4):
            self._sleep(0.7)

            snapshot = self.read_snapshot()
            runtime_item = self.find_interface_item(
                snapshot.runtime,
                name,
            )
            config_item = self.find_interface_item(
                snapshot.config,
                name,
            )
            runtime_enabled = self.runtime_admin_state(runtime_item)
            config_enabled = self.config_admin_state(config_item)

            last_state = (
                "runtime="
                + (
                    "enabled" if runtime_enabled else "disabled"
                    if runtime_enabled is not None else "unknown"
                )
                + ", config="
                + (
                    "enabled" if config_enabled else "disabled"
                    if config_enabled is not None else "unknown"
                )
            )

            if runtime_enabled == expected_enabled:
                return True, last_state

            if (
                runtime_enabled is None
                and config_enabled == expected_enabled
            ):
                return True, last_state

            if (
                not expected_enabled
                and runtime_item is None
                and config_item is None
            ):
                return True, "интерфейс отсутствует"

        return False, last_state

    def get_statistics(
        self,
        interface_id: str,
    ) -> dict[str, Any]:
        name = str(
            interface_id
            or ""
        ).strip()

        if (
            not name
            or self.client.session is None
            or self.client.base_url is None
        ):
            return {}

        attempts = (
            (
                "POST",
                "/rci/show/interface/stat",
                {"name": name},
            ),
            (
                "POST",
                "/rci/show/interface",
                {
                    "name": name,
                    "stat": {},
                },
            ),
            (
                "GET",
                "/rci/show/interface/stat"
                f"?name={quote(name, safe='')}",
                None,
            ),
        )

        for method, path, payload in attempts:
            response = self.client.transport.request(
                method=method,
                path=path,
                endpoint="show/interface/stat",
                required=False,
                timeout=self.client.timeout,
                json_payload=payload,
                allow_reauthentication=True,
                retry_transient=(method == "GET"),
            )
            if response is None:
                continue

            try:
                data = response.json()
            except ValueError:
                continue

            if not isinstance(data, dict):
                continue

            if self.extract_rci_error(data):
                continue

            normalized = self.normalize_statistics(
                data
            )

            if any(
                key in normalized
                for key in (
                    "rxbytes",
                    "rx-bytes",
                    "rx_bytes",
                    "txbytes",
                    "tx-bytes",
                    "tx_bytes",
                    "rxspeed",
                    "rx-speed",
                    "rx_speed",
                    "txspeed",
                    "tx-speed",
                    "tx_speed",
                )
            ):
                return data

        return {}

    @staticmethod
    def normalize_statistics(
        data: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}

        wanted = {
            "rxbytes",
            "rx-bytes",
            "rx_bytes",
            "txbytes",
            "tx-bytes",
            "tx_bytes",
            "rxspeed",
            "rx-speed",
            "rx_speed",
            "rx-bps",
            "txspeed",
            "tx-speed",
            "tx_speed",
            "tx-bps",
            "rxpackets",
            "rx-packets",
            "rx_packets",
            "txpackets",
            "tx-packets",
            "tx_packets",
        }

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized_key = (
                        str(key)
                        .strip()
                        .lower()
                    )

                    if normalized_key in wanted:
                        result[normalized_key] = nested

                    walk(nested)

            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        walk(data)
        return result

    def select_traffic_statistics(
        self,
        connection: dict[str, Any],
        interface_data: dict[str, Any],
        *,
        number_reader: Callable[..., float],
    ) -> tuple[str, dict[str, Any]]:
        logical_id = str(
            connection.get("interface_id")
            or ""
        ).strip()
        physical_id = str(
            connection.get("physical_interface")
            or ""
        ).strip()
        parent_id = str(
            connection.get("parent_interface")
            or ""
        ).strip()
        transport = str(
            connection.get("type")
            or ""
        ).lower()
        role = str(
            connection.get("role")
            or ""
        ).lower()

        candidates: list[str] = []

        def add(value: str) -> None:
            if (
                value
                and value != "—"
                and value not in candidates
            ):
                candidates.append(value)

        if (
            "ethernet" in transport
            and role != "vpn"
        ):
            add(physical_id)
            add(parent_id)
            add(logical_id)
        else:
            add(logical_id)
            add(parent_id)
            add(physical_id)

        best_source = logical_id
        best_data: dict[str, Any] = {}
        best_score = -1

        for candidate in candidates:
            data = self.get_statistics(
                candidate
            )
            normalized = self.normalize_statistics(
                data
            )
            rx = number_reader(
                normalized,
                keys=(
                    "rxbytes",
                    "rx-bytes",
                    "rx_bytes",
                ),
            )
            tx = number_reader(
                normalized,
                keys=(
                    "txbytes",
                    "tx-bytes",
                    "tx_bytes",
                ),
            )
            score = int(rx > 0) + int(tx > 0)

            if score > best_score:
                best_source = candidate
                best_data = data
                best_score = score

            if score == 2:
                break

        return best_source, best_data
