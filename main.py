import os
import sys
from pathlib import Path

from core.portable_runtime import configure_portable_environment


configure_portable_environment()

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, QSettings, QTimer, Signal, Slot

from core.app_profile import (
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
)
from core.credential_store import CredentialStoreError
from core.services.crash_report_service import get_crash_report_service
from core.settings_migration import migrate_legacy_settings
from ui.main_window import MainWindow
from ui.theme import apply_application_theme
from ui.window_icon_manager import install_window_icon_manager


APP_ICON_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "keenetic_manager.ico"
)


class ApplicationErrorNotifier(QObject):
    error_reported = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.error_reported.connect(self.show_error)

    @Slot(str)
    def show_error(self, message: str) -> None:
        QMessageBox.critical(
            None,
            "Непредвиденная ошибка",
            str(message),
        )


def release_smoke_test_timeout_ms() -> int | None:
    """Return an opt-in timeout used only by packaged release checks."""
    raw = os.environ.get(
        "KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS",
        "",
    ).strip()
    if not raw:
        return None
    try:
        timeout = int(raw)
    except ValueError:
        return None
    return max(250, min(timeout, 15_000))


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

    crash_reporter = get_crash_report_service()
    error_notifier = ApplicationErrorNotifier()
    crash_reporter.install(
        notify=error_notifier.error_reported.emit,
    )

    try:
        try:
            window = MainWindow()
        except CredentialStoreError as error:
            QMessageBox.critical(
                None,
                "Хранилище паролей недоступно",
                str(error),
            )
            return 1
        except Exception as error:
            crash_reporter.record_exception(
                error,
                context="application_startup",
                notify=False,
            )
            QMessageBox.critical(
                None,
                "Ошибка запуска",
                (
                    "Программа не смогла завершить запуск. "
                    "Обезличенные сведения сохранены в аварийном журнале."
                ),
            )
            return 1

        window.setWindowIcon(app_icon)
        # При каждом запуске открываем главное окно развёрнутым.
        # Это сохраняет системную панель задач и кнопки управления окном.
        window.showMaximized()

        smoke_timeout = release_smoke_test_timeout_ms()
        if smoke_timeout is not None:
            QTimer.singleShot(smoke_timeout, window.close)

        return app.exec()
    finally:
        crash_reporter.uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
