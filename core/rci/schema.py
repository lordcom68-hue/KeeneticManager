from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SupportPolicy = Literal[
    "required",
    "safe_optional",
    "capability_gated",
    "blocked",
]


@dataclass(frozen=True, slots=True)
class EndpointDefinition:
    """Metadata for a known Keenetic RCI endpoint."""

    path: str
    purpose: str
    policy: SupportPolicy
    cacheable: bool = False
    secret_bearing: bool = False


RCI_ENDPOINTS: dict[str, EndpointDefinition] = {
    "version": EndpointDefinition(
        path="show/version",
        purpose="Модель, версия и возможности KeeneticOS",
        policy="required",
        cacheable=True,
    ),
    "system": EndpointDefinition(
        path="show/system",
        purpose="Состояние системы",
        policy="required",
    ),
    "internet_status": EndpointDefinition(
        path="show/internet/status",
        purpose="Состояние доступа в интернет",
        policy="safe_optional",
    ),
    "interfaces": EndpointDefinition(
        path="show/interface",
        purpose="Оперативное состояние интерфейсов",
        policy="safe_optional",
    ),
    "interface_config": EndpointDefinition(
        path="show/sc/interface",
        purpose="Сохранённая конфигурация интерфейсов",
        policy="safe_optional",
        secret_bearing=True,
    ),
    "associations": EndpointDefinition(
        path="show/associations",
        purpose="Ассоциации Wi-Fi-клиентов",
        policy="safe_optional",
        secret_bearing=True,
    ),
    "hotspot": EndpointDefinition(
        path="show/ip/hotspot",
        purpose="Сегменты сети и клиенты",
        policy="safe_optional",
        secret_bearing=True,
    ),
    "usb": EndpointDefinition(
        path="show/usb",
        purpose="USB-устройства",
        policy="capability_gated",
    ),
    "storage": EndpointDefinition(
        path="show/storage",
        purpose="Накопители и разделы",
        policy="capability_gated",
    ),
    "hardware": EndpointDefinition(
        path="show/hardware",
        purpose="Непереносимый endpoint, вызывающий ошибки на ряде моделей",
        policy="blocked",
    ),
}

