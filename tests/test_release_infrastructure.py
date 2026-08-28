from __future__ import annotations

import re
import unittest
from pathlib import Path


class ReleaseInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    @staticmethod
    def requirement_names(text: str) -> set[str]:
        names: set[str] = set()
        for line in text.splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)", value)
            if match:
                names.add(match.group(1).lower().replace("_", "-"))
        return names

    def test_release_constraints_pin_all_direct_dependencies(self) -> None:
        constraints = (
            self.root / "constraints-release.txt"
        ).read_text(encoding="utf-8")
        direct_text = "\n".join(
            (self.root / filename).read_text(encoding="utf-8")
            for filename in (
                "requirements.txt",
                "requirements-build.txt",
                "requirements-dev.txt",
            )
        )

        direct = self.requirement_names(direct_text)
        pinned = self.requirement_names(constraints)

        self.assertTrue(direct <= pinned)
        for line in constraints.splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                self.assertRegex(value, r"^[A-Za-z0-9_.-]+==[^=]+$")

    def test_windows_ci_uses_locked_quality_and_release_checks(self) -> None:
        workflow = (
            self.root / ".github" / "workflows" / "quality.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn('python-version: "3.14"', workflow)
        self.assertIn("-c constraints-release.txt", workflow)
        self.assertIn("./tools/quality_check.ps1", workflow)
        self.assertIn("./tools/build_release_source.ps1", workflow)
        self.assertIn("./tools/build_release.ps1", workflow)
        self.assertIn("-SkipInstaller", workflow)

    def test_clean_source_contains_release_constraints(self) -> None:
        script = (
            self.root / "tools" / "build_release_source.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('"constraints-release.txt"', script)


if __name__ == "__main__":
    unittest.main()
