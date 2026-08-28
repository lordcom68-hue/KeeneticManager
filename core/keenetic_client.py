from __future__ import annotations

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
from core.services.interface_configuration_service import (
    InterfaceConfigurationService,
)
from core.services.connection_creation_options_service import (
    ConnectionCreationOptionsService,
)
from core.services.connection_classification_service import (
    ConnectionClassificationService,
)
from core.services.connection_details_service import (
    ConnectionDetailsService,
)
from core.services.connection_result_builder_service import (
    ConnectionResultBuilderService,
)
from core.services.cable_protocol_lifecycle_service import (
    CableProtocolLifecycleService,
)
from core.services.dedicated_wan_lifecycle_service import (
    DedicatedWanLifecycleService,
)
from core.services.wired_deletion_inspection_service import (
    WiredDeletionInspectionService,
)
from core.services.operation_history_service import OperationHistoryService
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
from core.services.wired_ipoe_lifecycle_service import (
    WiredIpoeLifecycleService,
)
from core.services.wired_tunnel_lifecycle_service import (
    WiredTunnelLifecycleService,
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
from core.services.router_refresh_service import RouterRefreshService
from core.services.router_compatibility_service import (
    RouterCompatibilityProfile,
    RouterCompatibilityService,
)
from core.services.system_log_service import SystemLogService
from core.services.system_status_service import SystemStatusService
from core.services.traffic_service import TrafficService
from core.services.usb_service import UsbService
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
    compatibility_profile: dict[str, Any] | None = None
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
        self.compatibility_service = RouterCompatibilityService(router_id)
        self.compatibility_profile: RouterCompatibilityProfile | None = (
            self.compatibility_service.profile
        )

        # Интерфейсы, выключенные пользователем из приложения.
        # Они остаются в списке, чтобы их можно было включить.
        self._manually_disabled_interfaces: set[str] = set()
        self._management_route_url = ""
        self._management_local_ip: str | None = None

        # Интерфейсная логика вынесена в отдельный сервис.
        # KeeneticClient сохраняет старые методы как совместимый фасад.
        self.interface_service = InterfaceService(self)
        self.interface_configuration_service = (
            InterfaceConfigurationService(self)
        )
        self.connection_creation_options_service = (
            ConnectionCreationOptionsService(self)
        )
        self.connection_classification_service = (
            ConnectionClassificationService(self)
        )
        self.connection_details_service = ConnectionDetailsService(self)
        self.connection_result_builder_service = (
            ConnectionResultBuilderService(
                self,
                result_factory=ConnectionResult,
            )
        )
        self.cable_protocol_lifecycle_service = (
            CableProtocolLifecycleService(self)
        )
        self.dedicated_wan_lifecycle_service = (
            DedicatedWanLifecycleService(self)
        )
        self.wired_deletion_inspection_service = (
            WiredDeletionInspectionService(self)
        )
        self.operation_history_service = OperationHistoryService(router_id)
        self.port_status_service = PortStatusService(self)
        self.traffic_service = TrafficService(self)
        self.ping_check_service = PingCheckService(self)
        self.priority_service = PriorityService(self)
        self.wired_internet_service = WiredInternetService(self)
        self.wired_ipoe_lifecycle_service = WiredIpoeLifecycleService(self)
        self.wired_tunnel_lifecycle_service = (
            WiredTunnelLifecycleService(self)
        )
        self.wireless_isp_service = WirelessIspService(self)
        self.wifi_network_service = WiFiNetworkService(self)
        self.wifi_client_service = WiFiClientService(self)
        self.rescue_mode_service = RescueModeService(self)
        self.system_log_service = SystemLogService(self)
        self.system_status_service = SystemStatusService()
        self.usb_service = UsbService(self)
        self.refresh_service = RouterRefreshService(
            self,
            monotonic=lambda: time.monotonic(),
        )

        # Совместимые aliases для старых тестов и внешнего кода.
        # Владельцем кэшей является WiFiNetworkService.
        self._known_wifi_networks = (
            self.wifi_network_service.known_networks
        )
        self._wifi_client_counters = (
            self.wifi_network_service.client_counters
        )
        self._traffic_counters = self.traffic_service.counters

        # Совместимый alias для сервисов предыдущих версий.
        self._ping_check_runtime_cache = (
            self.ping_check_service.runtime_cache
        )

        # Последний успешный RCI-снимок сохраняется немедленно при
        # первом обновлении, затем не чаще одного раза в 30 секунд.
        self._last_snapshot_cache_write = 0.0

    @property
    def session(self) -> requests.Session | None:
        return self.transport.session

    @property
    def _usb_rci_endpoint(self) -> str | None:
        """Backward-compatible alias owned by UsbService."""
        return self.usb_service.rci_endpoint

    @_usb_rci_endpoint.setter
    def _usb_rci_endpoint(self, value: str | None) -> None:
        self.usb_service.rci_endpoint = value

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
                compatibility_profile=self._compatibility_profile_dict(),
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
                self.compatibility_profile = (
                    self.compatibility_service.assess(
                        self.capabilities,
                        self.version_data,
                    )
                )
                self.system_log_service.sync_cached_support()

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
                    compatibility_profile=self._compatibility_profile_dict(),
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
            compatibility_profile=self._compatibility_profile_dict(),
            **self._result_state_fields(),
        )

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
                compatibility_profile=self._compatibility_profile_dict(),
                **self._result_state_fields(),
            )

        try:
            return self.refresh_service.refresh(force_full=force_full)

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
                compatibility_profile=self._compatibility_profile_dict(),
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
        self.usb_service.reset()
        self.refresh_service.clear()

    def _compatibility_profile_dict(self) -> dict[str, Any] | None:
        return (
            self.compatibility_profile.to_dict()
            if self.compatibility_profile is not None
            else None
        )

    def create_session(self) -> requests.Session:
        return self.transport.create_session()

    def get_usb_data(
        self,
        *,
        system_data: dict[str, Any] | None = None,
        interface_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Совместимый фасад UsbService."""
        return self.usb_service.get_data(
            system_data=system_data,
            interface_data=interface_data,
        )

    def detect_usb_capabilities_from_version(
        self,
    ) -> dict[str, bool]:
        """Совместимый фасад UsbService."""
        return self.usb_service.detect_capabilities_from_version()

    @staticmethod
    def split_capability_tokens(
        value: Any,
    ) -> set[str]:
        return UsbService.split_capability_tokens(value)

    def detect_usb_rci_endpoint(
        self,
        *,
        allow_usb: bool,
        allow_storage: bool,
    ) -> str:
        """Совместимый фасад UsbService."""
        return self.usb_service.detect_rci_endpoint(
            allow_usb=allow_usb,
            allow_storage=allow_storage,
        )

    def probe_optional_rci_endpoint(
        self,
        endpoint: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Совместимый фасад UsbService."""
        return self.usb_service.probe_optional_rci_endpoint(endpoint)

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
                compatibility_profile=self.compatibility_profile,
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
        """Совместимый фасад сборки полного снимка роутера."""
        return self.connection_result_builder_service.build(
            system_data=system_data,
            version_data=version_data,
            internet_data=internet_data,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
            ping_check_config_data=ping_check_config_data,
            ping_check_status_data=ping_check_status_data,
            associations_data=associations_data,
            hotspot_data=hotspot_data,
            usb_data=usb_data,
            force_wifi_logical_refresh=force_wifi_logical_refresh,
            client_data_refreshed=client_data_refreshed,
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

    def build_connection_creation_options(
        self,
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Совместимый фасад ConnectionCreationOptionsService."""
        return (
            self.connection_creation_options_service
            .build_connection_creation_options(
                interface_data,
                interface_config_data,
            )
        )

    def inspect_dedicated_wan_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Совместимый фасад WiredDeletionInspectionService."""
        return (
            self.wired_deletion_inspection_service
            .inspect_dedicated_wan_deletion(
                interface_id=interface_id,
                interface_config=interface_config,
                interface_runtime=interface_runtime,
            )
        )

    def inspect_wired_pppoe_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Совместимый фасад WiredDeletionInspectionService."""
        return (
            self.wired_deletion_inspection_service
            .inspect_wired_pppoe_deletion(
                interface_id=interface_id,
                interface_config=interface_config,
                interface_runtime=interface_runtime,
            )
        )

    def inspect_wired_pptp_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Совместимый фасад WiredDeletionInspectionService."""
        return (
            self.wired_deletion_inspection_service
            .inspect_wired_pptp_deletion(
                interface_id=interface_id,
                interface_config=interface_config,
                interface_runtime=interface_runtime,
            )
        )


    def inspect_wired_l2tp_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Совместимый фасад WiredDeletionInspectionService."""
        return (
            self.wired_deletion_inspection_service
            .inspect_wired_l2tp_deletion(
                interface_id=interface_id,
                interface_config=interface_config,
                interface_runtime=interface_runtime,
            )
        )


    @staticmethod
    def apply_connection_delete_context(
        connection: dict[str, Any],
        *,
        context: dict[str, Any],
        delete_kind: str,
        dedicated: bool = False,
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        ConnectionDetailsService.apply_connection_delete_context(
            connection,
            context=context,
            delete_kind=delete_kind,
            dedicated=dedicated,
        )

    @staticmethod
    def clear_connection_delete_context(
        connection: dict[str, Any],
        reason: str,
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        ConnectionDetailsService.clear_connection_delete_context(
            connection, reason
        )

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
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_wired_pppoe_details(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
        )

    def enrich_wired_pptp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_wired_pptp_details(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
        )


    def enrich_wired_l2tp_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_wired_l2tp_details(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
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
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return CableProtocolLifecycleService.inspect_cable_protocol_change(
            cls=cls,
            interface_id=interface_id,
            interface_config=interface_config,
            interface_runtime=interface_runtime,
        )

    def enrich_cable_protocol_change_details(
        self,
        *,
        connections: list[dict[str, Any]],
        interface_data: dict[str, Any],
        interface_config_data: dict[str, Any],
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_cable_protocol_change_details(
            connections=connections,
            interface_data=interface_data,
            interface_config_data=interface_config_data,
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
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.is_internet_candidate(
            item, interface_id, interface_type, is_wireless_isp, has_inet_port
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
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.connection_display_name(
            interface_id,
            interface_name,
            interface_type,
            is_wireless_isp,
            item,
            physical_info,
        )

    def connection_transport_type(
        self,
        interface_id: str,
        interface_type: str,
        is_wireless_isp: bool,
        item: dict[str, Any],
        physical_info: dict[str, str],
    ) -> str:
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.connection_transport_type(
            interface_id,
            interface_type,
            is_wireless_isp,
            item,
            physical_info,
        )

    @staticmethod
    def format_physical_port(
        physical_info: dict[str, str],
    ) -> str:
        """Совместимый фасад ConnectionClassificationService."""
        return ConnectionClassificationService.format_physical_port(
            physical_info
        )

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
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.build_physical_port_map(
            interface_data
        )

    def collect_connection_candidates(
        self,
        data: Any,
        output: list[dict[str, Any]],
    ) -> None:
        """Совместимый фасад ConnectionClassificationService."""
        self.connection_classification_service.collect_connection_candidates(
            data, output
        )

    def connection_is_active(
        self,
        item: dict[str, Any],
        status: str | None,
    ) -> bool:
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.connection_is_active(
            item, status
        )

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
        """Совместимый фасад ConnectionClassificationService."""
        return (
            self.connection_classification_service
            .filter_real_internet_connections(
                connections, interface_data
            )
        )

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
        """Совместимый фасад ConnectionClassificationService."""
        return self.connection_classification_service.is_real_wan_connection(
            item, connection, interface_id, interface_type
        )

    @staticmethod
    def extract_valid_priority(item: dict[str, Any]) -> int | None:
        """Совместимый фасад PriorityService."""
        return PriorityService.extract_valid_priority(item)

    @staticmethod
    def prefix_from_ipv4_mask(
        value: Any,
    ) -> int | None:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.prefix_from_ipv4_mask(value)

    @classmethod
    def extract_configured_ipv4(
        cls,
        address_config: Any,
    ) -> tuple[bool, str, int | None]:
        """Совместимый фасад InterfaceService."""
        del cls
        return InterfaceService.extract_configured_ipv4(address_config)

    @staticmethod
    def normalize_ipv4_text(
        value: Any,
    ) -> str:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.normalize_ipv4_text(value)

    @staticmethod
    def normalize_mac_address(
        value: Any,
    ) -> str:
        """Совместимый фасад InterfaceService."""
        return InterfaceService.normalize_mac_address(value)

    @classmethod
    def extract_configured_mac_settings(
        cls,
        config_item: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Совместимый фасад InterfaceService."""
        del cls
        return InterfaceService.extract_configured_mac_settings(config_item)

    @classmethod
    def validate_manual_mac(
        cls,
        value: Any,
    ) -> tuple[bool, str]:
        """Совместимый фасад InterfaceService."""
        del cls
        return InterfaceService.validate_manual_mac(value)

    def build_interface_mac_command(
        self,
        interface_id: str,
        mac_settings: dict[str, Any],
    ) -> str:
        """Совместимый фасад InterfaceService."""
        return self.interface_service.build_interface_mac_command(
            interface_id,
            mac_settings,
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
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_connection_details(
            connections,
            interface_data,
            interface_config_data,
            ping_check_config_data,
            ping_check_status_data,
        )

    def enrich_basic_interface_fields(
        self,
        *,
        connection: dict[str, Any],
        item: dict[str, Any],
        statistics_source: str,
        interface_data: dict[str, Any],
    ) -> None:
        """Совместимый фасад ConnectionDetailsService."""
        self.connection_details_service.enrich_basic_interface_fields(
            connection=connection,
            item=item,
            statistics_source=statistics_source,
            interface_data=interface_data,
        )

    @classmethod
    def extract_ipv6_address(
        cls,
        value: Any,
    ) -> str | None:
        """Совместимый фасад ConnectionDetailsService."""
        del cls
        return ConnectionDetailsService.extract_ipv6_address(value)

    @classmethod
    def normalize_connection_detail(
        cls,
        value: Any,
    ) -> Any | None:
        """Совместимый фасад ConnectionDetailsService."""
        del cls
        return ConnectionDetailsService.normalize_connection_detail(value)

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
        """Совместимый фасад ConnectionDetailsService."""
        return ConnectionDetailsService.extract_ipv6_details(item)

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
        """Совместимый фасад настройки DNS интерфейса."""
        return (
            self.interface_configuration_service.remove_interface_dns_record(
                interface_id=interface_id,
                address=address,
            )
        )

    def add_interface_dns_record(
        self,
        interface_id: str,
        address: str,
    ) -> tuple[bool, str]:
        """Совместимый фасад настройки DNS интерфейса."""
        return self.interface_configuration_service.add_interface_dns_record(
            interface_id=interface_id,
            address=address,
        )

    def replace_interface_dns_records(
        self,
        interface_id: str,
        addresses: list[str],
    ) -> tuple[bool, str]:
        """Совместимый фасад настройки DNS интерфейса."""
        return (
            self.interface_configuration_service.replace_interface_dns_records(
                interface_id=interface_id,
                addresses=addresses,
            )
        )

    @staticmethod
    def route_missing_message(
        message: str,
    ) -> bool:
        """Совместимый фасад распознавания отсутствующего маршрута."""
        return InterfaceConfigurationService.route_missing_message(message)

    def get_configured_interface_ipv4(
        self,
        interface_id: str,
    ) -> dict[str, Any]:
        """Совместимый фасад чтения сохранённого режима IPv4."""
        return (
            self.interface_configuration_service.get_configured_interface_ipv4(
                interface_id
            )
        )

    def remove_interface_default_route(
        self,
        interface_id: str,
        gateway: str = "",
    ) -> tuple[bool, str]:
        """Совместимый фасад удаления маршрута по умолчанию."""
        return (
            self.interface_configuration_service.remove_interface_default_route(
                interface_id=interface_id,
                gateway=gateway,
            )
        )

    def apply_interface_ipv4_settings(
        self,
        interface_id: str,
        ipv4_settings: dict[str, Any],
        current_gateway: str = "",
    ) -> tuple[bool, str]:
        """Совместимый фасад применения IPv4 интерфейса."""
        return (
            self.interface_configuration_service.apply_interface_ipv4_settings(
                interface_id=interface_id,
                ipv4_settings=ipv4_settings,
                current_gateway=current_gateway,
            )
        )

    def find_interface_default_gateway(
        self,
        interface_id: str,
    ) -> str:
        """Совместимый фасад поиска шлюза интерфейса."""
        return (
            self.interface_configuration_service.find_interface_default_gateway(
                interface_id
            )
        )

    def set_interface_settings(
        self,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str]:
        """Совместимый фасад конфигурации интернет-интерфейса."""
        return self.interface_configuration_service.set_interface_settings(
            interface_id=interface_id,
            settings=settings,
        )

    def verify_interface_settings(
        self,
        interface_id: str,
        expected: dict[str, Any],
    ) -> tuple[bool, str]:
        """Совместимый фасад проверки конфигурации интерфейса."""
        return self.interface_configuration_service.verify_interface_settings(
            interface_id=interface_id,
            expected=expected,
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

    def inspect_wired_ipoe_deletion(
        self,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Совместимый фасад WiredDeletionInspectionService."""
        return (
            self.wired_deletion_inspection_service
            .inspect_wired_ipoe_deletion(
                interface_id=interface_id,
                interface_config=interface_config,
                interface_runtime=interface_runtime,
            )
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.verify_wired_ipoe_skeleton(
            interface_id=interface_id,
            port_id=port_id,
            vlan_id=vlan_id,
        )

    def verify_created_wired_ipoe(
        self,
        *,
        interface_id: str,
        port_id: str,
        vlan_id: int,
        expected_order: int,
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.verify_created_wired_ipoe(
            interface_id=interface_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=expected_order,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.rollback_wired_ipoe_creation(
            interface_id=interface_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
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
        """Совместимый фасад жизненного цикла выделенного WAN."""
        return self.dedicated_wan_lifecycle_service.verify_dedicated_wan_active(
            transport_id=transport_id,
            bridge_id=bridge_id,
            expected_order=expected_order,
        )

    def rollback_dedicated_wan_creation(
        self,
        *,
        bridge_id: str,
        transport_id: str,
        old_priorities: dict[str, int],
        target_interface_id: str = "",
    ) -> tuple[bool, str]:
        """Совместимый фасад жизненного цикла выделенного WAN."""
        return self.dedicated_wan_lifecycle_service.rollback_dedicated_wan_creation(
            bridge_id=bridge_id,
            transport_id=transport_id,
            old_priorities=old_priorities,
            target_interface_id=target_interface_id,
        )

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
        """Совместимый фасад жизненного цикла выделенного WAN."""
        return self.dedicated_wan_lifecycle_service.verify_dedicated_wan_deleted(
            source_id=source_id,
            transport_id=transport_id,
            bridge_id=bridge_id,
            remaining_priorities=remaining_priorities,
        )

    def rollback_dedicated_wan_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад жизненного цикла выделенного WAN."""
        return self.dedicated_wan_lifecycle_service.rollback_dedicated_wan_deletion(
            context=context,
            old_priorities=old_priorities,
        )

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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_pppoe_creation(
            pppoe_id=pppoe_id,
            transport_id=transport_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_pptp_creation(
            pptp_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
        )


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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_l2tp_creation(
            l2tp_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            old_access_vid=old_access_vid,
            old_switch_mode=old_switch_mode,
            old_roles=old_roles,
            old_priorities=old_priorities,
        )


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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_created_wired_pppoe(
            pppoe_id=pppoe_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=expected_order,
            expected_identity=expected_identity,
            expected_service=expected_service,
            expected_mtu=expected_mtu,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_created_wired_pptp(
            pptp_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=expected_order,
            expected_identity=expected_identity,
            expected_server=expected_server,
            expected_mtu=expected_mtu,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_created_wired_l2tp(
            l2tp_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            vlan_id=vlan_id,
            expected_order=expected_order,
            expected_identity=expected_identity,
            expected_server=expected_server,
            expected_mtu=expected_mtu,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.verify_wired_ipoe_delete_preparation(
            interface_id=interface_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
        )

    def rollback_wired_ipoe_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.rollback_wired_ipoe_deletion(
            context=context,
            old_priorities=old_priorities,
        )

    def verify_deleted_wired_ipoe(
        self,
        *,
        interface_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_ipoe_lifecycle_service.verify_deleted_wired_ipoe(
            interface_id=interface_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
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
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return self.cable_protocol_lifecycle_service.create_protocol_on_existing_transport(
            target_protocol=target_protocol,
            target_id=target_id,
            transport_id=transport_id,
            description=description,
            username=username,
            password=password,
            service=service,
            server=server,
        )

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
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return self.cable_protocol_lifecycle_service.rollback_cable_protocol_change(
            source_id=source_id,
            target_id=target_id,
            target_protocol=target_protocol,
            transport_id=transport_id,
            old_priorities=old_priorities,
            ping_profile=ping_profile,
            transport_restore_settings=transport_restore_settings,
        )

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
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return self.cable_protocol_lifecycle_service.renew_authenticated_rci_session()

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
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return self.cable_protocol_lifecycle_service.verify_changed_cable_protocol(
            source_id=source_id,
            target_id=target_id,
            target_protocol=target_protocol,
            transport_id=transport_id,
            expected_order=expected_order,
            expected_username=expected_username,
            expected_service=expected_service,
            expected_server=expected_server,
            expected_mtu=expected_mtu,
            expected_ping_profile=expected_ping_profile,
        )

    def verify_old_protocol_removed_after_transient_failure(
        self,
        *,
        source_id: str,
        target_id: str,
    ) -> tuple[bool, str, bool]:
        """Совместимый фасад сервиса жизненного цикла протокола."""
        return self.cable_protocol_lifecycle_service.verify_old_protocol_removed_after_transient_failure(
            source_id=source_id,
            target_id=target_id,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_wired_pppoe_delete_preparation(
            pppoe_id=pppoe_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_wired_pptp_delete_preparation(
            pptp_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
        )


    def verify_wired_l2tp_delete_preparation(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        manager_profile_detached: bool,
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_wired_l2tp_delete_preparation(
            l2tp_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            manager_profile_detached=manager_profile_detached,
        )


    def rollback_wired_pppoe_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_pppoe_deletion(
            context=context,
            old_priorities=old_priorities,
        )

    def rollback_wired_pptp_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_pptp_deletion(
            context=context,
            old_priorities=old_priorities,
        )


    def rollback_wired_l2tp_deletion(
        self,
        *,
        context: dict[str, Any],
        old_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.rollback_wired_l2tp_deletion(
            context=context,
            old_priorities=old_priorities,
        )


    def verify_deleted_wired_pppoe(
        self,
        *,
        pppoe_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_deleted_wired_pppoe(
            pppoe_id=pppoe_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
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
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_deleted_wired_pptp(
            pptp_id=pptp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
        )


    def verify_deleted_wired_l2tp(
        self,
        *,
        l2tp_id: str,
        transport_id: str,
        port_id: str,
        home_vlan_id: int,
        remaining_priorities: dict[str, int],
    ) -> tuple[bool, str]:
        """Совместимый фасад сервиса жизненного цикла проводного подключения."""
        return self.wired_tunnel_lifecycle_service.verify_deleted_wired_l2tp(
            l2tp_id=l2tp_id,
            transport_id=transport_id,
            port_id=port_id,
            home_vlan_id=home_vlan_id,
            remaining_priorities=remaining_priorities,
        )


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
