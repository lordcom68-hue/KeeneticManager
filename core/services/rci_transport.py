from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from core.app_version import APP_USER_AGENT
from core.rci.diagnostics import DiagnosticCollector, sanitize_value
from core.services.errors import (
    KeeneticAuthenticationError,
    KeeneticConnectionError,
)
from core.services.runtime_paths import router_history_path


SessionGuard = Callable[[requests.Session], None]
EndpointPolicy = Callable[[str], bool]


class RciTransport:
    """
    Низкоуровневый транспорт Keenetic RCI.

    Сервис владеет HTTP-сессией, адресом подключения,
    авторизацией, повторами временных ошибок и журналом попыток.
    Бизнес-логика интерфейсов и подключений остаётся вне него.
    """

    TRANSIENT_HTTP_STATUSES = {429, 502, 503, 504}
    MAX_PERSISTED_HISTORY_BYTES = 2_000_000

    def __init__(
        self,
        *,
        username: str,
        password: str,
        timeout: float = 8.0,
        router_id: int | None = None,
        diagnostics: DiagnosticCollector | None = None,
        session_guard: SessionGuard | None = None,
        endpoint_policy: EndpointPolicy | None = None,
        history_root: Path | None = None,
    ) -> None:
        self.username = str(username or "")
        self.password = str(password or "")
        self.timeout = max(0.5, float(timeout))
        self.router_id = router_id
        self.diagnostics = diagnostics or DiagnosticCollector()
        self.session_guard = session_guard
        self.endpoint_policy = endpoint_policy
        self.history_root = Path(history_root) if history_root is not None else None

        self.session: requests.Session | None = None
        self.base_url: str | None = None
        self.connection_type: str | None = None
        self.verify_ssl = True
        self.connected = False

        self.consecutive_failures = 0
        self.last_success_at: str | None = None
        self.last_error_at: str | None = None
        self.last_error: str | None = None
        self.last_http_status: int | None = None
        self.last_result_message: str = ""
        self._state = "idle"
        self._last_recorded_state = "idle"

    @property
    def history_path(self) -> Path:
        if self.history_root is not None:
            suffix = (
                str(self.router_id)
                if self.router_id is not None
                else "unknown"
            )
            return (
                self.history_root
                / f"connection_attempts_router_{suffix}.jsonl"
            )
        return router_history_path(self.router_id)

    def update_credentials(
        self,
        *,
        username: str,
        password: str,
    ) -> None:
        self.username = str(username or "")
        self.password = str(password or "")

    def record_connection_plan(
        self,
        *,
        local_ip: str | None,
        keendns: str | None,
        connection_mode: str,
        candidates: list[tuple[str, str]],
    ) -> None:
        """Сохраняет начало цикла до создания HTTP-сессии."""
        self._transition_state(
            "connecting",
            message="Начат новый цикл подключения.",
            url="",
            details={
                "connection_mode": str(connection_mode or "auto"),
                "local_ip_configured": bool(str(local_ip or "").strip()),
                "keendns_configured": bool(str(keendns or "").strip()),
                "candidate_count": len(candidates),
                "candidates": [
                    {"url": url, "connection_type": kind}
                    for url, kind in candidates
                ],
            },
        )

    def mark_offline(self, message: str) -> None:
        self.connected = False
        self.last_result_message = str(message or "")
        self._transition_state(
            "offline",
            message=self.last_result_message or "Подключение не выполнено.",
            url=self.base_url or "",
        )

    def record_monitor_state(
        self,
        state: str,
        message: str,
    ) -> None:
        """Сохраняет подтверждённое состояние фонового монитора."""
        normalized = str(state or "").strip().lower()
        if normalized == "offline":
            self.mark_offline(message)
            return
        self._transition_state(
            normalized or "retrying",
            message=str(message or ""),
            url=self.base_url or "",
        )

    def _transition_state(
        self,
        state: str,
        *,
        message: str,
        url: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        normalized = str(state or "idle").strip().lower() or "idle"
        previous = self._state
        self._state = normalized
        self.last_result_message = str(message or self.last_result_message)

        if previous == normalized and self._last_recorded_state == normalized:
            return

        self._last_recorded_state = normalized
        self._record_attempt(
            phase="state_transition",
            method="STATE",
            endpoint="connection",
            url=url,
            attempt=0,
            success=(True if normalized == "connected" else None),
            message=message,
            extra={
                "previous_state": previous,
                "new_state": normalized,
                **dict(details or {}),
            },
        )

    def create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": APP_USER_AGENT,
            }
        )

        if self.session_guard is not None:
            self.session_guard(session)

        return session

    def close(self, *, clear_target: bool = True) -> None:
        if self.session is not None:
            self.session.close()

        self.session = None
        self.connected = False

        if clear_target:
            self.base_url = None
            self.connection_type = None
            self.verify_ssl = True

    def connect_candidate(
        self,
        *,
        base_url: str,
        connection_type: str,
    ) -> None:
        self.close(clear_target=True)

        verify_ssl = self.should_verify_ssl(base_url)
        self.base_url = base_url
        self.connection_type = connection_type
        self.verify_ssl = verify_ssl
        self._transition_state(
            "connecting",
            message=f"Проверяется кандидат {connection_type}.",
            url=base_url,
            details={"connection_type": connection_type},
        )
        self._record_attempt(
            phase="candidate_start",
            method="CONNECT",
            endpoint="candidate",
            url=base_url,
            attempt=1,
            success=None,
            message=f"Начата попытка подключения через {connection_type}.",
        )

        try:
            session = self.create_session()
            self.authenticate(
                session=session,
                base_url=base_url,
                verify_ssl=verify_ssl,
            )
        except Exception as error:
            if "session" in locals():
                session.close()
            self._record_attempt(
                phase="candidate_result",
                method="CONNECT",
                endpoint="candidate",
                url=base_url,
                attempt=1,
                success=False,
                message=f"Кандидат {connection_type} недоступен.",
                error=error,
            )
            self._transition_state(
                "retrying",
                message="Текущий адрес не подошёл; будет проверен следующий.",
                url=base_url,
            )
            raise

        self.session = session
        self.connected = True
        self._mark_success(
            phase="connect",
            method="AUTH",
            endpoint="auth",
            url=f"{base_url}/auth",
            attempt=1,
            status_code=200,
            message="Авторизация выполнена.",
        )
        self._record_attempt(
            phase="candidate_result",
            method="CONNECT",
            endpoint="candidate",
            url=base_url,
            attempt=1,
            success=True,
            status_code=200,
            message=f"Подключение через {connection_type} подтверждено.",
        )
        self._transition_state(
            "connected",
            message=f"Подключение через {connection_type} подтверждено.",
            url=base_url,
        )

    def authenticate(
        self,
        *,
        session: requests.Session,
        base_url: str,
        verify_ssl: bool,
    ) -> None:
        auth_url = f"{base_url}/auth"

        response = self._raw_request(
            session=session,
            method="GET",
            url=auth_url,
            timeout=self.timeout,
            verify_ssl=verify_ssl,
            phase="auth_probe",
            endpoint="auth",
            attempt=1,
        )

        if response.status_code == 200:
            return

        if response.status_code != 401:
            raise KeeneticConnectionError(
                "Неожиданный ответ службы "
                f"авторизации: HTTP {response.status_code}."
            )

        realm = response.headers.get("X-NDM-Realm")
        challenge = response.headers.get("X-NDM-Challenge")

        if not realm or not challenge:
            raise KeeneticConnectionError(
                "Роутер не передал параметры "
                "X-NDM-Realm и X-NDM-Challenge."
            )

        password_hash = self.create_password_hash(
            username=self.username,
            password=self.password,
            realm=realm,
            challenge=challenge,
        )

        auth_response = self._raw_request(
            session=session,
            method="POST",
            url=auth_url,
            timeout=self.timeout,
            verify_ssl=verify_ssl,
            phase="auth_submit",
            endpoint="auth",
            attempt=1,
            json_payload={
                "login": self.username,
                "password": password_hash,
            },
        )

        if auth_response.status_code in (401, 403):
            raise KeeneticAuthenticationError(
                "Неверный логин или пароль."
            )

        if not auth_response.ok:
            raise KeeneticAuthenticationError(
                "Авторизация не выполнена. "
                f"HTTP-код: {auth_response.status_code}."
            )

    def reauthenticate(self) -> bool:
        if not self.base_url:
            return False

        replacement = self.create_session()

        try:
            self.authenticate(
                session=replacement,
                base_url=self.base_url,
                verify_ssl=self.verify_ssl,
            )
        except Exception as error:
            replacement.close()
            self._mark_failure(
                phase="reauthenticate",
                method="AUTH",
                endpoint="auth",
                url=f"{self.base_url}/auth",
                attempt=1,
                error=error,
                message="Повторная авторизация не выполнена.",
            )
            return False

        old_session = self.session
        self.session = replacement
        self.connected = True

        if old_session is not None:
            old_session.close()

        self._mark_success(
            phase="reauthenticate",
            method="AUTH",
            endpoint="auth",
            url=f"{self.base_url}/auth",
            attempt=1,
            status_code=200,
            message="HTTP-сессия пересоздана и авторизована.",
        )
        return True

    def get_json(
        self,
        *,
        endpoint: str,
        required: bool,
    ) -> dict[str, Any]:
        normalized_endpoint = (
            str(endpoint or "")
            .strip()
            .strip("/")
            .lower()
        )

        if (
            self.endpoint_policy is not None
            and not self.endpoint_policy(normalized_endpoint)
        ):
            return {}

        if self.session is None or self.base_url is None:
            if required:
                raise KeeneticConnectionError(
                    "HTTP-сессия не создана."
                )
            return {}

        response = self.request(
            method="GET",
            path=f"/rci/{endpoint}",
            endpoint=normalized_endpoint,
            required=required,
            timeout=self.timeout,
            allow_reauthentication=True,
        )

        if response is None:
            return {}

        try:
            data = response.json()
        except ValueError as error:
            self.diagnostics.record_exception(
                error,
                endpoint=normalized_endpoint,
                message="RCI вернул ответ не в формате JSON.",
            )
            if required:
                raise KeeneticConnectionError(
                    f"Ответ {endpoint} получен не в формате JSON."
                ) from error
            return {}

        if isinstance(data, dict):
            self.diagnostics.record_snapshot(
                normalized_endpoint,
                data,
            )
            self.diagnostics.record_rci_success(
                normalized_endpoint
            )
            return data

        if required:
            raise KeeneticConnectionError(
                f"Команда {endpoint} вернула неожиданный формат."
            )

        return {}

    def request(
        self,
        *,
        method: str,
        path: str,
        endpoint: str,
        required: bool,
        timeout: float,
        json_payload: Any = None,
        allow_reauthentication: bool = True,
        retry_transient: bool | None = None,
    ) -> requests.Response | None:
        if self.session is None or self.base_url is None:
            if required:
                raise KeeneticConnectionError(
                    "HTTP-сессия не создана."
                )
            return None

        url = f"{self.base_url}{path}"
        retry_enabled = (
            method.upper() == "GET"
            if retry_transient is None
            else bool(retry_transient)
        )
        max_attempts = 2
        last_error: BaseException | None = None
        last_status: int | None = None

        for attempt in range(1, max_attempts + 1):
            current_session = self.session

            if current_session is None:
                break

            try:
                response = current_session.request(
                    method=method,
                    url=url,
                    json=json_payload,
                    timeout=max(0.5, float(timeout)),
                    verify=self.verify_ssl,
                    allow_redirects=True,
                )
            except requests.Timeout as error:
                last_error = error
                self._mark_failure(
                    phase="rci",
                    method=method,
                    endpoint=endpoint,
                    url=url,
                    attempt=attempt,
                    error=error,
                    message="Истекло время ожидания RCI.",
                )

                if (
                    retry_enabled
                    and required
                    and attempt < max_attempts
                ):
                    self._transition_state(
                        "retrying",
                        message="Повтор RCI после тайм-аута.",
                        url=url,
                    )
                    self._retry_backoff(attempt)
                    self.reauthenticate()
                    continue
                break
            except requests.ConnectionError as error:
                last_error = error
                self._mark_failure(
                    phase="rci",
                    method=method,
                    endpoint=endpoint,
                    url=url,
                    attempt=attempt,
                    error=error,
                    message="Ошибка соединения при RCI-запросе.",
                )

                if (
                    retry_enabled
                    and required
                    and attempt < max_attempts
                ):
                    self._transition_state(
                        "retrying",
                        message="Повтор RCI после ошибки соединения.",
                        url=url,
                    )
                    self._retry_backoff(attempt)
                    self.reauthenticate()
                    continue
                break
            except requests.RequestException as error:
                last_error = error
                self._mark_failure(
                    phase="rci",
                    method=method,
                    endpoint=endpoint,
                    url=url,
                    attempt=attempt,
                    error=error,
                    message="Ошибка выполнения RCI-запроса.",
                )
                break

            last_status = response.status_code

            if response.status_code in (401, 403):
                self._mark_failure(
                    phase="rci",
                    method=method,
                    endpoint=endpoint,
                    url=url,
                    attempt=attempt,
                    status_code=response.status_code,
                    message="Сессия авторизации завершена.",
                )

                if (
                    allow_reauthentication
                    and attempt < max_attempts
                ):
                    self._transition_state(
                        "retrying",
                        message="Повтор RCI после завершения авторизации.",
                        url=url,
                    )
                    if self.reauthenticate():
                        self._retry_backoff(attempt)
                        continue

                if required:
                    raise KeeneticAuthenticationError(
                        "Сессия авторизации завершена."
                    )
                return None

            if not response.ok:
                message = (
                    f"HTTP {response.status_code} "
                    "при RCI-запросе."
                )
                event_type = (
                    "keendns_unavailable"
                    if (
                        response.status_code == 503
                        and self.connection_type == "KeenDNS"
                    )
                    else "http_error"
                )
                self.diagnostics.record_event(
                    event_type,
                    message,
                    endpoint=endpoint,
                    details={
                        "status_code": response.status_code,
                        "connection_type": self.connection_type,
                        "response": response.text[:1000],
                        "attempt": attempt,
                    },
                )
                self._mark_failure(
                    phase="rci",
                    method=method,
                    endpoint=endpoint,
                    url=url,
                    attempt=attempt,
                    status_code=response.status_code,
                    message=message,
                )

                if (
                    response.status_code
                    in self.TRANSIENT_HTTP_STATUSES
                    and attempt < max_attempts
                    and retry_enabled
                    and (
                        required
                        or self.connection_type == "KeenDNS"
                    )
                ):
                    self._transition_state(
                        "retrying",
                        message=(
                            f"Повтор RCI после HTTP {response.status_code}."
                        ),
                        url=url,
                    )
                    self._retry_backoff(attempt)
                    continue

                if required:
                    if (
                        response.status_code == 503
                        and self.connection_type == "KeenDNS"
                    ):
                        raise KeeneticConnectionError(
                            "KeenDNS временно не может передать "
                            "запрос роутеру (HTTP 503)."
                        )
                    raise KeeneticConnectionError(
                        f"HTTP-ошибка {response.status_code} "
                        f"при запросе {endpoint}."
                    )
                return None

            had_failures = self.consecutive_failures > 0
            self._mark_success(
                phase="rci",
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                status_code=response.status_code,
                message="RCI-запрос выполнен.",
                record_attempt=(
                    had_failures or attempt > 1
                ),
            )
            return response

        if required:
            if isinstance(last_error, requests.Timeout):
                raise KeeneticConnectionError(
                    "Истекло время ожидания ответа."
                ) from last_error
            if isinstance(last_error, requests.ConnectionError):
                raise KeeneticConnectionError(
                    "Устройство недоступно или порт закрыт."
                ) from last_error
            if last_error is not None:
                raise KeeneticConnectionError(
                    str(last_error)
                ) from last_error
            if last_status is not None:
                raise KeeneticConnectionError(
                    f"HTTP-ошибка {last_status} "
                    f"при запросе {endpoint}."
                )
            raise KeeneticConnectionError(
                f"Не удалось выполнить запрос {endpoint}."
            )

        return None

    def _raw_request(
        self,
        *,
        session: requests.Session,
        method: str,
        url: str,
        timeout: float,
        verify_ssl: bool,
        phase: str,
        endpoint: str,
        attempt: int,
        json_payload: Any = None,
    ) -> requests.Response:
        try:
            response = session.request(
                method=method,
                url=url,
                json=json_payload,
                timeout=max(0.5, float(timeout)),
                verify=verify_ssl,
                allow_redirects=True,
            )
        except requests.Timeout as error:
            self._mark_failure(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                error=error,
                message="Истекло время ожидания ответа.",
            )
            raise KeeneticConnectionError(
                "Истекло время ожидания ответа."
            ) from error
        except requests.ConnectionError as error:
            self._mark_failure(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                error=error,
                message="Устройство недоступно или порт закрыт.",
            )
            raise KeeneticConnectionError(
                "Устройство недоступно или порт закрыт."
            ) from error
        except requests.RequestException as error:
            self._mark_failure(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                error=error,
                message="Ошибка HTTP-запроса.",
            )
            raise KeeneticConnectionError(str(error)) from error

        if (
            response.ok
            or (
                phase == "auth_probe"
                and response.status_code == 401
            )
        ):
            self._mark_success(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                status_code=response.status_code,
                message="HTTP-запрос выполнен.",
            )
        else:
            self._mark_failure(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                status_code=response.status_code,
                message=f"HTTP {response.status_code}.",
            )

        return response

    def connection_state(self) -> str:
        if self.connected and self.consecutive_failures > 0:
            return "unstable"
        if self.connected:
            return "connected"
        return self._state

    def _mark_success(
        self,
        *,
        phase: str,
        method: str,
        endpoint: str,
        url: str,
        attempt: int,
        status_code: int | None,
        message: str,
        record_attempt: bool = True,
    ) -> None:
        had_failures = self.consecutive_failures > 0
        self.consecutive_failures = 0
        self.last_success_at = self._now_iso()
        self.last_http_status = status_code
        self.last_result_message = message
        if self.connected and had_failures:
            self._transition_state(
                "connected",
                message="Связь с роутером восстановлена.",
                url=url,
            )
        if record_attempt:
            self._record_attempt(
                phase=phase,
                method=method,
                endpoint=endpoint,
                url=url,
                attempt=attempt,
                success=True,
                status_code=status_code,
                message=message,
            )

    def _mark_failure(
        self,
        *,
        phase: str,
        method: str,
        endpoint: str,
        url: str,
        attempt: int,
        message: str,
        status_code: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.consecutive_failures += 1
        self.last_error_at = self._now_iso()
        self.last_error = str(error or message)
        self.last_result_message = message
        self.last_http_status = status_code
        self._record_attempt(
            phase=phase,
            method=method,
            endpoint=endpoint,
            url=url,
            attempt=attempt,
            success=False,
            status_code=status_code,
            message=message,
            error=error,
        )
        self._transition_state(
            "unstable" if self.connected else "retrying",
            message=message,
            url=url,
        )

    def _record_attempt(
        self,
        *,
        phase: str,
        method: str,
        endpoint: str,
        url: str,
        attempt: int,
        success: bool | None,
        message: str,
        status_code: int | None = None,
        error: BaseException | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "timestamp": self._now_iso(),
            "router_id": self.router_id,
            "phase": phase,
            "method": method.upper(),
            "endpoint": endpoint,
            "url": url,
            "connection_type": self.connection_type,
            "attempt": attempt,
            "success": (None if success is None else bool(success)),
            "status_code": status_code,
            "message": message,
            "connection_state": self.connection_state(),
            "consecutive_failures": self.consecutive_failures,
        }

        parsed = urlparse(str(url or ""))
        if parsed.hostname:
            item["target_host"] = parsed.hostname
        if parsed.scheme:
            item["target_scheme"] = parsed.scheme

        failure_kind = None
        if success is False or error is not None:
            failure_kind = self.classify_failure(
                error=error,
                status_code=status_code,
                connection_type=self.connection_type,
                message=message,
            )
        if failure_kind:
            item["failure_kind"] = failure_kind

        if error is not None:
            item["error_type"] = type(error).__name__
            item["error"] = str(error)

        if extra:
            item.update(dict(extra))

        self.diagnostics.record_connection_attempt(item)
        self._append_history_file(item)

    @staticmethod
    def classify_failure(
        *,
        error: BaseException | None,
        status_code: int | None,
        connection_type: str | None,
        message: str,
    ) -> str | None:
        if status_code in (401, 403):
            return "authentication"
        if status_code == 503 and connection_type == "KeenDNS":
            return "keendns_http_503"
        if status_code is not None and status_code >= 400:
            return "http_error"

        # KeeneticConnectionError часто оборачивает исходную ошибку
        # requests через ``raise ... from error``. Проходим по цепочке,
        # чтобы офлайн-архив сохранил настоящую причину.
        chain: list[BaseException] = []
        current = error
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            chain.append(current)
            next_error = current.__cause__ or current.__context__
            current = next_error if isinstance(next_error, BaseException) else None

        if any(isinstance(item, KeeneticAuthenticationError) for item in chain):
            return "authentication"
        if any(isinstance(item, requests.Timeout) for item in chain):
            return "timeout"

        connection_errors = [
            item for item in chain
            if isinstance(item, requests.ConnectionError)
        ]
        if connection_errors:
            text = " ".join(str(item) for item in connection_errors).lower()
            if any(
                marker in text
                for marker in (
                    "name or service not known",
                    "temporary failure in name resolution",
                    "failed to resolve",
                    "getaddrinfo failed",
                    "nameresolutionerror",
                    "nodename nor servname provided",
                )
            ):
                return "dns_error"
            return "connection_error"

        combined = " ".join(
            [
                *(f"{type(item).__name__}: {item}" for item in chain),
                str(message or ""),
            ]
        ).lower()
        if "http 503" in combined and connection_type == "KeenDNS":
            return "keendns_http_503"
        if any(marker in combined for marker in ("auth", "парол", "логин")):
            return "authentication"
        if any(marker in combined for marker in ("тайм", "время ожидания", "timeout")):
            return "timeout"
        if any(
            marker in combined
            for marker in (
                "failed to resolve",
                "getaddrinfo",
                "name resolution",
                "dns",
            )
        ):
            return "dns_error"
        if error is not None:
            return "request_error"
        return None

    def _append_history_file(self, item: dict[str, Any]) -> None:
        try:
            path = self.history_path
            path.parent.mkdir(parents=True, exist_ok=True)

            if (
                path.exists()
                and path.stat().st_size
                > self.MAX_PERSISTED_HISTORY_BYTES
            ):
                rotated = path.with_suffix(".jsonl.1")
                rotated.unlink(missing_ok=True)
                path.replace(rotated)

            safe = sanitize_value(item)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        safe,
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        except Exception:
            # Диагностика не должна ломать сетевое соединение.
            pass

    @staticmethod
    def _retry_backoff(attempt: int) -> None:
        time.sleep(min(1.5, 0.35 * max(1, attempt)))

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat()

    def build_candidates(
        self,
        *,
        local_ip: str | None,
        keendns: str | None,
        connection_mode: str,
    ) -> list[tuple[str, str]]:
        local_ip = (local_ip or "").strip()
        keendns = self.normalize_host(keendns or "")
        candidates: list[tuple[str, str]] = []

        if connection_mode in ("auto", "local") and local_ip:
            # Локальный интерфейс Keenetic всегда открываем по HTTP.
            # Предварительная попытка HTTPS на моделях, которые
            # молча отбрасывают TCP/443, задерживала подключение на
            # полный сетевой тайм-аут. Схема, случайно введённая в
            # поле адреса, удаляется build_url и не меняет контракт.
            candidates.append(
                (self.build_url(local_ip, "http"), "Локальная сеть")
            )

        if connection_mode in ("auto", "keendns") and keendns:
            # Удалённый KeenDNS использует только защищённый HTTPS.
            candidates.append(
                (self.build_url(keendns, "https"), "KeenDNS")
            )

        return self.remove_duplicates(candidates)

    @staticmethod
    def create_password_hash(
        *,
        username: str,
        password: str,
        realm: str,
        challenge: str,
    ) -> str:
        md5_source = f"{username}:{realm}:{password}".encode("utf-8")
        md5_digest = hashlib.md5(md5_source).hexdigest()
        sha_source = f"{challenge}{md5_digest}".encode("utf-8")
        return hashlib.sha256(sha_source).hexdigest()

    @staticmethod
    def build_url(host: str, scheme: str) -> str:
        host = host.strip().rstrip("/")

        if "://" in host:
            parsed = urlparse(host)
            host = parsed.netloc or parsed.path

        try:
            address = ipaddress.ip_address(host)
            if address.version == 6:
                host = f"[{host}]"
        except ValueError:
            pass

        return f"{scheme}://{host}"

    @staticmethod
    def normalize_host(value: str) -> str:
        value = value.strip().rstrip("/")

        if "://" in value:
            parsed = urlparse(value)
            return parsed.netloc or parsed.path

        return value

    @staticmethod
    def should_verify_ssl(base_url: str) -> bool:
        parsed = urlparse(base_url)
        host = parsed.hostname or ""

        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True

    @staticmethod
    def remove_duplicates(
        candidates: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        seen: set[str] = set()

        for candidate in candidates:
            url = candidate[0]
            if url in seen:
                continue
            seen.add(url)
            result.append(candidate)

        return result
