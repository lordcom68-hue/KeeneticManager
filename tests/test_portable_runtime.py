from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_runtime import configure_portable_environment


class PortableRuntimeTests(unittest.TestCase):
    ENVIRONMENT_KEYS = (
        "KEENETIC_MANAGER_DATA_DIR",
        "KEENETIC_MANAGER_SETTINGS_FILE",
        "KEENETIC_MANAGER_PROFILE",
    )

    def test_portable_marker_isolates_data_settings_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "KeeneticManager.exe"
            (root / "portable.flag").write_text(
                "portable\n",
                encoding="utf-8",
            )
            (root / "portable_profile.json").write_text(
                json.dumps({"profile_id": "test-portable"}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                for key in self.ENVIRONMENT_KEYS:
                    os.environ.pop(key, None)

                resolved = configure_portable_environment(
                    executable_path=executable,
                    frozen=True,
                )

                self.assertEqual(resolved, root.resolve())
                self.assertEqual(
                    Path(os.environ["KEENETIC_MANAGER_DATA_DIR"]),
                    root / "data",
                )
                self.assertEqual(
                    Path(os.environ["KEENETIC_MANAGER_SETTINGS_FILE"]),
                    root / "data" / "settings.ini",
                )
                self.assertEqual(
                    Path(os.environ["KEENETIC_MANAGER_PROFILE"]),
                    root / "portable_profile.json",
                )

    def test_regular_executable_ignores_portable_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "KeeneticManager.exe"
            with patch.dict(os.environ, {}, clear=False):
                for key in self.ENVIRONMENT_KEYS:
                    os.environ.pop(key, None)

                resolved = configure_portable_environment(
                    executable_path=executable,
                    frozen=True,
                )

                self.assertIsNone(resolved)
                for key in self.ENVIRONMENT_KEYS:
                    self.assertNotIn(key, os.environ)


if __name__ == "__main__":
    unittest.main()
