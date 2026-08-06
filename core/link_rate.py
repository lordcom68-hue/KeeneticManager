from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def first_link_rate_mbps(
    data: Mapping[str, Any],
    aliases: Iterable[str],
) -> float | None:
    """
    Ищет PHY-скорость сначала на верхнем уровне, затем
    рекурсивно во вложенных словарях KeeneticOS.
    """
    alias_list = tuple(
        str(alias).lower()
        for alias in aliases
    )

    for key, value in data.items():
        if str(key).lower() in alias_list:
            normalized = normalize_link_rate_mbps(value)
            if normalized is not None:
                return normalized

    for value in data.values():
        if isinstance(value, Mapping):
            found = first_link_rate_mbps(
                value,
                alias_list,
            )
            if found is not None:
                return found

        if isinstance(value, list):
            for item in value:
                if not isinstance(item, Mapping):
                    continue

                found = first_link_rate_mbps(
                    item,
                    alias_list,
                )
                if found is not None:
                    return found

    return None


def collect_rate_like_fields(
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """Собирает поля, похожие на скорости, для диагностики."""
    result: dict[str, Any] = {}

    def walk(
        value: Any,
        prefix: str = "",
    ) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                name = (
                    f"{prefix}.{key}"
                    if prefix
                    else str(key)
                )
                lowered = str(key).lower()

                if any(
                    marker in lowered
                    for marker in (
                        "rate",
                        "speed",
                        "bitrate",
                        "phy",
                        "mcs",
                    )
                ):
                    result[name] = item

                walk(item, name)

        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(
                    item,
                    f"{prefix}[{index}]",
                )

    walk(data)
    return result


def normalize_link_rate_mbps(
    value: Any,
) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    unit = ""

    if isinstance(value, str):
        text = value.strip().lower()

        if not text:
            return None

        match = _NUMBER_RE.search(text)
        if match is None:
            return None

        try:
            number = float(
                match.group(0).replace(",", ".")
            )
        except ValueError:
            return None

        if any(x in text for x in ("gbit", "gbps", "гбит")):
            unit = "gbps"
        elif any(x in text for x in ("mbit", "mbps", "мбит")):
            unit = "mbps"
        elif any(x in text for x in ("kbit", "kbps", "кбит")):
            unit = "kbps"
        elif any(x in text for x in ("bit/s", "bps", "бит/с")):
            unit = "bps"
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

    if number <= 0:
        return None

    if unit == "gbps":
        mbps = number * 1000.0
    elif unit == "mbps":
        mbps = number
    elif unit == "kbps":
        mbps = number / 1000.0
    elif unit == "bps":
        mbps = number / 1_000_000.0
    else:
        if number >= 10_000_000:
            mbps = number / 1_000_000.0
        elif number >= 10_000:
            mbps = number / 1000.0
        else:
            mbps = number

    if not 0.1 <= mbps <= 20_000:
        return None

    return round(mbps, 1)
