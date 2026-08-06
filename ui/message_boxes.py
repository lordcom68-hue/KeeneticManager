from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def ask_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Icon.Question,
    default_button: QMessageBox.StandardButton = (
        QMessageBox.StandardButton.No
    ),
) -> QMessageBox.StandardButton:
    """Показывает подтверждение с русскими кнопками «Да» и «Нет»."""
    message_box = QMessageBox(parent)
    message_box.setWindowTitle(str(title))
    message_box.setText(str(text))
    message_box.setIcon(icon)
    message_box.setStandardButtons(
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No
    )
    message_box.setDefaultButton(default_button)

    yes_button = message_box.button(
        QMessageBox.StandardButton.Yes
    )
    no_button = message_box.button(
        QMessageBox.StandardButton.No
    )
    if yes_button is not None:
        yes_button.setText("Да")
    if no_button is not None:
        no_button.setText("Нет")

    return QMessageBox.StandardButton(message_box.exec())
