from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable


class JournalEventAnalyzer:
    """Classify, explain and compact router and application events."""

    CATEGORY_LABELS = {
        "application": "Действия",
        "clients": "Клиенты",
        "compatibility": "Совместимость",
        "connectivity": "Интернет",
        "ethernet": "Ethernet",
        "security": "Безопасность",
        "system": "Система",
        "vpn": "VPN",
        "wifi": "Wi-Fi",
        "other": "Прочее",
    }
    IMPORTANCE_LABELS = {
        "critical": "Требует внимания",
        "temporary": "Временное событие",
        "attention": "Проверьте при повторении",
        "normal": "Обычное событие",
    }
    REPEAT_PATTERN = re.compile(
        r"last\s+message\s+repeated\s+(\d+)\s+times?",
        re.IGNORECASE,
    )
    ROUTER_TIME_FORMATS = (
        "%b %d %H:%M:%S",
        "%b  %d %H:%M:%S",
    )

    @classmethod
    def analyze(
        cls,
        entries: Iterable[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        current = now or datetime.now().astimezone()
        prepared: list[tuple[int, dict[str, Any]]] = []
        for index, raw_entry in enumerate(entries):
            if not isinstance(raw_entry, dict):
                continue
            entry = cls.enrich(raw_entry, now=current)
            prepared.append((index, entry))

        prepared.sort(
            key=lambda item: (
                item[1].get("timestamp_epoch") is None,
                float(item[1].get("timestamp_epoch") or 0.0),
                item[0],
            )
        )
        compacted = cls._compact(entry for _index, entry in prepared)
        cls._link_application_actions(compacted)
        for entry in compacted:
            entry["display_message"] = cls.display_message(entry)
        return compacted

    @classmethod
    def enrich(
        cls,
        raw_entry: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        entry = dict(raw_entry)
        source = str(entry.get("source") or "").strip()
        message = str(entry.get("message") or "").strip()
        level = str(entry.get("level") or "unknown").strip().lower()
        combined = f"{source} {message}".casefold()
        category, explanation, override, transient = cls._classification(
            entry,
            combined,
        )
        if override and level in {"critical", "error"}:
            entry["original_level"] = level
            level = override
            entry["level"] = level
            entry["level_label"] = {
                "warning": "Предупреждение",
                "notice": "Уведомление",
                "info": "Информация",
            }.get(level, entry.get("level_label") or "Не определено")

        timestamp = cls.parse_timestamp(
            str(entry.get("timestamp") or ""),
            now=now,
        )
        entry["timestamp_epoch"] = timestamp.timestamp() if timestamp else None
        entry["category"] = category
        entry["category_label"] = cls.CATEGORY_LABELS[category]
        entry["explanation"] = explanation
        entry["transient"] = transient
        importance = cls._importance(level, transient)
        entry["importance"] = importance
        entry["importance_label"] = cls.IMPORTANCE_LABELS[importance]
        entry.setdefault("repeat_count", 1)
        return entry

    @classmethod
    def parse_timestamp(
        cls,
        value: str,
        *,
        now: datetime | None = None,
    ) -> datetime | None:
        text = str(value or "").strip()
        if not text or text == "—":
            return None
        current = now or datetime.now().astimezone()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None and current.tzinfo is not None:
                parsed = parsed.replace(tzinfo=current.tzinfo)
            return parsed

        for time_format in cls.ROUTER_TIME_FORMATS:
            try:
                parsed = datetime.strptime(
                    f"{current.year} {text}",
                    f"%Y {time_format}",
                ).replace(tzinfo=current.tzinfo)
            except ValueError:
                continue
            if parsed > current + timedelta(days=1):
                parsed = parsed.replace(year=current.year - 1)
            return parsed
        return None

    @classmethod
    def display_message(cls, entry: dict[str, Any]) -> str:
        message = str(entry.get("message") or "").strip()
        repeat_count = max(1, int(entry.get("repeat_count") or 1))
        if repeat_count > 1:
            message += f"  ·  Повторений: {repeat_count}"
        explanation = str(entry.get("explanation") or "").strip()
        if explanation:
            message += f"  —  {explanation}"
        related_action = str(entry.get("related_action") or "").strip()
        if related_action:
            message += f"  ·  После действия: {related_action}"
        return message

    @classmethod
    def _classification(
        cls,
        entry: dict[str, Any],
        combined: str,
    ) -> tuple[str, str, str | None, bool]:
        if entry.get("application_event"):
            operation_type = str(entry.get("operation_type") or "")
            category = cls._application_category(operation_type, combined)
            state = str(entry.get("operation_state") or "unknown")
            explanation = {
                "confirmed": "Изменение подтверждено роутером.",
                "rejected": "Роутер отклонил изменение.",
                "rolled_back": "Исходное состояние восстановлено.",
                "partial": "Изменение выполнено не полностью.",
                "unknown": "Итоговое состояние не удалось подтвердить.",
            }.get(state, "Операция выполнена через Keenetic Manager.")
            return category, explanation, None, False

        if "network unreachable" in combined:
            if "openvpn" in combined:
                return (
                    "vpn",
                    "VPN временно не видит маршрут в Интернет; обычно это следствие потери WAN.",
                    "warning",
                    True,
                )
            return (
                "connectivity",
                "Облачный сервис временно недоступен; это не означает неисправность роутера.",
                "warning",
                True,
            )
        if "core::configurator" in combined and "not found" in combined:
            return (
                "compatibility",
                "Команда отсутствует в этой версии KeeneticOS; функция должна быть отключена для этой модели.",
                "warning",
                False,
            )
        if "mac address" in combined and "is unknown" in combined:
            return (
                "clients",
                "Клиент уже отсутствует в таблице роутера; повторите действие после обновления списка.",
                "warning",
                False,
            )
        if any(token in combined for token in ("authentication", "login failed", "denied")):
            return (
                "security",
                "Проверьте учётные данные или правила доступа, если событие повторяется.",
                None,
                False,
            )
        if any(token in combined for token in ("openvpn", "wireguard", "sstp", "ipsec")):
            return "vpn", "Событие VPN-подключения.", None, False
        if any(token in combined for token in ("wifi", "wireless", "accesspoint", "wlan", "mws")):
            return "wifi", "Событие беспроводной сети.", None, False
        if any(token in combined for token in ("ethernet", "gigabitethernet", "link is", "port")):
            return "ethernet", "Событие физического Ethernet-подключения.", None, False
        if any(token in combined for token in ("keendns", "coalagent", "coala", "internet")):
            return "connectivity", "Событие подключения к Интернету или облаку Keenetic.", None, False
        if "kernel" in combined or "ndm" in combined or "syslog" in combined:
            return "system", "Системное событие KeeneticOS.", None, False
        return "other", "", None, False

    @classmethod
    def _compact(
        cls,
        entries: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entry in entries:
            repeat_match = cls.REPEAT_PATTERN.search(
                str(entry.get("message") or "")
            )
            if repeat_match and result:
                previous = result[-1]
                previous["repeat_count"] = max(
                    1,
                    int(previous.get("repeat_count") or 1),
                ) + int(repeat_match.group(1))
                previous["last_timestamp"] = entry.get("timestamp")
                continue

            if result and cls._event_key(result[-1]) == cls._event_key(entry):
                previous = result[-1]
                previous["repeat_count"] = max(
                    1,
                    int(previous.get("repeat_count") or 1),
                ) + 1
                previous["last_timestamp"] = entry.get("timestamp")
                continue
            result.append(entry)
        return result

    @classmethod
    def _link_application_actions(
        cls,
        entries: list[dict[str, Any]],
    ) -> None:
        actions = [
            entry
            for entry in entries
            if entry.get("application_event")
            and entry.get("timestamp_epoch") is not None
        ]
        for entry in entries:
            if entry.get("application_event"):
                continue
            if str(entry.get("level") or "") not in {
                "critical",
                "error",
                "warning",
            }:
                continue
            timestamp = entry.get("timestamp_epoch")
            if timestamp is None:
                continue
            category = str(entry.get("category") or "")
            candidates = [
                action
                for action in actions
                if abs(float(action["timestamp_epoch"]) - float(timestamp))
                <= 120.0
                and (
                    str(action.get("category") or "") == category
                    or category == "compatibility"
                )
            ]
            if not candidates:
                continue
            action = min(
                candidates,
                key=lambda item: abs(
                    float(item["timestamp_epoch"]) - float(timestamp)
                ),
            )
            entry["related_action"] = str(action.get("message") or "")
            entry["related_action_id"] = str(action.get("id") or "")

    @staticmethod
    def _event_key(entry: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(entry.get("source") or "").strip().casefold(),
            re.sub(
                r"\s+",
                " ",
                str(entry.get("message") or "").strip().casefold(),
            ),
            str(entry.get("category") or ""),
        )

    @staticmethod
    def _importance(level: str, transient: bool) -> str:
        if transient:
            return "temporary"
        if level in {"critical", "error"}:
            return "critical"
        if level == "warning":
            return "attention"
        return "normal"

    @staticmethod
    def _application_category(operation_type: str, combined: str) -> str:
        if operation_type == "wifi_client_access":
            return "clients"
        if operation_type == "wifi_network":
            return "wifi"
        if operation_type == "rescue_mode":
            return "connectivity"
        if operation_type in {"ethernet_port_state", "interface_state"}:
            return "ethernet"
        if "wi-fi" in combined or "wifi" in combined:
            return "wifi"
        if "ethernet" in combined or "lan" in combined:
            return "ethernet"
        return "application"
