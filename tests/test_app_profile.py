from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.app_profile import (
    DEFAULT_APP_PROFILE,
    load_app_profile,
)


class AppProfileTests(unittest.TestCase):
    def test_missing_profile_keeps_existing_application_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = load_app_profile(
                Path(directory) / "missing.json"
            )
        self.assertEqual(profile, DEFAULT_APP_PROFILE)

    def test_release_profile_isolates_all_persistent_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release_profile.json"
            path.write_text(
                json.dumps(
                    {
                        "storage_namespace": "KeeneticManager-Beta-Test",
                        "settings_organization": "KeeneticManagerBeta",
                        "settings_application": "KeeneticManager-Beta-Test",
                        "router_credential_service": "Beta.Router.Admin",
                        "smb_credential_service": "Beta.SMB",
                        "legacy_storage_namespaces": [
                            "KeeneticManager-Beta-Old",
                            "../unsafe",
                            "KeeneticManager-Beta-Old",
                        ],
                        "legacy_router_credential_services": [
                            "Old.Router.Admin",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            profile = load_app_profile(path)

        self.assertEqual(
            profile.storage_namespace,
            "KeeneticManager-Beta-Test",
        )
        self.assertEqual(
            profile.settings_application,
            "KeeneticManager-Beta-Test",
        )
        self.assertEqual(
            profile.router_credential_service,
            "Beta.Router.Admin",
        )
        self.assertEqual(profile.smb_credential_service, "Beta.SMB")
        self.assertEqual(
            profile.legacy_storage_namespaces,
            ("KeeneticManager-Beta-Old",),
        )
        self.assertEqual(
            profile.legacy_router_credential_services,
            ("Old.Router.Admin",),
        )

    def test_unsafe_storage_namespace_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release_profile.json"
            path.write_text(
                '{"storage_namespace": "../shared"}',
                encoding="utf-8",
            )
            profile = load_app_profile(path)
        self.assertEqual(
            profile.storage_namespace,
            DEFAULT_APP_PROFILE.storage_namespace,
        )


if __name__ == "__main__":
    unittest.main()
