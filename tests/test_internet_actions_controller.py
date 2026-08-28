from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.controllers.internet_actions_controller import (
    InternetActionsController,
)
from ui.main_window import MainWindow


class InternetActionsControllerTests(unittest.TestCase):
    def test_invalid_creation_uses_main_window_as_message_parent(self) -> None:
        window = SimpleNamespace()
        controller = InternetActionsController(window)

        with patch(
            "ui.controllers.internet_actions_controller.QMessageBox.warning"
        ) as warning:
            controller.open_connection_creation("", "")

        warning.assert_called_once_with(
            window,
            "Создание подключения",
            "Не удалось определить тип подключения.",
        )

    def test_delete_without_selected_router_releases_pending_card(self) -> None:
        finish_delete = MagicMock()
        window = SimpleNamespace(
            get_selected_router_id=lambda: None,
            internet_tab=SimpleNamespace(
                finish_connection_delete=finish_delete,
            ),
        )

        InternetActionsController(window).delete_internet_connection(
            "PPPoE0",
            "Провайдер",
            {},
        )

        finish_delete.assert_called_once_with("PPPoE0")

    def test_main_window_creation_method_remains_compatible_facade(self) -> None:
        window = SimpleNamespace()
        controller = MagicMock()

        with patch(
            "ui.main_window.InternetActionsController",
            return_value=controller,
        ) as controller_type:
            MainWindow.open_connection_creation(
                window,  # type: ignore[arg-type]
                "ethernet",
                "Кабельный Интернет",
            )

        controller_type.assert_called_once_with(window)
        controller.open_connection_creation.assert_called_once_with(
            "ethernet",
            "Кабельный Интернет",
        )


if __name__ == "__main__":
    unittest.main()
