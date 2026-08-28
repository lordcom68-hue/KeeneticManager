from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.rci.capabilities import RouterCapabilities
from core.services.runtime_paths import router_compatibility_cache_path


NowFunction = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class CompatibilityFeature:
    key: str
    label: str
    state: str
    state_label: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RouterCompatibilityProfile:
    schema_version: int
    router_id: int | None
    level: str
    level_label: str
    summary: str
    model: str
    release: str
    keeneticos_major: int
    keeneticos_minor: int
    wifi_management_mode: str
    signature: str
    detected_at: str
    features: tuple[CompatibilityFeature, ...]
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["features"] = [feature.to_dict() for feature in self.features]
        return value

    def feature_state(self, key: str) -> str:
        for feature in self.features:
            if feature.key == key:
                return feature.state
        return "unknown"

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any] | None,
    ) -> RouterCompatibilityProfile | None:
        if not isinstance(value, dict):
            return None
        raw_features = value.get("features")
        if not isinstance(raw_features, list):
            return None
        try:
            features = tuple(
                CompatibilityFeature(
                    key=str(item["key"]),
                    label=str(item["label"]),
                    state=str(item["state"]),
                    state_label=str(item["state_label"]),
                    detail=str(item.get("detail") or ""),
                )
                for item in raw_features
                if isinstance(item, dict)
            )
            return cls(
                schema_version=int(value.get("schema_version") or 1),
                router_id=(
                    int(value["router_id"])
                    if value.get("router_id") is not None
                    else None
                ),
                level=str(value.get("level") or "monitoring"),
                level_label=str(
                    value.get("level_label") or "Только мониторинг"
                ),
                summary=str(value.get("summary") or ""),
                model=str(value.get("model") or ""),
                release=str(value.get("release") or ""),
                keeneticos_major=int(value.get("keeneticos_major") or 0),
                keeneticos_minor=int(value.get("keeneticos_minor") or 0),
                wifi_management_mode=str(
                    value.get("wifi_management_mode") or "monitoring"
                ),
                signature=str(value.get("signature") or ""),
                detected_at=str(value.get("detected_at") or ""),
                features=features,
                cache_hit=True,
            )
        except (KeyError, TypeError, ValueError):
            return None


class RouterCompatibilityService:
    """Build and persist a safe capability matrix for one router."""

    SCHEMA_VERSION = 1
    STATE_LABELS = {
        "supported": "Поддерживается",
        "limited": "Ограниченно",
        "monitoring": "Только просмотр",
        "unavailable": "Недоступно",
        "unknown": "Определяется",
    }
    LEVEL_LABELS = {
        "full": "Полная поддержка",
        "limited": "Ограниченная поддержка",
        "monitoring": "Только мониторинг",
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
            else router_compatibility_cache_path(router_id)
        )
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = threading.RLock()
        self.profile = self.load() if router_id is not None else None

    def assess(
        self,
        capabilities: RouterCapabilities,
        version_data: dict[str, Any] | None,
    ) -> RouterCompatibilityProfile:
        fresh = self.build_profile(
            router_id=self.router_id,
            capabilities=capabilities,
            version_data=version_data,
            detected_at=self._now().isoformat(),
        )
        cached = self.profile
        if cached is not None and cached.signature == fresh.signature:
            cached_states = {
                feature.key: feature.state
                for feature in cached.features
                if feature.state != "unknown"
            }
            features = tuple(
                self._feature(
                    feature.key,
                    feature.label,
                    cached_states.get(feature.key, feature.state),
                    feature.detail,
                )
                for feature in fresh.features
            )
            fresh = replace(
                fresh,
                features=features,
                detected_at=cached.detected_at or fresh.detected_at,
                cache_hit=True,
            )
        self.profile = fresh
        if self.router_id is not None:
            self.save(fresh)
        return fresh

    def update_runtime_feature(
        self,
        key: str,
        supported: bool,
        *,
        detail: str = "",
    ) -> RouterCompatibilityProfile | None:
        profile = self.profile
        if profile is None:
            return None
        state = "supported" if supported else "unavailable"
        changed = False
        features: list[CompatibilityFeature] = []
        for feature in profile.features:
            if feature.key != key:
                features.append(feature)
                continue
            replacement = self._feature(
                feature.key,
                feature.label,
                state,
                detail or feature.detail,
            )
            features.append(replacement)
            changed = replacement != feature
        if not changed:
            return profile
        profile = replace(profile, features=tuple(features))
        self.profile = profile
        if self.router_id is not None:
            self.save(profile)
        return profile

    def load(self) -> RouterCompatibilityProfile | None:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return RouterCompatibilityProfile.from_dict(value)

    def save(self, profile: RouterCompatibilityProfile) -> bool:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            encoded = json.dumps(
                profile.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(encoded + "\n", encoding="utf-8")
                temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            temporary.unlink(missing_ok=True)
            return False

    @classmethod
    def build_profile(
        cls,
        *,
        router_id: int | None,
        capabilities: RouterCapabilities,
        version_data: dict[str, Any] | None,
        detected_at: str,
    ) -> RouterCompatibilityProfile:
        data = version_data if isinstance(version_data, dict) else {}
        ndw = data.get("ndw") if isinstance(data.get("ndw"), dict) else {}
        model = str(data.get("model") or ndw.get("model") or "").strip()
        release = str(data.get("release") or ndw.get("release") or "").strip()
        major = int(capabilities.keeneticos_major or 0)
        mode = str(capabilities.wifi_management_mode or "monitoring")

        if major <= 0 or major < 4:
            level = "monitoring"
            summary = (
                "Версия KeeneticOS не входит в проверенный диапазон; "
                "изменяющие команды ограничены фактически доступными функциями."
            )
        elif mode == "mws" or not capabilities.wifi:
            level = "full"
            summary = (
                "Основные функции мониторинга и управления поддерживаются."
            )
        elif mode == "legacy":
            level = "limited"
            summary = (
                "KeeneticOS 4 использует отдельный legacy-режим управления Wi-Fi."
            )
        else:
            level = "limited"
            summary = (
                "Мониторинг доступен, но изменение логических Wi-Fi сетей отключено."
            )

        features = cls._build_features(capabilities, major)
        signature_payload = {
            "model": model,
            "release": release,
            "capabilities": asdict(capabilities),
        }
        signature = hashlib.sha256(
            json.dumps(
                signature_payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return RouterCompatibilityProfile(
            schema_version=cls.SCHEMA_VERSION,
            router_id=router_id,
            level=level,
            level_label=cls.LEVEL_LABELS[level],
            summary=summary,
            model=model,
            release=release,
            keeneticos_major=major,
            keeneticos_minor=int(capabilities.keeneticos_minor or 0),
            wifi_management_mode=mode,
            signature=signature,
            detected_at=detected_at,
            features=features,
        )

    @classmethod
    def _build_features(
        cls,
        capabilities: RouterCapabilities,
        major: int,
    ) -> tuple[CompatibilityFeature, ...]:
        known_system = major >= 4
        wifi_mode = str(capabilities.wifi_management_mode or "monitoring")
        wifi_management_state = {
            "mws": "supported",
            "legacy": "limited",
        }.get(wifi_mode, "unavailable")
        rescue_state = (
            "supported"
            if wifi_mode == "mws"
            else "limited"
            if wifi_mode == "legacy"
            else "unavailable"
        )
        return (
            cls._feature(
                "monitoring",
                "Мониторинг устройства",
                "supported" if known_system else "monitoring",
                "Состояние, нагрузка, подключения и трафик.",
            ),
            cls._feature(
                "internet_management",
                "Управление Интернетом",
                "supported" if known_system else "monitoring",
                "Включение, отключение и настройка подключений.",
            ),
            cls._feature(
                "wifi_monitoring",
                "Мониторинг Wi-Fi",
                "supported" if capabilities.wifi else "unavailable",
                "Сети, диапазоны и подключённые клиенты.",
            ),
            cls._feature(
                "wifi_management",
                "Управление Wi-Fi",
                wifi_management_state,
                {
                    "mws": "Логические сети MWS KeeneticOS 5.1+.",
                    "legacy": "Физические AccessPoint KeeneticOS 4.",
                }.get(wifi_mode, "Доступен только просмотр состояния."),
            ),
            cls._feature(
                "client_access",
                "Управление Wi-Fi клиентами",
                "supported" if capabilities.wifi else "unavailable",
                "Отключение и восстановление доступа клиента.",
            ),
            cls._feature(
                "ethernet_ports",
                "Управление Ethernet-портами",
                "supported" if known_system else "monitoring",
                "Состояние и безопасное переключение портов.",
            ),
            cls._feature(
                "rescue_mode",
                "Режим Спасатель/Спасаемый/Смешанный",
                rescue_state,
                "Требует Wireless ISP и управляемую Wi-Fi сеть.",
            ),
            cls._feature(
                "system_log",
                "Системный журнал",
                "unknown",
                "Проверяется безопасной командой при первом открытии.",
            ),
            cls._feature(
                "diagnostics",
                "Диагностический архив",
                "supported",
                "Доступен также без активного подключения.",
            ),
            cls._feature(
                "usb_storage",
                "USB и накопители",
                "supported" if capabilities.usb else "unavailable",
                "Показывается только при наличии компонента USB.",
            ),
            cls._feature(
                "vpn",
                "WireGuard/OpenVPN",
                (
                    "supported"
                    if capabilities.wireguard or capabilities.openvpn
                    else "unavailable"
                ),
                "Зависит от установленных компонентов KeeneticOS.",
            ),
        )

    @classmethod
    def _feature(
        cls,
        key: str,
        label: str,
        state: str,
        detail: str,
    ) -> CompatibilityFeature:
        normalized_state = (
            state if state in cls.STATE_LABELS else "unknown"
        )
        return CompatibilityFeature(
            key=key,
            label=label,
            state=normalized_state,
            state_label=cls.STATE_LABELS[normalized_state],
            detail=detail,
        )
