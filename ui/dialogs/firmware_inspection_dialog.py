from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.services.firmware_inspection_service import (
    FirmwareComparisonResult,
    FirmwareInspectionCancelled,
    FirmwareInspectionError,
    FirmwareInspectionResult,
    FirmwareInspectionService,
    FirmwareRouterCheckResult,
)
from core.services.crash_report_service import report_background_exception


class FirmwareInspectionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress_changed = Signal(int, str)

    def __init__(
        self,
        path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = Path(path)

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            result = FirmwareInspectionService.inspect(
                self.path,
                cancelled=self.isInterruptionRequested,
                progress=self.progress_changed.emit,
            )
        except FirmwareInspectionCancelled:
            self.cancelled.emit()
        except FirmwareInspectionError as error:
            self.failed.emit(str(error))
        except Exception as error:
            report_background_exception(
                "firmware_inspection.inspect",
                error,
                details={"file_name": self.path.name},
            )
            self.failed.emit(str(error))
        else:
            self.completed.emit(result)


class FirmwareComparisonWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress_changed = Signal(int, str)

    def __init__(
        self,
        first_path: str | Path,
        second_path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.first_path = Path(first_path)
        self.second_path = Path(second_path)

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            first = FirmwareInspectionService.inspect(
                self.first_path,
                cancelled=self.isInterruptionRequested,
                progress=lambda value, message: self.progress_changed.emit(
                    int(value) // 2,
                    f"Первый файл: {message}",
                ),
            )
            second = FirmwareInspectionService.inspect(
                self.second_path,
                cancelled=self.isInterruptionRequested,
                progress=lambda value, message: self.progress_changed.emit(
                    50 + int(value) // 2,
                    f"Второй файл: {message}",
                ),
            )
            result = FirmwareInspectionService.compare(first, second)
        except FirmwareInspectionCancelled:
            self.cancelled.emit()
        except FirmwareInspectionError as error:
            self.failed.emit(str(error))
        except Exception as error:
            report_background_exception(
                "firmware_inspection.compare",
                error,
                details={
                    "first_file": self.first_path.name,
                    "second_file": self.second_path.name,
                },
            )
            self.failed.emit(str(error))
        else:
            self.completed.emit(result)


class FirmwareRouterCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress_changed = Signal(int, str)

    def __init__(
        self,
        path: str | Path,
        *,
        router_name: str,
        router_model: str,
        current_version: str,
        router_wifi_mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.router_name = str(router_name)
        self.router_model = str(router_model)
        self.current_version = str(current_version)
        self.router_wifi_mode = str(router_wifi_mode)

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            firmware = FirmwareInspectionService.inspect(
                self.path,
                cancelled=self.isInterruptionRequested,
                progress=self.progress_changed.emit,
            )
            result = FirmwareInspectionService.check_for_router(
                firmware,
                router_name=self.router_name,
                router_model=self.router_model,
                current_version=self.current_version,
                router_wifi_mode=self.router_wifi_mode,
            )
        except FirmwareInspectionCancelled:
            self.cancelled.emit()
        except FirmwareInspectionError as error:
            self.failed.emit(str(error))
        except Exception as error:
            report_background_exception(
                "firmware_inspection.router_check",
                error,
                details={"file_name": self.path.name},
            )
            self.failed.emit(str(error))
        else:
            self.completed.emit(result)


class FirmwareInspectionDialog(QDialog):
    def __init__(
        self,
        result: FirmwareInspectionResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.result = result
        self.report = self.build_report(result)

        self.setWindowTitle("Анализ прошивки")
        self.setMinimumSize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Безопасный офлайн-анализ завершён")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "Файл прочитан локально. Он не изменялся и не отправлялся на роутер."
        )
        description.setWordWrap(True)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlainText(self.report)
        self.report_view.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(
            QDialogButtonBox.StandardButton.Close
        )
        close_button.setText("Закрыть")
        copy_button = QPushButton("Копировать отчёт")
        copy_button.clicked.connect(self.copy_report)
        buttons.addButton(
            copy_button,
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        save_button = QPushButton("Сохранить отчёт")
        save_button.clicked.connect(self.save_report)
        buttons.addButton(
            save_button,
            QDialogButtonBox.ButtonRole.ActionRole,
        )

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.report_view, 1)
        layout.addWidget(buttons)

    def copy_report(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.report)

    def save_report(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить отчёт анализа",
            f"{Path(self.result.file_name).stem}_firmware_report.txt",
            "Текстовый отчёт (*.txt);;Все файлы (*)",
        )
        if filename:
            try:
                Path(filename).write_text(
                    self.report + "\n",
                    encoding="utf-8",
                )
            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Сохранение отчёта",
                    f"Не удалось сохранить отчёт: {error}",
                )

    @classmethod
    def build_report(cls, result: FirmwareInspectionResult) -> str:
        integrity = "Не проверена"
        if result.integrity_checked:
            integrity = (
                "Подтверждена"
                if result.integrity_valid
                else "НЕ ПОДТВЕРЖДЕНА"
            )
        origin = (
            "Обнаружены признаки портированной сборки"
            if result.origin == "ported"
            else "Метка портированной сборки не найдена"
        )
        models = ", ".join(result.compatible_models) or "Не определены"
        versions = ", ".join(result.discovered_versions) or "Не определены"
        rootfs = "Не найдена"
        if result.rootfs is not None:
            rootfs = (
                f"SquashFS {result.rootfs.version}, "
                f"{result.rootfs.compression}, "
                f"блок {cls.format_size(result.rootfs.block_size)}, "
                f"объём {cls.format_size(result.rootfs.bytes_used)}"
            )
        warnings = (
            "\n".join(f"• {item}" for item in result.warnings)
            if result.warnings
            else "• Предупреждений нет."
        )
        profile = result.capability_profile
        confidence = {
            "high": "высокая",
            "medium": "средняя",
            "low": "низкая",
        }.get(profile.confidence, "не определена")
        wifi_mode = FirmwareInspectionService._wifi_mode_label(
            profile.wifi_management_mode
        )
        state_labels = {
            "confirmed": "найдено в образе",
            "inferred": "предполагается",
            "unknown": "не подтверждено",
        }
        capability_lines = "\n".join(
            (
                f"• {feature.label}: "
                f"{state_labels.get(feature.state, feature.state)} — "
                f"{feature.detail}"
            )
            for feature in profile.features
        )
        endpoint_lines = (
            ", ".join(profile.discovered_rci_endpoints)
            or "Не найдены"
        )
        return "\n".join(
            (
                f"Файл: {result.file_name}",
                f"Путь: {result.file_path}",
                f"Размер: {cls.format_size(result.size_bytes)}",
                f"SHA-256: {result.sha256}",
                "",
                f"Контейнер: {result.container}",
                f"Внутренний профиль: {result.internal_model or 'Не определён'}",
                f"Совместимые модели: {models}",
                f"Архитектура: {result.architecture}",
                f"Версия KeeneticOS: {result.firmware_version or 'Не определена'}",
                f"Найденные версии: {versions}",
                f"Происхождение: {origin}",
                f"Маркеров портирования: {result.porting_marker_count}",
                f"Корневая система: {rootfs}",
                f"Целостность контейнера: {integrity}",
                "",
                "Предварительный профиль возможностей:",
                f"Режим Wi-Fi: {wifi_mode}",
                f"Уверенность статического анализа: {confidence}",
                f"RCI-признаки: {endpoint_lines}",
                capability_lines,
                "",
                "Предупреждения:",
                warnings,
                "",
                "Этот отчёт не подтверждает совместимость образа с конкретным "
                "устройством и не является разрешением на прошивку.",
            )
        )

    @staticmethod
    def format_size(value: int) -> str:
        size = max(0, int(value))
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.2f} МБ"
        if size >= 1024:
            return f"{size / 1024:.1f} КБ"
        return f"{size} байт"


class FirmwareComparisonDialog(QDialog):
    def __init__(
        self,
        result: FirmwareComparisonResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.result = result
        self.report = self.build_report(result)

        self.setWindowTitle("Сравнение прошивок")
        self.setMinimumSize(820, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(result.verdict_label)
        title.setObjectName("dialogTitle")
        description = QLabel(result.summary)
        description.setWordWrap(True)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlainText(self.report)
        self.report_view.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("Закрыть")
        copy_button = QPushButton("Копировать отчёт")
        copy_button.clicked.connect(self.copy_report)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        save_button = QPushButton("Сохранить отчёт")
        save_button.clicked.connect(self.save_report)
        buttons.addButton(save_button, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.report_view, 1)
        layout.addWidget(buttons)

    def copy_report(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.report)

    def save_report(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить сравнение прошивок",
            "firmware_comparison.txt",
            "Текстовый отчёт (*.txt);;Все файлы (*)",
        )
        if filename:
            try:
                Path(filename).write_text(
                    self.report + "\n",
                    encoding="utf-8",
                )
            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Сохранение отчёта",
                    f"Не удалось сохранить отчёт: {error}",
                )

    @classmethod
    def build_report(cls, result: FirmwareComparisonResult) -> str:
        changed_marker = "ИЗМЕНЕНО"
        field_lines = "\n".join(
            (
                f"{field.label}:\n"
                f"  1. {field.first_value}\n"
                f"  2. {field.second_value}"
                f"{'  [' + changed_marker + ']' if field.changed else ''}"
            )
            for field in result.fields
        )
        warnings = (
            "\n".join(f"• {item}" for item in result.warnings)
            if result.warnings
            else "• Дополнительных предупреждений нет."
        )
        return "\n".join(
            (
                "СРАВНЕНИЕ ПРОШИВОК",
                "",
                f"Вердикт: {result.verdict_label}",
                f"Пояснение: {result.summary}",
                "",
                f"Первый файл: {result.first.file_name}",
                f"Второй файл: {result.second.file_name}",
                "",
                field_lines,
                "",
                "Предупреждения:",
                warnings,
                "",
                "Статический анализ не подтверждает аппаратную совместимость "
                "и не является разрешением на прошивку.",
            )
        )


class FirmwareRouterCheckDialog(QDialog):
    def __init__(
        self,
        result: FirmwareRouterCheckResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.result = result
        self.report = self.build_report(result)

        self.setWindowTitle("Проверка прошивки для роутера")
        self.setMinimumSize(820, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(result.verdict_label)
        title.setObjectName("dialogTitle")
        title.setProperty("checkVerdict", result.verdict)
        description = QLabel(result.summary)
        description.setWordWrap(True)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlainText(self.report)
        self.report_view.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("Закрыть")
        copy_button = QPushButton("Копировать отчёт")
        copy_button.clicked.connect(self.copy_report)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        save_button = QPushButton("Сохранить отчёт")
        save_button.clicked.connect(self.save_report)
        buttons.addButton(save_button, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.report_view, 1)
        layout.addWidget(buttons)

    def copy_report(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.report)

    def save_report(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить проверку прошивки",
            "router_firmware_check.txt",
            "Текстовый отчёт (*.txt);;Все файлы (*)",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(
                self.report + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            QMessageBox.warning(
                self,
                "Сохранение отчёта",
                f"Не удалось сохранить отчёт: {error}",
            )

    @classmethod
    def build_report(cls, result: FirmwareRouterCheckResult) -> str:
        status_labels = {
            "passed": "ПРОЙДЕНО",
            "failed": "ОШИБКА",
            "warning": "ВНИМАНИЕ",
            "unknown": "НЕ ОПРЕДЕЛЕНО",
        }
        checks = "\n".join(
            (
                f"[{status_labels.get(item.status, item.status.upper())}] "
                f"{item.label}: {item.detail}"
            )
            for item in result.checks
        )
        warnings = (
            "\n".join(f"• {item}" for item in result.warnings)
            if result.warnings
            else "• Дополнительных предупреждений нет."
        )
        router_models = ", ".join(result.router_models) or "Не определены"
        return "\n".join(
            (
                "ПРОВЕРКА ПРОШИВКИ ДЛЯ ВЫБРАННОГО РОУТЕРА",
                "",
                f"Вердикт: {result.verdict_label}",
                f"Пояснение: {result.summary}",
                "",
                f"Роутер: {result.router_name}",
                f"Модель роутера: {result.router_model or 'Не определена'}",
                f"Внутренний код роутера: {router_models}",
                f"Текущая версия: {result.current_version or 'Не определена'}",
                "",
                f"Файл: {result.firmware.file_name}",
                f"Внутренняя модель файла: "
                f"{result.firmware.internal_model or 'Не определена'}",
                f"Версия файла: "
                f"{result.firmware.firmware_version or 'Не определена'}",
                f"SHA-256: {result.firmware.sha256}",
                "",
                "Проверки:",
                checks,
                "",
                "Предупреждения:",
                warnings,
                "",
                "Это предварительная проверка только для чтения. Она не "
                "загружает файл в роутер, не подтверждает аппаратную ревизию "
                "и не является разрешением на прошивку.",
            )
        )
