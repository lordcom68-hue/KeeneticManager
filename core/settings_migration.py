from __future__ import annotations

import os
from collections.abc import Callable

from core.app_profile import (
    LEGACY_SETTINGS_APPLICATIONS,
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
)


SETTINGS_SUFFIXES = (
    "",
    ".UsbSmbFileManager",
)


def migrate_legacy_settings(
    settings_factory: Callable[..., object],
) -> bool:
    """Copy missing settings from versioned beta profiles once."""
    if os.environ.get("KEENETIC_MANAGER_SETTINGS_FILE", "").strip():
        return False

    migrated = False
    for suffix in SETTINGS_SUFFIXES:
        target = settings_factory(
            SETTINGS_ORGANIZATION,
            f"{SETTINGS_APPLICATION}{suffix}",
        )
        marker = "migration/versioned_profiles_complete"
        if bool(target.value(marker, False, type=bool)):
            continue

        for legacy_application in LEGACY_SETTINGS_APPLICATIONS:
            if legacy_application == SETTINGS_APPLICATION:
                continue
            source = settings_factory(
                SETTINGS_ORGANIZATION,
                f"{legacy_application}{suffix}",
            )
            for key in source.allKeys():
                if not target.contains(key):
                    target.setValue(key, source.value(key))
                    migrated = True

        target.setValue(marker, True)
        target.sync()
    return migrated
