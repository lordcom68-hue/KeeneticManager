from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppProfile:
    storage_namespace: str = "KeeneticManager"
    settings_organization: str = "KeeneticManager"
    settings_application: str = "KeeneticManager"
    router_credential_service: str = "KeeneticManager.Router.Admin"
    smb_credential_service: str = "KeeneticManager.SMB"


DEFAULT_APP_PROFILE = AppProfile()


def _profile_path() -> Path | None:
    explicit = os.environ.get(
        "KEENETIC_MANAGER_PROFILE",
        "",
    ).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled = Path(bundle_root) / "release_profile.json"
        if bundled.is_file():
            return bundled

    source_profile = (
        Path(__file__).resolve().parents[1]
        / "release_profile.json"
    )
    if source_profile.is_file():
        return source_profile
    return None


def _safe_value(
    data: dict[str, Any],
    key: str,
    default: str,
    *,
    path_component: bool = False,
) -> str:
    value = str(data.get(key) or "").strip()
    if not value or len(value) > 128:
        return default
    if path_component and any(
        marker in value
        for marker in ("/", "\\", "..")
    ):
        return default
    return value


def load_app_profile(
    path: str | Path | None = None,
) -> AppProfile:
    profile_path = (
        Path(path)
        if path is not None
        else _profile_path()
    )
    if profile_path is None or not profile_path.is_file():
        return DEFAULT_APP_PROFILE

    try:
        raw = json.loads(
            profile_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return DEFAULT_APP_PROFILE
    if not isinstance(raw, dict):
        return DEFAULT_APP_PROFILE

    defaults = DEFAULT_APP_PROFILE
    return AppProfile(
        storage_namespace=_safe_value(
            raw,
            "storage_namespace",
            defaults.storage_namespace,
            path_component=True,
        ),
        settings_organization=_safe_value(
            raw,
            "settings_organization",
            defaults.settings_organization,
        ),
        settings_application=_safe_value(
            raw,
            "settings_application",
            defaults.settings_application,
        ),
        router_credential_service=_safe_value(
            raw,
            "router_credential_service",
            defaults.router_credential_service,
        ),
        smb_credential_service=_safe_value(
            raw,
            "smb_credential_service",
            defaults.smb_credential_service,
        ),
    )


APP_PROFILE = load_app_profile()
APP_STORAGE_NAMESPACE = APP_PROFILE.storage_namespace
SETTINGS_ORGANIZATION = APP_PROFILE.settings_organization
SETTINGS_APPLICATION = APP_PROFILE.settings_application
ROUTER_CREDENTIAL_SERVICE = APP_PROFILE.router_credential_service
SMB_CREDENTIAL_SERVICE = APP_PROFILE.smb_credential_service
