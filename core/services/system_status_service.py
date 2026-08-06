from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SystemStatus:
    """Normalized read-only system information returned by KeeneticOS."""

    model: str | None
    version: str | None
    uptime: str | None
    cpu_load: str | None
    ram_usage: str | None
    memory_total: int | None
    memory_free: int | None


class SystemStatusService:
    """Extracts stable device metrics from differently shaped RCI replies."""

    MODEL_KEYS = (
        "model",
        "model-name",
        "model_name",
        "device-model",
        "device_model",
        "product",
        "product-name",
        "product_name",
        "description",
    )
    VERSION_KEYS = (
        "release",
        "version",
        "os-version",
        "os_version",
        "keeneticos",
        "keeneticos-version",
        "keeneticos_version",
        "firmware",
        "firmware-version",
        "firmware_version",
        "ndm",
    )
    MEMORY_TOTAL_KEYS = (
        "memtotal",
        "memory-total",
        "memory_total",
        "total-memory",
    )
    MEMORY_FREE_KEYS = (
        "memfree",
        "memory-free",
        "memory_free",
        "free-memory",
    )

    def build(
        self,
        system_data: dict[str, Any],
        version_data: dict[str, Any],
    ) -> SystemStatus:
        memory_total = self.extract_integer(
            system_data,
            self.MEMORY_TOTAL_KEYS,
        )
        memory_free = self.extract_integer(
            system_data,
            self.MEMORY_FREE_KEYS,
        )

        model = self.extract_model(version_data) or self.extract_model(system_data)
        if not model:
            hostname = self.find_first_value(system_data, ("hostname",))
            model = str(hostname).strip() if hostname is not None else None
            model = model or None

        version = self.extract_version(version_data) or self.extract_version(system_data)

        return SystemStatus(
            model=model,
            version=version,
            uptime=self.extract_uptime(system_data),
            cpu_load=self.extract_cpu_load(system_data),
            ram_usage=self.calculate_ram_usage(
                memory_total=memory_total,
                memory_free=memory_free,
                data=system_data,
            ),
            memory_total=memory_total,
            memory_free=memory_free,
        )

    def extract_model(self, data: dict[str, Any]) -> str | None:
        value = self.find_first_value(data, self.MODEL_KEYS)
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    def extract_version(self, data: dict[str, Any]) -> str | None:
        value = self.find_first_value(data, self.VERSION_KEYS)
        if value is not None:
            text = self.value_to_version_text(value)
            if text:
                return text

        return self.find_component_version(data)

    def find_component_version(self, data: Any) -> str | None:
        if isinstance(data, dict):
            name = data.get("name") or data.get("component") or data.get("id")
            if isinstance(name, str) and name.lower() in {
                "ndm",
                "keeneticos",
                "base",
                "core",
            }:
                for key in ("version", "release", "current", "installed"):
                    value = data.get(key)
                    if value is not None:
                        text = self.value_to_version_text(value)
                        if text:
                            return text

            for value in data.values():
                found = self.find_component_version(value)
                if found:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = self.find_component_version(item)
                if found:
                    return found

        return None

    @staticmethod
    def value_to_version_text(value: Any) -> str | None:
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            return text or None

        if isinstance(value, dict):
            for key in ("version", "release", "title", "current", "installed"):
                nested = value.get(key)
                if isinstance(nested, (str, int, float)):
                    text = str(nested).strip()
                    if text:
                        return text

        return None

    def extract_cpu_load(self, data: dict[str, Any]) -> str | None:
        value = self.find_first_value(
            data,
            ("cpuload", "cpu-load", "cpu_load", "cpu"),
        )
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None
        if text.endswith("%"):
            return text

        try:
            number = float(text)
        except ValueError:
            return text

        if not math.isfinite(number):
            return None
        return f"{int(number)}%" if number.is_integer() else f"{number:.1f}%"

    def extract_uptime(self, data: dict[str, Any]) -> str | None:
        value = self.find_first_value(data, ("uptime", "up-time", "up_time"))
        if value is None:
            return None

        try:
            seconds = int(float(str(value).strip()))
        except (OverflowError, TypeError, ValueError):
            return str(value)

        return self.format_uptime_seconds(seconds)

    def calculate_ram_usage(
        self,
        memory_total: int | None,
        memory_free: int | None,
        data: dict[str, Any],
    ) -> str | None:
        """Calculate used RAM from the most specific available router field."""
        direct_usage = self.find_first_value(
            data,
            (
                "memload",
                "memory-load",
                "memory_load",
                "ram-load",
                "ram_usage",
                "memory-usage",
            ),
        )
        if direct_usage is not None:
            text = str(direct_usage).strip()
            if text.endswith("%"):
                return text
            try:
                number = float(text)
            except ValueError:
                pass
            else:
                if math.isfinite(number):
                    return self.format_percentage(number)

        memory_pair = self.find_first_value(
            data,
            ("memory", "ram", "memory-used-total", "memory_used_total"),
        )
        parsed_pair = self.parse_memory_pair(memory_pair)
        if parsed_pair is not None:
            used, total = parsed_pair
            return self.format_percentage(used / total * 100)

        memory_buffers = self.extract_integer(
            data,
            ("membuffers", "mem-buffers", "memory-buffers", "memory_buffers"),
        ) or 0
        memory_cache = self.extract_integer(
            data,
            ("memcache", "mem-cache", "memory-cache", "memory_cache", "cached"),
        ) or 0

        if memory_total is not None and memory_free is not None and memory_total > 0:
            used_memory = max(
                0,
                memory_total - memory_free - memory_buffers - memory_cache,
            )
            return self.format_percentage(used_memory / memory_total * 100)

        return None

    @staticmethod
    def parse_memory_pair(value: Any) -> tuple[float, float] | None:
        if value is None:
            return None

        text = str(value).strip()
        if "/" not in text:
            return None

        used_text, total_text = text.split("/", maxsplit=1)
        try:
            used = float(used_text.strip())
            total = float(total_text.strip())
        except ValueError:
            return None

        if not math.isfinite(used) or not math.isfinite(total) or used < 0 or total <= 0:
            return None
        return used, total

    @staticmethod
    def format_percentage(number: float) -> str:
        if not math.isfinite(number):
            return "—"

        number = max(0.0, min(100.0, number))
        if number.is_integer():
            return f"{int(number)}%"
        return f"{number:.1f}%"

    @staticmethod
    def format_uptime_seconds(total_seconds: int) -> str:
        if total_seconds < 0:
            return "—"

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts: list[str] = []
        if days:
            parts.append(f"{days} дн.")
        if hours or days:
            parts.append(f"{hours} ч.")
        if minutes or hours or days:
            parts.append(f"{minutes} мин.")
        if not parts:
            parts.append(f"{seconds} сек.")
        return " ".join(parts)

    def extract_integer(
        self,
        data: dict[str, Any],
        keys: tuple[str, ...],
    ) -> int | None:
        value = self.find_first_value(data, keys)
        if value is None or isinstance(value, bool):
            return None

        try:
            return int(float(str(value).strip()))
        except (OverflowError, TypeError, ValueError):
            return None

    def find_first_value(self, data: Any, keys: tuple[str, ...]) -> Any | None:
        normalized_keys = {key.lower() for key in keys}

        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).lower() in normalized_keys and value is not None:
                    return value
            for value in data.values():
                found = self.find_first_value(value, keys)
                if found is not None:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = self.find_first_value(item, keys)
                if found is not None:
                    return found

        return None
