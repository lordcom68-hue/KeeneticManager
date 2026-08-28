from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.services.router_self_check_service import RouterSelfCheckResult


class RouterSelfCheckDialog(QDialog):
    STATUS_TEXT = {
        "passed": "● Успешно",
        "warning": "● Предупреждение",
        "failed": "● Ошибка",
        "skipped": "○ Не требуется",
    }
    STATUS_COLORS = {
        "passed": "#34d399",
        "warning": "#fbbf24",
        "failed": "#fb7185",
        "skipped": "#94a3b8",
    }
    CHANGE_TEXT = {
        "new_problem": "Новая проблема",
        "resolved": "Устранено",
        "changed": "Статус изменился",
        "unchanged": "—",
    }
    CHANGE_COLORS = {
        "new_problem": "#fb7185",
        "resolved": "#34d399",
        "changed": "#fbbf24",
    }

    def __init__(
        self,
        result: RouterSelfCheckResult,
        parent: QWidget | None = None,
        *,
        history: tuple[RouterSelfCheckResult, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        results = list(history or ())
        if not results or results[-1].finished_at != result.finished_at:
            results.append(result)
        self.results = tuple(reversed(results))
        self.result = self.results[0]
        self.setWindowTitle("Самопроверка роутера")
        self.setMinimumSize(980, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Самопроверка завершена")
        title.setObjectName("dialogTitle")
        self.summary_label = QLabel()
        self.summary_label.setObjectName("selfCheckSummary")
        self.summary_label.setProperty(
            "checkStatus",
            self.result.overall_status,
        )
        self.summary_label.setWordWrap(True)

        privacy = QLabel(
            "Все проверки выполнялись только на чтение. Отчёт не содержит "
            "адресов роутера, имени устройства или учётных данных."
        )
        privacy.setObjectName("mutedLabel")
        privacy.setWordWrap(True)

        history_layout = QHBoxLayout()
        history_label = QLabel("Сохранённая проверка:")
        self.history_combo = QComboBox()
        for item in self.results:
            self.history_combo.addItem(self._history_title(item))
        self.history_combo.setEnabled(len(self.results) > 1)
        history_layout.addWidget(history_label)
        history_layout.addWidget(self.history_combo, 1)

        self.comparison_label = QLabel()
        self.comparison_label.setObjectName("mutedLabel")
        self.comparison_label.setWordWrap(True)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            (
                "Статус",
                "Изменение",
                "Раздел",
                "Проверка",
                "Результат",
                "Время",
            )
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        action_layout = QHBoxLayout()
        self.copy_button = QPushButton("Копировать отчёт")
        self.save_button = QPushButton("Сохранить JSON")
        action_layout.addWidget(self.copy_button)
        action_layout.addWidget(self.save_button)
        action_layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("Закрыть")
        buttons.rejected.connect(self.reject)

        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addWidget(privacy)
        layout.addLayout(history_layout)
        layout.addWidget(self.comparison_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(action_layout)
        layout.addWidget(buttons)

        self.copy_button.clicked.connect(self.copy_report)
        self.save_button.clicked.connect(self.save_json)
        self.history_combo.currentIndexChanged.connect(
            self.show_history_result
        )
        self._render_result(self.result)

    @staticmethod
    def _history_title(result: RouterSelfCheckResult) -> str:
        try:
            timestamp = datetime.fromisoformat(result.finished_at).strftime(
                "%d.%m.%Y %H:%M:%S"
            )
        except ValueError:
            timestamp = result.finished_at or "Время неизвестно"
        mode = "проблемные" if result.run_mode == "problems" else "полная"
        return f"{timestamp} · {result.overall_label} · {mode}"

    def show_history_result(self, index: int) -> None:
        if 0 <= int(index) < len(self.results):
            self._render_result(self.results[int(index)])

    def _render_result(self, result: RouterSelfCheckResult) -> None:
        self.result = result
        self.summary_label.setText(self._summary_text(result))
        self.summary_label.setProperty("checkStatus", result.overall_status)
        self.summary_label.style().unpolish(self.summary_label)
        self.summary_label.style().polish(self.summary_label)
        comparison_parts = []
        if result.new_problem_count:
            comparison_parts.append(
                f"Новых проблем: {result.new_problem_count}"
            )
        if result.resolved_count:
            comparison_parts.append(f"Устранено: {result.resolved_count}")
        if not comparison_parts:
            comparison_parts.append("Изменений относительно предыдущей проверки нет")
        if result.run_mode == "problems":
            comparison_parts.append("повторены только проблемные проверки")
        self.comparison_label.setText(" · ".join(comparison_parts) + ".")

        self.table.setRowCount(len(result.checks))
        for row, item in enumerate(result.checks):
            values = (
                self.STATUS_TEXT.get(item.status, item.status),
                self.CHANGE_TEXT.get(item.change, item.change),
                item.category,
                item.title,
                item.summary,
                (
                    f"{item.duration_ms / 1000:.2f} с"
                    if item.duration_ms is not None
                    else "—"
                ),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole, item.status)
                if column == 0:
                    cell.setForeground(
                        QColor(
                            self.STATUS_COLORS.get(item.status, "#cbd5e1")
                        )
                    )
                elif column == 1 and item.change != "unchanged":
                    cell.setForeground(
                        QColor(
                            self.CHANGE_COLORS.get(item.change, "#cbd5e1")
                        )
                    )
                self.table.setItem(row, column, cell)
        self.table.resizeRowsToContents()

    @staticmethod
    def _summary_text(result: RouterSelfCheckResult) -> str:
        return (
            f"{result.overall_label}. "
            f"Успешно: {result.passed_count}; "
            f"предупреждений: {result.warning_count}; "
            f"ошибок: {result.failed_count}; "
            f"не требуется: {result.skipped_count}. "
            f"Общее время: {result.duration_ms / 1000:.2f} с."
        )

    @classmethod
    def build_text_report(cls, result: RouterSelfCheckResult) -> str:
        lines = [
            "Keenetic Manager — самопроверка роутера",
            cls._summary_text(result),
            (
                "Режим: повтор проблемных проверок"
                if result.run_mode == "problems"
                else "Режим: полная проверка"
            ),
            (
                f"Новых проблем: {result.new_problem_count}; "
                f"устранено: {result.resolved_count}"
            ),
            f"Модель: {result.model or 'Не определена'}",
            f"KeeneticOS: {result.version or 'Не определена'}",
            f"Тип подключения: {result.connection_type or 'Не определён'}",
            "",
        ]
        for item in result.checks:
            duration = (
                f" · {item.duration_ms / 1000:.2f} с"
                if item.duration_ms is not None
                else ""
            )
            lines.append(
                f"{cls.STATUS_TEXT.get(item.status, item.status)} · "
                f"{item.category} · {item.title}{duration} · "
                f"{cls.CHANGE_TEXT.get(item.change, item.change)}"
            )
            lines.append(f"  {item.summary}")
        lines.extend(
            (
                "",
                "Отчёт обезличен и не содержит адресов или учётных данных.",
            )
        )
        return "\n".join(lines)

    def copy_report(self) -> None:
        QApplication.clipboard().setText(
            self.build_text_report(self.result)
        )

    def save_json(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить отчёт самопроверки",
            str(Path.home() / "keenetic_self_check.json"),
            "Отчёт JSON (*.json);;Все файлы (*)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            path.write_text(
                json.dumps(
                    self.result.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as error:
            QMessageBox.warning(
                self,
                "Отчёт не сохранён",
                str(error),
            )
            return
        QMessageBox.information(
            self,
            "Отчёт сохранён",
            str(path),
        )
