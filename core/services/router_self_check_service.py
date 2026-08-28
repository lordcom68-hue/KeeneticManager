from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.services.rci_transport import RciTransport
from core.services.runtime_paths import (
    router_self_check_history_path,
    router_self_check_path,
)


ProgressCallback = Callable[[int, str], None]
CancellationCheck = Callable[[], bool]
TransportFactory = Callable[..., RciTransport]


class RouterSelfCheckCancelled(Exception):
    """Read-only router self-check was interrupted."""


@dataclass(frozen=True, slots=True)
class RouterSelfCheckItem:
    key: str
    category: str
    title: str
    status: str
    summary: str
    duration_ms: int | None = None
    change: str = "unchanged"
    previous_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RouterSelfCheckItem:
        duration = value.get("duration_ms")
        return cls(
            key=str(value.get("key") or ""),
            category=str(value.get("category") or ""),
            title=str(value.get("title") or ""),
            status=str(value.get("status") or "skipped"),
            summary=str(value.get("summary") or ""),
            duration_ms=(int(duration) if duration is not None else None),
            change=str(value.get("change") or "unchanged"),
            previous_status=(
                str(value["previous_status"])
                if value.get("previous_status") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RouterSelfCheckResult:
    schema_version: int
    router_id: int | None
    started_at: str
    finished_at: str
    overall_status: str
    overall_label: str
    duration_ms: int
    model: str
    version: str
    connection_type: str
    checks: tuple[RouterSelfCheckItem, ...]
    run_mode: str = "full"
    executed_keys: tuple[str, ...] = ()

    @property
    def passed_count(self) -> int:
        return sum(item.status == "passed" for item in self.checks)

    @property
    def warning_count(self) -> int:
        return sum(item.status == "warning" for item in self.checks)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.checks)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "skipped" for item in self.checks)

    @property
    def problem_keys(self) -> tuple[str, ...]:
        return tuple(
            item.key
            for item in self.checks
            if item.status in {"warning", "failed"}
        )

    @property
    def new_problem_count(self) -> int:
        return sum(item.change == "new_problem" for item in self.checks)

    @property
    def resolved_count(self) -> int:
        return sum(item.change == "resolved" for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "router_id": self.router_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status,
            "overall_label": self.overall_label,
            "duration_ms": self.duration_ms,
            "model": self.model,
            "version": self.version,
            "connection_type": self.connection_type,
            "run_mode": self.run_mode,
            "executed_keys": list(self.executed_keys),
            "counts": {
                "passed": self.passed_count,
                "warning": self.warning_count,
                "failed": self.failed_count,
                "skipped": self.skipped_count,
            },
            "checks": [item.to_dict() for item in self.checks],
            "privacy": {
                "contains_router_name": False,
                "contains_addresses": False,
                "contains_credentials": False,
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RouterSelfCheckResult:
        checks = tuple(
            RouterSelfCheckItem.from_dict(item)
            for item in (value.get("checks") or [])
            if isinstance(item, dict)
        )
        return cls(
            schema_version=int(value.get("schema_version") or 1),
            router_id=(
                int(value["router_id"])
                if value.get("router_id") is not None
                else None
            ),
            started_at=str(value.get("started_at") or ""),
            finished_at=str(value.get("finished_at") or ""),
            overall_status=str(value.get("overall_status") or "passed"),
            overall_label=str(value.get("overall_label") or ""),
            duration_ms=int(value.get("duration_ms") or 0),
            model=str(value.get("model") or ""),
            version=str(value.get("version") or ""),
            connection_type=str(value.get("connection_type") or ""),
            checks=checks,
            run_mode=str(value.get("run_mode") or "full"),
            executed_keys=tuple(
                str(key)
                for key in (value.get("executed_keys") or [])
                if str(key).strip()
            ),
        )


class RouterSelfCheckService:
    """Perform bounded read-only checks against an already connected router."""

    SCHEMA_VERSION = 2
    HISTORY_LIMIT = 10
    SLOW_REQUEST_MS = 6_000
    SNAPSHOT_CHECK_KEYS = frozenset(
        {
            "full_refresh",
            "snapshot_data",
            "system_data",
            "internet_data",
            "clients_data",
            "ports_data",
            "wifi_data",
            "compatibility_profile",
        }
    )
    OPTIONAL_COMPATIBILITY_FEATURES = frozenset(
        {
            "usb_storage",
            "vpn",
        }
    )
    STATUS_LABELS = {
        "passed": "Проверка пройдена",
        "warning": "Есть предупреждения",
        "failed": "Обнаружены ошибки",
    }
    FAILURE_LABELS = {
        "authentication": "Авторизация не выполнена.",
        "connection_error": "Адрес не отвечает.",
        "dns_error": "Имя KeenDNS не разрешается.",
        "keendns_http_503": "Служба KeenDNS временно недоступна.",
        "timeout": "Превышено время ожидания ответа.",
        "http_error": "Сервер вернул ошибку HTTP.",
        "request_error": "Запрос завершился ошибкой.",
    }

    def __init__(
        self,
        client: Any,
        *,
        router_id: int | None = None,
        path: str | Path | None = None,
        history_path: str | Path | None = None,
        transport_factory: TransportFactory = RciTransport,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.router_id = router_id
        self.path = (
            Path(path)
            if path is not None
            else router_self_check_path(router_id)
        )
        self.history_path = (
            Path(history_path)
            if history_path is not None
            else (
                router_self_check_history_path(router_id)
                if path is None
                else self.path.with_name(
                    f"{self.path.stem}_history{self.path.suffix}"
                )
            )
        )
        self.transport_factory = transport_factory
        self.now = now or (lambda: datetime.now().astimezone())
        self.monotonic = monotonic

    def run(
        self,
        *,
        local_ip: str | None,
        keendns: str | None,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCheck | None = None,
        check_keys: set[str] | tuple[str, ...] | list[str] | None = None,
    ) -> RouterSelfCheckResult:
        notify = progress or (lambda _value, _message: None)
        is_cancelled = cancelled or (lambda: False)
        started_at = self.now()
        started = self.monotonic()
        checks: list[RouterSelfCheckItem] = []
        previous = self.load_latest()
        selected = (
            {
                str(key).strip()
                for key in check_keys
                if str(key).strip()
            }
            if check_keys is not None
            else None
        )
        if selected is not None and previous is None:
            selected = None
        run_mode = "problems" if selected is not None else "full"
        self.last_refresh_result: Any = None

        def checkpoint(value: int, message: str) -> None:
            if is_cancelled():
                raise RouterSelfCheckCancelled(
                    "Самопроверка остановлена вместе с мониторингом."
                )
            notify(max(0, min(100, int(value))), str(message))

        if selected is None or "active_session" in selected:
            checkpoint(3, "Проверяется активное подключение...")
            connected = bool(
                getattr(self.client, "connected", False)
                and getattr(self.client, "session", None) is not None
                and getattr(self.client, "base_url", None)
            )
            checks.append(
                RouterSelfCheckItem(
                    key="active_session",
                    category="Подключение",
                    title="Активная сессия мониторинга",
                    status="passed" if connected else "failed",
                    summary=(
                        "Авторизованная HTTP-сессия активна."
                        if connected
                        else "У монитора нет активной авторизованной сессии."
                    ),
                )
            )

        candidates = self._channel_candidates(local_ip, keendns)
        for index, (key, title, target, connection_type) in enumerate(candidates):
            if selected is not None and key not in selected:
                continue
            checkpoint(
                10 + index * 17,
                f"Проверяется канал «{title}»...",
            )
            checks.append(
                self._probe_channel(
                    key=key,
                    title=title,
                    target=target,
                    connection_type=connection_type,
                )
            )

        refresh_result: Any = None
        needs_snapshot = bool(
            selected is None or selected.intersection(self.SNAPSHOT_CHECK_KEYS)
        )
        if needs_snapshot:
            checkpoint(48, "Получается полный контрольный снимок...")
            refresh_started = self.monotonic()
            refresh_error = ""
            try:
                refresh_result = self.client.refresh(force_full=True)
            except Exception as error:
                refresh_error = self._safe_failure_summary(error, None)
            self.last_refresh_result = refresh_result
            refresh_duration = self._elapsed_ms(refresh_started)
            refresh_success = bool(
                refresh_result is not None
                and getattr(refresh_result, "success", False)
            )
            if refresh_success:
                refresh_status = (
                    "warning"
                    if refresh_duration > self.SLOW_REQUEST_MS
                    else "passed"
                )
                refresh_summary = (
                    "Снимок получен, но запрос выполнялся дольше 6 секунд."
                    if refresh_status == "warning"
                    else "Полный снимок получен без изменения настроек."
                )
            else:
                refresh_status = "failed"
                refresh_summary = (
                    refresh_error
                    or "Контрольный цикл обновления не завершён."
                )
            checks.append(
                RouterSelfCheckItem(
                    key="full_refresh",
                    category="Обновление",
                    title="Контрольный цикл обновления",
                    status=refresh_status,
                    summary=refresh_summary,
                    duration_ms=refresh_duration,
                )
            )
            checkpoint(65, "Проверяется состав полученных данных...")
            checks.extend(self._data_checks(refresh_result))
            checkpoint(82, "Проверяется профиль совместимости...")
            checks.append(self._compatibility_check(refresh_result))

        if selected is None or "system_log" in selected:
            checkpoint(90, "Проверяется чтение системного журнала...")
            checks.append(self._system_log_check(refresh_result))

        checkpoint(97, "Формируется обезличенный отчёт...")
        executed_keys = tuple(item.key for item in checks)
        compared_checks = self._compare_and_merge_checks(
            checks,
            previous=previous,
            partial=selected is not None,
        )
        overall_status = self._overall_status(list(compared_checks))
        finished_at = self.now()
        result = RouterSelfCheckResult(
            schema_version=self.SCHEMA_VERSION,
            router_id=self.router_id,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            overall_status=overall_status,
            overall_label=self.STATUS_LABELS[overall_status],
            duration_ms=self._elapsed_ms(started),
            model=str(
                getattr(refresh_result, "model", "")
                or getattr(previous, "model", "")
                or ""
            ),
            version=str(
                getattr(refresh_result, "version", "")
                or getattr(previous, "version", "")
                or ""
            ),
            connection_type=str(
                getattr(self.client, "connection_type", "") or ""
            ),
            checks=compared_checks,
            run_mode=run_mode,
            executed_keys=executed_keys,
        )
        self.save(result)
        notify(100, "Самопроверка завершена.")
        return result

    def _channel_candidates(
        self,
        local_ip: str | None,
        keendns: str | None,
    ) -> tuple[tuple[str, str, str, str], ...]:
        builder = getattr(getattr(self.client, "transport", None), "build_url", None)
        if not callable(builder):
            builder = RciTransport.build_url
        local_target = (
            builder(str(local_ip), "http")
            if str(local_ip or "").strip()
            else ""
        )
        keendns_target = (
            builder(str(keendns), "https")
            if str(keendns or "").strip()
            else ""
        )
        return (
            ("local_http", "Локальный HTTP", local_target, "Локальная сеть"),
            ("keendns_https", "KeenDNS HTTPS", keendns_target, "KeenDNS"),
        )

    def _probe_channel(
        self,
        *,
        key: str,
        title: str,
        target: str,
        connection_type: str,
    ) -> RouterSelfCheckItem:
        if not target:
            return RouterSelfCheckItem(
                key=key,
                category="Подключение",
                title=title,
                status="skipped",
                summary="Адрес этого канала не настроен.",
            )

        timeout = min(
            5.0,
            max(1.0, float(getattr(self.client, "timeout", 5.0) or 5.0)),
        )
        transport = self.transport_factory(
            username=str(getattr(self.client, "username", "") or ""),
            password=str(getattr(self.client, "password", "") or ""),
            timeout=timeout,
            router_id=self.router_id,
            session_guard=getattr(
                self.client,
                "install_optional_rci_guard",
                None,
            ),
            persist_history=False,
        )
        started = self.monotonic()
        try:
            transport.connect_candidate(
                base_url=target,
                connection_type=connection_type,
            )
            version_data = transport.get_json(
                endpoint="show/version",
                required=True,
            )
            if not version_data:
                raise RuntimeError("empty version response")
        except Exception as error:
            duration = self._elapsed_ms(started)
            return RouterSelfCheckItem(
                key=key,
                category="Подключение",
                title=title,
                status="warning",
                summary=self._safe_failure_summary(error, connection_type),
                duration_ms=duration,
            )
        finally:
            transport.close()

        duration = self._elapsed_ms(started)
        slow = duration > self.SLOW_REQUEST_MS
        return RouterSelfCheckItem(
            key=key,
            category="Подключение",
            title=title,
            status="warning" if slow else "passed",
            summary=(
                "Канал отвечает, но медленнее 6 секунд."
                if slow
                else "Авторизация и чтение RCI подтверждены."
            ),
            duration_ms=duration,
        )

    def _data_checks(self, result: Any) -> list[RouterSelfCheckItem]:
        if result is None or not getattr(result, "success", False):
            return [
                RouterSelfCheckItem(
                    key="snapshot_data",
                    category="Данные",
                    title="Состав контрольного снимка",
                    status="failed",
                    summary="Данные нельзя проверить без успешного снимка.",
                )
            ]

        checks = [
            self._value_check(
                "system_data",
                "Система и ресурсы",
                getattr(result, "system_data", None),
            ),
            self._value_check(
                "internet_data",
                "Интернет-подключения",
                getattr(result, "internet_connections", None),
            ),
            self._value_check(
                "clients_data",
                "Клиенты Wi-Fi и Ethernet",
                getattr(result, "clients", None),
            ),
            self._value_check(
                "ports_data",
                "Физические порты",
                getattr(result, "ports", None),
            ),
        ]
        wifi_state = self._feature_state(result, "wifi_management")
        if wifi_state == "unavailable":
            checks.append(
                RouterSelfCheckItem(
                    key="wifi_data",
                    category="Данные",
                    title="Сети Wi-Fi",
                    status="skipped",
                    summary="Управление Wi-Fi не поддерживается этим профилем.",
                )
            )
        else:
            checks.append(
                self._value_check(
                    "wifi_data",
                    "Сети Wi-Fi",
                    getattr(result, "wifi_networks", None),
                )
            )
        return checks

    @staticmethod
    def _value_check(
        key: str,
        title: str,
        value: Any,
    ) -> RouterSelfCheckItem:
        if value is None:
            return RouterSelfCheckItem(
                key=key,
                category="Данные",
                title=title,
                status="failed",
                summary="Раздел отсутствует в контрольном снимке.",
            )
        count = len(value) if isinstance(value, (dict, list, tuple, set)) else 1
        return RouterSelfCheckItem(
            key=key,
            category="Данные",
            title=title,
            status="passed",
            summary=f"Раздел получен; объектов: {count}.",
        )

    def _compatibility_check(self, result: Any) -> RouterSelfCheckItem:
        profile = getattr(result, "compatibility_profile", None)
        if not isinstance(profile, dict):
            profile_object = getattr(self.client, "compatibility_profile", None)
            to_dict = getattr(profile_object, "to_dict", None)
            profile = to_dict() if callable(to_dict) else {}
        features = [
            item
            for item in (profile.get("features") or [])
            if isinstance(item, dict)
        ]
        if not features:
            return RouterSelfCheckItem(
                key="compatibility_profile",
                category="Совместимость",
                title="Профиль возможностей",
                status="warning",
                summary="Профиль возможностей ещё не сформирован.",
            )
        unavailable: list[str] = []
        limited: list[str] = []
        optional_unavailable: list[str] = []
        for item in features:
            key = str(item.get("key") or "").strip()
            state = str(item.get("state") or "").strip()
            label = str(item.get("label") or key or "Функция").strip()
            if state == "unavailable":
                target = (
                    optional_unavailable
                    if key in self.OPTIONAL_COMPATIBILITY_FEATURES
                    else unavailable
                )
                target.append(label)
            elif state in {"limited", "monitoring"}:
                limited.append(label)

        summary_parts = [f"Функций проверено: {len(features)}."]
        if limited:
            summary_parts.append(
                "Ограничены: " + ", ".join(limited) + "."
            )
        if unavailable:
            summary_parts.append(
                "Недоступны: " + ", ".join(unavailable) + "."
            )
        if optional_unavailable:
            summary_parts.append(
                "Необязательные функции отсутствуют: "
                + ", ".join(optional_unavailable)
                + "."
            )
        if not limited and not unavailable:
            summary_parts.append("Основные функции доступны.")

        status = "warning" if unavailable or limited else "passed"
        return RouterSelfCheckItem(
            key="compatibility_profile",
            category="Совместимость",
            title="Профиль возможностей",
            status=status,
            summary=" ".join(summary_parts),
        )

    def _system_log_check(self, result: Any) -> RouterSelfCheckItem:
        if self._feature_state(result, "system_log") == "unavailable":
            return RouterSelfCheckItem(
                key="system_log",
                category="Журнал",
                title="Системный журнал KeeneticOS",
                status="skipped",
                summary="Команда журнала не поддерживается этим роутером.",
            )
        service = getattr(self.client, "system_log_service", None)
        read = getattr(service, "read", None)
        if not callable(read):
            return RouterSelfCheckItem(
                key="system_log",
                category="Журнал",
                title="Системный журнал KeeneticOS",
                status="warning",
                summary="Сервис чтения журнала недоступен.",
            )
        started = self.monotonic()
        try:
            success, _message, entries = read(200)
        except Exception as error:
            return RouterSelfCheckItem(
                key="system_log",
                category="Журнал",
                title="Системный журнал KeeneticOS",
                status="warning",
                summary=self._safe_failure_summary(error, None),
                duration_ms=self._elapsed_ms(started),
            )
        duration = self._elapsed_ms(started)
        status = (
            "passed"
            if success and duration <= self.SLOW_REQUEST_MS
            else "warning"
        )
        return RouterSelfCheckItem(
            key="system_log",
            category="Журнал",
            title="Системный журнал KeeneticOS",
            status=status,
            summary=(
                f"Чтение выполнено; записей: {len(entries or [])}."
                if success
                else "Журнал не удалось прочитать."
            ),
            duration_ms=duration,
        )

    @staticmethod
    def _feature_state(result: Any, key: str) -> str:
        profile = getattr(result, "compatibility_profile", None)
        if not isinstance(profile, dict):
            return "unknown"
        for item in profile.get("features") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("key") or "") == key:
                return str(item.get("state") or "unknown")
        return "unknown"

    @staticmethod
    def _overall_status(checks: list[RouterSelfCheckItem]) -> str:
        if any(item.status == "failed" for item in checks):
            return "failed"
        if any(item.status == "warning" for item in checks):
            return "warning"
        return "passed"

    @staticmethod
    def _is_problem(status: str | None) -> bool:
        return str(status or "") in {"warning", "failed"}

    def _compare_and_merge_checks(
        self,
        checks: list[RouterSelfCheckItem],
        *,
        previous: RouterSelfCheckResult | None,
        partial: bool,
    ) -> tuple[RouterSelfCheckItem, ...]:
        previous_by_key = {
            item.key: item
            for item in (previous.checks if previous is not None else ())
        }
        compared: list[RouterSelfCheckItem] = []
        for item in checks:
            old = previous_by_key.get(item.key)
            change = "unchanged"
            previous_status = old.status if old is not None else None
            if old is not None:
                old_problem = self._is_problem(old.status)
                new_problem = self._is_problem(item.status)
                if not old_problem and new_problem:
                    change = "new_problem"
                elif old_problem and not new_problem:
                    change = "resolved"
                elif old.status != item.status:
                    change = "changed"
            compared.append(
                replace(
                    item,
                    change=change,
                    previous_status=previous_status,
                )
            )

        if not partial or previous is None:
            return tuple(compared)

        replacement_by_key = {item.key: item for item in compared}
        merged: list[RouterSelfCheckItem] = []
        seen: set[str] = set()
        for old in previous.checks:
            replacement_item = replacement_by_key.get(old.key)
            if replacement_item is not None:
                merged.append(replacement_item)
            else:
                merged.append(
                    replace(
                        old,
                        change="unchanged",
                        previous_status=None,
                    )
                )
            seen.add(old.key)
        merged.extend(item for item in compared if item.key not in seen)
        return tuple(merged)

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int(round((self.monotonic() - started) * 1000)))

    @classmethod
    def _safe_failure_summary(
        cls,
        error: BaseException,
        connection_type: str | None,
    ) -> str:
        kind = RciTransport.classify_failure(
            error=error,
            status_code=None,
            connection_type=connection_type,
            message="",
        )
        return cls.FAILURE_LABELS.get(
            str(kind or "request_error"),
            cls.FAILURE_LABELS["request_error"],
        )

    def save(self, result: RouterSelfCheckResult) -> bool:
        payload = result.to_dict()
        try:
            history = self.load_history(self.history_path)
            previous_latest = self.load_latest()
            if (
                previous_latest is not None
                and (
                    not history
                    or history[-1].finished_at
                    != previous_latest.finished_at
                )
            ):
                history.append(previous_latest)
            self._write_json(self.path, payload)
            history.append(result)
            history = history[-self.HISTORY_LIMIT :]
            self._write_json(
                self.history_path,
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "router_id": self.router_id,
                    "results": [item.to_dict() for item in history],
                    "privacy": {
                        "contains_router_name": False,
                        "contains_addresses": False,
                        "contains_credentials": False,
                    },
                },
            )
            return True
        except OSError:
            return False

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def load_latest(self) -> RouterSelfCheckResult | None:
        value = self.load_summary(self.path)
        if not value:
            return None
        try:
            return RouterSelfCheckResult.from_dict(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def load_history(path: str | Path) -> list[RouterSelfCheckResult]:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_results = (
            value.get("results")
            if isinstance(value, dict)
            else value
        )
        if not isinstance(raw_results, list):
            return []
        results: list[RouterSelfCheckResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                results.append(RouterSelfCheckResult.from_dict(item))
            except (TypeError, ValueError):
                continue
        return results

    @staticmethod
    def load_summary(path: str | Path) -> dict[str, Any]:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, dict) else {}
