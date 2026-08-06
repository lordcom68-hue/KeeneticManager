from __future__ import annotations

import ipaddress
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.message_boxes import ask_yes_no


class AddRouterDialog(QDialog):
    CONNECTION_MODES = {
        "Автоматически": "auto",
        "Только локальный IP": "local",
        "Только KeenDNS": "keendns",
    }

    def __init__(
        self,
        parent=None,
        router_data: dict | None = None,
    ) -> None:
        super().__init__(parent)

        self.router_data = router_data
        self.is_edit_mode = router_data is not None
        self.password_was_configured = bool(
            (router_data or {}).get(
                "password_configured",
                False,
            )
        )

        self.setWindowTitle(
            "Изменить роутер"
            if self.is_edit_mode
            else "Добавить роутер"
        )
        self.setMinimumWidth(480)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(
            "Например: Главный офис"
        )

        self.connection_mode_input = QComboBox()
        self.connection_mode_input.addItems(
            self.CONNECTION_MODES.keys()
        )

        self.local_ip_input = QLineEdit()
        self.local_ip_input.setPlaceholderText(
            "192.168.1.1"
        )

        self.keendns_input = QLineEdit()
        self.keendns_input.setPlaceholderText(
            "router.example.keenetic.pro"
        )

        self.username_input = QLineEdit()
        self.username_input.setText("admin")

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.password_input.setMaxLength(256)
        self.password_input.textChanged.connect(
            self.password_text_changed
        )

        self.password_visibility_button = QPushButton(
            "Показать"
        )
        self.password_visibility_button.setCheckable(True)
        self.password_visibility_button.setFixedWidth(96)
        self.password_visibility_button.setToolTip(
            "Показать введённый пароль"
        )
        self.password_visibility_button.toggled.connect(
            self.password_visibility_toggled
        )

        password_row = QWidget()
        password_row_layout = QHBoxLayout(password_row)
        password_row_layout.setContentsMargins(0, 0, 0, 0)
        password_row_layout.setSpacing(8)
        password_row_layout.addWidget(self.password_input, 1)
        password_row_layout.addWidget(
            self.password_visibility_button
        )

        self.clear_password_checkbox = QCheckBox(
            "Удалить сохранённый пароль"
        )
        self.clear_password_checkbox.toggled.connect(
            self.clear_password_toggled
        )
        self.clear_password_checkbox.setVisible(
            self.is_edit_mode
            and self.password_was_configured
        )

        self.password_storage_label = QLabel(
            "Пароль хранится в Windows Credential Manager "
            "и не записывается в SQLite."
        )
        self.password_storage_label.setWordWrap(True)
        self.password_storage_label.setStyleSheet(
            "color: palette(mid);"
        )

        self.group_input = QLineEdit()
        self.group_input.setPlaceholderText(
            "Например: Офисы"
        )

        self.error_label = QLabel()
        self.error_label.setStyleSheet(
            "color: #fb7185;"
        )
        self.error_label.setWordWrap(True)

        form_layout = QFormLayout()
        form_layout.addRow(
            "Название:",
            self.name_input,
        )
        form_layout.addRow(
            "Режим подключения:",
            self.connection_mode_input,
        )
        form_layout.addRow(
            "Локальный IP:",
            self.local_ip_input,
        )
        form_layout.addRow(
            "KeenDNS:",
            self.keendns_input,
        )
        form_layout.addRow(
            "Логин:",
            self.username_input,
        )
        form_layout.addRow(
            "Пароль:",
            password_row,
        )
        form_layout.addRow(
            "",
            self.clear_password_checkbox,
        )
        form_layout.addRow(
            "",
            self.password_storage_label,
        )
        form_layout.addRow(
            "Группа:",
            self.group_input,
        )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )

        save_button.setText(
            "Сохранить изменения"
            if self.is_edit_mode
            else "Добавить"
        )
        cancel_button.setText("Назад")

        self.buttons.accepted.connect(
            self.validate_and_accept
        )
        self.buttons.rejected.connect(
            self.reject
        )

        layout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(self.error_label)
        layout.addWidget(self.buttons)

        if self.is_edit_mode:
            self.load_router_data()
        else:
            self.password_input.setPlaceholderText(
                "Пароль администратора роутера"
            )

    def load_router_data(self) -> None:
        if self.router_data is None:
            return

        self.name_input.setText(
            self.router_data.get("name") or ""
        )
        self.local_ip_input.setText(
            self.router_data.get("local_ip") or ""
        )
        self.keendns_input.setText(
            self.router_data.get("keendns") or ""
        )
        self.username_input.setText(
            self.router_data.get("username") or "admin"
        )

        # Секрет намеренно не извлекается из Credential Manager и
        # не подставляется обратно в GUI. Пустое поле сохраняет
        # существующий пароль без изменений.
        self.password_input.clear()
        self.password_input.setPlaceholderText(
            (
                "Оставьте пустым, чтобы сохранить текущий пароль"
                if self.password_was_configured
                else "Задайте новый пароль или оставьте пустым"
            )
        )

        self.group_input.setText(
            self.router_data.get("group_name") or ""
        )

        saved_mode = self.router_data.get(
            "connection_mode",
            "auto",
        )

        for visible_name, mode_value in (
            self.CONNECTION_MODES.items()
        ):
            if mode_value == saved_mode:
                self.connection_mode_input.setCurrentText(
                    visible_name
                )
                break

    def password_text_changed(
        self,
        value: str,
    ) -> None:
        if value and self.clear_password_checkbox.isChecked():
            self.clear_password_checkbox.setChecked(False)

    def password_visibility_toggled(
        self,
        checked: bool,
    ) -> None:
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if checked
            else QLineEdit.EchoMode.Password
        )
        self.password_visibility_button.setText(
            "Скрыть" if checked else "Показать"
        )
        self.password_visibility_button.setToolTip(
            "Скрыть введённый пароль"
            if checked
            else "Показать введённый пароль"
        )

    def clear_password_toggled(
        self,
        checked: bool,
    ) -> None:
        if checked:
            self.password_visibility_button.setChecked(False)

        self.password_input.setEnabled(not checked)
        self.password_visibility_button.setEnabled(not checked)

        if checked:
            self.password_input.clear()

    def validate_and_accept(self) -> None:
        self.error_label.clear()

        name = self.name_input.text().strip()
        local_ip = self.local_ip_input.text().strip()
        keendns = self.normalize_keendns(
            self.keendns_input.text()
        )
        username = self.username_input.text().strip()
        password = self.password_input.text()
        connection_mode = self.get_connection_mode()

        if not name:
            self.show_error(
                "Введите название роутера."
            )
            return

        if local_ip:
            try:
                ipaddress.ip_address(local_ip)
            except ValueError:
                self.show_error(
                    "Локальный IP имеет неверный формат. "
                    "Пример: 192.168.1.1"
                )
                return

        if keendns and (
            " " in keendns
            or "." not in keendns
        ):
            self.show_error(
                "Введите корректный KeenDNS-адрес."
            )
            return

        if (
            connection_mode == "local"
            and not local_ip
        ):
            self.show_error(
                "Для локального режима укажите IP."
            )
            return

        if (
            connection_mode == "keendns"
            and not keendns
        ):
            self.show_error(
                "Для режима KeenDNS укажите адрес."
            )
            return

        if (
            connection_mode == "auto"
            and not local_ip
            and not keendns
        ):
            self.show_error(
                "Укажите локальный IP, KeenDNS "
                "или оба адреса."
            )
            return

        if not username:
            self.show_error(
                "Введите имя пользователя."
            )
            return

        if any(
            character in "\r\n\x00"
            for character in password
        ):
            self.show_error(
                "Пароль содержит недопустимые управляющие символы."
            )
            return

        password_is_preserved = bool(
            self.is_edit_mode
            and self.password_was_configured
            and not password
            and not self.clear_password_checkbox.isChecked()
        )

        if not password and not password_is_preserved:
            action_text = (
                "Сохранить роутер без пароля?"
                if self.is_edit_mode
                else "Пароль не указан. Продолжить?"
            )
            answer = ask_yes_no(
                self,
                "Пустой пароль",
                action_text,
            )

            if answer != QMessageBox.StandardButton.Yes:
                return

        self.accept()

    @staticmethod
    def normalize_keendns(
        value: str,
    ) -> str:
        value = value.strip().lower()

        for prefix in (
            "https://",
            "http://",
        ):
            if value.startswith(prefix):
                value = value[len(prefix):]

        return value.rstrip("/")

    def get_connection_mode(self) -> str:
        visible_name = (
            self.connection_mode_input.currentText()
        )

        return self.CONNECTION_MODES[
            visible_name
        ]

    def show_error(
        self,
        message: str,
    ) -> None:
        self.error_label.setText(message)

    def get_data(self) -> dict[str, Any]:
        password_value: str | None = (
            self.password_input.text()
        )

        if self.clear_password_checkbox.isChecked():
            password_value = None
        elif (
            self.is_edit_mode
            and not password_value
        ):
            password_value = None

        return {
            "name": self.name_input.text().strip(),
            "local_ip": (
                self.local_ip_input.text().strip()
            ),
            "keendns": self.normalize_keendns(
                self.keendns_input.text()
            ),
            "connection_mode": (
                self.get_connection_mode()
            ),
            "username": (
                self.username_input.text().strip()
            ),
            "password": password_value,
            "clear_password": (
                self.clear_password_checkbox.isChecked()
            ),
            "group_name": (
                self.group_input.text().strip()
            ),
        }
