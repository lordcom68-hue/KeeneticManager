from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.services.firmware_catalog_service import (
    FirmwareCatalogRecord,
    FirmwareCatalogService,
)
from core.services.firmware_inspection_service import (
    FirmwareInspectionCancelled,
    FirmwareInspectionError,
    FirmwareInspectionResult,
    FirmwareInspectionService,
)
from core.services.crash_report_service import report_background_exception
from ui.dialogs.firmware_inspection_dialog import (
    FirmwareComparisonDialog,
    FirmwareInspectionDialog,
)
from ui.message_boxes import ask_yes_no


class FirmwareCatalogBatchWorker(QThread):
    completed = Signal(object)
    cancelled = Signal()
    progress_changed = Signal(int, str)

    def __init__(
        self,
        paths: list[str | Path],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = tuple(Path(path) for path in paths)

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        results: list[FirmwareInspectionResult] = []
        errors: list[tuple[str, str]] = []
        total = max(1, len(self.paths))
        for index, path in enumerate(self.paths):
            if self.isInterruptionRequested():
                self.completed.emit(
                    {"results": results, "errors": errors}
                )
                self.cancelled.emit()
                return
            base = int(index * 100 / total)
            span = 100 / total
            try:
                result = FirmwareInspectionService.inspect(
                    path,
                    cancelled=self.isInterruptionRequested,
                    progress=lambda value, message, current=index, name=path.name,
                    width=span, total_count=total: (
                        self.progress_changed.emit(
                            min(
                                100,
                                int(
                                    current * 100 / total_count
                                    + value * width / 100
                                ),
                            ),
                            f"{current + 1} из {total_count} · {name}: {message}",
                        )
                    ),
                )
            except FirmwareInspectionCancelled:
                self.completed.emit(
                    {"results": results, "errors": errors}
                )
                self.cancelled.emit()
                return
            except FirmwareInspectionError as error:
                errors.append((path.name, str(error)))
                self.progress_changed.emit(
                    min(100, int(base + span)),
                    f"{index + 1} из {total} · {path.name}: файл не распознан",
                )
            except Exception as error:
                report_background_exception(
                    "firmware_catalog.batch_inspection",
                    error,
                    details={"file_name": path.name},
                )
                errors.append((path.name, str(error)))
                self.progress_changed.emit(
                    min(100, int(base + span)),
                    f"{index + 1} из {total} · {path.name}: ошибка анализа",
                )
            else:
                results.append(result)
        self.progress_changed.emit(100, "Пакетный анализ завершён.")
        self.completed.emit({"results": results, "errors": errors})


class FirmwareCatalogDialog(QDialog):
    FILTERS = (
        ("Все записи", "all"),
        ("Портированные", "ported"),
        ("Без метки портирования", "unverified"),
        ("Файл недоступен или изменён", "file_problem"),
    )
    FILE_STATE_LABELS = {
        "available": "Доступен",
        "missing": "Не найден",
        "changed": "Возможно изменён",
    }

    def __init__(
        self,
        service: FirmwareCatalogService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.records: list[FirmwareCatalogRecord] = []
        self.visible_records: list[FirmwareCatalogRecord] = []
        self.worker: FirmwareCatalogBatchWorker | None = None
        self.worker_mode = ""
        self.worker_expected_sha256 = ""

        self.setWindowTitle("Каталог прошивок")
        self.setMinimumSize(1120, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Каталог исследованных прошивок")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "Хранятся только метаданные и отчёты. BIN-файлы не копируются "
            "и не изменяются."
        )
        description.setWordWrap(True)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Поиск по файлу, модели, версии, архитектуре или SHA-256"
        )
        self.filter_combo = QComboBox()
        for label, value in self.FILTERS:
            self.filter_combo.addItem(label, value)
        self.refresh_button = QPushButton("Обновить список")
        filters.addWidget(self.search_input, 1)
        filters.addWidget(self.filter_combo)
        filters.addWidget(self.refresh_button)

        file_actions = QHBoxLayout()
        file_actions.setSpacing(10)
        self.add_files_button = QPushButton("Добавить файлы")
        self.reanalyze_button = QPushButton("Повторно проверить")
        self.locate_button = QPushButton("Найти перемещённый файл")
        self.export_button = QPushButton("Экспорт JSON")
        self.import_button = QPushButton("Импорт JSON")
        self.cancel_worker_button = QPushButton("Отменить анализ")
        self.cancel_worker_button.setVisible(False)
        file_actions.addWidget(self.add_files_button)
        file_actions.addWidget(self.reanalyze_button)
        file_actions.addWidget(self.locate_button)
        file_actions.addWidget(self.export_button)
        file_actions.addWidget(self.import_button)
        file_actions.addStretch()
        file_actions.addWidget(self.cancel_worker_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            (
                "Файл",
                "Модель",
                "Версия",
                "Архитектура",
                "Происхождение",
                "Целостность",
                "Состояние файла",
                "Последняя проверка",
                "Дата анализа",
            )
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.open_button = QPushButton("Открыть отчёт")
        self.compare_button = QPushButton("Сравнить выбранные")
        self.remove_button = QPushButton("Удалить запись")
        self.clear_button = QPushButton("Очистить каталог")
        actions.addWidget(self.open_button)
        actions.addWidget(self.compare_button)
        actions.addStretch()
        actions.addWidget(self.remove_button)
        actions.addWidget(self.clear_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.close_button = buttons.button(
            QDialogButtonBox.StandardButton.Close
        )
        self.close_button.setText("Закрыть")

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(filters)
        layout.addLayout(file_actions)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(actions)
        layout.addWidget(buttons)

        self.search_input.textChanged.connect(self.apply_filters)
        self.filter_combo.currentIndexChanged.connect(self.apply_filters)
        self.refresh_button.clicked.connect(self.reload)
        self.add_files_button.clicked.connect(self.add_files)
        self.reanalyze_button.clicked.connect(self.reanalyze_selected)
        self.locate_button.clicked.connect(self.locate_selected)
        self.export_button.clicked.connect(self.export_catalog)
        self.import_button.clicked.connect(self.import_catalog)
        self.cancel_worker_button.clicked.connect(self.cancel_worker)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_report())
        self.open_button.clicked.connect(self.open_report)
        self.compare_button.clicked.connect(self.compare_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_catalog)

        self.reload()

    def reload(self) -> None:
        self.records = self.service.list_records(refresh_file_state=True)
        self.apply_filters()

    def apply_filters(self) -> None:
        query = self.search_input.text().strip().casefold()
        selected_filter = str(self.filter_combo.currentData() or "all")
        self.visible_records = [
            record
            for record in self.records
            if self.record_matches(record, query, selected_filter)
        ]
        self._populate_table()

    @staticmethod
    def record_matches(
        record: FirmwareCatalogRecord,
        query: str,
        selected_filter: str,
    ) -> bool:
        inspection = record.inspection
        normalized_query = str(query or "").strip().casefold()
        searchable = " ".join(
            (
                inspection.file_name,
                inspection.file_path,
                inspection.internal_model,
                " ".join(inspection.compatible_models),
                inspection.firmware_version,
                inspection.architecture,
                inspection.sha256,
                (
                    record.last_router_check.verdict_label
                    if record.last_router_check is not None
                    else ""
                ),
            )
        ).casefold()
        if normalized_query and normalized_query not in searchable:
            return False
        if selected_filter == "ported":
            return inspection.origin == "ported"
        if selected_filter == "unverified":
            return inspection.origin != "ported"
        if selected_filter == "file_problem":
            return record.file_state != "available"
        return True

    @classmethod
    def record_row_values(
        cls,
        record: FirmwareCatalogRecord,
    ) -> tuple[str, ...]:
        inspection = record.inspection
        origin = (
            "Портированная"
            if inspection.origin == "ported"
            else "Не подтверждено"
        )
        if inspection.integrity_checked:
            integrity = (
                "Подтверждена"
                if inspection.integrity_valid
                else "Не подтверждена"
            )
        else:
            integrity = "Не проверена"
        last_check = (
            record.last_router_check.verdict_label
            if record.last_router_check is not None
            else "—"
        )
        file_state = (
            "Импортировано, файл не привязан"
            if record.record_source == "imported"
            and record.file_state == "missing"
            else cls.FILE_STATE_LABELS.get(record.file_state, "Неизвестно")
        )
        return (
            inspection.file_name,
            inspection.internal_model or "—",
            inspection.firmware_version or "—",
            inspection.architecture or "—",
            origin,
            integrity,
            file_state,
            last_check,
            cls._format_timestamp(record.last_analyzed_at),
        )

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for row, record in enumerate(self.visible_records):
            inspection = record.inspection
            values = self.record_row_values(record)
            self.table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.sha256)
                if column == 0:
                    item.setToolTip(
                        f"{inspection.file_path}\nSHA-256: {inspection.sha256}"
                    )
                self.table.setItem(row, column, item)

        problem_count = sum(
            record.file_state != "available" for record in self.records
        )
        self.status_label.setText(
            f"Показано: {len(self.visible_records)} из {len(self.records)}. "
            f"Недоступных или изменённых файлов: {problem_count}."
        )
        self.update_actions()

    def selected_records(self) -> list[FirmwareCatalogRecord]:
        selected_rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()}
        )
        result: list[FirmwareCatalogRecord] = []
        for row in selected_rows:
            item = self.table.item(row, 0)
            if item is None:
                continue
            sha256 = str(item.data(Qt.ItemDataRole.UserRole) or "")
            record = next(
                (
                    candidate
                    for candidate in self.visible_records
                    if candidate.sha256 == sha256
                ),
                None,
            )
            if record is not None:
                result.append(record)
        return result

    def update_actions(self) -> None:
        selected = self.selected_records()
        count = len(selected)
        busy = self.worker is not None
        selected_file_available = bool(
            count == 1
            and Path(selected[0].inspection.file_path).is_file()
        )
        self.open_button.setEnabled(not busy and count == 1)
        self.compare_button.setEnabled(not busy and count == 2)
        self.remove_button.setEnabled(not busy and count >= 1)
        self.clear_button.setEnabled(not busy and bool(self.records))
        self.add_files_button.setEnabled(not busy)
        self.reanalyze_button.setEnabled(
            not busy and selected_file_available
        )
        self.locate_button.setEnabled(not busy and count == 1)
        self.export_button.setEnabled(not busy and bool(self.records))
        self.import_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)

    def open_report(self) -> None:
        selected = self.selected_records()
        if len(selected) != 1:
            return
        self._warn_about_stale_files(selected)
        dialog = FirmwareInspectionDialog(selected[0].inspection, self)
        dialog.exec()

    def compare_selected(self) -> None:
        selected = self.selected_records()
        if len(selected) != 2:
            return
        self._warn_about_stale_files(selected)
        comparison = FirmwareInspectionService.compare(
            selected[0].inspection,
            selected[1].inspection,
        )
        dialog = FirmwareComparisonDialog(comparison, self)
        dialog.exec()

    def _warn_about_stale_files(
        self,
        records: list[FirmwareCatalogRecord],
    ) -> None:
        stale = [
            record
            for record in records
            if record.file_state != "available"
        ]
        if not stale:
            return
        QMessageBox.information(
            self,
            "Сохранённые метаданные",
            (
                "Один или несколько исходных файлов недоступны либо изменены. "
                "Будет открыт ранее сохранённый отчёт без повторного анализа."
            ),
        )

    def add_files(self) -> None:
        filenames, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Выберите файлы прошивок",
            self._initial_directory(),
            "Образы прошивки (*.bin);;Все файлы (*)",
        )
        if filenames:
            self._start_worker(filenames, mode="batch")

    def reanalyze_selected(self) -> None:
        selected = self.selected_records()
        if len(selected) != 1:
            return
        path = Path(selected[0].inspection.file_path)
        if not path.is_file():
            QMessageBox.information(
                self,
                "Повторная проверка",
                "Исходный файл не найден. Используйте «Найти перемещённый файл».",
            )
            return
        self._start_worker([path], mode="reanalyze")

    def locate_selected(self) -> None:
        selected = self.selected_records()
        if len(selected) != 1:
            return
        record = selected[0]
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            f"Найдите файл «{record.inspection.file_name}»",
            self._initial_directory(record),
            "Образы прошивки (*.bin);;Все файлы (*)",
        )
        if not filename:
            return
        self._start_worker(
            [filename],
            mode="relink",
            expected_sha256=record.sha256,
        )

    def export_catalog(self) -> None:
        selected = self.selected_records()
        records = selected if selected else self.records
        suggested_name = (
            "firmware_catalog_selected.json"
            if selected
            else "firmware_catalog.json"
        )
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Экспорт каталога прошивок",
            str(Path(self._initial_directory()) / suggested_name),
            "Каталог прошивок (*.json);;Все файлы (*)",
        )
        if not filename:
            return
        if not self.service.export_to_file(filename, records=records):
            QMessageBox.warning(
                self,
                "Экспорт каталога",
                "Не удалось сохранить JSON-файл.",
            )
            return
        QMessageBox.information(
            self,
            "Экспорт каталога",
            (
                f"Экспортировано записей: {len(records)}.\n\n"
                "Локальные пути, имена роутеров и BIN-файлы не включены."
            ),
        )

    def import_catalog(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Импорт каталога прошивок",
            self._initial_directory(),
            "Каталог прошивок (*.json);;Все файлы (*)",
        )
        if not filename:
            return
        result = self.service.import_from_file(filename)
        if not result.success:
            QMessageBox.warning(
                self,
                "Импорт каталога",
                "\n".join(result.errors),
            )
            return
        self.reload()
        QMessageBox.information(
            self,
            "Импорт каталога",
            (
                f"Добавлено: {result.imported}.\n"
                f"Обновлено: {result.updated}.\n"
                f"Пропущено: {result.skipped}.\n\n"
                "Для импортированных записей путь к BIN можно восстановить "
                "по SHA-256."
            ),
        )

    def _start_worker(
        self,
        paths: list[str | Path],
        *,
        mode: str,
        expected_sha256: str = "",
    ) -> None:
        if self.worker is not None or not paths:
            return
        worker = FirmwareCatalogBatchWorker(paths, self)
        self.worker = worker
        self.worker_mode = str(mode)
        self.worker_expected_sha256 = str(expected_sha256)
        worker.progress_changed.connect(self._worker_progress)
        worker.completed.connect(self._worker_completed)
        worker.cancelled.connect(self._worker_cancelled)
        worker.finished.connect(self._worker_finished)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.cancel_worker_button.setVisible(True)
        self.status_label.setText(
            f"Подготовка к анализу файлов: {len(paths)}..."
        )
        self.update_actions()
        worker.start()

    def cancel_worker(self) -> None:
        if self.worker is None:
            return
        self.cancel_worker_button.setEnabled(False)
        self.status_label.setText("Отмена анализа после текущей операции...")
        self.worker.stop()

    def _worker_progress(self, percentage: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, int(percentage))))
        self.status_label.setText(str(message))

    def _worker_completed(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        results = [
            item
            for item in data.get("results") or []
            if isinstance(item, FirmwareInspectionResult)
        ]
        errors = [
            (str(item[0]), str(item[1]))
            for item in data.get("errors") or []
            if isinstance(item, (list, tuple)) and len(item) >= 2
        ]
        saved = 0
        mismatch = False
        if self.worker_mode == "relink":
            if results:
                record = self.service.relink_inspection(
                    self.worker_expected_sha256,
                    results[0],
                )
                saved = 1 if record is not None else 0
                mismatch = record is None
        else:
            for result in results:
                if self.service.upsert_inspection(result) is not None:
                    saved += 1

        self.reload()
        if mismatch:
            QMessageBox.warning(
                self,
                "Файл не совпадает",
                (
                    "SHA-256 выбранного файла не совпадает с записью каталога. "
                    "Путь не изменён."
                ),
            )
        if errors:
            details = "\n".join(
                f"• {name}: {message}" for name, message in errors[:5]
            )
            if len(errors) > 5:
                details += f"\n• Ещё ошибок: {len(errors) - 5}."
            QMessageBox.warning(
                self,
                "Не все файлы обработаны",
                details,
            )
        self.status_label.setText(
            f"Обработано файлов: {len(results) + len(errors)}; "
            f"записей обновлено: {saved}; ошибок: {len(errors)}."
        )

    def _worker_cancelled(self) -> None:
        self.status_label.setText("Анализ файлов отменён.")

    def _worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self.worker_mode = ""
        self.worker_expected_sha256 = ""
        if worker is not None:
            worker.deleteLater()
        self.progress_bar.setVisible(False)
        self.cancel_worker_button.setVisible(False)
        self.cancel_worker_button.setEnabled(True)
        self.update_actions()

    def _initial_directory(
        self,
        preferred: FirmwareCatalogRecord | None = None,
    ) -> str:
        candidates = (
            [preferred]
            if preferred is not None
            else self.records
        )
        for record in candidates:
            if record is None:
                continue
            parent = Path(record.inspection.file_path).parent
            if parent.is_dir():
                return str(parent)
        return str(Path.home())

    def remove_selected(self) -> None:
        selected = self.selected_records()
        if not selected:
            return
        answer = ask_yes_no(
            self,
            "Удалить записи каталога",
            (
                f"Удалить выбранные записи: {len(selected)}?\n\n"
                "Исходные BIN-файлы останутся без изменений."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = 0
        for record in selected:
            if not self.service.remove(record.sha256):
                failed += 1
        self.reload()
        if failed:
            QMessageBox.warning(
                self,
                "Каталог прошивок",
                f"Не удалось удалить записей: {failed}.",
            )

    def clear_catalog(self) -> None:
        if not self.records:
            return
        answer = ask_yes_no(
            self,
            "Очистить каталог прошивок",
            (
                "Удалить все сохранённые отчёты и метаданные прошивок?\n\n"
                "Исходные BIN-файлы останутся без изменений."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.service.clear():
            QMessageBox.warning(
                self,
                "Каталог прошивок",
                "Не удалось очистить каталог.",
            )
            return
        self.reload()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            QMessageBox.information(
                self,
                "Анализ выполняется",
                "Дождитесь завершения анализа или нажмите «Отменить анализ».",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self.worker is not None:
            QMessageBox.information(
                self,
                "Анализ выполняется",
                "Дождитесь завершения анализа или нажмите «Отменить анализ».",
            )
            return
        super().reject()

    @staticmethod
    def _format_timestamp(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "—"
        return text.replace("T", " ").split("+", 1)[0]
