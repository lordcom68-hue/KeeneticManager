from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_portable_environment(
    *,
    executable_path: str | Path | None = None,
    frozen: bool | None = None,
) -> Path | None:
    """Изолирует данные сборки, рядом с которой лежит portable.flag."""
    is_frozen = (
        bool(getattr(sys, "frozen", False))
        if frozen is None
        else bool(frozen)
    )
    if not is_frozen:
        return None

    executable = Path(executable_path or sys.executable).resolve()
    portable_root = executable.parent
    if not (portable_root / "portable.flag").is_file():
        return None

    os.environ.setdefault(
        "KEENETIC_MANAGER_DATA_DIR",
        str(portable_root / "data"),
    )
    os.environ.setdefault(
        "KEENETIC_MANAGER_SETTINGS_FILE",
        str(portable_root / "data" / "settings.ini"),
    )
    profile_path = portable_root / "portable_profile.json"
    if profile_path.is_file():
        os.environ.setdefault(
            "KEENETIC_MANAGER_PROFILE",
            str(profile_path),
        )
    return portable_root
