from __future__ import annotations

import re
from typing import Any

from core.rci.diagnostics import sanitize_text
from core.services.errors import KeeneticError


class SystemLogService:
    """Читает конечный снимок циклического системного журнала KeeneticOS."""

    DEFAULT_MAX_LINES = 500
    MIN_LINES = 20
    MAX_LINES = 2000

    LEVEL_ALIASES = {
        "emergency": "critical",
        "emerg": "critical",
        "alert": "critical",
        "critical": "critical",
        "crit": "critical",
        "c": "critical",
        "error": "error",
        "err": "error",
        "e": "error",
        "warning": "warning",
        "warn": "warning",
        "w": "warning",
        "notice": "notice",
        "n": "notice",
        "info": "info",
        "information": "info",
        "i": "info",
        "debug": "debug",
        "d": "debug",
    }

    LEVEL_LABELS = {
        "critical": "Критическое",
        "error": "Ошибка",
        "warning": "Предупреждение",
        "notice": "Уведомление",
        "info": "Информация",
        "debug": "Отладка",
        "unknown": "Не определено",
    }

    def __init__(self, client: Any) -> None:
        self.client = client

    def read(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        limit = self.normalize_limit(max_lines)
        try:
            response = self.client.transport.request(
                method="POST",
                path="/rci/",
                endpoint="show/log",
                required=True,
                timeout=max(float(self.client.timeout), 12.0),
                json_payload=[
                    {"parse": f"show log {limit} once"}
                ],
                allow_reauthentication=True,
                retry_transient=False,
            )
        except KeeneticError as error:
            return (
                False,
                f"Не удалось получить системный журнал: {error}",
                [],
            )

        if response is None:
            return False, "Роутер вернул пустой ответ журнала.", []

        try:
            payload: Any = response.json()
        except ValueError:
            return False, "Роутер вернул журнал в неизвестном формате.", []

        errors = self.client.extract_batch_rci_errors(payload)
        if errors:
            return (
                False,
                "Не удалось получить системный журнал: "
                + "; ".join(errors),
                [],
            )

        entries = self.normalize_response(payload)
        return (
            True,
            f"Получено записей журнала: {len(entries)}.",
            entries,
        )

    @classmethod
    def normalize_limit(cls, value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = cls.DEFAULT_MAX_LINES
        return max(cls.MIN_LINES, min(cls.MAX_LINES, parsed))

    @classmethod
    def normalize_response(
        cls,
        payload: Any,
    ) -> list[dict[str, Any]]:
        parse_payload: Any = None
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if "parse" in candidate:
                parse_payload = candidate.get("parse")
                break
            if "log" in candidate:
                parse_payload = candidate
                break

        raw_log = (
            parse_payload.get("log")
            if isinstance(parse_payload, dict)
            else None
        )
        if isinstance(raw_log, dict):
            raw_entries = list(raw_log.items())
        elif isinstance(raw_log, list):
            raw_entries = list(enumerate(raw_log))
        else:
            raw_entries = []

        result: list[dict[str, Any]] = []
        for fallback_id, raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            raw_message = raw_entry.get("message")
            message_data = (
                raw_message
                if isinstance(raw_message, dict)
                else {}
            )
            message = (
                message_data.get("message")
                if message_data
                else raw_message
            )
            safe_message = cls.clean_message(message)
            if not safe_message:
                continue

            raw_level = str(
                message_data.get("level")
                or message_data.get("label")
                or raw_entry.get("level")
                or raw_entry.get("label")
                or ""
            ).strip()
            level = cls.normalize_level(raw_level)
            entry_id = raw_entry.get("id", fallback_id)
            result.append(
                {
                    "id": str(entry_id),
                    "timestamp": str(
                        raw_entry.get("timestamp")
                        or raw_entry.get("time")
                        or "—"
                    ).strip()
                    or "—",
                    "level": level,
                    "level_label": cls.LEVEL_LABELS[level],
                    "source": str(
                        raw_entry.get("ident")
                        or raw_entry.get("source")
                        or "ndm"
                    ).strip()
                    or "ndm",
                    "message": safe_message,
                }
            )
        return result

    @classmethod
    def normalize_level(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        return cls.LEVEL_ALIASES.get(text, "unknown")

    @staticmethod
    def clean_message(value: Any) -> str:
        safe = sanitize_text(str(value or ""))
        return re.sub(r"\s+", " ", safe).strip()
