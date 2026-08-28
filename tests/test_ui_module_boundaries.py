from __future__ import annotations

import ast
import unittest
from pathlib import Path


class UiModuleBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    @staticmethod
    def method_span(path: Path, class_name: str, method_name: str) -> int:
        module = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        return int(method.end_lineno) - int(method.lineno) + 1

    def test_settings_tab_is_owned_by_controller(self) -> None:
        main_window = self.root / "ui" / "main_window.py"
        controller = (
            self.root / "ui" / "controllers" / "settings_tab_controller.py"
        )
        self.assertLess(
            self.method_span(main_window, "MainWindow", "create_settings_tab"),
            10,
        )
        self.assertGreater(
            self.method_span(
                controller,
                "SettingsTabController",
                "create_settings_tab",
            ),
            200,
        )

    def test_internet_settings_dialog_has_dedicated_module(self) -> None:
        internet_tab = self.root / "ui" / "widgets" / "internet_tab.py"
        dialog = (
            self.root
            / "ui"
            / "widgets"
            / "internet_connection_settings_dialog.py"
        )
        tab_source = internet_tab.read_text(encoding="utf-8")
        dialog_source = dialog.read_text(encoding="utf-8")
        self.assertIn(
            "from ui.widgets.internet_connection_settings_dialog import (",
            tab_source,
        )
        self.assertNotIn(
            "class InternetConnectionSettingsDialog",
            tab_source,
        )
        self.assertIn(
            "class InternetConnectionSettingsDialog",
            dialog_source,
        )
        self.assertLess(len(tab_source.splitlines()), 4000)

    def test_internet_actions_are_owned_by_controller(self) -> None:
        main_window = self.root / "ui" / "main_window.py"
        controller = (
            self.root
            / "ui"
            / "controllers"
            / "internet_actions_controller.py"
        )
        method_names = (
            "open_connection_creation",
            "change_cable_connection_protocol",
            "delete_internet_connection",
            "change_connection_interface_settings",
        )

        for method_name in method_names:
            self.assertLessEqual(
                self.method_span(main_window, "MainWindow", method_name),
                15,
            )
            self.assertGreater(
                self.method_span(
                    controller,
                    "InternetActionsController",
                    method_name,
                ),
                100,
            )


if __name__ == "__main__":
    unittest.main()
