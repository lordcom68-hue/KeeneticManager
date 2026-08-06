from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLineEdit
    from ui.dialogs.add_router_dialog import AddRouterDialog
except ImportError:
    QApplication = None
    AddRouterDialog = None
    QLineEdit = None


@unittest.skipIf(
    QApplication is None or AddRouterDialog is None,
    "PySide6 не установлен в текущей среде",
)
class AddRouterDialogCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_edit_dialog_never_prefills_saved_password(self) -> None:
        dialog = AddRouterDialog(
            router_data={
                "name": "Router",
                "local_ip": "192.0.2.1",
                "keendns": "router.example.invalid",
                "connection_mode": "auto",
                "username": "admin",
                "password_configured": True,
                "group_name": "Test",
            }
        )

        self.assertEqual(dialog.password_input.text(), "")
        self.assertIn(
            "сохранить текущий пароль",
            dialog.password_input.placeholderText().lower(),
        )
        self.assertFalse(dialog.clear_password_checkbox.isHidden())

        data = dialog.get_data()
        self.assertIsNone(data["password"])
        self.assertFalse(data["clear_password"])
        dialog.close()

    def test_edit_dialog_can_replace_or_clear_password(self) -> None:
        dialog = AddRouterDialog(
            router_data={
                "name": "Router",
                "local_ip": "192.0.2.1",
                "keendns": "router.example.invalid",
                "connection_mode": "auto",
                "username": "admin",
                "password_configured": True,
                "group_name": "Test",
            }
        )

        dialog.password_input.setText("replacement")
        changed = dialog.get_data()
        self.assertEqual(changed["password"], "replacement")
        self.assertFalse(changed["clear_password"])

        dialog.clear_password_checkbox.setChecked(True)
        cleared = dialog.get_data()
        self.assertIsNone(cleared["password"])
        self.assertTrue(cleared["clear_password"])
        dialog.close()

    def test_password_visibility_can_be_toggled_safely(self) -> None:
        dialog = AddRouterDialog()
        dialog.password_input.setText("temporary-secret")

        self.assertEqual(
            dialog.password_input.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.assertEqual(
            dialog.password_visibility_button.text(),
            "Показать",
        )

        dialog.password_visibility_button.setChecked(True)
        self.assertEqual(
            dialog.password_input.echoMode(),
            QLineEdit.EchoMode.Normal,
        )
        self.assertEqual(
            dialog.password_visibility_button.text(),
            "Скрыть",
        )

        dialog.password_visibility_button.setChecked(False)
        self.assertEqual(
            dialog.password_input.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        dialog.close()

    def test_clearing_saved_password_disables_visibility(self) -> None:
        dialog = AddRouterDialog(
            router_data={
                "name": "Router",
                "local_ip": "192.0.2.1",
                "connection_mode": "local",
                "username": "admin",
                "password_configured": True,
            }
        )
        dialog.password_input.setText("replacement")
        dialog.password_visibility_button.setChecked(True)

        dialog.clear_password_checkbox.setChecked(True)

        self.assertEqual(dialog.password_input.text(), "")
        self.assertFalse(
            dialog.password_visibility_button.isChecked()
        )
        self.assertFalse(
            dialog.password_visibility_button.isEnabled()
        )
        self.assertEqual(
            dialog.password_input.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        dialog.close()


if __name__ == "__main__":
    unittest.main()
