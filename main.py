import sys
from pathlib import Path

from core.portable_runtime import configure_portable_environment


configure_portable_environment()

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QSettings

from core.app_profile import (
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
)
from core.credential_store import CredentialStoreError
from core.settings_migration import migrate_legacy_settings
from ui.main_window import MainWindow
from ui.theme import apply_application_theme
from ui.window_icon_manager import install_window_icon_manager


APP_ICON_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "keenetic_manager.ico"
)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Keenetic Manager")
    app.setOrganizationName(SETTINGS_ORGANIZATION)
    app.setOrganizationDomain(SETTINGS_APPLICATION)
    migrate_legacy_settings(QSettings)
    app_icon = QIcon(str(APP_ICON_PATH))
    app.setWindowIcon(app_icon)
    install_window_icon_manager(app, app_icon)

    apply_application_theme(app)

    try:
        window = MainWindow()
    except CredentialStoreError as error:
        QMessageBox.critical(
            None,
            "Хранилище паролей недоступно",
            str(error),
        )
        return 1

    window.setWindowIcon(app_icon)
    # При каждом запуске открываем главное окно развёрнутым.
    # Это сохраняет системную панель задач и кнопки управления окном.
    window.showMaximized()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
