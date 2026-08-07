from __future__ import annotations

import copy
import ipaddress
from pathlib import Path
import re
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from core.topology_engine import TopologyEngine, TopologyResult
from core.port_engine import PortEngine
from core.rci import (
    DiagnosticCollector,
    RCI_ENDPOINTS,
    RouterCapabilities,
)
from core.rci.diagnostics import sanitize_value
from core.services.errors import (
    KeeneticAuthenticationError,
    KeeneticError,
)
from core.services.rci_transport import RciTransport
from core.services.interface_service import InterfaceService
from core.services.port_status_service import PortStatusService
from core.services.ping_check_service import (
    PingCheckService,
)
from core.services.priority_service import (
    PriorityService,
)
from core.services.wired_internet_service import (
    WiredInternetService,
)
from core.services.wireless_isp_service import (
    WirelessIspService,
)
from core.services.wifi_network_service import (
    WiFiNetworkService,
)
from core.services.wifi_client_service import (
    WiFiClientService,
)
from core.services.rescue_mode_service import (
    RescueModeService,
)
from core.services.system_log_service import SystemLogService
from core.services.system_status_service import SystemStatusService
from core.services.traffic_service import TrafficService
from core.services.diagnostics_service import DiagnosticsService
from core.services.runtime_paths import LOGS_DIR


@dataclass
class ConnectionResult:
    success: bool
    message: str

    connected_url: str | None = None
    connection_type: str | None = None

    system_data: dict[str, Any] | None = None
    version_data: dict[str, Any] | None = None
    internet_data: dict[str, Any] | None = None
    interface_data: dict[str, Any] | None = None
    internet_connections: list[dict[str, Any]] | None = None
    topology: TopologyResult | None = None
    associations_data: Any = None
    hotspot_data: Any = None
    wifi_networks: list[dict[str, Any]] | None = None
    clients: list[dict[str, Any]] | None = None
    wifi_capabilities: dict[str, Any] | None = None
    router_capabilities: dict[str, Any] | None = None
    ports: list[dict[str, Any]] | None = None
    usb_data: Any = None
    creation_options: dict[str, Any] | None = None

    model: str | None = None
    version: str | None = None
    uptime: str | None = None
    cpu_load: str | None = None
    ram_usage: str | None = None

    memory_total: int | None = None
    memory_free: int | None = None

    # Состояние фонового мониторинга. Поля имеют значения по
    # умолчанию, чтобы сохранить совместимость со старым UI и тестами.
    connection_state: str = ""
    consecutive_failures: int = 0
    confirmed_offline: bool = False
    transient_failure_suppressed: bool = False
    last_result_message: str = ""
    failure_kind: str = ""


class KeeneticClient:
    """
    Клиент одного роутера.

    После успешного подключения сохраняет requests.Session,
    адрес и параметры SSL. Последующие обновления используют
    ту же HTTP-сессию без повторной авторизации.
    """

    CLIENT_REFRESH_INTERVAL_SECONDS = 5.0
    CONFIG_REFRESH_INTERVAL_SECONDS = 15.0
    USB_REFRESH_INTERVAL_SECONDS = 15.0

    def __init__(
        self,
        username: str,
        password: str,
        timeout: float = 8.0,
        router_id: int | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout
        self.router_id = router_id

        self.diagnostics = DiagnosticCollector()
        self.transport = RciTransport(
            username=username,
            password=password,
            timeout=timeout,
            router_id=router_id,
            diagnostics=self.diagnostics,
            session_guard=self.install_optional_rci_guard,
            endpoint_policy=self.endpoint_is_allowed,
        )

        self.version_data: dict[str, Any] = {}
        self.capabilities = RouterCapabilities()

        # Интерфейсы, выключенные пользователем из приложения.
        # Они остаются в списке, чтобы их можно было включить.
        self._manually_disabled_interfaces: set[str] = set()
        self._management_route_url = ""
        self._management_local_ip: str | None = None

        # Интерфейсная логика вынесена в отдельный сервис.
        # KeeneticClient сохраняет старые методы как совместимый фасад.
        self.interface_service = InterfaceService(self)
        self.port_status_service = PortStatusService(self)
        self.traffic_service = TrafficService(self)
        self.ping_check_service = PingCheckService(self)
        self.priority_service = PriorityService(self)
        self.wired_internet_service = WiredInternetService(self)
        self.wireless_isp_service = WirelessIspService(self)
        self.wifi_network_service = WiFiNetworkService(self)
        self.wifi_client_service = WiFiClientService(self)
        self.rescue_mode_service = RescueModeService(self)
        self.system_log_service = SystemLogService(self)
        self.system_status_service = SystemStatusService()

        # Совместимые aliases для старых тестов и внешнего кода.
        # Владельцем кэшей является WiFiNetworkService.
        self._known_wifi_networks = (
            self.wifi_network_service.known_networks
        )
        self._wifi_client_counters = (
            self.wifi_network_service.client_counters
        )
        self._traffic_counters = self.traffic_service.counters

        # Поддерживаемый USB RCI-раздел определяется один раз
        # после подключения к конкретному роутеру.
        #
        # None  — проверка ещё не выполнялась;
        # ""    — show/usb и show/storage не поддерживаются;
        # строка — найденный рабочий endpoint.
        self._usb_rci_endpoint: str | None = None

        # Совместимый alias для сервисов предыдущих версий.
        self._ping_check_runtime_cache = (
            self.ping_check_service.runtime_cache
        )

        # Последний успешный RCI-снимок сохраняется немедленно при
        # первом обновлении, затем не чаще одного раза в 30 секунд.
        self._last_snapshot_cache_write = 0.0
        self._refresh_cache: dict[str, Any] = {}
        self._refresh_cache_times: dict[str, float] = {}

    @property
    def session(self) -> requests.Session | None:
        return self.transport.session

    @session.setter
    def session(
        self,
        value: requests.Session | None,
    ) -> None:
        self.transport.session = value

    @property
    def base_url(self) -> str | None:
        return self.transport.base_url

    @base_url.setter
    def base_url(self, value: str | None) -> None:
        self.transport.base_url = value

    @property
    def connection_type(self) -> str | None:
        return self.transport.connection_type

    @connection_type.setter
    def connection_type(
        self,
        value: str | None,
    ) -> None:
        self.transport.connection_type = value

    @property
    def verify_ssl(self) -> bool:
        return self.transport.verify_ssl

    @verify_ssl.setter
    def verify_ssl(self, value: bool) -> None:
        self.transport.verify_ssl = bool(value)

    @property
    def connected(self) -> bool:
        return self.transport.connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self.transport.connected = bool(value)

    def priority_log_path(self) -> Path:
        """Совместимый фасад PriorityService."""
        return self.priority_service.log_path()

    def write_priority_log(self, message: str) -> None:
        """Совместимый фасад PriorityService."""
        self.priority_service.write_log(message)

    def _result_state_fields(self) -> dict[str, Any]:
        state = self.transport.connection_state()
        return {
            "connection_state": state,
            "consecutive_failures": int(
                self.transport.consecutive_failures
            ),
            "confirmed_offline": state == "offline",
            "transient_failure_suppressed": False,
            "last_result_message": str(
                self.transport.last_result_message or ""
            ),
        }

    def _cache_successful_snapshot(self) -> None:
        if self.router_id is None or not self.diagnostics.snapshots:
            return

        now = time.monotonic()
        if (
            self._last_snapshot_cache_write > 0
            and now - self._last_snapshot_cache_write < 30.0
        ):
            return

        try:
            DiagnosticsService().save_snapshot_cache(
                router_id=self.router_id,
                collector=self.diagnostics,
                metadata={
                    "connected_url": self.base_url,
                    "connection_type": self.connection_type,
                    "model": self.extract_model(self.version_data),
                    "version": self.extract_version(self.version_data),
                },
            )
        except OSError:
            return

        self._last_snapshot_cache_write = now

    def connect_to_router(
        self,
        local_ip: str | None,
        keendns: str | None,
        connection_mode: str = "auto",
    ) -> ConnectionResult:
        self.close()

        candidates = self.build_candidates(
            local_ip=local_ip,
            keendns=keendns,
            connection_mode=connection_mode,
        )

        self.transport.record_connection_plan(
            local_ip=local_ip,
            keendns=keendns,
            connection_mode=connection_mode,
            candidates=candidates,
        )

        if not candidates:
            message = "Не указан адрес для подключения."
            self.transport.mark_offline(message)
            return ConnectionResult(
                success=False,
                message=message,
                **self._result_state_fields(),
            )

        errors: list[str] = []

        for base_url, connection_type in candidates:
            try:
                self.transport.connect_candidate(
                    base_url=base_url,
                    connection_type=connection_type,
                )

                self.version_data = (
                    self.get_version_data()
                )
                self.capabilities = (
                    RouterCapabilities.from_version_data(
                        self.version_data
                    )
                )

                return self.refresh()

            except KeeneticAuthenticationError as error:
                self.close()

                self.transport.record_monitor_state(
                    "retrying",
                    str(error),
                )
                return ConnectionResult(
                    success=False,
                    message=str(error),
                    connected_url=base_url,
                    connection_type=connection_type,
                    failure_kind="authentication",
                    **self._result_state_fields(),
                )

            except KeeneticError as error:
                self.close()
                errors.append(
                    f"{base_url}: {error}"
                )

        message = (
            "Не удалось подключиться ни по одному адресу.\n\n"
            + "\n".join(errors)
        )
        self.transport.record_monitor_state(
            "retrying",
            message,
        )
        return ConnectionResult(
            success=False,
            message=message,
            **self._result_state_fields(),
        )

    def _cached_refresh_value(
        self,
        key: str,
        *,
        now: float,
        interval_seconds: float,
        force: bool,
        loader: Any,
    ) -> Any:
        last_refresh = self._refresh_cache_times.get(key, 0.0)
        if (
            force
            or key not in self._refresh_cache
            or now - last_refresh >= interval_seconds
        ):
            self._refresh_cache[key] = loader()
            self._refresh_cache_times[key] = now
        return self._refresh_cache[key]

    def refresh(
        self,
        *,
        force_full: bool = False,
    ) -> ConnectionResult:
        if (
            not self.connected
            or self.session is None
            or self.base_url is None
        ):
            return ConnectionResult(
                success=False,
                message="Клиент не подключён к роутеру.",
                **self._result_state_fields(),
            )

        try:
            now = time.monotonic()
            system_data = self.get_rci_data(
                endpoint=RCI_ENDPOINTS["system"].path,
                required=True,
            )

            internet_data = self.get_rci_data(
                endpoint=RCI_ENDPOINTS["internet_status"].path,
                required=False,
            )

            interface_data = self.interface_service.read_runtime()

            # Реальный порядок интернет-каналов хранится здесь:
            # ip.global.order, а не в show/interface.priority.
            interface_config_data = self._cached_refresh_value(
                "interface_config",
                now=now,
                interval_seconds=self.CONFIG_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=self.interface_service.read_config,
            )

            ping_check_config_data = self._cached_refresh_value(
                "ping_check_config",
                now=now,
                interval_seconds=self.CONFIG_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=self.ping_check_service.read_config,
            )
            ping_check_status_data = self._cached_refresh_value(
                "ping_check_status",
                now=now,
                interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=self.ping_check_service.read_status,
            )

            associations_data = self._cached_refresh_value(
                "associations",
                now=now,
                interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=lambda: self.get_rci_data(
                    endpoint=RCI_ENDPOINTS["associations"].path,
                    required=False,
                ),
            )

            hotspot_data = self._cached_refresh_value(
                "hotspot",
                now=now,
                interval_seconds=self.CLIENT_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=lambda: self.get_rci_data(
                    endpoint=RCI_ENDPOINTS["hotspot"].path,
                    required=False,
                ),
            )
            client_data_refreshed = (
                force_full
                or self._refresh_cache_times.get("associations") == now
                or self._refresh_cache_times.get("hotspot") == now
            )

            usb_data = self._cached_refresh_value(
                "usb",
                now=now,
                interval_seconds=self.USB_REFRESH_INTERVAL_SECONDS,
                force=force_full,
                loader=lambda: self.get_usb_data(
                    system_data=system_data,
                    interface_data=interface_data,
                ),
            )

            result = self.build_result(
                system_data=system_data,
                version_data=self.version_data,
                internet_data=internet_data,
                interface_data=interface_data,
                interface_config_data=interface_config_data,
                ping_check_config_data=ping_check_config_data,
                ping_check_status_data=ping_check_status_data,
                associations_data=associations_data,
                hotspot_data=hotspot_data,
                usb_data=usb_data,
                force_wifi_logical_refresh=(
                    force_full
                    or self._refresh_cache_times.get(
                        "interface_config"
                    )
                    == now
                ),
                client_data_refreshed=client_data_refreshed,
            )
            result.connection_state = "connected"
            result.consecutive_failures = 0
            result.confirmed_offline = False
            result.transient_failure_suppressed = False
            result.last_result_message = result.message
            self._cache_successful_snapshot()
            return result

        except KeeneticError as error:
            # Сессия могла истечь или связь могла пропасть.
            self.connected = False
            failure_kind = self.transport.classify_failure(
                error=error,
                status_code=None,
                connection_type=self.connection_type,
                message=str(error),
            )

            return ConnectionResult(
                success=False,
                message=str(error),
                connected_url=self.base_url,
                connection_type=self.connection_type,
                failure_kind=str(failure_kind or ""),
                **self._result_state_fields(),
            )

    def install_optional_rci_guard(
        self,
        session: requests.Session | None = None,
    ) -> None:
        """
        Блокирует неподдерживаемый show/hardware на уровне
        всей requests.Session.

        Это перехватывает оба варианта:
            GET/POST /rci/show/hardware
            POST /rci/ с {"show": {"hardware": {}}}

        Поэтому защита работает даже для вызовов из других
        модулей, которые используют self.session напрямую.
        """
        target_session = session or self.session

        if target_session is None:
            return

        if getattr(
            target_session,
            "_keenetic_optional_rci_guard",
            False,
        ):
            return

        original_request = target_session.request

        def guarded_request(
            method: str,
            url: str,
            **kwargs: Any,
        ) -> requests.Response:
            normalized_url = str(
                url
                or ""
            ).rstrip("/").lower()

            if normalized_url.endswith(
                "/rci/show/hardware"
            ):
                self.log_hardware_guard_event(
                    method=method,
                    url=url,
                    payload=kwargs.get("json"),
                )
                return self.build_empty_rci_response(
                    url
                )

            payload = kwargs.get("json")
            filtered_payload, changed = (
                self.remove_show_hardware_commands(
                    payload
                )
            )

            if changed:
                self.log_hardware_guard_event(
                    method=method,
                    url=url,
                    payload=payload,
                )

                if filtered_payload in (
                    None,
                    [],
                    {},
                ):
                    return self.build_empty_rci_response(
                        url
                    )

                kwargs["json"] = filtered_payload

            return original_request(
                method,
                url,
                **kwargs,
            )

        target_session.request = guarded_request
        target_session._keenetic_optional_rci_guard = True

    @staticmethod
    def log_hardware_guard_event(
        method: str,
        url: str,
        payload: Any,
    ) -> None:
        try:
            log_path = LOGS_DIR / "hardware_guard.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            with log_path.open(
                "a",
                encoding="utf-8",
            ) as stream:
                stream.write(
                    (
                        f"BLOCKED {method} {url} "
                        f"payload={sanitize_value(payload)!r}\n"
                    )
                )
                stream.flush()
        except Exception:
            pass

    @classmethod
    def remove_show_hardware_commands(
        cls,
        payload: Any,
    ) -> tuple[Any, bool]:
        """
        Удаляет команды show.hardware из одиночного или
        пакетного JSON-RCI запроса.
        """
        if cls.is_show_hardware_command(payload):
            return None, True

        if isinstance(payload, list):
            result: list[Any] = []
            changed = False

            for item in payload:
                if cls.is_show_hardware_command(item):
                    changed = True
                    continue

                result.append(item)

            return result, changed

        return payload, False

    @staticmethod
    def is_show_hardware_command(
        value: Any,
    ) -> bool:
        if not isinstance(value, dict):
            return False

        show = value.get("show")

        if not isinstance(show, dict):
            return False

        return "hardware" in show

    @staticmethod
    def build_empty_rci_response(
        url: str,
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response.url = str(url)
        response.headers[
            "Content-Type"
        ] = "application/json"
        response._content = b"{}"
        response.encoding = "utf-8"
        return response

    def close(self) -> None:
        self.transport.close(clear_target=True)
        self.version_data = {}
        self.capabilities = RouterCapabilities()
        self.traffic_service.clear()
        self._wifi_client_counters.clear()
        self._usb_rci_endpoint = None
        self._refresh_cache.clear()
        self._refresh_cache_times.clear()

    def create_session(self) -> requests.Session:
        return self.transport.create_session()

    def get_usb_data(
        self,
        *,
        system_data: dict[str, Any] | None = None,
        interface_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Автоматически определяет поддержку USB по безопасному
        show/version и только затем обращается к USB RCI.

        На роутере без USB запросы show/usb и show/storage
        вообще не отправляются.
        """
        del system_data
        del interface_data

        if self._usb_rci_endpoint == "":
            return {}

        capabilities = (
            self.detect_usb_capabilities_from_version()
        )

        if not capabilities["supported"]:
            self._usb_rci_endpoint = ""
            return {}

        if self._usb_rci_endpoint is None:
            self._usb_rci_endpoint = (
                self.detect_usb_rci_endpoint(
                    allow_usb=capabilities["usb"],
                    allow_storage=capabilities["storage"],
                )
            )

        if not self._usb_rci_endpoint:
            return {}

        return self.get_rci_data(
            endpoint=self._usb_rci_endpoint,
            required=False,
        )

    def detect_usb_capabilities_from_version(
        self,
    ) -> dict[str, bool]:
        """
        Совместимый фасад над централизованным
        RouterCapabilities.

        Старый формат результата сохранён, чтобы не ломать
        уже работающий код USB:
            supported
            usb
            storage
        """
        self.capabilities = (
            RouterCapabilities.from_version_data(
                self.version_data
            )
        )

        return {
            "supported": self.capabilities.usb,
            "usb": self.capabilities.usb,
            "storage": self.capabilities.storage,
        }

    @staticmethod
    def split_capability_tokens(
        value: Any,
    ) -> set[str]:
        if isinstance(value, str):
            return {
                item.strip().lower()
                for item in value.split(",")
                if item.strip()
            }

        if isinstance(value, (list, tuple, set)):
            return {
                str(item).strip().lower()
                for item in value
                if str(item).strip()
            }

        return set()

    def detect_usb_rci_endpoint(
        self,
        *,
        allow_usb: bool,
        allow_storage: bool,
    ) -> str:
        """
        Проверяет только те endpoints, поддержку которых уже
        подтвердил show/version.
        """
        endpoints: list[str] = []

        if allow_usb:
            endpoints.append(
                RCI_ENDPOINTS["usb"].path
            )

        if allow_storage:
            endpoints.append(
                RCI_ENDPOINTS["storage"].path
            )

        for endpoint in endpoints:
            supported, _data = (
                self.probe_optional_rci_endpoint(
                    endpoint
                )
            )

            if supported:
                return endpoint

        return ""

    def probe_optional_rci_endpoint(
        self,
        endpoint: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Однократно проверяет необязательный RCI endpoint."""
        response = self.transport.request(
            method="GET",
            path=f"/rci/{endpoint}",
            endpoint=str(endpoint).strip().strip("/").lower(),
            required=False,
            timeout=self.timeout,
            allow_reauthentication=True,
        )

        if response is None:
            return False, {}

        try:
            data = response.json()
        except ValueError:
            return False, {}

        if not isinstance(data, dict):
            return False, {}

        error_text = self.extract_rci_error(data)
        if error_text:
            lowered = error_text.lower()
            if (
                "not found" in lowered
                or "unknown" in lowered
                or "unsupported" in lowered
            ):
                return False, {}

        return True, data

    def get_version_data(self) -> dict[str, Any]:
        for endpoint in (
            RCI_ENDPOINTS["version"].path,
            "show/components",
        ):
            data = self.get_rci_data(
                endpoint=endpoint,
                required=False,
            )

            if data:
                if self.extract_version(data):
                    return data

                if not self.version_data:
                    self.version_data = data

        return self.version_data

    @staticmethod
    def get_rci_endpoint_definition(
        endpoint: str,
    ) -> Any:
        """
        Возвращает описание endpoint из централизованного
        реестра, если команда там зарегистрирована.
        """
        normalized = (
            str(endpoint or "")
            .strip()
            .strip("/")
            .lower()
        )

        for definition in RCI_ENDPOINTS.values():
            if definition.path.lower() == normalized:
                return definition

        return None

    def endpoint_is_allowed(
        self,
        endpoint: str,
    ) -> bool:
        """
        Применяет политику безопасности реестра RCI.

        Неизвестные endpoints пока разрешены для обратной
        совместимости. Политика blocked запрещает вызов.
        capability_gated проверяется отдельной логикой
        конкретного сервиса, например USB.
        """
        definition = self.get_rci_endpoint_definition(
            endpoint
        )

        if definition is None:
            return True

        return definition.policy != "blocked"

    def get_rci_data(
        self,
        endpoint: str,
        required: bool,
    ) -> dict[str, Any]:
        return self.transport.get_json(
            endpoint=endpoint,
            required=required,
        )

    def create_diagnostic_archive(
        self,
        destination: str | Path,
        *,
        application_settings: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Создаёт архив даже при нестабильной связи."""
        try:
            archive_path = DiagnosticsService().create_archive(
                destination=destination,
                router_id=self.router_id,
                collector=self.diagnostics,
                metadata={
                    "connected_url": self.base_url,
                    "connection_type": self.connection_type,
                    "model": self.extract_model(
                        self.version_data
                    ),
                    "version": self.extract_version(
                        self.version_data
                    ),
                },
                capabilities=self.capabilities,
                application_settings=application_settings,
                connection_state={
                    "router_connected": self.connected,
                    "snapshot_source": (
                        "live"
                        if self.diagnostics.snapshots
                        else "unavailable"
                    ),
                    "transport_state": (
                        self.transport.connection_state()
                    ),
                    "consecutive_failures": (
                        self.transport.consecutive_failures
                    ),
                    "last_success_at": (
                        self.transport.last_success_at
                    ),
                    "last_error_at": (
                        self.transport.last_error_at
                    ),
                    "last_error": self.transport.last_error,
                    "last_http_status": (
                        self.transport.last_http_status
                    ),
                    "current_connection_status": (
                        self.transport.connection_state()
                    ),
                    "last_result_message": (
                        self.transport.last_result_message
                    ),
                },
            )
        except Exception as error:
            self.diagnostics.record_exception(
                error,
                message=(
                    "Не удалось создать диагностический архив."
                ),
            )
            return False, str(error)

        return True, str(archive_path)

    def clear_diagnostics(self) -> None:
        self.diagnostics.clear()
        DiagnosticsService().clear_local_data(self.router_id)

    def authenticate(
        self,
        session: requests.Session,
        base_url: str,
        verify_ssl: bool,
    ) -> None:
        self.transport.authenticate(
            session=session,
            base_url=base_url,
            verify_ssl=verify_ssl,
        )

    @staticmethod
    def detect_local_source_ip(base_url: str | None) -> str | None:
        """Return the local address used for the current router route."""
        parsed = urlparse(str(base_url or ""))
        host = str(parsed.hostname or "").strip()
        if not host:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        try:
            target_ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            # KeenDNS uses HTTPS and may require a slow DNS lookup. The
            # self-disconnect guard is intentionally limited to direct local
            # IP management, where the route is deterministic and immediate.
            return None

        family = socket.AF_INET6 if target_ip.version == 6 else socket.AF_INET
        address: tuple[Any, ...] = (
            (str(target_ip), port, 0, 0)
            if target_ip.version == 6
            else (str(target_ip), port)
        )
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.settimeout(0.2)
                probe.connect(address)
                local_ip = str(probe.getsockname()[0]).split("%", 1)[0]
            parsed_ip = ipaddress.ip_address(local_ip)
            if not parsed_ip.is_loopback and not parsed_ip.is_unspecified:
                return str(parsed_ip)
        except (OSError, ValueError):
            return None
        return None

    @staticmethod
    def same_ip_address(first: Any, second: Any) -> bool:
        try:
            return ipaddress.ip_address(
                str(first or "").split("%", 1)[0]
            ) == ipaddress.ip_address(
                str(second or "").split("%", 1)[0]
            )
        except ValueError:
            return False

    def build_result(
        self,
        system_data: dict[str, Any],
        version_data: dict[str, Any],
        internet_data: dict[str, Any],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any] | None = None,
        ping_check_config_data: dict[str, Any] | None = None,
        ping_check_status_data: dict[str, Any] | None = None,
        associations_data: Any = None,
        hotspot_data: Any = None,
        usb_data: Any = None,
        force_wifi_logical_refresh: bool = False,
        client_data_refreshed: bool = True,
    ) -> ConnectionResult:
        system_status = self.system_status_service.build(
            system_data=system_data,
            version_data=version_data,
        )

        topology = TopologyEngine(
            internet_data=internet_data,
            interface_data=interface_data,
            manually_disabled=(
                self._manually_disabled_interfaces
            ),
        ).build()

        internet_connections = (
            self.filter_real_internet_connections(
                topology.connections,
                interface_data,
            )
        )
        internet_connections = (
            self.priority_service.merge_configured_global_connections(
                connections=internet_connections,
                interface_data=interface_data,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )

        self.enrich_connection_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
            ping_check_config_data=(
                ping_check_config_data or {}
            ),
            ping_check_status_data=(
                ping_check_status_data or {}
            ),
        )
        self.wireless_isp_service.enrich_wireless_isp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.enrich_wired_pppoe_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.enrich_wired_pptp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.enrich_wired_l2tp_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        self.enrich_cable_protocol_change_details(
            connections=internet_connections,
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        internet_connections = (
            self.group_authenticated_cable_connections(
                connections=internet_connections,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )
        self.priority_service.sort_connections(internet_connections)

        wifi_networks = self.wifi_network_service.build_networks(
            interface_data=interface_data,
            interface_config_data=(
                interface_config_data or {}
            ),
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            force_logical_refresh=force_wifi_logical_refresh,
            client_data_refreshed=client_data_refreshed,
        )
        client_inventory = list(
            self.wifi_network_service.client_inventory
        )
        self.wifi_client_service.enrich_clients(
            client_inventory,
            interface_config=(interface_config_data or {}),
        )
        route_url = str(self.base_url or "")
        if route_url != self._management_route_url:
            self._management_route_url = route_url
            self._management_local_ip = self.detect_local_source_ip(
                route_url
            )
        management_ip = self._management_local_ip
        for client in client_inventory:
            client["management_client"] = self.same_ip_address(
                client.get("ip"),
                management_ip,
            )

        wifi_capabilities_snapshot = (
            self.wifi_network_service.capabilities(
                interface_data=interface_data,
                interface_config_data=(
                    interface_config_data or {}
                ),
            )
        )
        wifi_capabilities = {
            "supported_bands": list(
                wifi_capabilities_snapshot.supported_bands
            ),
            "available_slots": {
                band: list(values)
                for band, values
                in wifi_capabilities_snapshot.available_slots.items()
            },
            "physical_available_slots": {
                band: list(values)
                for band, values
                in wifi_capabilities_snapshot.physical_available_slots.items()
            },
            "home_bridge_id": (
                wifi_capabilities_snapshot.home_bridge_id
            ),
            "mws_supported": (
                wifi_capabilities_snapshot.mws_supported
            ),
            "legacy_supported": (
                wifi_capabilities_snapshot.legacy_supported
            ),
            "bandless_supported": (
                wifi_capabilities_snapshot.bandless_supported
            ),
            "management_mode": (
                wifi_capabilities_snapshot.management_mode
            ),
        }

        ports = PortEngine(
            interface_data=interface_data,
            usb_data=usb_data,
        ).build()

        # show/interface может сохранять runtime-флаг public=True
        # у порта встроенного коммутатора даже после удаления
        # роли inet и возврата порта в домашний VLAN. Для
        # назначения LAN/WAN используем сохранённую конфигурацию
        # show/sc/interface, а не этот переходный runtime-флаг.
        self.correct_switch_port_assignments_from_config(
            ports=ports,
            interface_config_data=(
                interface_config_data or {}
            ),
        )
        management_ports = {
            str(item.get("interface") or "").strip().casefold()
            for item in client_inventory
            if bool(item.get("management_client"))
            and str(item.get("interface") or "").strip()
        }
        for port in ports:
            interface_id = str(
                port.get("interface_id") or port.get("id") or ""
            ).strip()
            port["management_port"] = (
                interface_id.casefold() in management_ports
            )

        return ConnectionResult(
            success=True,
            message="Данные роутера получены.",
            connected_url=self.base_url,
            connection_type=self.connection_type,
            system_data=system_data,
            version_data=version_data,
            internet_data=internet_data,
            interface_data=interface_data,
            internet_connections=internet_connections,
            topology=topology,
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            wifi_networks=wifi_networks,
            clients=client_inventory,
            wifi_capabilities=wifi_capabilities,
            router_capabilities={
                "wifi": self.capabilities.wifi,
                "wifi_management_mode": (
                    self.capabilities.wifi_management_mode
                ),
                "logical_mws": self.capabilities.logical_mws,
                "legacy_wifi": self.capabilities.legacy_wifi,
                "keeneticos_major": (
                    self.capabilities.keeneticos_major
                ),
                "keeneticos_minor": (
                    self.capabilities.keeneticos_minor
                ),
            },
            ports=ports,
            usb_data=usb_data,
            creation_options=(
                self.build_connection_creation_options(
                    interface_data=interface_data,
                    interface_config_data=(
                        interface_config_data or {}
                    ),
                )
            ),
            model=system_status.model,
            version=system_status.version,
            uptime=system_status.uptime,
            cpu_load=system_status.cpu_load,
            ram_usage=system_status.ram_usage,
            memory_total=system_status.memory_total,
            memory_free=system_status.memory_free,
        )

    @classmethod
    def correct_switch_port_assignments_from_config(
        cls,
        ports: list[dict[str, Any]],
        interface_config_data: dict[str, Any],
    ) -> None:
        """
        Исправляет назначение портов встроенного коммутатора
        по авторитетной сохранённой конфигурации.

        Некоторые версии KeeneticOS продолжают возвращать
        public=True в show/interface после снятия роли inet.
        Если доверять только runtime-данным, уже возвращённый
        в домашний VLAN LAN-порт ошибочно отображается как WAN.

        Для switch-порта правило однозначно:
            есть роль inet -> WAN;
            роли inet нет  -> LAN.

        Отдельный аппаратный WAN не имеет switchport-конфигурации
        и этим методом не изменяется.
        """
        if not isinstance(ports, list):
            return

        if not isinstance(
            interface_config_data,
            dict,
        ):
            return

        internet_roles = {
            "inet",
            "internet",
            "wan",
        }

        for port in ports:
            if not isinstance(port, dict):
                continue

            if str(
                port.get("kind")
                or "ethernet"
            ).strip().lower() != "ethernet":
                continue

            port_id = str(
                port.get("interface_id")
                or port.get("id")
                or ""
            ).strip()

            if not port_id:
                continue

            port_config = (
                interface_config_data.get(
                    port_id
                )
            )

            if not isinstance(port_config, dict):
                continue

            # Только порты встроенного коммутатора.
            # Отдельный аппаратный WAN здесь не трогаем.
            if not isinstance(
                port_config.get("switchport"),
                dict,
            ):
                continue

            has_internet_role = any(
                str(
                    role.get("role")
                    or ""
                ).strip().lower()
                in internet_roles
                for role in cls.extract_port_roles(
                    port_config
                )
            )

            assignment = (
                "WAN"
                if has_internet_role
                else "LAN"
            )
            port["role"] = assignment

            physical_name = str(
                port.get("physical_name")
                or port.get("name")
                or ""
            ).strip()

            port["description"] = (
                ""
                if physical_name == assignment
                else f"Назначение: {assignment}"
            )

    @staticmethod
    def split_config_tokens(
        value: Any,
    ) -> list[str]:
        if isinstance(value, list):
            result: list[str] = []

            for item in value:
                result.extend(
                    KeeneticClient.split_config_tokens(
                        item
                    )
                )

            return list(
                dict.fromkeys(result)
            )

        text = str(value or "").strip()

        if not text:
            return []

        return [
            token.strip()
            for token in text.split(",")
            if token.strip()
        ]

    @classmethod
    def find_home_bridge_id(
        cls,
        interface_config: dict[str, Any],
    ) -> str:
        """Возвращает основной домашний Bridge для Ethernet-порта."""
        bridge0 = interface_config.get("Bridge0")

        if isinstance(bridge0, dict):
            return "Bridge0"

        candidates: list[tuple[int, str]] = []

        for interface_id, item in interface_config.items():
            if not isinstance(item, dict):
                continue

            name = str(interface_id or "").strip()

            if not name.lower().startswith("bridge"):
                continue

            security = item.get("security-level")
            is_private = False

            if isinstance(security, dict):
                is_private = cls.truthy(
                    security.get("private")
                )
            else:
                is_private = (
                    str(security or "").strip().lower()
                    == "private"
                )

            description = str(
                item.get("description")
                or item.get("rename")
                or ""
            ).strip().lower()
            score = 0

            if is_private:
                score += 10

            if any(
                marker in description
                for marker in (
                    "home",
                    "домаш",
                    "lan",
                )
            ):
                score += 5

            if cls.collect_included_interfaces(
                item.get("include")
            ):
                score += 1

            candidates.append((-score, name))

        if not candidates:
            return ""

        candidates.sort()
        return candidates[0][1]

    @classmethod
    def dedicated_wan_runtime_info(
        cls,
        *,
        transport_id: str,
        interface_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Возвращает физический порт отдельного Ethernet-контроллера."""
        runtime = interface_data.get(transport_id)

        if not isinstance(runtime, dict):
            return {}

        traits = {
            str(value or "").strip().lower()
            for value in (runtime.get("traits") or [])
        }

        if "switch" in traits:
            return {}

        interface_type = str(
            runtime.get("type") or ""
        ).strip().lower()

        if "ethernet" not in interface_type:
            return {}

        raw_port = runtime.get("port")
        port: dict[str, Any] | None = None

        if isinstance(raw_port, dict):
            if raw_port.get("id"):
                port = raw_port
            else:
                candidates = [
                    candidate
                    for candidate in raw_port.values()
                    if isinstance(candidate, dict)
                    and candidate.get("id")
                ]

                if len(candidates) == 1:
                    port = candidates[0]

        if not isinstance(port, dict):
            return {}

        port_id = str(port.get("id") or "").strip()

        if not port_id:
            return {}

        label = str(
            port.get("label")
            or port.get("interface-name")
            or "0"
        ).strip()
        link = str(
            port.get("link")
            or runtime.get("link")
            or "unknown"
        ).strip().lower()

        return {
            "transport_id": transport_id,
            "port_id": port_id,
            "port_label": "WAN",
            "port_number": label or "0",
            "port_display_name": (
                f"WAN (порт {label})"
                if label
                else "WAN"
            ),
            "parent_id": transport_id,
            "link": link,
            "speed": port.get("speed") or runtime.get("speed"),
            "duplex": port.get("duplex") or runtime.get("duplex"),
        }

    @classmethod
    def build_connection_creation_options(
        cls,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Возвращает безопасные варианты создания подключений.

        Поддерживаются:
        - свободные access-порты встроенного коммутатора;
        - отдельный физический WAN, если он включён в домашний
          Bridge и в данный момент логически является LAN-портом.
        """
        free_labels: set[str] = set()

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            ipoe = item.get("ipoe")

            if isinstance(ipoe, dict):
                free_labels.update(
                    cls.split_config_tokens(
                        ipoe.get("free-port")
                    )
                )

        if not free_labels:
            bridge0 = interface_config_data.get(
                "Bridge0"
            )

            if isinstance(bridge0, dict):
                iseg = bridge0.get("iseg")

                if isinstance(iseg, dict):
                    free_labels.update(
                        cls.split_config_tokens(
                            iseg.get("free-port")
                        )
                    )

        port_runtime_by_label: dict[
            str,
            dict[str, Any],
        ] = {}

        for parent_id, parent in interface_data.items():
            if not isinstance(parent, dict):
                continue

            raw_ports = parent.get("port")
            ports: list[dict[str, Any]] = []

            if isinstance(raw_ports, dict):
                if raw_ports.get("id"):
                    ports = [raw_ports]
                else:
                    ports = [
                        candidate
                        for candidate in raw_ports.values()
                        if isinstance(candidate, dict)
                    ]

            for port in ports:
                label = str(
                    port.get("label")
                    or port.get("interface-name")
                    or ""
                ).strip()
                port_id = str(
                    port.get("id")
                    or ""
                ).strip()

                if not label or not port_id:
                    continue

                port_runtime_by_label[label] = {
                    "port_label": label,
                    "port_display_name": f"LAN{label}",
                    "port_id": port_id,
                    "parent_id": str(parent_id),
                    "link": str(
                        port.get("link")
                        or "unknown"
                    ).strip().lower(),
                    "speed": port.get("speed"),
                    "duplex": port.get("duplex"),
                }

        available_ports: list[dict[str, Any]] = []

        for label in free_labels:
            runtime = port_runtime_by_label.get(label)

            if not runtime:
                continue

            port_id = str(runtime["port_id"])
            port_config = interface_config_data.get(
                port_id
            )

            if not isinstance(port_config, dict):
                continue

            switchport = port_config.get(
                "switchport"
            )

            if not isinstance(switchport, dict):
                continue

            # Порт с Provider-ролью уже является WAN, даже если
            # старое поле free-port ещё не успело обновиться.
            if any(
                str(role.get("role") or "").strip().lower()
                in {"inet", "internet", "wan"}
                for role in cls.extract_port_roles(port_config)
            ):
                continue

            access = switchport.get("access")
            old_vid = None

            if isinstance(access, dict):
                try:
                    old_vid = int(
                        access.get("vid")
                    )
                except (TypeError, ValueError):
                    old_vid = None

            option = dict(runtime)
            option["mode"] = "switch_vlan"
            option["current_access_vid"] = old_vid
            available_ports.append(option)

        # Отдельный WAN-контроллер становится доступен в мастере,
        # когда он включён в домашний Bridge. Это ровно то
        # состояние, которое создаёт официальный интерфейс после
        # удаления Wired-подключения.
        protocol_transports: set[str] = set()

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            connect = item.get("connect")

            if isinstance(connect, dict):
                via = str(
                    connect.get("via") or ""
                ).strip()

                if via:
                    protocol_transports.add(via)

        for bridge_id, bridge_item in interface_config_data.items():
            if not isinstance(bridge_item, dict):
                continue

            if not str(bridge_id).lower().startswith("bridge"):
                continue

            for included_id in cls.collect_included_interfaces(
                bridge_item.get("include")
            ):
                if included_id in protocol_transports:
                    continue

                runtime = cls.dedicated_wan_runtime_info(
                    transport_id=included_id,
                    interface_data=interface_data,
                )

                if not runtime:
                    continue

                config_item = interface_config_data.get(included_id)

                if not isinstance(config_item, dict):
                    continue

                ip_config = config_item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )

                if isinstance(global_config, dict):
                    continue

                security = config_item.get("security-level")
                is_public = False

                if isinstance(security, dict):
                    is_public = cls.truthy(
                        security.get("public")
                    )
                else:
                    is_public = (
                        str(security or "").strip().lower()
                        == "public"
                    )

                if is_public:
                    continue

                option = dict(runtime)
                option.update(
                    {
                        "mode": "dedicated_wan",
                        "bridge_id": str(bridge_id),
                        "current_access_vid": None,
                    }
                )
                available_ports.append(option)

        def port_sort_key(
            item: dict[str, Any],
        ) -> tuple[int, int, str]:
            if str(item.get("mode") or "") == "dedicated_wan":
                return (0, 0, "WAN")

            label = str(
                item.get("port_label")
                or ""
            )

            try:
                return (1, int(label), label)
            except ValueError:
                return (1, 9999, label)

        # Удаляем возможные дубли, если одна и та же runtime-запись
        # встретилась одновременно в нескольких представлениях.
        unique_ports: dict[tuple[str, str], dict[str, Any]] = {}

        for option in available_ports:
            key = (
                str(option.get("mode") or ""),
                str(option.get("port_id") or ""),
            )
            unique_ports[key] = option

        available_ports = list(unique_ports.values())
        available_ports.sort(key=port_sort_key)

        priority_count = 0

        for item in interface_config_data.values():
            if not isinstance(item, dict):
                continue

            ip_config = item.get("ip")

            if not isinstance(ip_config, dict):
                continue

            global_config = ip_config.get(
                "global"
            )

            if (
                isinstance(global_config, dict)
                and global_config.get("order")
                is not None
            ):
                priority_count += 1

        cable_options = {
            "available": bool(available_ports),
            "ports": available_ports,
            "existing_priority_count": priority_count,
            "scope": "switch_and_dedicated_wan",
            "protocols": [
                {
                    "id": "none",
                    "title": "Не использовать",
                    "available": True,
                },
                {
                    "id": "pppoe",
                    "title": "PPPoE",
                    "available": True,
                },
                {
                    "id": "pptp",
                    "title": "PPTP",
                    "available": True,
                },
                {
                    "id": "l2tp",
                    "title": "L2TP",
                    "available": True,
                },
            ],
        }

        wireless_bands: list[dict[str, Any]] = []

        for band_id, title, master_id in (
            ("2.4", "2,4 ГГц", "WifiMaster0"),
            ("5", "5 ГГц", "WifiMaster1"),
        ):
            station_id = f"{master_id}/WifiStation0"
            runtime_item = interface_data.get(station_id)
            config_item = interface_config_data.get(station_id)
            supported = isinstance(runtime_item, dict) or isinstance(
                config_item,
                dict,
            )
            item = config_item if isinstance(config_item, dict) else {}
            runtime = runtime_item if isinstance(runtime_item, dict) else {}
            ip_config = item.get("ip")
            global_config = (
                ip_config.get("global")
                if isinstance(ip_config, dict)
                else None
            )
            ssid = str(
                item.get("ssid")
                or runtime.get("ssid")
                or ""
            ).strip()
            configured = cls.wireless_isp_configured_item(
                item,
                runtime,
            )
            wireless_bands.append(
                {
                    "id": band_id,
                    "title": title,
                    "interface_id": station_id,
                    "master_id": master_id,
                    "supported": supported,
                    "configured": configured,
                    "available": bool(supported and not configured),
                    "ssid": ssid,
                    "description": str(
                        item.get("description")
                        or runtime.get("description")
                        or ""
                    ).strip(),
                    "link": str(
                        runtime.get("link")
                        or "unknown"
                    ).strip().lower(),
                }
            )

        wireless_options = {
            "available": any(
                bool(item.get("available"))
                for item in wireless_bands
            ),
            "bands": wireless_bands,
            "existing_priority_count": priority_count,
        }

        return {
            "ethernet.cable": copy.deepcopy(cable_options),
            # Совместимость с мастером и тестами ветки 0.19.x.
            "ethernet.ipoe": copy.deepcopy(cable_options),
            "wireless_isp": wireless_options,
        }

    @classmethod
    def inspect_dedicated_wan_deletion(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """
        Проверяет удаление подключения на отдельном физическом WAN.

        Сам аппаратный Ethernet-интерфейс не удаляется. Он
        включается в домашний Bridge и становится LAN-портом,
        как это делает официальный веб-интерфейс Keenetic.
        """
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация подключения не найдена."

        source_ip = item.get("ip")
        source_global = (
            source_ip.get("global")
            if isinstance(source_ip, dict)
            else None
        )

        if not isinstance(source_global, dict):
            return {}, "Подключение не является активным интернет-каналом."

        protocol = cls.cable_protocol_from_config(name, item)

        if protocol == "none":
            transport_id = name
        else:
            connect = item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

        if not re.fullmatch(
            r"(?:GigabitEthernet|FastEthernet|Ethernet)\d+",
            transport_id,
            flags=re.IGNORECASE,
        ):
            return {}, "Подключение не использует отдельный аппаратный WAN."

        transport_item = interface_config.get(transport_id)

        if not isinstance(transport_item, dict):
            return {}, "Конфигурация аппаратного WAN не найдена."

        runtime_map = (
            interface_runtime
            if isinstance(interface_runtime, dict)
            else {}
        )
        runtime_info = cls.dedicated_wan_runtime_info(
            transport_id=transport_id,
            interface_data=runtime_map,
        )

        if not runtime_info:
            return {}, "Не удалось подтвердить физический WAN-порт."

        for _candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = cls.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                return {}, "Аппаратный WAN уже включён в локальный Bridge."

        references: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            connect = candidate.get("connect")
            via = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if protocol == "none":
            if references:
                return (
                    {},
                    "Аппаратный WAN используется протоколом "
                    + ", ".join(references)
                    + ".",
                )
        elif set(references) != {name}:
            return (
                {},
                "Аппаратный WAN используется несколькими протоколами; "
                "автоматическое удаление заблокировано.",
            )

        bridge_id = cls.find_home_bridge_id(interface_config)

        if not bridge_id:
            return {}, "Не удалось определить домашний сетевой Bridge."

        runtime_transport = runtime_map.get(transport_id)
        link = str(runtime_info.get("link") or "unknown").lower()
        connected = ""
        state = ""

        if isinstance(runtime_transport, dict):
            connected = str(
                runtime_transport.get("connected") or ""
            ).strip().lower()
            state = str(
                runtime_transport.get("state") or ""
            ).strip().lower()

        link_known = bool(
            link in {"up", "down"}
            or connected in {"yes", "no", "true", "false", "1", "0"}
            or state in {"up", "down", "connected", "disconnected"}
        )
        link_active = bool(
            link == "up"
            or connected in {"yes", "true", "1"}
            or state in {"up", "connected"}
        )

        ping_profile = cls.assigned_ping_profile(item)

        try:
            global_order = int(source_global.get("order"))
        except (TypeError, ValueError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "protocol": protocol,
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "bridge_id": bridge_id,
                "port_id": str(runtime_info.get("port_id") or transport_id),
                "port_label": "WAN",
                "port_number": str(runtime_info.get("port_number") or "0"),
                "port_link_known": link_known,
                "port_link_active": link_active,
                "port_link": link or state or "unknown",
                "port_speed": runtime_info.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": (
                    cls.interface_is_enabled(
                        runtime_map.get(name)
                    )
                    if isinstance(runtime_map.get(name), dict)
                    else item.get("up") is not False
                ),
                "transport_was_up": (
                    cls.interface_is_enabled(runtime_transport)
                    if isinstance(runtime_transport, dict)
                    else transport_item.get("up") is not False
                ),
                "description": str(
                    item.get("description")
                    or item.get("rename")
                    or name
                ).strip(),
            },
            "",
        )

    @classmethod
    def inspect_wired_pppoe_deletion(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация PPPoE не найдена."

        is_pppoe = bool(item.get("pppoe")) or bool(
            re.fullmatch(r"PPPoE\d+", name)
        )

        if not is_pppoe:
            return {}, "Это подключение не является PPPoE."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = cls.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт PPPoE.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт PPPoE не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт PPPoE одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = cls.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт PPPoE включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in cls.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт PPPoE.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "PPPoE использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "PPPoE использует неподдерживаемый порт."

        access_vid = cls.extract_port_access_vid(port_item)
        switch_mode = cls.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом PPPoE.",
            )

        home_vlan_id = cls.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = cls.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": cls.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": cls.truthy(item.get("up")),
                "transport_was_up": cls.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )

    @classmethod
    def inspect_wired_pptp_deletion(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация PPTP не найдена."

        is_pptp = bool(
            re.fullmatch(r"PPTP\d+", name)
        )

        if not is_pptp:
            return {}, "Это подключение не является PPTP."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = cls.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт PPTP.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт PPTP не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт PPTP одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = cls.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт PPTP включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in cls.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт PPTP.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "PPTP использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "PPTP использует неподдерживаемый порт."

        access_vid = cls.extract_port_access_vid(port_item)
        switch_mode = cls.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом PPTP.",
            )

        home_vlan_id = cls.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = cls.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": cls.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": cls.truthy(item.get("up")),
                "transport_was_up": cls.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )


    @classmethod
    def inspect_wired_l2tp_deletion(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация L2TP не найдена."

        is_l2tp = bool(
            re.fullmatch(r"L2TP\d+", name)
        )

        if not is_l2tp:
            return {}, "Это подключение не является L2TP."

        connect = item.get("connect")
        transport_id = (
            str(connect.get("via") or "").strip()
            if isinstance(connect, dict)
            else ""
        )
        parts = cls.extract_vlan_interface_parts(
            transport_id
        )

        if parts is None:
            return (
                {},
                "Не удалось определить проводной транспорт L2TP.",
            )

        parent_id, vlan_id = parts
        transport_item = interface_config.get(
            transport_id
        )

        if not isinstance(transport_item, dict):
            return {}, "Транспорт L2TP не найден."

        transport_ip = transport_item.get("ip")
        transport_global = (
            transport_ip.get("global")
            if isinstance(transport_ip, dict)
            else None
        )

        if isinstance(transport_global, dict):
            return (
                {},
                "Транспорт L2TP одновременно используется как "
                "самостоятельный интернет-канал; автоматическое "
                "удаление заблокировано.",
            )

        included_by: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            included = cls.collect_included_interfaces(
                candidate.get("include")
            )

            if transport_id in included:
                included_by.append(str(candidate_id))

        if included_by:
            return (
                {},
                "Транспорт L2TP включён в другой сетевой интерфейс "
                "и не может быть безопасно удалён автоматически.",
            )

        references = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_connect = candidate.get("connect")
            via = (
                str(candidate_connect.get("via") or "").strip()
                if isinstance(candidate_connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if references != [name] and set(references) != {name}:
            return (
                {},
                "Транспорт используется несколькими подключениями; "
                "автоматическое удаление заблокировано.",
            )

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in cls.extract_port_roles(port_item):
                if (
                    str(role.get("role") or "").strip().lower()
                    == "inet"
                    and str(role.get("ifor") or "").strip()
                    == transport_id
                ):
                    bindings.append((str(port_id), port_item))
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт L2TP.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, "L2TP использует неподдерживаемый порт."

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, "L2TP использует неподдерживаемый порт."

        access_vid = cls.extract_port_access_vid(port_item)
        switch_mode = cls.extract_port_switch_mode(port_item)

        if switch_mode != "access" or access_vid != vlan_id:
            return (
                {},
                "Назначение VLAN физического порта не совпадает "
                "с транспортом L2TP.",
            )

        home_vlan_id = cls.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(port_item.get("rename") or "").strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = cls.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )
        assignment = item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile") or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        ip_config = item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        try:
            global_order = int(global_config.get("order"))
        except (TypeError, ValueError, AttributeError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(item),
                "transport_id": transport_id,
                "transport_item": copy.deepcopy(transport_item),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": cls.extract_port_roles(port_item),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(runtime_state.get("known")),
                "port_link_active": bool(runtime_state.get("active")),
                "port_link": str(
                    runtime_state.get("link") or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": cls.truthy(item.get("up")),
                "transport_was_up": cls.truthy(
                    transport_item.get("up")
                ),
                "description": str(
                    item.get("description") or name
                ).strip(),
            },
            "",
        )


    @staticmethod
    def apply_connection_delete_context(
        connection: dict[str, Any],
        *,
        context: dict[str, Any],
        delete_kind: str,
        dedicated: bool = False,
    ) -> None:
        """Записывает единые метаданные безопасного удаления WAN."""
        connection["delete_supported"] = True
        connection["delete_kind"] = str(delete_kind)
        connection["delete_port_id"] = context.get("port_id")
        connection["delete_port_label"] = context.get("port_label")
        connection["delete_port_number"] = context.get("port_number")
        connection["delete_port_display_name"] = (
            context.get("port_display_name")
            or (
                "WAN (порт "
                + str(context.get("port_number") or "0")
                + ")"
                if dedicated
                else "LAN" + str(context.get("port_label") or "?")
            )
        )
        connection["provider_port_label"] = context.get("port_label")
        connection["provider_port_id"] = context.get("port_id")
        connection["provider_port_number"] = context.get("port_number")
        connection["delete_transport_id"] = context.get("transport_id")
        connection["delete_bridge_id"] = context.get("bridge_id")
        connection["delete_dedicated_wan"] = bool(dedicated)

        if context.get("home_vlan_id") is not None:
            connection["delete_restore_vlan"] = context.get("home_vlan_id")
        else:
            connection.pop("delete_restore_vlan", None)

        connection["delete_port_link_known"] = bool(
            context.get("port_link_known")
        )
        connection["delete_port_link_active"] = bool(
            context.get("port_link_active")
        )
        connection["delete_port_link"] = str(
            context.get("port_link") or "unknown"
        )
        connection["delete_port_speed"] = context.get("port_speed")
        connection["delete_reason"] = ""

    @staticmethod
    def clear_connection_delete_context(
        connection: dict[str, Any],
        reason: str,
    ) -> None:
        for key in (
            "delete_kind",
            "delete_port_id",
            "delete_port_label",
            "delete_port_number",
            "delete_port_display_name",
            "delete_restore_vlan",
            "delete_port_link_known",
            "delete_port_link_active",
            "delete_port_link",
            "delete_port_speed",
            "delete_transport_id",
            "delete_bridge_id",
            "delete_dedicated_wan",
        ):
            connection.pop(key, None)

        connection["delete_supported"] = False
        connection["delete_reason"] = str(reason or "")

    @classmethod
    def wireless_isp_band_from_interface(
        cls,
        interface_id: str,
    ) -> str:
        return WirelessIspService.wireless_isp_band_from_interface(
            interface_id
        )

    @classmethod
    def wireless_isp_configured_item(
        cls,
        item: dict[str, Any],
        runtime_item: dict[str, Any] | None = None,
    ) -> bool:
        return WirelessIspService.wireless_isp_configured_item(
            item,
            runtime_item,
        )

    def enrich_wireless_isp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        return self.wireless_isp_service.enrich_wireless_isp_details(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
        )

    def enrich_wired_pppoe_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_pppoe = (
                bool(config_item.get("pppoe"))
                or "pppoe" in runtime_type
                or bool(re.fullmatch(r"PPPoE\d+", interface_id))
            )

            if not is_pppoe:
                continue

            connection["provider_protocol"] = "PPPoE"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            pppoe = config_item.get("pppoe")

            if isinstance(pppoe, dict):
                service = str(pppoe.get("service") or "").strip()

                if service:
                    connection["pppoe_service"] = service

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.inspect_wired_pppoe_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.pppoe"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )

    def enrich_wired_pptp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_pptp = (
                bool(config_item.get("pptp"))
                or "pptp" in runtime_type
                or bool(re.fullmatch(r"PPTP\d+", interface_id))
            )

            if not is_pptp:
                continue

            connection["provider_protocol"] = "PPTP"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            server = str(
                config_item.get("peer") or ""
            ).strip()

            if server:
                connection["provider_server"] = server

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.inspect_wired_pptp_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.pptp"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )


    def enrich_wired_l2tp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)
            runtime_item = interface_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            runtime_type = (
                str(runtime_item.get("type") or "").lower()
                if isinstance(runtime_item, dict)
                else ""
            )
            is_l2tp = (
                bool(config_item.get("l2tp"))
                or "l2tp" in runtime_type
                or bool(re.fullmatch(r"L2TP\d+", interface_id))
            )

            if not is_l2tp:
                continue

            connection["provider_protocol"] = "L2TP"
            authentication = config_item.get("authentication")

            if isinstance(authentication, dict):
                identity = str(
                    authentication.get("identity") or ""
                ).strip()

                if identity:
                    connection["provider_username"] = identity

            server = str(
                config_item.get("peer") or ""
            ).strip()

            if server:
                connection["provider_server"] = server

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if transport_id:
                connection["transport_interface_id"] = transport_id

            context, reason = self.inspect_wired_l2tp_deletion(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            dedicated = False

            if not context:
                context, dedicated_reason = (
                    self.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(context)

                if not context and dedicated_reason:
                    reason = reason or dedicated_reason

            if context:
                self.apply_connection_delete_context(
                    connection,
                    context=context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.l2tp"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    reason,
                )


    @staticmethod
    def cable_protocol_from_config(
        interface_id: str,
        item: dict[str, Any],
    ) -> str:
        name = str(interface_id or "").strip()

        if item.get("pppoe") or re.fullmatch(
            r"PPPoE\d+", name, flags=re.IGNORECASE
        ):
            return "pppoe"
        if item.get("pptp") or re.fullmatch(
            r"PPTP\d+", name, flags=re.IGNORECASE
        ):
            return "pptp"
        if item.get("l2tp") or re.fullmatch(
            r"L2TP\d+", name, flags=re.IGNORECASE
        ):
            return "l2tp"
        return "none"

    @staticmethod
    def is_wired_transport_interface(interface_id: str) -> bool:
        return bool(
            re.match(
                r"^(?:GigabitEthernet|FastEthernet|Ethernet)\d+",
                str(interface_id or "").strip(),
                flags=re.IGNORECASE,
            )
        )

    @classmethod
    def inspect_cable_protocol_change(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)

        if not isinstance(item, dict):
            return {}, "Конфигурация подключения не найдена."

        current_protocol = cls.cable_protocol_from_config(
            name,
            item,
        )

        if current_protocol == "none":
            transport_id = name
        else:
            connect = item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

        if not transport_id:
            return {}, "Не удалось определить транспорт подключения."

        if not cls.is_wired_transport_interface(transport_id):
            return {}, "Смена типа доступна только для проводного WAN."

        transport_item = interface_config.get(transport_id)

        if not isinstance(transport_item, dict):
            return {}, "Конфигурация проводного транспорта не найдена."

        source_ip = item.get("ip")
        source_global = (
            source_ip.get("global")
            if isinstance(source_ip, dict)
            else None
        )

        if not isinstance(source_global, dict):
            return {}, "Подключение не является активным интернет-каналом."

        references: list[str] = []

        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue

            candidate_protocol = cls.cable_protocol_from_config(
                str(candidate_id),
                candidate,
            )

            if candidate_protocol == "none":
                continue

            connect = candidate.get("connect")
            via = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if via == transport_id:
                references.append(str(candidate_id))

        if current_protocol == "none":
            if references:
                return (
                    {},
                    "Транспорт уже используется протоколом "
                    + ", ".join(references)
                    + ".",
                )
        elif any(reference != name for reference in references):
            return (
                {},
                "Транспорт используется несколькими протоколами; "
                "автоматическая смена типа заблокирована.",
            )

        try:
            global_order = int(source_global.get("order"))
        except (TypeError, ValueError):
            return {}, "Не удалось определить приоритет подключения."

        port_id = ""
        port_label = ""
        port_link_known = False
        port_link_active = False
        port_link = "unknown"

        bindings: list[tuple[str, dict[str, Any]]] = []
        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue
            for role in cls.extract_port_roles(candidate):
                if (
                    str(role.get("role") or "").strip().lower() == "inet"
                    and str(role.get("ifor") or "").strip() == transport_id
                ):
                    bindings.append((str(candidate_id), candidate))
                    break

        if len(bindings) == 1:
            port_id, port_item = bindings[0]
            port_label = str(port_item.get("rename") or "").strip()
            parent_id = port_id.rsplit("/", 1)[0] if "/" in port_id else ""

            if not port_label and "/" in port_id:
                suffix = port_id.rsplit("/", 1)[1]
                if suffix.isdigit():
                    port_label = str(int(suffix) + 1)

            runtime_state = cls.runtime_ethernet_port_state(
                interface_runtime=(
                    interface_runtime
                    if isinstance(interface_runtime, dict)
                    else {}
                ),
                port_id=port_id,
                parent_id=parent_id,
            )
            port_link_known = bool(runtime_state.get("known"))
            port_link_active = bool(runtime_state.get("active"))
            port_link = str(runtime_state.get("link") or "unknown")
        else:
            runtime_item = (
                interface_runtime.get(transport_id)
                if isinstance(interface_runtime, dict)
                else None
            )
            if isinstance(runtime_item, dict):
                link = str(runtime_item.get("link") or "").strip().lower()
                connected = str(
                    runtime_item.get("connected") or ""
                ).strip().lower()
                state = str(runtime_item.get("state") or "").strip().lower()
                port_link_known = (
                    link in {"up", "down"}
                    or connected in {"yes", "no", "true", "false", "1", "0"}
                    or state in {"up", "down", "connected", "disconnected"}
                )
                port_link_active = (
                    link == "up"
                    or connected in {"yes", "true", "1"}
                    or state in {"up", "connected"}
                )
                port_link = link or state or "unknown"

            if re.fullmatch(
                r"(?:GigabitEthernet|FastEthernet|Ethernet)\d+",
                transport_id,
                flags=re.IGNORECASE,
            ):
                port_label = "WAN"

        return (
            {
                "interface_id": name,
                "current_protocol": current_protocol,
                "transport_id": transport_id,
                "global_order": global_order,
                "port_id": port_id,
                "port_label": port_label,
                "port_link_known": port_link_known,
                "port_link_active": port_link_active,
                "port_link": port_link,
            },
            "",
        )

    def enrich_cable_protocol_change_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            context, reason = self.inspect_cable_protocol_change(
                interface_id=interface_id,
                interface_config=interface_config_data,
                interface_runtime=interface_data,
            )
            connection["protocol_change_supported"] = bool(context)

            if not context:
                connection["protocol_change_reason"] = reason
                continue

            connection["protocol_change_reason"] = ""
            connection["protocol_change_current"] = context[
                "current_protocol"
            ]
            connection["protocol_change_transport_id"] = context[
                "transport_id"
            ]
            connection["protocol_change_port_id"] = context["port_id"]
            connection["protocol_change_port_label"] = context[
                "port_label"
            ]
            connection["protocol_change_link_known"] = bool(
                context.get("port_link_known")
            )
            connection["protocol_change_link_active"] = bool(
                context.get("port_link_active")
            )
            connection["protocol_change_link"] = str(
                context.get("port_link") or "unknown"
            )

    @staticmethod
    def group_authenticated_cable_connections(
        *,
        connections: list[dict[str, Any]],
        interface_config_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        by_id = {
            str(connection.get("interface_id") or "").strip(): connection
            for connection in connections
        }
        hidden_transports: set[str] = set()

        for connection in connections:
            interface_id = str(
                connection.get("interface_id") or ""
            ).strip()
            config_item = interface_config_data.get(interface_id)

            if not isinstance(config_item, dict):
                continue

            if not (
                config_item.get("pppoe")
                or re.fullmatch(r"PPPoE\d+", interface_id)
                or re.fullmatch(r"PPTP\d+", interface_id)
                or re.fullmatch(r"L2TP\d+", interface_id)
            ):
                continue

            connect = config_item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )

            if not transport_id:
                continue

            connection["transport_interface_id"] = transport_id
            transport = by_id.get(transport_id)
            transport_config = interface_config_data.get(transport_id)
            transport_is_independent = False

            if isinstance(transport_config, dict):
                transport_ip = transport_config.get("ip")
                transport_is_independent = (
                    isinstance(transport_ip, dict)
                    and isinstance(transport_ip.get("global"), dict)
                )

            reference_count = 0

            for candidate in interface_config_data.values():
                if not isinstance(candidate, dict):
                    continue

                candidate_connect = candidate.get("connect")

                if (
                    isinstance(candidate_connect, dict)
                    and str(candidate_connect.get("via") or "").strip()
                    == transport_id
                ):
                    reference_count += 1

            if isinstance(transport, dict):
                connection["transport_name"] = str(
                    transport.get("name")
                    or transport.get("description")
                    or transport_id
                )
                transport_ipv4 = (
                    transport.get("ipv4")
                    or transport.get("ip")
                )

                try:
                    parsed_transport_ipv4 = ipaddress.ip_address(
                        str(transport_ipv4 or "").split("/", 1)[0]
                    )
                except ValueError:
                    parsed_transport_ipv4 = None

                if (
                    parsed_transport_ipv4 is not None
                    and parsed_transport_ipv4.version == 4
                ):
                    connection["transport_ipv4"] = str(
                        transport_ipv4
                    )
                elif transport.get("ipv4_dhcp"):
                    connection["transport_ipv4"] = "DHCP (ожидание)"

                if (
                    not transport_is_independent
                    and reference_count == 1
                ):
                    hidden_transports.add(transport_id)

        return [
            connection
            for connection in connections
            if str(
                connection.get("interface_id") or ""
            ).strip()
            not in hidden_transports
        ]

    def update_wifi_client_activity(
        self,
        networks: list[dict[str, Any]],
    ) -> None:
        """Совместимый фасад WiFiNetworkService."""
        self.wifi_network_service.update_client_activity(networks)

    @staticmethod
    def safe_float(value: Any) -> float:
        """Совместимый числовой helper."""
        return WiFiNetworkService._safe_float(value)

    def merge_known_wifi_networks(
        self,
        current_networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Совместимый фасад WiFiNetworkService."""
        return self.wifi_network_service.merge_known_networks(
            current_networks
        )

    @staticmethod
    def wifi_network_cache_key(
        network: dict[str, Any],
    ) -> str:
        """Совместимый фасад WiFiNetworkService."""
        return WiFiNetworkService.network_cache_key(network)

    @staticmethod
    def sort_wifi_networks(
        networks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Совместимый фасад WiFiNetworkService."""
        return WiFiNetworkService.sort_networks(networks)

    def create_wifi_network(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад создания Wi-Fi сети."""
        return self.wifi_network_service.create_network(settings)

    def update_wifi_network(
        self,
        interface_ids: list[str],
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад изменения Wi-Fi сети."""
        return self.wifi_network_service.update_network(
            interface_ids,
            settings,
        )

    def set_wifi_network_enabled(
        self,
        network_reference: Any,
        enabled: bool,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад состояния логической Wi-Fi сети."""
        return self.wifi_network_service.set_network_enabled(
            network_reference,
            enabled,
        )

    def delete_wifi_network(
        self,
        network_reference: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления логической Wi-Fi сети."""
        return self.wifi_network_service.delete_network(
            network_reference
        )

    def extract_internet_connections(
        self,
        internet_data: dict[str, Any],
        interface_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Совместимый фасад. Вся классификация выполняется
        отдельным TopologyEngine.
        """
        return TopologyEngine(
            internet_data=internet_data,
            interface_data=interface_data,
            manually_disabled=(
                self._manually_disabled_interfaces
            ),
        ).build_connections()

    @staticmethod
    def interface_is_enabled(
        item: dict[str, Any],
    ) -> bool:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.interface_is_enabled(item)

    @staticmethod
    def interface_is_up(
        item: dict[str, Any],
    ) -> bool:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.interface_is_up(item)

    @staticmethod
    def truthy(
        value: Any,
    ) -> bool:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.truthy(value)

    @staticmethod
    def is_vpn_interface(
        interface_type: str,
        interface_id: str,
    ) -> bool:
        text = (
            f"{interface_type} {interface_id}"
        ).lower()

        return any(
            marker in text
            for marker in (
                "openvpn",
                "wireguard",
                "zerotier",
                "l2tp",
                "pptp",
                "sstp",
                "ipsec",
                "gre",
                "vpn",
            )
        )

    @staticmethod
    def wireless_isp_display_name(
        interface_id: str,
        interface_name: str,
        item: dict[str, Any],
    ) -> str:
        return WirelessIspService.wireless_isp_display_name(
            interface_id,
            interface_name,
            item,
        )

    @staticmethod
    def is_wireless_isp_interface(
        interface_type: str,
        interface_id: str,
        interface_name: str,
    ) -> bool:
        return WirelessIspService.is_wireless_isp_interface(
            interface_type,
            interface_id,
            interface_name,
        )

    @staticmethod
    def is_local_or_service_interface(
        interface_type: str,
        interface_id: str,
        interface_name: str,
        description: str,
    ) -> bool:
        text = " ".join(
            [
                interface_type,
                interface_id,
                interface_name,
                description,
            ]
        ).lower()

        # WifiStation — это Wireless ISP, а не точка доступа.
        if (
            "wifistation" in text
            or "wirelessisp" in text
            or "wireless isp" in text
        ):
            return False

        return any(
            marker in text
            for marker in (
                "bridge",
                "home network",
                "guest network",
                "accesspoint",
                "access point",
                "wifimaster",
                "loopback",
                "localhost",
                "ndms",
                "dummy",
            )
        )

    def is_internet_candidate(
        self,
        item: dict[str, Any],
        interface_id: str,
        interface_type: str,
        is_wireless_isp: bool,
        has_inet_port: bool,
    ) -> bool:
        """
        Определяет именно интернет-подключение, а не любой
        Ethernet-интерфейс.

        Пользовательское имя вроде "ISP" не используется
        как доказательство WAN-роли.
        """
        if is_wireless_isp:
            return True

        if self.truthy(item.get("global")):
            return True

        if self.truthy(item.get("defaultgw")):
            return True

        if has_inet_port:
            return True

        type_text = interface_type.lower()
        id_text = interface_id.lower()

        # Эти типы сами по себе являются подключениями к
        # провайдеру, даже без физического Ethernet-порта.
        return any(
            marker in f"{type_text} {id_text}"
            for marker in (
                "pppoe",
                "lte",
                "modem",
                "usbmodem",
                "cellular",
            )
        )

    def detect_dedicated_wan_port(
        self,
        item: dict[str, Any],
        interface_id: str,
        interface_type: str,
    ) -> dict[str, str]:
        """
        Определяет выделенный WAN-интерфейс Keenetic.

        На некоторых моделях WAN представлен напрямую как
        GigabitEthernet1 с вложенным объектом port, без связи
        role=inet. Пользовательское имя у него часто равно ISP.
        """
        type_text = interface_type.lower()

        if not any(
            marker in type_text
            for marker in (
                "gigabitethernet",
                "ethernet",
            )
        ):
            return {}

        if not (
            self.truthy(item.get("global"))
            or self.truthy(item.get("defaultgw"))
        ):
            return {}

        port = item.get("port")

        if not isinstance(port, dict):
            return {}

        port_id = str(
            port.get("id")
            or ""
        ).strip()

        # Для выделенного WAN-порта номер часто имеет
        # техническое значение 0, поэтому не показываем его
        # как пользовательский номер порта.
        return {
            "id": port_id or interface_id,
            "label": "",
            "port_role": "WAN-порт",
        }

    def connection_display_name(
        self,
        interface_id: str,
        interface_name: str,
        interface_type: str,
        is_wireless_isp: bool,
        item: dict[str, Any],
        physical_info: dict[str, str],
    ) -> str:
        """
        Формирует название по фактической конфигурации.

        Номер порта не задаётся вручную. Он берётся из
        физического интерфейса, связанного через role=inet.
        """
        if is_wireless_isp:
            return self.wireless_isp_display_name(
                interface_id=interface_id,
                interface_name=interface_name,
                item=item,
            )

        if physical_info:
            return self.format_physical_port(
                physical_info
            )

        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if "pppoe" in text:
            return "PPPoE-подключение"

        if any(
            marker in text
            for marker in (
                "lte",
                "modem",
                "cellular",
            )
        ):
            return "Мобильный интернет"

        return interface_name or interface_id

    def connection_transport_type(
        self,
        interface_id: str,
        interface_type: str,
        is_wireless_isp: bool,
        item: dict[str, Any],
        physical_info: dict[str, str],
    ) -> str:
        """
        Возвращает понятный тип физического подключения.
        """
        text = (
            f"{interface_id} {interface_type}"
        ).lower()

        if is_wireless_isp:
            if "wifimaster0" in text:
                return "Wireless ISP 2.4 ГГц"
            if "wifimaster1" in text:
                return "Wireless ISP 5 ГГц"
            return "Wireless ISP"

        if "pppoe" in text:
            return "PPPoE"

        if any(
            marker in text
            for marker in (
                "lte",
                "modem",
                "cellular",
            )
        ):
            return "Mobile"

        if physical_info:
            role = physical_info.get(
                "port_role",
                "WAN",
            )
            return f"Ethernet ({role})"

        if any(
            marker in text
            for marker in (
                "gigabitethernet",
                "ethernet",
                "vlan",
            )
        ):
            return "Ethernet"

        return interface_type or "Интернет"

    @staticmethod
    def format_physical_port(
        physical_info: dict[str, str],
    ) -> str:
        if not physical_info:
            return "—"

        role = physical_info.get(
            "port_role",
            "Интернет-порт",
        )

        label = str(
            physical_info.get("label")
            or ""
        ).strip()

        physical_id = str(
            physical_info.get("id")
            or ""
        ).strip()

        # Если Keenetic не передал label, пытаемся получить
        # номер из физического ID, например:
        # GigabitEthernet0/3 -> порт 4.
        if not label or label == "—":
            match = re.search(
                r"/(\d+)$",
                physical_id,
            )

            if match:
                label = str(
                    int(match.group(1)) + 1
                )

        if label and label != "—":
            if "порт" in role.lower():
                return f"{role} {label}"

            return f"{role}, порт {label}"

        return role

    @staticmethod
    def physical_parent_interface(
        interface_id: str,
        item: dict[str, Any],
    ) -> str | None:
        via = item.get("via")

        if isinstance(via, str) and via.strip():
            return via.strip()

        if "/Vlan" in interface_id:
            return interface_id.split(
                "/Vlan",
                maxsplit=1,
            )[0]

        if "/WifiStation" in interface_id:
            return interface_id.split(
                "/WifiStation",
                maxsplit=1,
            )[0]

        return None

    @staticmethod
    def extract_tunnel_gateway(
        item: dict[str, Any],
    ) -> str:
        for key in (
            "remote-endpoint-address",
            "via",
            "peer",
        ):
            value = item.get(key)

            if isinstance(value, str):
                value = value.strip()

                if value:
                    return value

        return "—"

    @staticmethod
    def extract_interface_gateway(
        item: dict[str, Any],
    ) -> str:
        for key in (
            "gateway",
            "peer",
            "remote-address",
            "via",
        ):
            value = item.get(key)

            if isinstance(value, str):
                value = value.strip()

                if value:
                    return value

        return "—"

    @staticmethod
    def interface_priority(item: dict[str, Any]) -> int:
        """Совместимый фасад PriorityService."""
        return PriorityService.interface_priority(item)

    @staticmethod
    def connection_role_order(interface_type: str) -> int:
        """Совместимый фасад PriorityService."""
        return PriorityService.connection_role_order(interface_type)

    def build_physical_port_map(
        self,
        interface_data: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        """
        Строит связь логического интернет-интерфейса
        с физическим Ethernet-портом.

        Пример:
            GigabitEthernet0/3
                role: inet
                for: GigabitEthernet0/Vlan4

        Результат:
            GigabitEthernet0/Vlan4 ->
                WAN, порт 4
        """
        result: dict[
            str,
            dict[str, str],
        ] = {}

        for item in interface_data.values():
            if not isinstance(item, dict):
                continue

            role_entries = item.get("role")

            if not isinstance(role_entries, list):
                continue

            physical_id = str(
                item.get("id")
                or ""
            ).strip()

            label = str(
                item.get("label")
                or item.get("interface-name")
                or ""
            ).strip()

            is_public = self.truthy(
                item.get("public")
            )

            for role_item in role_entries:
                if not isinstance(role_item, dict):
                    continue

                role_name = str(
                    role_item.get("role")
                    or ""
                ).strip().lower()

                if role_name != "inet":
                    continue

                target = str(
                    role_item.get("for")
                    or ""
                ).strip()

                if not target:
                    continue

                # Порт, назначенный для inet, считаем WAN.
                # Даже если физически это переназначенный LAN.
                port_role = (
                    "WAN"
                    if is_public
                    else "Интернет-порт"
                )

                result[target] = {
                    "id": physical_id,
                    "label": label,
                    "port_role": port_role,
                }

        return result

    def collect_connection_candidates(
        self,
        data: Any,
        output: list[dict[str, Any]],
    ) -> None:
        if isinstance(data, dict):
            keys = {
                str(key).lower()
                for key in data.keys()
            }

            connection_markers = {
                "interface",
                "name",
                "ifname",
                "address",
                "ip",
                "status",
                "state",
                "connected",
                "online",
                "gateway",
                "via",
            }

            if keys & connection_markers:
                output.append(data)

            for value in data.values():
                self.collect_connection_candidates(
                    value,
                    output,
                )

        elif isinstance(data, list):
            for item in data:
                self.collect_connection_candidates(
                    item,
                    output,
                )

    def connection_is_active(
        self,
        item: dict[str, Any],
        status: str | None,
    ) -> bool:
        positive = {
            "up",
            "online",
            "connected",
            "running",
            "ready",
            "yes",
            "true",
            "1",
            "active",
        }

        negative = {
            "down",
            "offline",
            "disconnected",
            "stopped",
            "no",
            "false",
            "0",
            "inactive",
        }

        if status:
            normalized = status.strip().lower()

            if normalized in positive:
                return True

            if normalized in negative:
                return False

        for key in (
            "connected",
            "online",
            "up",
            "running",
            "active",
        ):
            value = item.get(key)

            if isinstance(value, bool):
                return value

            if isinstance(value, (int, float)):
                return value != 0

            if isinstance(value, str):
                normalized = value.strip().lower()

                if normalized in positive:
                    return True

                if normalized in negative:
                    return False

        # Наличие адреса обычно означает активный интерфейс.
        address = self.first_text(
            item,
            (
                "address",
                "ip",
                "ip-address",
                "ip_address",
                "local-address",
                "local_address",
            ),
        )

        if address and address not in {
            "0.0.0.0",
            "::",
            "—",
        }:
            gateway = self.first_text(
                item,
                (
                    "gateway",
                    "default-gateway",
                    "default_gateway",
                    "peer",
                    "remote-address",
                ),
            )

            if gateway and gateway not in {
                "0.0.0.0",
                "::",
                "—",
            }:
                return True

            for key in (
                "default",
                "default-route",
                "default_route",
                "primary",
                "main",
            ):
                value = item.get(key)

                if isinstance(value, bool) and value:
                    return True

                if isinstance(value, (int, float)) and value != 0:
                    return True

                if isinstance(value, str):
                    if value.strip().lower() in {
                        "yes",
                        "true",
                        "1",
                        "primary",
                        "main",
                        "default",
                    }:
                        return True

            # Для физического WAN/LAN-порта с назначенной
            # ролью интернет-подключения IP уже достаточен.
            name = self.first_text(
                item,
                (
                    "interface",
                    "name",
                    "id",
                    "ifname",
                    "via",
                    "device",
                ),
            ) or ""

            description = self.first_text(
                item,
                (
                    "description",
                    "title",
                    "label",
                    "provider",
                    "type",
                ),
            ) or ""

            interface_text = (
                f"{name} {description}"
            ).lower()

            if any(
                marker in interface_text
                for marker in (
                    "wan",
                    "ethernet",
                    "gigabitethernet",
                    "pppoe",
                    "isp",
                    "internet",
                    "интернет",
                )
            ):
                return True

        return False

    @staticmethod
    def first_text(
        data: dict[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        normalized = {
            str(key).lower(): value
            for key, value in data.items()
        }

        for key in keys:
            value = normalized.get(
                key.lower()
            )

            if isinstance(value, bool):
                return (
                    "online"
                    if value
                    else "offline"
                )

            if isinstance(
                value,
                (str, int, float),
            ):
                text = str(value).strip()

                if text:
                    return text

        return None

    @staticmethod
    def normalize_status(
        status: str | None,
    ) -> str:
        if not status:
            return "Online"

        normalized = status.strip().lower()

        if normalized in {
            "up",
            "online",
            "connected",
            "running",
            "ready",
            "yes",
            "true",
            "1",
            "active",
        }:
            return "Online"

        return status

    def merge_configured_global_connections(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Совместимый фасад PriorityService."""
        return self.priority_service.merge_configured_global_connections(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
        )

    def filter_real_internet_connections(
        self,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Оставляет только реальные интернет-каналы.

        Wireless ISP сохраняется, если WifiStation действительно
        настроен или активен. Пустые служебные WifiStation без
        адреса, подключения, global/defaultgw и priority исключаются.
        """
        result: list[dict[str, Any]] = []

        for connection in connections:
            if not isinstance(connection, dict):
                continue

            interface_id = str(
                connection.get("interface_id")
                or connection.get("id")
                or ""
            ).strip()

            item = interface_data.get(
                interface_id,
                {},
            )

            if not isinstance(item, dict):
                item = {}

            interface_type = str(
                item.get("type")
                or connection.get("type")
                or connection.get("connection_type")
                or ""
            ).strip()

            interface_name = str(
                item.get("interface-name")
                or connection.get("interface_name")
                or ""
            ).strip()

            if self.is_wireless_isp_interface(
                interface_type,
                interface_id,
                interface_name,
            ):
                if self.is_configured_wireless_isp(
                    item,
                    connection,
                ):
                    result.append(connection)
                continue

            if self.is_real_wan_connection(
                item,
                connection,
                interface_id,
                interface_type,
            ):
                result.append(connection)

        return result

    def is_configured_wireless_isp(
        self,
        item: dict[str, Any],
        connection: dict[str, Any],
    ) -> bool:
        return self.wireless_isp_service.is_configured_wireless_isp(
            item,
            connection,
        )

    def is_real_wan_connection(
        self,
        item: dict[str, Any],
        connection: dict[str, Any],
        interface_id: str,
        interface_type: str,
    ) -> bool:
        text = " ".join(
            (
                interface_id,
                interface_type,
                str(item.get("description") or ""),
                str(connection.get("type") or ""),
                str(connection.get("role") or ""),
            )
        ).lower()

        if any(
            marker in text
            for marker in (
                "pppoe",
                "openvpn",
                "wireguard",
                "zerotier",
                "l2tp",
                "pptp",
                "sstp",
                "ipsec",
                "lte",
                "modem",
                "cellular",
            )
        ):
            return True

        if self.truthy(item.get("global")):
            return True

        if self.truthy(item.get("defaultgw")):
            return True

        security_level = str(
            item.get("security-level")
            or ""
        ).strip().lower()

        if security_level == "public":
            return True

        role = item.get("role")

        if isinstance(role, list):
            for role_item in role:
                if isinstance(role_item, dict):
                    role_name = str(
                        role_item.get("role")
                        or ""
                    ).strip().lower()

                    if role_name == "inet":
                        return True

        return False

    @staticmethod
    def extract_valid_priority(item: dict[str, Any]) -> int | None:
        """Совместимый фасад PriorityService."""
        return PriorityService.extract_valid_priority(item)

    @staticmethod
    def prefix_from_ipv4_mask(
        value: Any,
    ) -> int | None:
        text = str(value or "").strip()

        if not text:
            return None

        if text.startswith("/"):
            text = text[1:]

        try:
            prefix = int(text)
        except ValueError:
            try:
                network = ipaddress.IPv4Network(
                    f"0.0.0.0/{text}"
                )
            except ValueError:
                return None

            return int(network.prefixlen)

        if 0 <= prefix <= 32:
            return prefix

        return None

    @classmethod
    def extract_configured_ipv4(
        cls,
        address_config: Any,
    ) -> tuple[bool, str, int | None]:
        """
        Возвращает DHCP, статический адрес и длину префикса
        из show/sc/interface -> ip -> address.
        """
        if isinstance(address_config, dict):
            if cls.truthy(
                address_config.get("dhcp")
            ):
                return True, "", None

            raw_address = (
                address_config.get("address")
                or address_config.get("ip")
                or address_config.get("local")
                or ""
            )
            raw_mask = (
                address_config.get("mask")
                or address_config.get("prefix")
                or address_config.get(
                    "prefix-length"
                )
            )
        elif isinstance(address_config, str):
            raw_address = address_config
            raw_mask = None
        else:
            return False, "", None

        address_text = str(
            raw_address or ""
        ).strip()

        if not address_text:
            return False, "", None

        if "/" in address_text:
            try:
                interface = ipaddress.IPv4Interface(
                    address_text
                )
            except ValueError:
                return False, "", None

            return (
                False,
                str(interface.ip),
                int(interface.network.prefixlen),
            )

        try:
            address = ipaddress.IPv4Address(
                address_text
            )
        except ValueError:
            return False, "", None

        prefix = cls.prefix_from_ipv4_mask(
            raw_mask
        )

        return False, str(address), prefix

    @staticmethod
    def normalize_ipv4_text(
        value: Any,
    ) -> str:
        text = str(value or "").strip()

        if not text:
            return ""

        if "/" in text:
            text = text.split("/", 1)[0]

        try:
            address = ipaddress.ip_address(
                text
            )
        except ValueError:
            return ""

        if not isinstance(
            address,
            ipaddress.IPv4Address,
        ):
            return ""

        return str(address)

    @staticmethod
    def normalize_mac_address(
        value: Any,
    ) -> str:
        compact = "".join(
            character
            for character in str(value or "")
            if character.isalnum()
        ).upper()

        if (
            len(compact) != 12
            or any(
                character not in "0123456789ABCDEF"
                for character in compact
            )
        ):
            return ""

        return ":".join(
            compact[index:index + 2]
            for index in range(0, 12, 2)
        )

    @classmethod
    def extract_configured_mac_settings(
        cls,
        config_item: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """
        Возвращает поддержку, режим и ручной MAC
        из show/sc/interface.
        """
        mac_config = config_item.get("mac")

        if not isinstance(mac_config, dict):
            return False, "factory", ""

        address_config = mac_config.get(
            "address"
        )

        if address_config is None:
            return False, "factory", ""

        if isinstance(address_config, dict):
            factory = address_config.get(
                "factory"
            )

            if isinstance(factory, dict):
                if cls.truthy(
                    factory.get("wan")
                ):
                    return True, "factory", ""

            for key in (
                "address",
                "mac",
                "value",
            ):
                normalized = cls.normalize_mac_address(
                    address_config.get(key)
                )

                if normalized:
                    return True, "manual", normalized

        normalized = cls.normalize_mac_address(
            address_config
        )

        if normalized:
            return True, "manual", normalized

        return True, "factory", ""

    @classmethod
    def validate_manual_mac(
        cls,
        value: Any,
    ) -> tuple[bool, str]:
        normalized = cls.normalize_mac_address(
            value
        )

        if not normalized:
            return False, ""

        octets = [
            int(part, 16)
            for part in normalized.split(":")
        ]

        if normalized in {
            "00:00:00:00:00:00",
            "FF:FF:FF:FF:FF:FF",
        }:
            return False, normalized

        if octets[0] & 1:
            return False, normalized

        return True, normalized

    def build_interface_mac_command(
        self,
        interface_id: str,
        mac_settings: dict[str, Any],
    ) -> str:
        cli_name = self.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.quote_cli_argument(
            cli_name
        )
        mode = str(
            mac_settings.get("mode")
            or "factory"
        ).strip().lower()

        if mode == "factory":
            return (
                f"interface {interface_arg} "
                "mac address factory wan"
            )

        return (
            f"interface {interface_arg} "
            "mac address "
            f"{mac_settings['address']}"
        )

    @staticmethod
    def ping_check_profile_name(interface_id: str) -> str:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.ping_check_profile_name(interface_id)

    @classmethod
    def find_ping_check_profile(cls, data: Any, profile_name: str) -> dict[str, Any]:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.find_ping_check_profile(data, profile_name)

    @staticmethod
    def ping_check_scalar(data: dict[str, Any], keys: tuple[str, ...], default: Any) -> Any:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.ping_check_scalar(data, keys, default)

    @classmethod
    def ping_check_hosts(cls, data: dict[str, Any]) -> list[str]:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.ping_check_hosts(data)

    @staticmethod
    def automatic_ping_check_hosts() -> tuple[str, str, str]:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.automatic_ping_check_hosts()

    @classmethod
    def is_automatic_ping_check_profile(cls, details: dict[str, Any]) -> bool:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.is_automatic_ping_check_profile(details)

    @classmethod
    def find_ping_check_runtime(cls, status_data: Any, profile_name: str, interface_id: str) -> dict[str, Any]:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.find_ping_check_runtime(status_data, profile_name, interface_id)

    @staticmethod
    def normalize_ping_check_runtime_status(value: Any) -> str:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.normalize_ping_check_runtime_status(value)

    @staticmethod
    def ping_check_runtime_addresses(runtime: dict[str, Any]) -> list[str]:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.ping_check_runtime_addresses(runtime)

    @staticmethod
    def safe_non_negative_integer(value: Any) -> int:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.safe_non_negative_integer(value)

    @classmethod
    def extract_ping_check_runtime(cls, status_data: Any, profile_name: str, interface_id: str, enabled: bool) -> dict[str, Any]:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.extract_ping_check_runtime(status_data, profile_name, interface_id, enabled)

    @staticmethod
    def ping_check_status_text(status: str) -> str:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.ping_check_status_text(status)

    @classmethod
    def ping_check_state_reason(cls, connection: dict[str, Any], interface_item: dict[str, Any], config_item: dict[str, Any], runtime: dict[str, Any], ping_enabled: bool) -> str:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.ping_check_state_reason(connection, interface_item, config_item, runtime, ping_enabled)

    def update_ping_check_runtime_history(self, interface_id: str, connection: dict[str, Any], interface_item: dict[str, Any], config_item: dict[str, Any], runtime: dict[str, Any], ping_enabled: bool) -> dict[str, Any]:
        """Совместимый фасад PingCheckService."""
        return self.ping_check_service.update_ping_check_runtime_history(interface_id, connection, interface_item, config_item, runtime, ping_enabled)

    @classmethod
    def extract_ping_check_settings(cls, config_item: dict[str, Any], config_data: dict[str, Any], status_data: dict[str, Any]) -> dict[str, Any]:
        """Совместимый фасад PingCheckService."""
        del cls
        return PingCheckService.extract_ping_check_settings(config_item, config_data, status_data)

    def build_ping_check_commands(self, interface_id: str, settings: dict[str, Any], current_profile: str) -> tuple[list[str], str]:
        """Совместимый фасад PingCheckService."""
        return self.ping_check_service.build_ping_check_commands(interface_id, settings, current_profile)

    def enrich_connection_details(
        self,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
        ping_check_config_data: dict[str, Any],
        ping_check_status_data: dict[str, Any],
    ) -> None:
        # Все каналы одного ответа получают одинаковую метку. UI
        # отличает новое измерение от повторной отрисовки кэша.
        traffic_sample_time = self.traffic_service.sample_time()

        for connection in connections:
            interface_id = str(
                connection.get("interface_id")
                or ""
            )

            item = interface_data.get(
                interface_id,
                {},
            )

            if not isinstance(item, dict):
                item = {}

            # Передаём реальный ip global priority из
            # show/interface во вкладку «Интернет».
            try:
                connection["priority"] = int(
                    float(item.get("priority"))
                )
            except (TypeError, ValueError):
                connection.pop(
                    "priority",
                    None,
                )

            # Роль канала берём только из конфигурации роутера:
            # show/sc/interface -> ip -> global -> order.
            config_item = interface_config_data.get(
                interface_id,
                {},
            )
            global_config: dict[str, Any] = {}

            if isinstance(config_item, dict):
                self.ping_check_service.enrich_connection(
                    connection=connection,
                    interface_id=interface_id,
                    interface_item=item,
                    config_item=config_item,
                    config_data=ping_check_config_data,
                    status_data=ping_check_status_data,
                )

                (
                    mac_supported,
                    mac_mode,
                    configured_mac,
                ) = self.extract_configured_mac_settings(
                    config_item
                )

                connection[
                    "mac_settings_supported"
                ] = mac_supported
                connection[
                    "mac_mode"
                ] = mac_mode

                if configured_mac:
                    connection[
                        "configured_mac"
                    ] = configured_mac
                else:
                    connection.pop(
                        "configured_mac",
                        None,
                    )
            else:
                connection[
                    "mac_settings_supported"
                ] = False
                connection.pop(
                    "configured_mac",
                    None,
                )

            if isinstance(config_item, dict):
                ip_config = config_item.get("ip")

                if isinstance(ip_config, dict):
                    candidate = ip_config.get("global")

                    if isinstance(candidate, dict):
                        global_config = candidate

            if isinstance(config_item, dict):
                ip_config = config_item.get("ip")

                if isinstance(ip_config, dict):
                    connection[
                        "use_provider_dns"
                    ] = self.truthy(
                        ip_config.get(
                            "name-servers",
                            True,
                        )
                    )

                    configured_dns: list[str] = []
                    name_server_items = ip_config.get(
                        "name-server"
                    )

                    if isinstance(
                        name_server_items,
                        dict,
                    ):
                        name_server_items = [
                            name_server_items
                        ]

                    if isinstance(
                        name_server_items,
                        list,
                    ):
                        for dns_item in name_server_items:
                            if isinstance(
                                dns_item,
                                dict,
                            ):
                                dns_value = str(
                                    dns_item.get(
                                        "name-server"
                                    )
                                    or dns_item.get(
                                        "address"
                                    )
                                    or ""
                                ).strip()
                            else:
                                dns_value = str(
                                    dns_item
                                    or ""
                                ).strip()

                            if (
                                dns_value
                                and dns_value
                                not in configured_dns
                            ):
                                configured_dns.append(
                                    dns_value
                                )

                    if configured_dns:
                        connection[
                            "configured_dns"
                        ] = configured_dns
                    else:
                        connection.pop(
                            "configured_dns",
                            None,
                        )

                    dhcp_config = ip_config.get(
                        "dhcp"
                    )
                    dhcp_client_config: dict[str, Any] = {}

                    if isinstance(dhcp_config, dict):
                        candidate = dhcp_config.get(
                            "client"
                        )

                        if isinstance(candidate, dict):
                            dhcp_client_config = candidate

                    if dhcp_client_config:
                        connection[
                            "dhcp_client_supported"
                        ] = True
                        connection[
                            "dhcp_client_hostname"
                        ] = str(
                            dhcp_client_config.get(
                                "hostname"
                            )
                            or "Keenetic"
                        ).strip()
                        connection[
                            "dhcp_client_routes"
                        ] = self.truthy(
                            dhcp_client_config.get(
                                "routes",
                                True,
                            )
                        )
                        connection[
                            "dhcp_client_dns_routes"
                        ] = self.truthy(
                            dhcp_client_config.get(
                                "dns-routes",
                                True,
                            )
                        )
                    else:
                        connection[
                            "dhcp_client_supported"
                        ] = False
                        connection.pop(
                            "dhcp_client_hostname",
                            None,
                        )
                        connection.pop(
                            "dhcp_client_routes",
                            None,
                        )
                        connection.pop(
                            "dhcp_client_dns_routes",
                            None,
                        )

                    address_config = ip_config.get(
                        "address"
                    )

                    if isinstance(
                        address_config,
                        (dict, str),
                    ):
                        (
                            ipv4_dhcp,
                            configured_address,
                            configured_prefix,
                        ) = self.extract_configured_ipv4(
                            address_config
                        )

                        connection[
                            "ipv4_settings_supported"
                        ] = True
                        connection[
                            "ipv4_dhcp"
                        ] = ipv4_dhcp

                        if configured_address:
                            connection[
                                "configured_ipv4_address"
                            ] = configured_address
                        else:
                            connection.pop(
                                "configured_ipv4_address",
                                None,
                            )

                        if configured_prefix is not None:
                            connection[
                                "configured_ipv4_prefix"
                            ] = configured_prefix
                        else:
                            connection.pop(
                                "configured_ipv4_prefix",
                                None,
                            )

                        if not ipv4_dhcp:
                            configured_gateway = (
                                self.normalize_ipv4_text(
                                    connection.get(
                                        "gateway"
                                    )
                                )
                            )

                            if configured_gateway:
                                connection[
                                    "configured_ipv4_gateway"
                                ] = configured_gateway
                            else:
                                connection.pop(
                                    "configured_ipv4_gateway",
                                    None,
                                )
                        else:
                            connection.pop(
                                "configured_ipv4_gateway",
                                None,
                            )
                    else:
                        connection[
                            "ipv4_settings_supported"
                        ] = False
                        connection.pop(
                            "configured_ipv4_address",
                            None,
                        )
                        connection.pop(
                            "configured_ipv4_prefix",
                            None,
                        )
                        connection.pop(
                            "configured_ipv4_gateway",
                            None,
                        )

            delete_context, delete_reason = (
                self.inspect_wired_ipoe_deletion(
                    interface_id=interface_id,
                    interface_config=interface_config_data,
                    interface_runtime=interface_data,
                )
            )
            dedicated = False

            if not delete_context:
                delete_context, dedicated_reason = (
                    self.inspect_dedicated_wan_deletion(
                        interface_id=interface_id,
                        interface_config=interface_config_data,
                        interface_runtime=interface_data,
                    )
                )
                dedicated = bool(delete_context)

                if not delete_context and dedicated_reason:
                    delete_reason = delete_reason or dedicated_reason

            if delete_context:
                self.apply_connection_delete_context(
                    connection,
                    context=delete_context,
                    delete_kind=(
                        "ethernet.dedicated"
                        if dedicated
                        else "ethernet.ipoe"
                    ),
                    dedicated=dedicated,
                )
            else:
                self.clear_connection_delete_context(
                    connection,
                    delete_reason,
                )

            try:
                global_order = int(
                    global_config.get("order")
                )
            except (TypeError, ValueError):
                connection.pop(
                    "global_order",
                    None,
                )
            else:
                if global_order >= 0:
                    connection[
                        "global_order"
                    ] = global_order
                else:
                    connection.pop(
                        "global_order",
                        None,
                    )

            traffic_sample = self.traffic_service.measure_connection(
                connection=connection,
                interface_data=interface_data,
                sampled_at=traffic_sample_time,
            )
            self.traffic_service.apply_sample(
                connection,
                traffic_sample,
            )
            statistics_source = traffic_sample.source_id

            # Дополнительные параметры для раскрытой карточки
            # во вкладке «Интернет». Используем фактические
            # данные show/interface и не подставляем значения,
            # которых роутер не передал.
            self.enrich_basic_interface_fields(
                connection=connection,
                item=item,
                statistics_source=statistics_source,
                interface_data=interface_data,
            )

    def enrich_basic_interface_fields(
        self,
        *,
        connection: dict[str, Any],
        item: dict[str, Any],
        statistics_source: str,
        interface_data: dict[str, Any],
    ) -> None:
        """
        Добавляет безопасные диагностические поля интернет-
        подключения из show/interface.

        Значение записывается только когда оно реально найдено.
        Уже подготовленные TopologyEngine поля не перезаписываются.
        """
        source_item = interface_data.get(
            statistics_source,
            {},
        )

        if not isinstance(source_item, dict):
            source_item = {}

        combined = {
            "logical": item,
            "traffic": source_item,
        }

        field_keys: dict[str, tuple[str, ...]] = {
            "address": (
                "address",
                "ipv4",
                "ip-address",
                "ip_address",
                "local-address",
                "local_address",
            ),
            "ipv6_address": (
                "ipv6",
                "ipv6-address",
                "ipv6_address",
                "global-address",
                "global_address",
            ),
            "gateway": (
                "gateway",
                "default-gateway",
                "default_gateway",
                "peer",
                "remote-address",
                "remote_address",
            ),
            "dns": (
                "dns",
                "dns-server",
                "dns_server",
                "dns-servers",
                "dns_servers",
                "nameserver",
                "nameservers",
            ),
            "mtu": (
                "mtu",
            ),
            "mac": (
                "mac",
                "mac-address",
                "mac_address",
                "hwaddr",
                "hardware-address",
            ),
            "uptime": (
                "uptime",
                "connected-time",
                "connected_time",
                "session-time",
                "session_time",
            ),
        }

        for target_key, keys in field_keys.items():
            existing = connection.get(target_key)

            if existing not in (
                None,
                "",
                "-",
                "—",
                [],
                {},
            ):
                continue

            if target_key == "ipv6_address":
                # Не используем find_first_value: оно может
                # первым найти служебный флаг `ipv6: false`
                # и остановить поиск до настоящего адреса.
                # Просматриваем всю структуру интерфейса.
                normalized = self.extract_ipv6_address(
                    combined
                )
            else:
                value = self.find_first_value(
                    combined,
                    keys,
                )
                normalized = self.normalize_connection_detail(
                    value
                )

            if normalized is not None:
                connection[target_key] = normalized

        link_speed = self.extract_link_speed_bps(
            source_item
        )

        if link_speed is None:
            link_speed = self.extract_link_speed_bps(
                item
            )

        if link_speed is not None and link_speed > 0:
            connection["link_speed_bps"] = link_speed

    @classmethod
    def extract_ipv6_address(
        cls,
        value: Any,
    ) -> str | None:
        """
        Извлекает только сам IPv6-адрес из строки или
        вложенной структуры show/interface.

        Булевы значения True/False полностью игнорируются.
        Глобальный адрес имеет приоритет над link-local.
        """
        global_addresses: list[str] = []
        link_local_addresses: list[str] = []

        def collect(candidate: Any) -> None:
            if candidate is None or isinstance(candidate, bool):
                return

            if isinstance(candidate, dict):
                for key in (
                    "address",
                    "ipv6-address",
                    "ipv6_address",
                    "global-address",
                    "global_address",
                ):
                    if key in candidate:
                        collect(candidate.get(key))

                for key, nested in candidate.items():
                    if key in {
                        "address",
                        "ipv6-address",
                        "ipv6_address",
                        "global-address",
                        "global_address",
                    }:
                        continue
                    collect(nested)
                return

            if isinstance(candidate, (list, tuple, set)):
                for nested in candidate:
                    collect(nested)
                return

            if not isinstance(candidate, str):
                return

            for part in candidate.replace(
                "\n",
                " ",
            ).replace(
                ",",
                " ",
            ).replace(
                ";",
                " ",
            ).split():
                address_text = (
                    part.strip("[]() ")
                    .split("/", 1)[0]
                    .split("%", 1)[0]
                    .strip()
                )

                try:
                    address = ipaddress.ip_address(
                        address_text
                    )
                except ValueError:
                    continue

                if not isinstance(
                    address,
                    ipaddress.IPv6Address,
                ):
                    continue

                normalized = str(address)

                if address.is_link_local:
                    if normalized not in link_local_addresses:
                        link_local_addresses.append(normalized)
                elif normalized not in global_addresses:
                    global_addresses.append(normalized)

        collect(value)

        if global_addresses:
            return global_addresses[0]

        if link_local_addresses:
            return link_local_addresses[0]

        return None

    @classmethod
    def normalize_connection_detail(
        cls,
        value: Any,
    ) -> Any | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            text = value.strip()

            if text in {
                "",
                "-",
                "—",
                "None",
                "null",
            }:
                return None

            return text

        if isinstance(value, (list, tuple, set)):
            items: list[str] = []

            for item in value:
                normalized = cls.normalize_connection_detail(
                    item
                )

                if normalized is None:
                    continue

                text = str(normalized).strip()

                if text and text not in items:
                    items.append(text)

            return items or None

        if isinstance(value, dict):
            # Часто DNS и адреса приходят как словари с
            # произвольными числовыми ключами.
            items: list[str] = []

            for nested_value in value.values():
                normalized = cls.normalize_connection_detail(
                    nested_value
                )

                if normalized is None:
                    continue

                if isinstance(normalized, list):
                    candidates = normalized
                else:
                    candidates = [normalized]

                for candidate in candidates:
                    text = str(candidate).strip()

                    if text and text not in items:
                        items.append(text)

            return items or None

        text = str(value).strip()
        return text or None

    def select_traffic_statistics(
        self,
        connection: dict[str, Any],
        interface_data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Совместимый фасад выбора интерфейсной статистики."""
        return self.interface_service.select_traffic_statistics(
            connection=connection,
            interface_data=interface_data,
            number_reader=self.first_number,
        )

    def get_interface_statistics(
        self,
        interface_id: str,
    ) -> dict[str, Any]:
        """Совместимый фасад получения статистики интерфейса."""
        return self.interface_service.get_statistics(
            interface_id
        )

    def normalize_interface_statistics(
        self,
        data: Any,
    ) -> dict[str, Any]:
        """Совместимый фасад нормализации статистики."""
        return self.interface_service.normalize_statistics(data)

    @staticmethod
    def first_number(
        *sources: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float:
        return TrafficService.first_number(*sources, keys=keys)

    @staticmethod
    def first_optional_number(
        *sources: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float | None:
        return TrafficService.first_optional_number(*sources, keys=keys)

    def calculate_counter_speeds(
        self,
        interface_id: str,
        rxbytes: float | None,
        txbytes: float | None,
        link_speed_bps: float | None = None,
    ) -> tuple[float, float]:
        return self.traffic_service.calculate_counter_speeds(
            interface_id=interface_id,
            rxbytes=rxbytes,
            txbytes=txbytes,
            link_speed_bps=link_speed_bps,
        )

    @staticmethod
    def extract_link_speed_bps(
        item: dict[str, Any],
    ) -> float | None:
        return TrafficService.extract_link_speed_bps(item)

    @staticmethod
    def extract_ipv6_details(
        item: dict[str, Any],
    ) -> tuple[str, str]:
        ipv6 = item.get("ipv6")

        if not isinstance(ipv6, dict):
            return "—", "—"

        addresses = ipv6.get("addresses")

        if not isinstance(addresses, list):
            return "—", "—"

        for address_item in addresses:
            if not isinstance(address_item, dict):
                continue

            address = str(
                address_item.get("address")
                or ""
            )

            if not address or address.startswith("fe80:"):
                continue

            prefix_length = address_item.get(
                "prefix-length"
            )

            prefix = (
                f"{address}/{prefix_length}"
                if prefix_length is not None
                else address
            )

            return address, prefix

        for address_item in addresses:
            if not isinstance(address_item, dict):
                continue

            address = str(
                address_item.get("address")
                or ""
            )

            if address:
                prefix_length = address_item.get(
                    "prefix-length"
                )
                prefix = (
                    f"{address}/{prefix_length}"
                    if prefix_length is not None
                    else address
                )
                return address, prefix

        return "—", "—"

    @staticmethod
    def extract_ipv6_gateway(
        item: dict[str, Any],
    ) -> str:
        ipv6 = item.get("ipv6")

        if isinstance(ipv6, dict):
            for key in (
                "gateway",
                "default-gateway",
                "default_gateway",
            ):
                value = ipv6.get(key)

                if isinstance(value, str) and value:
                    return value

        return "—"

    def extract_dns_servers(
        self,
        item: dict[str, Any],
    ) -> list[str]:
        result: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    key_text = str(key).lower()

                    if "dns" in key_text:
                        collect(nested)
                    else:
                        walk(nested)

            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        def collect(value: Any) -> None:
            if isinstance(value, str):
                if value and value not in result:
                    result.append(value)

            elif isinstance(value, (list, tuple)):
                for nested in value:
                    collect(nested)

            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)

        walk(item)
        return result

    def set_internet_priorities(
        self, priorities: dict[str, int]
    ) -> tuple[bool, str]:
        """Совместимый фасад PriorityService."""
        return self.priority_service.set_internet_priorities(priorities)

    def verify_global_interface_orders(
        self, expected: dict[str, int]
    ) -> tuple[bool, str]:
        """Совместимый фасад PriorityService."""
        return self.priority_service.verify_global_interface_orders(expected)

    def extract_batch_rci_errors(
        self,
        data: Any,
    ) -> list[str]:
        """
        Извлекает только реальные ошибки из пакетного ответа RCI.
        Информационные сообщения успешных команд не считаются
        ошибками.
        """
        errors: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return

            if not isinstance(value, dict):
                return

            status = str(
                value.get("status")
                or ""
            ).strip().lower()

            if status == "error":
                message = str(
                    value.get("message")
                    or value.get("error")
                    or value
                ).strip()

                if message:
                    errors.append(message)

            nested_status = value.get("status")

            if isinstance(nested_status, list):
                walk(nested_status)

            for key, nested in value.items():
                if key == "status":
                    continue

                if isinstance(
                    nested,
                    (dict, list),
                ):
                    walk(nested)

        walk(data)
        return list(
            dict.fromkeys(errors)
        )

    def get_configured_interface_dns(
        self,
        interface_id: str,
    ) -> list[str]:
        config = self.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        item = (
            config.get(interface_id)
            if isinstance(config, dict)
            else None
        )

        if not isinstance(item, dict):
            return []

        ip_config = item.get("ip")

        if not isinstance(ip_config, dict):
            return []

        dns_items = ip_config.get(
            "name-server"
        )

        if isinstance(dns_items, dict):
            dns_items = [dns_items]

        result: list[str] = []

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

                if normalized not in result:
                    result.append(normalized)

        return result

    @staticmethod
    def quote_cli_argument(
        value: str,
    ) -> str:
        """
        Безопасно оформляет один строковый аргумент CLI.
        """
        text = str(value or "")

        if re.fullmatch(
            r"[A-Za-z0-9_./:@+-]+",
            text,
        ):
            return text

        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
        )
        return f'"{escaped}"'

    @staticmethod
    def redact_secrets_from_text(
        text: str,
        *secrets: str,
    ) -> str:
        """Удаляет известные секреты из сообщения роутера."""
        result = str(text or "")

        for secret in secrets:
            value = str(secret or "")

            if value:
                result = result.replace(value, "[REDACTED]")

        return result

    @staticmethod
    def extract_explicit_rci_errors(
        data: Any,
    ) -> list[str]:
        """
        Извлекает только явные ошибки.

        Поле message само по себе не считается ошибкой:
        Keenetic использует его и для успешных сообщений
        вида "name server ... added".
        """
        errors: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return

            if not isinstance(value, dict):
                return

            status = str(
                value.get("status")
                or ""
            ).strip().lower()

            if status in {
                "error",
                "failed",
                "failure",
            }:
                message = str(
                    value.get("message")
                    or value.get("error")
                    or value
                ).strip()

                if message:
                    errors.append(message)

            explicit_error = value.get("error")

            if isinstance(explicit_error, str):
                text = explicit_error.strip()

                if text:
                    errors.append(text)

            elif isinstance(explicit_error, dict):
                walk(explicit_error)

            for key, nested in value.items():
                if key in {
                    "message",
                    "warning",
                    "error",
                }:
                    continue

                if isinstance(
                    nested,
                    (dict, list),
                ):
                    walk(nested)

        walk(data)

        return list(
            dict.fromkeys(errors)
        )

    def post_dns_rci_command(
        self,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Отправляет структурную DNS-команду через /rci/.
        """
        success, _data, message = self.post_rci_query(
            payload,
            timeout=15,
        )
        return success, message

    def execute_parse_command(
        self,
        command: str,
    ) -> tuple[bool, str]:
        """
        Выполняет CLI-команду через RCI parse.

        Информационные сообщения успешных команд не считаются
        ошибками. Фактический результат проверяется отдельно
        через show/sc/interface.
        """
        success, _data, message = self.post_rci_query(
            {"parse": str(command)},
            timeout=15,
        )
        return success, message

    def get_interface_cli_name(
        self,
        interface_id: str,
    ) -> str:
        """
        Возвращает предпочтительное имя интерфейса для CLI.

        При наличии rename используется псевдоним, иначе
        технический идентификатор.
        """
        technical_name = str(
            interface_id
            or ""
        ).strip()

        config = self.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        item = (
            config.get(technical_name)
            if isinstance(config, dict)
            else None
        )

        if isinstance(item, dict):
            alias = str(
                item.get("rename")
                or item.get("interface-name")
                or ""
            ).strip()

            if alias:
                return alias

        return technical_name

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
            self.get_interface_cli_name(
                interface_id
            )
        )

        command = (
            f"no {family} name-server "
            f"{self.quote_cli_argument(str(parsed_address))} "
            f"\"\" on "
            f"{self.quote_cli_argument(interface_name)}"
        )

        success, message = self.execute_parse_command(
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
            self.get_interface_cli_name(
                interface_id
            )
        )

        command = (
            f"{family} name-server "
            f"{self.quote_cli_argument(str(parsed_address))} "
            f"\"\" on "
            f"{self.quote_cli_argument(interface_name)}"
        )

        success, message = self.execute_parse_command(
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
            self.get_configured_interface_dns(
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
        config = self.get_rci_data(
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
            self.extract_configured_ipv4(
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
        cli_name = self.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.quote_cli_argument(
            cli_name
        )

        commands = [
            (
                "no ip route default "
                f"{interface_arg}"
            )
        ]

        normalized_gateway = (
            self.normalize_ipv4_text(
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
                self.execute_parse_command(
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
        cli_name = self.get_interface_cli_name(
            interface_id
        )
        interface_arg = self.quote_cli_argument(
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
                self.execute_parse_command(
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

        address = self.normalize_ipv4_text(
            ipv4_settings.get("address")
        )
        gateway = self.normalize_ipv4_text(
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
                self.execute_parse_command(
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
                self.execute_parse_command(
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
            self.get_interface_cli_name(
                interface_id
            ).lower(),
        }

        route_data = self.get_rci_data(
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
            gateway = self.normalize_ipv4_text(
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

        interface_data = self.get_rci_data(
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
            return self.normalize_ipv4_text(
                self.find_first_value(
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
        if self.session is None or self.base_url is None:
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
                self.wireless_isp_service.update_wireless_isp_settings(
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
                address = self.normalize_ipv4_text(
                    raw_ipv4.get("address")
                )
                gateway = self.normalize_ipv4_text(
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
                    self.validate_manual_mac(
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
                        self.build_interface_mac_command(
                            interface_id=name,
                            mac_settings=changes["mac"],
                        )
                    )
                }
            )

        if "ping_check" in changes:
            current_config = self.get_rci_data(
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
            ) = self.ping_check_service.build_ping_check_commands(
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

        applied, apply_message = self.execute_rci_batch(
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

            config = self.get_rci_data(
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
                actual_provider_dns = self.truthy(
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
                ) = self.extract_configured_mac_settings(
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
                        or self.ping_check_profile_name(
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
                        profile_config = self.get_rci_data(
                            endpoint="show/sc/ping-check",
                            required=False,
                        )
                        profile_status = self.get_rci_data(
                            endpoint="show/ping-check",
                            required=False,
                        )
                        profile_details = (
                            self.find_ping_check_profile(
                                profile_config,
                                expected_profile,
                            )
                        )

                        if not profile_details:
                            profile_details = (
                                self.find_ping_check_profile(
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
                            elif not self.is_automatic_ping_check_profile(
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
                                self.ping_check_scalar(
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
                                for value in self.ping_check_hosts(
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
                                        self.ping_check_scalar(
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
                                    self.ping_check_scalar(
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
                                    self.ping_check_scalar(
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

                actual_routes = self.truthy(
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

                actual_dns_routes = self.truthy(
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
                ) = self.extract_configured_ipv4(
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

    @staticmethod
    def extract_port_access_vid(
        port_config: dict[str, Any],
    ) -> int | None:
        switchport = port_config.get(
            "switchport"
        )

        if not isinstance(switchport, dict):
            return None

        access = switchport.get("access")

        if not isinstance(access, dict):
            return None

        try:
            return int(access.get("vid"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def extract_port_switch_mode(
        port_config: dict[str, Any],
    ) -> str:
        switchport = port_config.get(
            "switchport"
        )

        if not isinstance(switchport, dict):
            return "access"

        raw_modes = switchport.get("mode")

        if isinstance(raw_modes, list):
            for item in raw_modes:
                if not isinstance(item, dict):
                    continue

                mode = str(
                    item.get("mode")
                    or ""
                ).strip().lower()

                if mode:
                    return mode

        if isinstance(raw_modes, dict):
            mode = str(
                raw_modes.get("mode")
                or ""
            ).strip().lower()

            if mode:
                return mode

        return "access"

    @staticmethod
    def extract_port_roles(
        port_config: dict[str, Any],
    ) -> list[dict[str, str]]:
        raw_roles = port_config.get("role")
        result: list[dict[str, str]] = []

        if isinstance(raw_roles, dict):
            raw_roles = [raw_roles]

        if not isinstance(raw_roles, list):
            return result

        for item in raw_roles:
            if not isinstance(item, dict):
                continue

            role = str(
                item.get("role")
                or ""
            ).strip()
            ifor = str(
                item.get("ifor")
                or ""
            ).strip()

            if not role:
                continue

            result.append(
                {
                    "role": role,
                    "ifor": ifor,
                }
            )

        return result

    @staticmethod
    def current_global_orders(
        interface_config: dict[str, Any],
    ) -> dict[str, int]:
        """Совместимый фасад PriorityService."""
        return PriorityService.current_global_orders(interface_config)

    @staticmethod
    def choose_internal_vlan_id(
        interface_config: dict[str, Any],
    ) -> int | None:
        used: set[int] = {1}

        for interface_id, item in (
            interface_config.items()
        ):
            match = re.search(
                r"/Vlan(\d+)$",
                str(interface_id),
                flags=re.IGNORECASE,
            )

            if match:
                used.add(
                    int(match.group(1))
                )

            if not isinstance(item, dict):
                continue

            vid = KeeneticClient.extract_port_access_vid(
                item
            )

            if vid is not None:
                used.add(vid)

            for key in ("ipoe", "iseg"):
                metadata = item.get(key)

                if not isinstance(metadata, dict):
                    continue

                for token in (
                    KeeneticClient.split_config_tokens(
                        metadata.get("busy-vlan")
                    )
                ):
                    try:
                        used.add(int(token))
                    except ValueError:
                        pass

        for vid in range(2, 4095):
            if vid not in used:
                return vid

        return None

    @staticmethod
    def extract_vlan_interface_parts(
        interface_id: str,
    ) -> tuple[str, int] | None:
        match = re.fullmatch(
            r"(.+)/Vlan(\d+)",
            str(interface_id or "").strip(),
            flags=re.IGNORECASE,
        )

        if match is None:
            return None

        return match.group(1), int(match.group(2))

    @staticmethod
    def collect_included_interfaces(
        value: Any,
    ) -> list[str]:
        result: list[str] = []

        def walk(candidate: Any) -> None:
            if isinstance(candidate, str):
                text = candidate.strip()

                if text and text not in result:
                    result.append(text)
                return

            if isinstance(candidate, list):
                for nested in candidate:
                    walk(nested)
                return

            if not isinstance(candidate, dict):
                return

            direct = candidate.get("interface")

            if isinstance(direct, str):
                walk(direct)

            for key, nested in candidate.items():
                if key == "interface":
                    continue

                if isinstance(nested, (dict, list)):
                    walk(nested)

        walk(value)
        return result

    @classmethod
    def find_home_vlan_id(
        cls,
        *,
        parent_id: str,
        provider_port_id: str,
        provider_vlan_id: int,
        interface_config: dict[str, Any],
    ) -> int | None:
        bridge_candidates: list[tuple[int, int]] = []

        for bridge_id, item in interface_config.items():
            if not isinstance(item, dict):
                continue

            included = cls.collect_included_interfaces(
                item.get("include")
            )

            if not included:
                continue

            description = str(
                item.get("description")
                or ""
            ).strip().lower()
            security = item.get("security-level")
            is_private = False

            if isinstance(security, dict):
                is_private = cls.truthy(
                    security.get("private")
                )
            else:
                is_private = (
                    str(security or "")
                    .strip()
                    .lower()
                    == "private"
                )

            score = 0

            if str(bridge_id).lower() == "bridge0":
                score += 4

            if is_private:
                score += 3

            if (
                "home" in description
                or "домаш" in description
            ):
                score += 2

            for included_id in included:
                parts = cls.extract_vlan_interface_parts(
                    included_id
                )

                if parts is None:
                    continue

                included_parent, included_vlan = parts

                if (
                    included_parent == parent_id
                    and included_vlan != provider_vlan_id
                ):
                    bridge_candidates.append(
                        (score, included_vlan)
                    )

        if bridge_candidates:
            bridge_candidates.sort(
                key=lambda item: (
                    item[0],
                    -item[1],
                ),
                reverse=True,
            )
            best_score = bridge_candidates[0][0]
            best_vlans = {
                vlan
                for score, vlan in bridge_candidates
                if score == best_score
            }

            if len(best_vlans) == 1:
                return next(iter(best_vlans))

        sibling_vlans: list[int] = []
        prefix = parent_id + "/"

        for port_id, port_item in interface_config.items():
            if (
                port_id == provider_port_id
                or not str(port_id).startswith(prefix)
                or not isinstance(port_item, dict)
            ):
                continue

            suffix = str(port_id)[len(prefix):]

            if not suffix.isdigit():
                continue

            roles = cls.extract_port_roles(port_item)

            if any(
                str(role.get("role") or "")
                .strip()
                .lower()
                == "inet"
                for role in roles
            ):
                continue

            access_vid = cls.extract_port_access_vid(
                port_item
            )

            if (
                access_vid is not None
                and access_vid != provider_vlan_id
            ):
                sibling_vlans.append(access_vid)

        if sibling_vlans:
            counts = {
                vlan: sibling_vlans.count(vlan)
                for vlan in set(sibling_vlans)
            }
            ordered = sorted(
                counts.items(),
                key=lambda item: (
                    item[1],
                    -item[0],
                ),
                reverse=True,
            )

            if (
                len(ordered) == 1
                or ordered[0][1] > ordered[1][1]
            ):
                return ordered[0][0]

        return None

    @classmethod
    def find_runtime_ethernet_port(
        cls,
        *,
        interface_runtime: dict[str, Any],
        port_id: str,
        parent_id: str,
    ) -> dict[str, Any]:
        """
        Находит физический порт в show/interface.

        На разных версиях KeeneticOS порт может находиться
        непосредственно по ID либо внутри parent -> port.
        """
        if not isinstance(interface_runtime, dict):
            return {}

        target = str(port_id or "").strip()

        if not target:
            return {}

        direct = interface_runtime.get(target)

        if isinstance(direct, dict):
            return direct

        parents: list[dict[str, Any]] = []
        parent = interface_runtime.get(parent_id)

        if isinstance(parent, dict):
            parents.append(parent)

        parents.extend(
            item
            for key, item in interface_runtime.items()
            if key != parent_id and isinstance(item, dict)
        )

        for candidate_parent in parents:
            raw_ports = candidate_parent.get("port")
            candidates: list[dict[str, Any]] = []

            if isinstance(raw_ports, dict):
                if raw_ports.get("id"):
                    candidates = [raw_ports]
                else:
                    candidates = [
                        item
                        for item in raw_ports.values()
                        if isinstance(item, dict)
                    ]
            elif isinstance(raw_ports, list):
                candidates = [
                    item
                    for item in raw_ports
                    if isinstance(item, dict)
                ]

            for candidate in candidates:
                candidate_id = str(
                    candidate.get("id")
                    or ""
                ).strip()

                if candidate_id == target:
                    return candidate

        return {}

    @classmethod
    def runtime_ethernet_port_state(
        cls,
        *,
        interface_runtime: dict[str, Any],
        port_id: str,
        parent_id: str,
    ) -> dict[str, Any]:
        """Возвращает подтверждённое физическое состояние порта."""
        port = cls.find_runtime_ethernet_port(
            interface_runtime=interface_runtime,
            port_id=port_id,
            parent_id=parent_id,
        )

        if not port:
            return {
                "known": False,
                "active": False,
                "link": "unknown",
                "speed": None,
            }

        link = str(
            port.get("link")
            or ""
        ).strip().lower()
        connected = str(
            port.get("connected")
            or ""
        ).strip().lower()
        state = str(
            port.get("state")
            or ""
        ).strip().lower()

        known = bool(
            link in {"up", "down"}
            or connected in {
                "yes",
                "no",
                "true",
                "false",
                "1",
                "0",
            }
            or state in {"up", "down"}
        )
        active = bool(
            link == "up"
            or connected in {"yes", "true", "1"}
            or (not link and state == "up")
        )

        return {
            "known": known,
            "active": active,
            "link": link or state or "unknown",
            "speed": port.get("speed"),
        }

    @classmethod
    def inspect_wired_ipoe_deletion(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        parts = cls.extract_vlan_interface_parts(name)

        unsupported = (
            "В этой версии можно удалить только Проводное "
            "IPoE на переназначенном LAN-порту."
        )

        if parts is None:
            return {}, unsupported

        parent_id, vlan_id = parts
        interface_item = interface_config.get(name)

        if not isinstance(interface_item, dict):
            return {}, "Конфигурация подключения не найдена."

        security = interface_item.get("security-level")
        is_public = False

        if isinstance(security, dict):
            is_public = cls.truthy(
                security.get("public")
            )
        else:
            is_public = (
                str(security or "")
                .strip()
                .lower()
                == "public"
            )

        ip_config = interface_item.get("ip")
        global_config = (
            ip_config.get("global")
            if isinstance(ip_config, dict)
            else None
        )

        if not is_public or not isinstance(
            global_config,
            dict,
        ):
            return {}, unsupported

        bindings: list[tuple[str, dict[str, Any]]] = []

        for port_id, port_item in interface_config.items():
            if not isinstance(port_item, dict):
                continue

            for role in cls.extract_port_roles(port_item):
                role_name = str(
                    role.get("role")
                    or ""
                ).strip().lower()
                target = str(
                    role.get("ifor")
                    or ""
                ).strip()

                if role_name == "inet" and target == name:
                    bindings.append(
                        (str(port_id), port_item)
                    )
                    break

        if len(bindings) != 1:
            return (
                {},
                "Не удалось однозначно определить LAN-порт подключения.",
            )

        port_id, port_item = bindings[0]

        if not port_id.startswith(parent_id + "/"):
            return {}, unsupported

        suffix = port_id[len(parent_id) + 1:]

        if not suffix.isdigit():
            return {}, unsupported

        access_vid = cls.extract_port_access_vid(
            port_item
        )
        switch_mode = cls.extract_port_switch_mode(
            port_item
        )

        if switch_mode != "access":
            return (
                {},
                "Удаление trunk-порта этой версией не поддерживается.",
            )

        if access_vid != vlan_id:
            return (
                {},
                "VLAN интерфейса и назначение LAN-порта не совпадают.",
            )

        home_vlan_id = cls.find_home_vlan_id(
            parent_id=parent_id,
            provider_port_id=port_id,
            provider_vlan_id=vlan_id,
            interface_config=interface_config,
        )

        if home_vlan_id is None:
            return (
                {},
                "Не удалось безопасно определить VLAN домашней сети.",
            )

        port_label = str(
            port_item.get("rename")
            or ""
        ).strip()

        if not port_label:
            port_label = str(int(suffix) + 1)

        runtime_state = cls.runtime_ethernet_port_state(
            interface_runtime=(
                interface_runtime
                if isinstance(interface_runtime, dict)
                else {}
            ),
            port_id=port_id,
            parent_id=parent_id,
        )

        assignment = interface_item.get("ping-check")
        ping_profile = ""

        if isinstance(assignment, dict):
            ping_profile = str(
                assignment.get("profile")
                or ""
            ).strip()
        elif isinstance(assignment, str):
            ping_profile = assignment.strip()

        try:
            global_order = int(
                global_config.get("order")
            )
        except (TypeError, ValueError):
            global_order = None

        return (
            {
                "interface_id": name,
                "interface_item": copy.deepcopy(
                    interface_item
                ),
                "parent_id": parent_id,
                "vlan_id": vlan_id,
                "port_id": port_id,
                "port_label": port_label,
                "port_item": copy.deepcopy(port_item),
                "old_access_vid": access_vid,
                "old_switch_mode": switch_mode,
                "old_roles": cls.extract_port_roles(
                    port_item
                ),
                "home_vlan_id": home_vlan_id,
                "port_link_known": bool(
                    runtime_state.get("known")
                ),
                "port_link_active": bool(
                    runtime_state.get("active")
                ),
                "port_link": str(
                    runtime_state.get("link")
                    or "unknown"
                ),
                "port_speed": runtime_state.get("speed"),
                "ping_profile": ping_profile,
                "global_order": global_order,
                "was_up": cls.truthy(
                    interface_item.get("up")
                ),
                "description": str(
                    interface_item.get("description")
                    or name
                ).strip(),
            },
            "",
        )

    @classmethod
    def find_static_ipv4_network_conflict(
        cls,
        *,
        ipv4_settings: Any,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any],
    ) -> dict[str, str]:
        if (
            not isinstance(ipv4_settings, dict)
            or cls.truthy(ipv4_settings.get("dhcp"))
        ):
            return {}

        address = cls.normalize_ipv4_text(
            ipv4_settings.get("address")
        )

        try:
            prefix = int(
                ipv4_settings.get("prefix")
            )
            target = ipaddress.IPv4Interface(
                f"{address}/{prefix}"
            ).network
        except (TypeError, ValueError):
            return {}

        for interface_id, runtime_item in (
            interface_runtime.items()
        ):
            if not isinstance(runtime_item, dict):
                continue

            config_item = interface_config.get(
                interface_id
            )

            if isinstance(config_item, dict):
                if (
                    "up" in config_item
                    and not cls.truthy(
                        config_item.get("up")
                    )
                ):
                    continue

            if not cls.interface_is_enabled(runtime_item):
                continue

            security = str(
                runtime_item.get("security-level")
                or ""
            ).strip().lower()

            if not (
                cls.truthy(runtime_item.get("global"))
                or security == "public"
            ):
                continue

            current_address = cls.normalize_ipv4_text(
                runtime_item.get("address")
            )
            current_mask = str(
                runtime_item.get("mask")
                or ""
            ).strip()

            if not current_address or not current_mask:
                continue

            try:
                current_network = ipaddress.IPv4Interface(
                    f"{current_address}/{current_mask}"
                ).network
            except ValueError:
                continue

            if target.overlaps(current_network):
                return {
                    "interface_id": str(interface_id),
                    "network": str(current_network),
                    "target_network": str(target),
                }

        return {}

    def execute_rci_batch(
        self,
        commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        try:
            response = self.transport.request(
                method="POST",
                path="/rci/",
                endpoint=f"batch:{operation_name}",
                required=True,
                timeout=max(self.timeout, 20),
                json_payload=commands,
                allow_reauthentication=True,
                retry_transient=False,
            )
        except KeeneticError as error:
            return False, f"{operation_name}: {error}"

        if response is None:
            return False, f"{operation_name}: пустой HTTP-ответ."

        try:
            response_data: Any = response.json()
        except ValueError:
            response_data = None

        errors = self.extract_batch_rci_errors(
            response_data
        )

        if errors:
            return (
                False,
                f"{operation_name}: " + "; ".join(errors),
            )

        return True, ""

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

            config = self.get_rci_data(
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
            elif self.truthy(
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
                    self.extract_port_access_vid(
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

                roles = self.extract_port_roles(
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

            config = self.get_rci_data(
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
                if not self.truthy(
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
                self.extract_port_access_vid(
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
        port_arg = self.quote_cli_argument(
            port_id
        )
        interface_arg = self.quote_cli_argument(
            interface_id
        )
        manager_profile = self.ping_check_profile_name(
            interface_id
        )
        profile_config = self.get_rci_data(
            endpoint="show/sc/ping-check",
            required=False,
        )
        manager_profile_exists = bool(
            self.find_ping_check_profile(
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
            role_name = self.quote_cli_argument(
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
                    + self.quote_cli_argument(ifor)
                )

            commands.append(command)

        for command in commands:
            success, message = (
                self.execute_parse_command(command)
            )

            if not success:
                errors.append(
                    f"{command}: {message}"
                )

        current_config = self.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if (
            isinstance(current_config, dict)
            and interface_id in current_config
        ):
            if manager_profile_exists:
                detach_success, detach_message = (
                    self.execute_parse_command(
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
                self.execute_parse_command(
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
                self.execute_parse_command(
                    (
                        "no ping-check profile "
                        + self.quote_cli_argument(
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
            self.execute_parse_command(
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
                self.set_internet_priorities(
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

    @staticmethod
    def normalize_wireless_isp_band(value: Any) -> str:
        return WirelessIspService.normalize_wireless_isp_band(value)

    @classmethod
    def wireless_isp_interface_for_band(
        cls,
        band: Any,
    ) -> tuple[str, str]:
        return WirelessIspService.wireless_isp_interface_for_band(
            band
        )

    def post_rci_query(
        self,
        payload: Any,
        *,
        timeout: float = 25.0,
    ) -> tuple[bool, Any, str]:
        try:
            response = self.transport.request(
                method="POST",
                path="/rci/",
                endpoint="rci/query",
                required=True,
                timeout=max(self.timeout, timeout),
                json_payload=payload,
                allow_reauthentication=True,
                retry_transient=True,
            )
        except KeeneticError as error:
            return False, None, str(error)

        if response is None:
            return False, None, "Пустой HTTP-ответ."

        try:
            data: Any = response.json()
        except ValueError:
            data = response.text

        errors = self.extract_explicit_rci_errors(data)
        if errors:
            return False, data, "; ".join(errors)

        return True, data, ""

    @classmethod
    def parse_wireless_site_survey(
        cls,
        data: Any,
    ) -> list[dict[str, Any]]:
        return WirelessIspService.parse_wireless_site_survey(data)

    def scan_wireless_isp(
        self,
        band: Any,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        return self.wireless_isp_service.scan_wireless_isp(band)

    @staticmethod
    def valid_wireless_isp_ssid(value: Any) -> bool:
        return WirelessIspService.valid_wireless_isp_ssid(value)

    @staticmethod
    def valid_wireless_isp_password(value: Any) -> bool:
        return WirelessIspService.valid_wireless_isp_password(value)

    def rollback_wireless_isp_creation(
        self,
        *,
        interface_id: str,
        ipv4_settings: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        return self.wireless_isp_service.rollback_wireless_isp_creation(
            interface_id=interface_id,
            ipv4_settings=ipv4_settings,
            old_priorities=old_priorities,
        )

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
        return self.wireless_isp_service.verify_wireless_isp_configuration(
            interface_id=interface_id,
            ssid=ssid,
            open_network=open_network,
            bssid=bssid,
            expected_order=expected_order,
            expected_up=expected_up,
        )

    def create_wireless_isp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        return self.wireless_isp_service.create_wireless_isp(
            settings
        )

    def update_wireless_isp_settings(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        return self.wireless_isp_service.update_wireless_isp_settings(
            interface_id=interface_id,
            settings=settings,
        )

    def delete_wireless_isp(
        self,
        *,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        return self.wireless_isp_service.delete_wireless_isp(
            interface_id=interface_id,
        )

    def create_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredInternetService."""
        return self.wired_internet_service.create_cable_internet(
            settings
        )

    @staticmethod
    def cable_settings_for_interface(
        settings: dict[str, Any],
        *,
        description: str,
    ) -> dict[str, Any]:
        """Совместимый фасад WiredInternetService."""
        return WiredInternetService.cable_settings_for_interface(
            settings,
            description=description,
        )

    @staticmethod
    def priorities_with_inserted_interface(
        old_priorities: dict[str, int],
        *,
        interface_id: str,
        requested_order: int,
    ) -> dict[str, int]:
        """Совместимый фасад PriorityService."""
        return PriorityService.priorities_with_inserted_interface(
            old_priorities,
            interface_id=interface_id,
            requested_order=requested_order,
        )

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
            config = self.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            mismatches = []
            bridge_item = config.get(bridge_id)
            included = (
                self.collect_included_interfaces(
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
                    self.truthy(security.get("public"))
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
            config = self.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if isinstance(config, dict) and target in config:
                success, message = self.execute_rci_batch(
                    [
                        {
                            "parse": (
                                "interface "
                                + self.quote_cli_argument(target)
                                + " no up"
                            )
                        },
                        {
                            "parse": (
                                "no interface "
                                + self.quote_cli_argument(target)
                            )
                        },
                        {"system": {"configuration": {"save": {}}}},
                    ],
                    operation_name="Не удалось удалить новый протокол",
                )

                if not success:
                    errors.append(message)

        success, message = self.execute_rci_batch(
            [
                {
                    "parse": (
                        "interface "
                        + self.quote_cli_argument(bridge_id)
                        + " include "
                        + self.quote_cli_argument(transport_id)
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
            success, message = self.set_internet_priorities(
                old_priorities
            )

            if not success:
                errors.append(
                    "Не удалось восстановить приоритеты: " + message
                )

        if errors:
            return False, "; ".join(errors)

        return True, "Аппаратный WAN возвращён в домашнюю сеть."

    def create_dedicated_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад аппаратного WAN."""
        return (
            self.wired_internet_service
            .create_dedicated_cable_internet(settings)
        )

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
            config = self.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict):
                continue

            mismatches = []
            bridge = config.get(bridge_id)
            included = (
                self.collect_included_interfaces(
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
                    self.truthy(security.get("public"))
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

        config = self.get_rci_data(
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

        success, message = self.execute_rci_batch(
            [
                {
                    "parse": (
                        "no interface "
                        + self.quote_cli_argument(bridge_id)
                        + " include "
                        + self.quote_cli_argument(transport_id)
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
            success, message = self.execute_parse_command(
                "interface "
                + self.quote_cli_argument(source_id)
                + " ping-check profile "
                + self.quote_cli_argument(ping_profile)
            )

            if not success:
                errors.append("Не удалось восстановить Ping Check: " + message)

        if old_priorities:
            success, message = self.set_internet_priorities(old_priorities)

            if not success:
                errors.append("Не удалось восстановить приоритеты: " + message)

        self.execute_parse_command(
            "interface "
            + self.quote_cli_argument(source_id)
            + (" up" if context.get("was_up") else " no up")
        )
        self.execute_parse_command("system configuration save")

        if errors:
            return False, "; ".join(errors)

        return True, "Исходное назначение аппаратного WAN восстановлено."

    def delete_dedicated_wan(
        self,
        *,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления аппаратного WAN."""
        return self.wired_internet_service.delete_dedicated_wan(
            interface_id=interface_id,
        )

    @staticmethod
    def next_numbered_interface_id(
        interface_config: dict[str, Any],
        prefix: str,
    ) -> str:
        used: set[int] = set()
        pattern = re.compile(
            rf"{re.escape(prefix)}(\d+)$",
            flags=re.IGNORECASE,
        )

        for interface_id in interface_config:
            match = pattern.fullmatch(str(interface_id))

            if match:
                used.add(int(match.group(1)))

        number = 0

        while number in used:
            number += 1

        return f"{prefix}{number}"

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
        config = self.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )
        pppoe_arg = self.quote_cli_argument(pppoe_id)
        profile_name = self.ping_check_profile_name(pppoe_id)

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
                success, message = self.execute_parse_command(
                    f"interface {pppoe_arg} no ping-check profile"
                )

                if not success:
                    errors.append(
                        "Не удалось отсоединить Ping Check PPPoE: "
                        + message
                    )

            success, message = self.execute_parse_command(
                f"no interface {pppoe_arg}"
            )

            if not success:
                errors.append(
                    "Не удалось удалить незавершённый PPPoE: "
                    + message
                )

            if assigned_profile.startswith("_KM_"):
                success, message = self.execute_parse_command(
                    "no ping-check profile "
                    + self.quote_cli_argument(assigned_profile)
                )

                if not success:
                    errors.append(
                        "Не удалось удалить профиль Ping Check: "
                        + message
                    )
        else:
            profile_config = self.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.find_ping_check_profile(
                profile_config,
                profile_name,
            ):
                success, message = self.execute_parse_command(
                    "no ping-check profile "
                    + self.quote_cli_argument(profile_name)
                )

                if not success:
                    errors.append(
                        "Не удалось удалить профиль Ping Check: "
                        + message
                    )

        save_success, save_message = self.execute_parse_command(
            "system configuration save"
        )

        if not save_success:
            errors.append(
                "Не удалось сохранить удаление PPPoE: "
                + save_message
            )

        transport_success, transport_message = (
            self.rollback_wired_ipoe_creation(
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
            config = self.get_rci_data(
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

                if not self.truthy(pppoe_item.get("up")):
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
                if self.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.extract_port_roles(port_item)

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
            config = self.get_rci_data(
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

                if not self.truthy(pptp_item.get("up")):
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
                if self.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.extract_port_roles(port_item)

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
            config = self.get_rci_data(
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

                if not self.truthy(l2tp_item.get("up")):
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
                if self.extract_port_access_vid(port_item) != vlan_id:
                    mismatches.append(
                        "VLAN Ethernet-порта изменился"
                    )

                roles = self.extract_port_roles(port_item)

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


    def create_wired_pppoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад создания PPPoE."""
        return self.wired_internet_service.create_wired_pppoe(
            settings
        )

    def create_wired_pptp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад создания PPTP."""
        return self.wired_internet_service.create_wired_pptp(
            settings
        )


    def create_wired_l2tp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад создания L2TP."""
        return self.wired_internet_service.create_wired_l2tp(
            settings
        )


    def create_wired_ipoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад создания IPoE."""
        return self.wired_internet_service.create_wired_ipoe(
            settings
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
            config = self.get_rci_data(
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
                if self.truthy(interface_item.get("up")):
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
                actual_vid = self.extract_port_access_vid(
                    port_item
                )

                if actual_vid != home_vlan_id:
                    mismatches.append(
                        (
                            "LAN-порт не возвращён в домашний "
                            f"VLAN {home_vlan_id}"
                        )
                    )

                roles = self.extract_port_roles(port_item)

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
        interface_arg = self.quote_cli_argument(
            interface_id
        )
        port_arg = self.quote_cli_argument(port_id)

        current_config = self.get_rci_data(
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
                + self.quote_cli_argument(role_name)
            )
            ifor = str(
                role.get("ifor")
                or ""
            ).strip()

            if ifor:
                command += (
                    " for "
                    + self.quote_cli_argument(ifor)
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
                    + self.quote_cli_argument(
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
            success, message = self.execute_parse_command(
                command
            )

            if not success:
                errors.append(
                    f"{command}: {message}"
                )

        if old_priorities:
            priority_success, priority_message = (
                self.set_internet_priorities(
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
            config = self.get_rci_data(
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
                actual_vid = self.extract_port_access_vid(
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
                    for role in self.extract_port_roles(
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

    @staticmethod
    def configured_name_server_settings(
        config_item: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        ip_config = config_item.get("ip")

        if not isinstance(ip_config, dict):
            return True, []

        use_provider_dns = KeeneticClient.truthy(
            ip_config.get("name-servers", True)
        )
        raw = ip_config.get("name-server")

        if isinstance(raw, dict):
            raw = [raw]

        manual_dns: list[str] = []

        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    value = str(
                        item.get("name-server")
                        or item.get("address")
                        or ""
                    ).strip()
                else:
                    value = str(item or "").strip()

                if value and value not in manual_dns:
                    manual_dns.append(value)

        return use_provider_dns, manual_dns

    @staticmethod
    def assigned_ping_profile(config_item: dict[str, Any]) -> str:
        """Совместимый фасад PingCheckService."""
        return PingCheckService.assigned_ping_profile(config_item)

    def set_interface_global_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        name = str(interface_id or "").strip()

        if not name:
            return False, "Не указан интернет-интерфейс."

        return self.execute_rci_batch(
            [
                {
                    "interface": {
                        "ip": {
                            "global": {
                                "enabled": bool(enabled),
                            }
                        },
                        "name": name,
                    }
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name=(
                "Не удалось включить интернет-канал"
                if enabled
                else "Не удалось отключить интернет-канал"
            ),
        )

    def create_protocol_on_existing_transport(
        self,
        *,
        target_protocol: str,
        target_id: str,
        transport_id: str,
        description: str,
        username: str,
        password: str,
        service: str,
        server: str,
    ) -> tuple[bool, str]:
        target_arg = self.quote_cli_argument(target_id)
        transport_arg = self.quote_cli_argument(transport_id)
        commands: list[dict[str, Any]] = [
            {"parse": f"interface {target_arg}"},
            {"parse": f"interface {target_arg} no up"},
            {
                "parse": (
                    f"interface {target_arg} description "
                    + self.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {target_arg} security-level public"
                )
            },
            {"parse": f"interface {target_arg} role inet"},
            {
                "parse": (
                    f"interface {target_arg} authentication identity "
                    + self.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {target_arg} authentication password "
                    + self.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {target_arg} no ccp"},
            {"parse": f"interface {target_arg} lcp echo 30 3"},
            {"parse": f"interface {target_arg} ipcp address"},
            {"parse": f"interface {target_arg} ipcp default-route"},
            {"parse": f"interface {target_arg} ipcp dns-routes"},
            {"parse": f"interface {target_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {target_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {target_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]

        if target_protocol == "pppoe" and service:
            commands.append(
                {
                    "parse": (
                        f"interface {target_arg} pppoe service "
                        + self.quote_cli_argument(service)
                    )
                }
            )
        elif target_protocol in {"pptp", "l2tp"}:
            commands.append(
                {
                    "parse": (
                        f"interface {target_arg} peer "
                        + self.quote_cli_argument(server)
                    )
                }
            )

        commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.execute_rci_batch(
            commands,
            operation_name=(
                f"Не удалось создать {target_protocol.upper()}"
            ),
        )
        return success, self.redact_secrets_from_text(message, password)

    def rollback_cable_protocol_change(
        self,
        *,
        source_id: str,
        target_id: str,
        target_protocol: str,
        transport_id: str,
        old_priorities: dict[str, int],
        ping_profile: str,
        transport_restore_settings: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        errors: list[str] = []
        source_arg = self.quote_cli_argument(source_id)
        target_arg = self.quote_cli_argument(target_id)

        if ping_profile:
            self.execute_parse_command(
                f"interface {target_arg} no ping-check profile"
            )

        if target_protocol != "none":
            success, message = self.execute_parse_command(
                f"no interface {target_arg}"
            )
            if not success:
                errors.append("не удалось удалить новый протокол: " + message)
        else:
            success, message = self.set_interface_global_enabled(
                target_id,
                False,
            )
            if not success:
                errors.append("не удалось отключить временный IPoE: " + message)

            if transport_restore_settings:
                success, message = self.set_interface_settings(
                    interface_id=transport_id,
                    settings=dict(transport_restore_settings),
                )
                if not success:
                    errors.append("не удалось восстановить транспорт: " + message)

        if ping_profile:
            success, message = self.execute_parse_command(
                f"interface {source_arg} ping-check profile "
                + self.quote_cli_argument(ping_profile)
            )
            if not success:
                errors.append("не удалось восстановить Ping Check: " + message)

        if old_priorities:
            success, message = self.set_internet_priorities(old_priorities)
            if not success:
                errors.append("не удалось восстановить приоритеты: " + message)

        self.execute_parse_command(f"interface {source_arg} up")
        self.execute_parse_command("system configuration save")

        if errors:
            return False, "; ".join(errors)
        return True, "Исходный тип подключения восстановлен."

    @staticmethod
    def is_transient_rci_failure(
        message: Any,
    ) -> bool:
        """Определяет временный обрыв HTTP/RCI во время перестройки WAN."""
        value = str(message or "").strip().lower()

        if not value:
            return False

        markers = (
            "remote end closed",
            "remote disconnected",
            "connection aborted",
            "connection reset",
            "connection refused",
            "connectionerror",
            "read timed out",
            "connect timeout",
            "timed out",
            "timeout",
            "ошибка соединения",
            "соединение разорвано",
            "соединение закрыто",
            "время ожидания",
            "устройство недоступно",
        )
        return any(marker in value for marker in markers)

    def renew_authenticated_rci_session(
        self,
    ) -> tuple[bool, str]:
        """Пересоздаёт авторизованную сессию через RciTransport."""
        if not self.base_url:
            return False, "Адрес подключённого роутера неизвестен."

        if self.transport.reauthenticate():
            return True, "RCI-сессия восстановлена."

        return (
            False,
            self.transport.last_error
            or "Не удалось восстановить RCI-сессию.",
        )

    def verify_changed_cable_protocol(
        self,
        *,
        source_id: str,
        target_id: str,
        target_protocol: str,
        transport_id: str,
        expected_order: int,
        expected_username: str,
        expected_service: str,
        expected_server: str,
        expected_mtu: int,
        expected_ping_profile: str,
    ) -> tuple[bool, str, bool]:
        """
        Подтверждает результат смены протокола с повторной авторизацией.

        Возвращает третий флаг, если для подтверждения понадобилось
        восстановить HTTP/RCI-сессию после временного обрыва.
        """
        mismatches: list[str] = []
        session_recovered = False
        recovery_messages: list[str] = []
        empty_reads = 0

        # После save/up роутеру требуется время на перестройку L3 и RCI.
        time.sleep(1.0)

        for attempt in range(18):
            if attempt:
                time.sleep(min(0.55 + attempt * 0.05, 1.0))

            config = self.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict) or not config:
                empty_reads += 1

                # Не переавторизуемся после каждого пустого ответа.
                # Двух подряд достаточно, чтобы считать старое
                # keep-alive соединение закрытым роутером.
                if empty_reads >= 2 and attempt in {1, 4, 8, 12, 15}:
                    recovered, recovery_message = (
                        self.renew_authenticated_rci_session()
                    )
                    recovery_messages.append(recovery_message)
                    if recovered:
                        session_recovered = True
                        empty_reads = 0
                continue

            empty_reads = 0
            mismatches = []
            item = config.get(target_id)

            if source_id != target_id:
                source_item = config.get(source_id)
                source_ip = (
                    source_item.get("ip")
                    if isinstance(source_item, dict)
                    else None
                )
                source_global = (
                    source_ip.get("global")
                    if isinstance(source_ip, dict)
                    else None
                )
                if isinstance(source_global, dict):
                    mismatches.append(
                        "прежний интерфейс остался интернет-каналом"
                    )

            if not isinstance(item, dict):
                mismatches.append("целевой интерфейс не найден")
            else:
                actual_protocol = self.cable_protocol_from_config(
                    target_id,
                    item,
                )
                if actual_protocol != target_protocol:
                    mismatches.append("тип протокола не совпадает")

                if target_protocol != "none":
                    connect = item.get("connect")
                    via = (
                        str(connect.get("via") or "").strip()
                        if isinstance(connect, dict)
                        else ""
                    )
                    if via != transport_id:
                        mismatches.append("неверный транспорт")

                    auth = item.get("authentication")
                    identity = (
                        str(auth.get("identity") or "").strip()
                        if isinstance(auth, dict)
                        else ""
                    )
                    if identity != expected_username:
                        mismatches.append("логин не сохранён")
                    if not (
                        isinstance(auth, dict)
                        and auth.get("password") is not None
                    ):
                        mismatches.append("пароль не сохранён")

                    if target_protocol == "pppoe":
                        pppoe = item.get("pppoe")
                        service = (
                            str(pppoe.get("service") or "").strip()
                            if isinstance(pppoe, dict)
                            else ""
                        )
                        if service != expected_service:
                            mismatches.append("сервис PPPoE не совпадает")
                    else:
                        peer = str(item.get("peer") or "").strip()
                        if peer != expected_server:
                            mismatches.append("сервер провайдера не совпадает")

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
                    mismatches.append("приоритет не перенесён")

                try:
                    actual_mtu = int(ip_config.get("mtu"))
                except (TypeError, ValueError, AttributeError):
                    actual_mtu = None
                if actual_mtu != expected_mtu:
                    mismatches.append(
                        f"MTU: ожидалось {expected_mtu}, получено {actual_mtu}"
                    )

                if expected_ping_profile:
                    actual_profile = self.assigned_ping_profile(item)
                    if actual_profile != expected_ping_profile:
                        mismatches.append("Ping Check не перенесён")

            if not mismatches:
                message = "Новый протокол подтверждён."
                if session_recovered:
                    message = (
                        "Новый протокол подтверждён после "
                        "восстановления RCI-сессии."
                    )
                return True, message, session_recovered

        suffix = ""
        if recovery_messages:
            last_recovery = next(
                (
                    value
                    for value in reversed(recovery_messages)
                    if value
                ),
                "",
            )
            if last_recovery and not session_recovered:
                suffix = f" Последняя попытка восстановления: {last_recovery}"

        return (
            False,
            "Проверка нового протокола не пройдена: "
            + "; ".join(mismatches or ["нет актуальных данных"])
            + suffix,
            session_recovered,
        )

    def verify_old_protocol_removed_after_transient_failure(
        self,
        *,
        source_id: str,
        target_id: str,
    ) -> tuple[bool, str, bool]:
        """Проверяет, успел ли роутер удалить старый интерфейс до обрыва."""
        session_recovered = False
        empty_reads = 0

        for attempt in range(10):
            if attempt:
                time.sleep(min(0.55 + attempt * 0.08, 1.0))

            config = self.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )

            if not isinstance(config, dict) or not config:
                empty_reads += 1
                if empty_reads >= 2 and attempt in {1, 4, 7}:
                    recovered, _message = (
                        self.renew_authenticated_rci_session()
                    )
                    if recovered:
                        session_recovered = True
                        empty_reads = 0
                continue

            target_exists = isinstance(config.get(target_id), dict)
            source_exists = isinstance(config.get(source_id), dict)

            if target_exists and not source_exists:
                return (
                    True,
                    "Прежний интерфейс удалён; результат подтверждён.",
                    session_recovered,
                )

        return (
            False,
            "Не удалось подтвердить удаление прежнего интерфейса.",
            session_recovered,
        )

    def change_cable_internet_protocol(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад смены протокола."""
        return (
            self.wired_internet_service
            .change_cable_internet_protocol(
                interface_id=interface_id,
                settings=settings,
            )
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
            config = self.get_rci_data(
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
                if self.truthy(pppoe_item.get("up")):
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
            elif self.truthy(transport_item.get("up")):
                mismatches.append("транспорт PPPoE не выключен")

            if not isinstance(port_item, dict):
                mismatches.append(
                    "конфигурация LAN-порта не найдена"
                )
            else:
                actual_vid = self.extract_port_access_vid(
                    port_item
                )

                if actual_vid != home_vlan_id:
                    mismatches.append(
                        "LAN-порт не возвращён в домашний "
                        f"VLAN {home_vlan_id}"
                    )

                roles = self.extract_port_roles(port_item)

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
        pppoe_arg = self.quote_cli_argument(pppoe_id)
        transport_arg = self.quote_cli_argument(transport_id)
        port_arg = self.quote_cli_argument(port_id)

        current_config = self.get_rci_data(
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
                + self.quote_cli_argument(role_name)
            )
            ifor = str(role.get("ifor") or "").strip()

            if ifor:
                command += (
                    " for " + self.quote_cli_argument(ifor)
                )

            commands.append(command)

        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()

        if ping_profile.startswith("_KM_"):
            commands.append(
                f"interface {pppoe_arg} ping-check profile "
                + self.quote_cli_argument(ping_profile)
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
            success, message = self.execute_parse_command(command)

            if not success:
                errors.append(f"{command}: {message}")

        if old_priorities:
            success, message = self.set_internet_priorities(
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
            config = self.get_rci_data(
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
                if self.extract_port_access_vid(port_item) != home_vlan_id:
                    mismatches.append(
                        f"LAN-порт не находится в VLAN {home_vlan_id}"
                    )

                roles = self.extract_port_roles(port_item)

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

            actual_priorities = self.current_global_orders(config)

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


    def delete_wired_pppoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления PPPoE."""
        return self.wired_internet_service.delete_wired_pppoe(
            interface_id
        )

    def delete_wired_pptp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления PPTP."""
        return self.wired_internet_service.delete_wired_pptp(
            interface_id
        )


    def delete_wired_l2tp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления L2TP."""
        return self.wired_internet_service.delete_wired_l2tp(
            interface_id
        )


    def delete_wired_ipoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад удаления IPoE."""
        return self.wired_internet_service.delete_wired_ipoe(
            interface_id
        )

    def set_interface_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        """Совместимый фасад включения и выключения интерфейса."""
        if self.port_status_service.PHYSICAL_PORT_PATTERN.fullmatch(
            str(interface_id or "").strip()
        ):
            return self.port_status_service.set_enabled(
                interface_id=interface_id,
                enabled=enabled,
            )
        return self.interface_service.set_enabled(
            interface_id=interface_id,
            enabled=enabled,
        )

    def reconnect_interface(
        self,
        interface_id: str,
    ) -> tuple[bool, str]:
        """Совместимый фасад переподключения интерфейса."""
        return self.interface_service.reconnect(interface_id)

    def verify_interface_admin_state(
        self,
        interface_id: str,
        expected_enabled: bool,
    ) -> tuple[bool, str]:
        """Совместимый фасад проверки административного состояния."""
        return self.interface_service.verify_admin_state(
            interface_id=interface_id,
            expected_enabled=expected_enabled,
        )

    def extract_rci_error(
        self,
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
                    nested = self.extract_rci_error(value)

                    if nested:
                        return nested

            for value in data.values():
                nested = self.extract_rci_error(value)

                if nested:
                    return nested

        elif isinstance(data, list):
            for value in data:
                nested = self.extract_rci_error(value)

                if nested:
                    return nested

        return None

    def build_candidates(
        self,
        local_ip: str | None,
        keendns: str | None,
        connection_mode: str,
    ) -> list[tuple[str, str]]:
        return self.transport.build_candidates(
            local_ip=local_ip,
            keendns=keendns,
            connection_mode=connection_mode,
        )

    @staticmethod
    def create_password_hash(
        username: str,
        password: str,
        realm: str,
        challenge: str,
    ) -> str:
        return RciTransport.create_password_hash(
            username=username,
            password=password,
            realm=realm,
            challenge=challenge,
        )

    def extract_model(
        self,
        data: dict[str, Any],
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.extract_model(data)

    def extract_version(
        self,
        data: dict[str, Any],
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.extract_version(data)

    def find_component_version(
        self,
        data: Any,
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.find_component_version(data)

    @staticmethod
    def value_to_version_text(
        value: Any,
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return SystemStatusService.value_to_version_text(value)

    def extract_cpu_load(
        self,
        data: dict[str, Any],
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.extract_cpu_load(data)

    def extract_uptime(
        self,
        data: dict[str, Any],
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.extract_uptime(data)

    def calculate_ram_usage(
        self,
        memory_total: int | None,
        memory_free: int | None,
        data: dict[str, Any],
    ) -> str | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.calculate_ram_usage(
            memory_total=memory_total,
            memory_free=memory_free,
            data=data,
        )

    @staticmethod
    def parse_memory_pair(
        value: Any,
    ) -> tuple[float, float] | None:
        """Совместимый фасад SystemStatusService."""
        return SystemStatusService.parse_memory_pair(value)

    @staticmethod
    def format_percentage(
        number: float,
    ) -> str:
        """Совместимый фасад SystemStatusService."""
        return SystemStatusService.format_percentage(number)

    @staticmethod
    def format_uptime_seconds(
        total_seconds: int,
    ) -> str:
        """Совместимый фасад SystemStatusService."""
        return SystemStatusService.format_uptime_seconds(total_seconds)

    def extract_integer(
        self,
        data: dict[str, Any],
        keys: tuple[str, ...],
    ) -> int | None:
        """Совместимый фасад SystemStatusService."""
        return self.system_status_service.extract_integer(data, keys)

    def find_first_value(
        self,
        data: Any,
        keys: tuple[str, ...],
    ) -> Any | None:
        normalized_keys = {
            key.lower()
            for key in keys
        }

        if isinstance(data, dict):
            for key, value in data.items():
                if (
                    str(key).lower()
                    in normalized_keys
                    and value is not None
                ):
                    return value

            for value in data.values():
                found = self.find_first_value(
                    value,
                    keys,
                )
                if found is not None:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = self.find_first_value(
                    item,
                    keys,
                )
                if found is not None:
                    return found

        return None

    @staticmethod
    def build_url(
        host: str,
        scheme: str,
    ) -> str:
        return RciTransport.build_url(host, scheme)

    @staticmethod
    def normalize_host(
        value: str,
    ) -> str:
        return RciTransport.normalize_host(value)

    @staticmethod
    def should_verify_ssl(
        base_url: str,
    ) -> bool:
        return RciTransport.should_verify_ssl(base_url)

    @staticmethod
    def remove_duplicates(
        candidates: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        return RciTransport.remove_duplicates(candidates)
