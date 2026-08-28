from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.rci.diagnostics import sanitize_value
from core.services.runtime_paths import router_operation_history_path


NowFunction = Callable[[], datetime]


class OperationHistoryService:
    """Persistent, bounded history of important application mutations."""

    MAX_BYTES = 1_000_000
    MAX_ENTRIES = 500
    LEVEL_LABELS = {
        "error": "Ошибка",
        "warning": "Предупреждение",
        "notice": "Уведомление",
        "info": "Информация",
    }
    STATE_LEVELS = {
        "confirmed": "info",
        "rejected": "warning",
        "unknown": "warning",
        "rolled_back": "warning",
        "partial": "error",
    }

    def __init__(
        self,
        router_id: int | None,
        *,
        path: str | Path | None = None,
        now: NowFunction | None = None,
    ) -> None:
        self.router_id = router_id
        self.path = (
            Path(path)
            if path is not None
            else router_operation_history_path(router_id)
        )
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = threading.RLock()

    def record(
        self,
        event_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> bool:
        event = sanitize_value(
            {
                "timestamp": self._now().isoformat(),
                "type": str(event_type or "operation"),
                "message": str(message or ""),
                "details": dict(details or {}),
            }
        )
        try:
            encoded = (
                json.dumps(event, ensure_ascii=False, sort_keys=True)
                + "\n"
            ).encode("utf-8")
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(encoded))
                with self.path.open("ab") as stream:
                    stream.write(encoded)
                    stream.flush()
            return True
        except (OSError, TypeError, ValueError):
            # History is supplementary and must never break a router action.
            return False

    def read(self, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
        try:
            requested_limit = int(limit)
        except (TypeError, ValueError):
            requested_limit = self.MAX_ENTRIES
        maximum = max(1, min(self.MAX_ENTRIES, requested_limit))
        result: list[dict[str, Any]] = []
        with self._lock:
            for path in (self.rotated_path, self.path):
                if not path.is_file():
                    continue
                try:
                    lines = path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ).splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        result.append(value)
        result.sort(key=lambda item: str(item.get("timestamp") or ""))
        return result[-maximum:]

    def journal_entries(self, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, event in enumerate(self.read(limit), 1):
            details = event.get("details")
            safe_details = details if isinstance(details, dict) else {}
            state = str(safe_details.get("state") or "unknown").lower()
            level = self.STATE_LEVELS.get(state, "notice")
            result.append(
                {
                    "id": f"app-{event.get('timestamp') or index}-{index}",
                    "timestamp": str(event.get("timestamp") or "—"),
                    "level": level,
                    "level_label": self.LEVEL_LABELS[level],
                    "source": self._source_label(event),
                    "message": self._journal_message(event, safe_details),
                    "operation_type": str(
                        event.get("type") or "operation"
                    ),
                    "operation_state": state,
                    "application_event": True,
                }
            )
        return result

    @property
    def rotated_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".1")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.is_file():
            return
        try:
            current_size = self.path.stat().st_size
        except OSError:
            return
        if current_size + incoming_bytes <= self.MAX_BYTES:
            return
        self.rotated_path.unlink(missing_ok=True)
        self.path.replace(self.rotated_path)

    @staticmethod
    def _source_label(event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "")
        suffix = {
            "ethernet_port_state": "Ethernet",
            "interface_state": "Интернет",
            "wifi_network": "Wi-Fi",
            "wifi_client_access": "Клиенты",
            "rescue_mode": "Режим",
            "wired_connection": "Интернет",
            "cable_protocol_change": "Протокол",
            "dedicated_wan": "Dedicated WAN",
        }.get(event_type)
        if suffix:
            return f"Keenetic Manager · {suffix}"
        return "Keenetic Manager"

    @staticmethod
    def _journal_message(
        event: dict[str, Any],
        details: dict[str, Any],
    ) -> str:
        event_type = str(event.get("type") or "operation")
        message = str(event.get("message") or "").strip()
        if event_type != "ethernet_port_state":
            operation = str(details.get("operation") or "").strip()
            display_name = str(
                details.get("display_name")
                or details.get("interface_id")
                or ""
            ).strip()
            prefix = {
                "interface_state": (
                    "Включение подключения"
                    if details.get("requested_enabled") is True
                    else "Выключение подключения"
                    if details.get("requested_enabled") is False
                    else "Изменение подключения"
                ),
                "wifi_network": {
                    "create": "Создание Wi-Fi сети",
                    "update": "Изменение Wi-Fi сети",
                    "state": "Переключение Wi-Fi сети",
                    "delete": "Удаление Wi-Fi сети",
                }.get(operation, "Изменение Wi-Fi сети"),
                "wifi_client_access": (
                    "Разрешение доступа Wi-Fi клиента"
                    if details.get("allowed") is True
                    else "Отключение Wi-Fi клиента"
                ),
                "rescue_mode": "Переключение режима работы",
                "wired_connection": "Создание проводного подключения",
                "cable_protocol_change": "Смена протокола подключения",
                "dedicated_wan": (
                    "Создание Dedicated WAN"
                    if operation == "create"
                    else "Удаление Dedicated WAN"
                    if operation == "delete"
                    else "Изменение Dedicated WAN"
                ),
            }.get(event_type, "Операция")
            if display_name:
                prefix += f" «{display_name}»"
            return f"{prefix}: {message}" if message else prefix

        interface_id = str(details.get("interface_id") or "").strip()
        display_name = str(
            details.get("display_name") or interface_id or "Порт"
        ).strip()
        requested = details.get("requested_enabled")
        action = (
            "Включение"
            if requested is True
            else "Выключение"
            if requested is False
            else "Изменение"
        )
        target = display_name
        if interface_id and interface_id != display_name:
            target += f" ({interface_id})"
        return f"{action} {target}: {message}" if message else f"{action} {target}"
