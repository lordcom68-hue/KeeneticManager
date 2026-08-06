from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.services.errors import KeeneticError
from core.services.interface_service import InterfaceService


@dataclass(frozen=True)
class PingCheckSnapshot:
    """Сохранённая конфигурация и текущее состояние Ping Check."""

    config: dict[str, Any]
    status: dict[str, Any]


class PingCheckService:
    """
    Чтение, нормализация, история и конфигурация Ping Check.

    Сервис не выполняет ICMP с компьютера. Он работает только с RCI
    роутера и сохраняет историю изменений счётчиков в текущем сеансе.
    """

    CONFIG_ENDPOINT = "show/sc/ping-check"
    STATUS_ENDPOINT = "show/ping-check"

    def __init__(self, client: Any) -> None:
        self.client = client
        self.runtime_cache: dict[str, dict[str, Any]] = {}

    def read_config(self, *, required: bool = False) -> dict[str, Any]:
        data = self.client.get_rci_data(
            endpoint=self.CONFIG_ENDPOINT,
            required=required,
        )
        return data if isinstance(data, dict) else {}

    def read_status(self, *, required: bool = False) -> dict[str, Any]:
        data = self.client.get_rci_data(
            endpoint=self.STATUS_ENDPOINT,
            required=required,
        )
        return data if isinstance(data, dict) else {}

    def read_snapshot(
        self,
        *,
        config_required: bool = False,
        status_required: bool = False,
    ) -> PingCheckSnapshot:
        return PingCheckSnapshot(
            config=self.read_config(required=config_required),
            status=self.read_status(required=status_required),
        )

    @staticmethod
    def truthy(value: Any) -> bool:
        return InterfaceService.truthy(value)
    @staticmethod
    def ping_check_profile_name(
        interface_id: str,
    ) -> str:
        digest = hashlib.sha1(
            str(interface_id or "")
            .encode("utf-8")
        ).hexdigest()[:12]

        return f"_KM_{digest}"

    @classmethod
    def find_ping_check_profile(
        cls,
        data: Any,
        profile_name: str,
    ) -> dict[str, Any]:
        target = str(
            profile_name
            or ""
        ).strip()

        if not target:
            return {}

        def walk(value: Any) -> dict[str, Any]:
            if isinstance(value, list):
                for item in value:
                    result = walk(item)

                    if result:
                        return result

                return {}

            if not isinstance(value, dict):
                return {}

            direct = value.get(target)

            if isinstance(direct, dict):
                return direct

            candidate_name = str(
                value.get("profile")
                or value.get("name")
                or ""
            ).strip()

            if candidate_name == target:
                return value

            for nested in value.values():
                if isinstance(
                    nested,
                    (dict, list),
                ):
                    result = walk(nested)

                    if result:
                        return result

            return {}

        return walk(data)

    @staticmethod
    def ping_check_scalar(
        data: dict[str, Any],
        keys: tuple[str, ...],
        default: Any,
    ) -> Any:
        for key in keys:
            if key not in data:
                continue

            value = data.get(key)

            if isinstance(value, dict):
                for nested_key in (
                    "value",
                    "seconds",
                    "count",
                    "host",
                    "name",
                ):
                    if nested_key in value:
                        return value.get(
                            nested_key
                        )

            if isinstance(value, list):
                if value:
                    first = value[0]

                    if isinstance(first, dict):
                        for nested_key in (
                            "host",
                            "address",
                            "value",
                        ):
                            if nested_key in first:
                                return first.get(
                                    nested_key
                                )
                    return first

            return value

        return default

    @classmethod
    def ping_check_hosts(
        cls,
        data: dict[str, Any],
    ) -> list[str]:
        raw_value: Any = None

        for key in (
            "host",
            "hosts",
            "address",
            "target",
        ):
            if key in data:
                raw_value = data.get(key)
                break

        result: list[str] = []

        def add(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    add(item)
                return

            if isinstance(value, dict):
                for key in (
                    "host",
                    "address",
                    "target",
                    "value",
                    "name",
                ):
                    if key in value:
                        add(value.get(key))
                return

            text = str(value or "").strip()

            if text and text not in result:
                result.append(text)

        add(raw_value)
        return result

    @staticmethod
    def automatic_ping_check_hosts(
    ) -> tuple[str, str, str]:
        return (
            "google.com",
            "facebook.com",
            "yahoo.com",
        )

    @classmethod
    def is_automatic_ping_check_profile(
        cls,
        details: dict[str, Any],
    ) -> bool:
        mode = str(
            cls.ping_check_scalar(
                details,
                ("mode",),
                "",
            )
            or ""
        ).strip().lower()

        try:
            port = int(
                cls.ping_check_scalar(
                    details,
                    ("port",),
                    0,
                )
            )
        except (TypeError, ValueError):
            port = 0

        hosts = {
            host.rstrip(".").lower()
            for host in cls.ping_check_hosts(
                details
            )
        }
        expected_hosts = set(
            cls.automatic_ping_check_hosts()
        )

        return (
            mode in {"connect", "tls"}
            and port == 443
            and expected_hosts.issubset(hosts)
        )

    @classmethod
    def find_ping_check_runtime(
        cls,
        status_data: Any,
        profile_name: str,
        interface_id: str,
    ) -> dict[str, Any]:
        """
        Извлекает рабочее состояние выбранного профиля
        для конкретного интерфейса из show/ping-check.
        """
        profile = cls.find_ping_check_profile(
            status_data,
            profile_name,
        )

        if not isinstance(profile, dict):
            return {}

        interfaces = profile.get(
            "interface"
        )

        if not isinstance(interfaces, dict):
            return {}

        runtime = interfaces.get(
            interface_id
        )

        if isinstance(runtime, dict):
            return runtime

        # На отдельных моделях ключ может совпадать с alias
        # без учёта регистра.
        target = str(
            interface_id or ""
        ).strip().lower()

        for name, candidate in interfaces.items():
            if (
                str(name).strip().lower()
                == target
                and isinstance(candidate, dict)
            ):
                return candidate

        return {}

    @staticmethod
    def normalize_ping_check_runtime_status(
        value: Any,
    ) -> str:
        text = str(
            value or ""
        ).strip().lower()

        if text in {
            "pass",
            "passed",
            "success",
            "successful",
            "ok",
            "up",
            "online",
        }:
            return "pass"

        if text in {
            "fail",
            "failed",
            "failure",
            "error",
            "down",
            "offline",
        }:
            return "fail"

        if text in {
            "init",
            "initializing",
            "pending",
            "wait",
            "waiting",
            "checking",
            "unknown",
        }:
            return "pending"

        return "unknown"

    @staticmethod
    def safe_non_negative_integer(
        value: Any,
        default: int = 0,
    ) -> int:
        try:
            number = int(
                float(value)
            )
        except (TypeError, ValueError):
            return default

        return max(
            0,
            number,
        )

    @staticmethod
    def ping_check_runtime_addresses(
        runtime: dict[str, Any],
    ) -> list[str]:
        result: list[str] = []
        ipcache = runtime.get(
            "ipcache"
        )

        if not isinstance(ipcache, list):
            return result

        for item in ipcache:
            if not isinstance(item, dict):
                continue

            addresses = item.get(
                "addresses"
            )

            if not isinstance(addresses, list):
                addresses = [
                    addresses
                ]

            for address in addresses:
                text = str(
                    address or ""
                ).strip()

                if text and text not in result:
                    result.append(text)

        return result

    @classmethod
    def extract_ping_check_runtime(
        cls,
        status_data: Any,
        profile_name: str,
        interface_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        if not enabled:
            return {
                "status": "disabled",
                "success_count": 0,
                "fail_count": 0,
                "ignore_fail": False,
                "addresses": [],
            }

        runtime = cls.find_ping_check_runtime(
            status_data=status_data,
            profile_name=profile_name,
            interface_id=interface_id,
        )

        if not runtime:
            return {
                "status": "pending",
                "success_count": 0,
                "fail_count": 0,
                "ignore_fail": False,
                "addresses": [],
            }

        return {
            "status": (
                cls.normalize_ping_check_runtime_status(
                    runtime.get("status")
                )
            ),
            "success_count": (
                cls.safe_non_negative_integer(
                    runtime.get(
                        "successcount"
                    )
                )
            ),
            "fail_count": (
                cls.safe_non_negative_integer(
                    runtime.get(
                        "failcount"
                    )
                )
            ),
            "ignore_fail": cls.truthy(
                runtime.get(
                    "ignore-fail",
                    False,
                )
            ),
            "addresses": (
                cls.ping_check_runtime_addresses(
                    runtime
                )
            ),
        }

    @staticmethod
    def ping_check_status_text(
        status: str,
    ) -> str:
        return {
            "disabled": "Выключен",
            "pass": "Контрольный узел доступен",
            "fail": "Контрольный узел недоступен",
            "pending": "Ожидание результата",
            "unknown": "Нет данных",
        }.get(
            str(status or ""),
            "Нет данных",
        )

    @classmethod
    def ping_check_state_reason(
        cls,
        connection: dict[str, Any],
        interface_item: dict[str, Any],
        config_item: dict[str, Any],
        runtime: dict[str, Any],
        ping_enabled: bool,
    ) -> str:
        config_up = config_item.get(
            "up"
        )

        if config_up is False:
            return "Интерфейс выключен"

        state = str(
            interface_item.get("state")
            or ""
        ).strip().lower()
        link = str(
            interface_item.get("link")
            or ""
        ).strip().lower()
        connected = str(
            interface_item.get("connected")
            or ""
        ).strip().lower()

        if (
            state in {"disabled", "off"}
            or link in {"down", "disconnected"}
            or connected in {"no", "false"}
        ):
            return "Нет физического соединения"

        status = str(
            runtime.get("status")
            or "unknown"
        )

        if ping_enabled and status == "fail":
            if bool(
                runtime.get("ignore_fail")
            ):
                return (
                    "Ошибка Ping Check обнаружена, "
                    "но игнорируется роутером"
                )

            return (
                "Ping Check не пройден — канал может "
                "быть исключён из маршрутизации"
            )

        if ping_enabled and status == "pending":
            return "Ожидание результата Ping Check"

        if cls.truthy(
            interface_item.get(
                "defaultgw"
            )
        ):
            return (
                "Канал используется как маршрут "
                "по умолчанию"
            )

        if (
            connection.get("global_order")
            is not None
            or cls.truthy(
                interface_item.get(
                    "global"
                )
            )
        ):
            return (
                "Резервный канал ожидает "
                "переключения по приоритету"
            )

        return "Канал не выбран для доступа в интернет"

    def update_ping_check_runtime_history(
        self,
        interface_id: str,
        connection: dict[str, Any],
        interface_item: dict[str, Any],
        config_item: dict[str, Any],
        runtime: dict[str, Any],
        ping_enabled: bool,
    ) -> dict[str, Any]:
        now = (
            datetime.now()
            .astimezone()
        )
        now_iso = now.isoformat(
            timespec="seconds"
        )

        cache = self.runtime_cache.setdefault(
            interface_id,
            {},
        )

        success_count = self.safe_non_negative_integer(
            runtime.get(
                "success_count"
            )
        )
        fail_count = self.safe_non_negative_integer(
            runtime.get(
                "fail_count"
            )
        )
        status = str(
            runtime.get("status")
            or "unknown"
        )

        previous_success_count = (
            self.safe_non_negative_integer(
                cache.get(
                    "success_count"
                ),
                -1,
            )
        )
        previous_fail_count = (
            self.safe_non_negative_integer(
                cache.get(
                    "fail_count"
                ),
                -1,
            )
        )

        if (
            status == "pass"
            and (
                success_count > previous_success_count
                or not cache.get(
                    "last_success_at"
                )
            )
        ):
            cache[
                "last_success_at"
            ] = now_iso

        if (
            status == "fail"
            and (
                fail_count > previous_fail_count
                or not cache.get(
                    "last_failure_at"
                )
            )
        ):
            cache[
                "last_failure_at"
            ] = now_iso

        current_default_gateway = self.truthy(
            interface_item.get(
                "defaultgw"
            )
        )
        previous_default_gateway = cache.get(
            "default_gateway"
        )

        reason = self.ping_check_state_reason(
            connection=connection,
            interface_item=interface_item,
            config_item=config_item,
            runtime=runtime,
            ping_enabled=ping_enabled,
        )

        if (
            previous_default_gateway is not None
            and bool(previous_default_gateway)
            != current_default_gateway
        ):
            if current_default_gateway:
                switch_reason = (
                    "Канал стал маршрутом по умолчанию"
                )
            else:
                switch_reason = reason

            cache[
                "last_switch_reason"
            ] = switch_reason
            cache[
                "last_switch_at"
            ] = now_iso

        cache[
            "success_count"
        ] = success_count
        cache[
            "fail_count"
        ] = fail_count
        cache[
            "default_gateway"
        ] = current_default_gateway
        cache[
            "state_reason"
        ] = reason

        return {
            "last_success_at": cache.get(
                "last_success_at"
            ),
            "last_failure_at": cache.get(
                "last_failure_at"
            ),
            "last_switch_reason": cache.get(
                "last_switch_reason"
            ),
            "last_switch_at": cache.get(
                "last_switch_at"
            ),
            "state_reason": reason,
        }

    @classmethod
    def extract_ping_check_settings(
        cls,
        config_item: dict[str, Any],
        config_data: dict[str, Any],
        status_data: dict[str, Any],
    ) -> dict[str, Any]:
        assignment = config_item.get(
            "ping-check"
        )
        profile_name = ""

        if isinstance(assignment, dict):
            profile_name = str(
                assignment.get("profile")
                or ""
            ).strip()
        elif isinstance(assignment, str):
            profile_name = assignment.strip()

        if not profile_name:
            return {
                "supported": True,
                "enabled": False,
                "mode": "disabled",
                "profile": "",
                "host": "",
                "port": 443,
                "interval": 10,
                "max_fails": 5,
            }

        details = cls.find_ping_check_profile(
            config_data,
            profile_name,
        )

        if not details:
            details = cls.find_ping_check_profile(
                status_data,
                profile_name,
            )

        hosts = cls.ping_check_hosts(details)
        host = hosts[0] if hosts else ""

        raw_mode = str(
            cls.ping_check_scalar(
                details,
                ("mode",),
                "",
            )
            or ""
        ).strip().lower()

        try:
            port = int(
                cls.ping_check_scalar(
                    details,
                    ("port",),
                    0,
                )
            )
        except (TypeError, ValueError):
            port = 0

        if (
            profile_name == "default"
            or cls.is_automatic_ping_check_profile(
                details
            )
        ):
            mode = "automatic"
        elif raw_mode in {"", "icmp"} and host:
            mode = "icmp"
        elif raw_mode == "tls" and host and port:
            mode = "tls"
        elif raw_mode == "connect" and host and port:
            mode = "connect"
        else:
            mode = "custom"

        try:
            interval = int(
                cls.ping_check_scalar(
                    details,
                    (
                        "update-interval",
                        "update_interval",
                        "interval",
                    ),
                    10,
                )
            )
        except (TypeError, ValueError):
            interval = 10

        try:
            max_fails = int(
                cls.ping_check_scalar(
                    details,
                    (
                        "max-fails",
                        "max_fails",
                        "fail-count",
                    ),
                    5,
                )
            )
        except (TypeError, ValueError):
            max_fails = 5

        return {
            "supported": True,
            "enabled": True,
            "mode": mode,
            "profile": profile_name,
            "host": host,
            "port": max(
                1,
                min(
                    65535,
                    port or (
                        53
                        if mode == "connect"
                        else 443
                    ),
                ),
            ),
            "interval": max(
                3,
                min(3600, interval),
            ),
            "max_fails": max(
                1,
                min(10, max_fails),
            ),
        }

    def build_ping_check_commands(
        self,
        interface_id: str,
        settings: dict[str, Any],
        current_profile: str,
    ) -> tuple[list[str], str]:
        cli_name = self.client.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.client.quote_cli_argument(
            cli_name
        )

        mode = str(
            settings.get("mode")
            or (
                "icmp"
                if settings.get("enabled")
                else "disabled"
            )
        ).strip().lower()
        commands: list[str] = []

        if mode == "disabled":
            commands.append(
                (
                    f"interface {interface_arg} "
                    "no ping-check profile"
                )
            )

            if str(current_profile).startswith(
                "_KM_"
            ):
                commands.append(
                    (
                        "no ping-check profile "
                        f"{self.client.quote_cli_argument(current_profile)}"
                    )
                )

            return commands, ""

        if mode == "automatic":
            # The Keenetic web interface uses the built-in
            # profile named "default" for Automatic mode.
            commands.append(
                (
                    f"interface {interface_arg} "
                    "ping-check profile default"
                )
            )

            # Delete only a profile previously created by
            # Keenetic Manager, and only after the interface
            # has been switched to the built-in profile.
            if str(current_profile).startswith(
                "_KM_"
            ):
                commands.append(
                    (
                        "no ping-check profile "
                        f"{self.client.quote_cli_argument(current_profile)}"
                    )
                )

            return commands, "default"

        if mode not in {
            "icmp",
            "tls",
            "connect",
        }:
            raise KeeneticError(
                "Неподдерживаемый режим Ping Check."
            )

        profile_name = self.ping_check_profile_name(
            interface_id
        )
        profile_arg = self.client.quote_cli_argument(
            profile_name
        )
        host_arg = self.client.quote_cli_argument(
            str(settings["host"])
        )

        # Keenetic creates a Ping Check profile only after
        # the standalone `ping-check profile <name>` command.
        commands.extend(
            (
                (
                    f"ping-check profile {profile_arg}"
                ),
                (
                    f"ping-check profile {profile_arg} "
                    "no host"
                ),
                (
                    f"ping-check profile {profile_arg} "
                    f"mode {mode}"
                ),
            )
        )

        if mode in {
            "tls",
            "connect",
        }:
            commands.append(
                (
                    f"ping-check profile {profile_arg} "
                    f"port {int(settings['port'])}"
                )
            )
        else:
            commands.append(
                (
                    f"ping-check profile {profile_arg} "
                    "no port"
                )
            )

        commands.extend(
            (
                (
                    f"ping-check profile {profile_arg} "
                    f"host {host_arg}"
                ),
                (
                    f"ping-check profile {profile_arg} "
                    "update-interval "
                    f"{int(settings['interval'])}"
                ),
                (
                    f"ping-check profile {profile_arg} "
                    "max-fails "
                    f"{int(settings['max_fails'])}"
                ),
                (
                    f"interface {interface_arg} "
                    f"ping-check profile {profile_arg}"
                ),
            )
        )

        if (
            str(current_profile).startswith("_KM_")
            and current_profile != profile_name
        ):
            commands.append(
                (
                    "no ping-check profile "
                    f"{self.client.quote_cli_argument(current_profile)}"
                )
            )

        return commands, profile_name

    @staticmethod
    def assigned_ping_profile(config_item: dict[str, Any]) -> str:
        assignment = config_item.get("ping-check")

        if isinstance(assignment, dict):
            return str(assignment.get("profile") or "").strip()
        if isinstance(assignment, str):
            return assignment.strip()
        return ""

    def enrich_connection(
        self,
        *,
        connection: dict[str, Any],
        interface_id: str,
        interface_item: dict[str, Any],
        config_item: dict[str, Any],
        config_data: dict[str, Any],
        status_data: dict[str, Any],
    ) -> None:
        """Добавляет в карточку подключения все поля Ping Check."""
        settings = self.extract_ping_check_settings(
            config_item=config_item,
            config_data=config_data,
            status_data=status_data,
        )

        connection["ping_check_supported"] = settings["supported"]
        connection["ping_check_enabled"] = settings["enabled"]
        connection["ping_check_mode"] = settings["mode"]
        connection["ping_check_profile"] = settings["profile"]
        connection["ping_check_host"] = settings["host"]
        connection["ping_check_port"] = int(settings.get("port", 443))
        connection["ping_check_interval"] = settings["interval"]
        connection["ping_check_max_fails"] = settings["max_fails"]

        runtime = self.extract_ping_check_runtime(
            status_data=status_data,
            profile_name=settings["profile"],
            interface_id=interface_id,
            enabled=bool(settings["enabled"]),
        )
        history = self.update_ping_check_runtime_history(
            interface_id=interface_id,
            connection=connection,
            interface_item=interface_item,
            config_item=config_item,
            runtime=runtime,
            ping_enabled=bool(settings["enabled"]),
        )

        connection["ping_check_runtime_status"] = runtime["status"]
        connection["ping_check_runtime_text"] = self.ping_check_status_text(runtime["status"])
        connection["ping_check_success_count"] = runtime["success_count"]
        connection["ping_check_fail_count"] = runtime["fail_count"]
        connection["ping_check_ignore_fail"] = runtime["ignore_fail"]
        connection["ping_check_addresses"] = runtime["addresses"]
        connection["ping_check_last_success"] = history["last_success_at"]
        connection["ping_check_last_failure"] = history["last_failure_at"]
        connection["ping_check_state_reason"] = history["state_reason"]
        connection["ping_check_last_switch_reason"] = history["last_switch_reason"]
        connection["ping_check_last_switch_at"] = history["last_switch_at"]

