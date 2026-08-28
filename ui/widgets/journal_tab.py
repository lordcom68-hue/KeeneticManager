from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from time import time
from typing import Any
import zipfile

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.services.journal_event_analyzer import JournalEventAnalyzer


class JournalSummaryChip(QFrame):
    def __init__(
        self,
        caption: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("journalSummaryChip")
        self.setFixedHeight(42)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(7)

        self.value_label = QLabel("0")
        self.value_label.setObjectName("journalSummaryValue")
        caption_label = QLabel(caption)
        caption_label.setObjectName("journalSummaryCaption")
        layout.addWidget(self.value_label)
        layout.addWidget(caption_label)

    def set_value(self, value: int) -> None:
        self.value_label.setText(str(max(0, int(value))))


class JournalTab(QWidget):
    refresh_requested = Signal(int)
    message_requested = Signal(str)

    ASYNC_RENDER_THRESHOLD = 150
    RENDER_CHUNK_SIZE = 64
    FILTER_DEBOUNCE_MS = 180

    LEVEL_COLORS = {
        "critical": "#fb7185",
        "error": "#fb7185",
        "warning": "#fbbf24",
        "notice": "#a78bfa",
        "info": "#cbd5e1",
        "debug": "#64748b",
        "unknown": "#94a3b8",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._router_id: int | None = None
        self._entries: list[dict[str, Any]] = []
        self._visible_entries: list[dict[str, Any]] = []
        self._loaded_once = False
        self._loading = False
        self._router_log_supported: bool | None = None
        self._render_generation = 0
        self._finish_loading_after_render = False
        self._pending_render_status: str | None = None
        self._search_text_cache: dict[int, str] = {}
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(self.FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self.apply_filter)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Журнал")
        title.setObjectName("journalTitle")
        self.subtitle_label = QLabel(
            "Системные события роутера и операции Keenetic Manager."
        )
        self.subtitle_label.setObjectName("journalSubtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle_label)

        self.copy_button = QPushButton("Копировать")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_selected)
        self.export_button = QPushButton("Экспорт CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_csv)
        self.archive_button = QPushButton("Архив ZIP")
        self.archive_button.setEnabled(False)
        self.archive_button.setToolTip(
            "Сохранить отфильтрованные записи и пояснения в диагностический ZIP"
        )
        self.archive_button.clicked.connect(self.export_diagnostic_archive)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.request_refresh)

        header.addLayout(titles, 1)
        header.addWidget(self.copy_button)
        header.addWidget(self.export_button)
        header.addWidget(self.archive_button)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        summary = QHBoxLayout()
        summary.setSpacing(10)
        self.summary_chips = {
            "total": JournalSummaryChip("Всего"),
            "errors": JournalSummaryChip("Ошибки"),
            "warnings": JournalSummaryChip("Предупреждения"),
            "info": JournalSummaryChip("Информация"),
        }
        for chip in self.summary_chips.values():
            summary.addWidget(chip)
        root.addLayout(summary)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Поиск по времени, источнику или сообщению"
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._schedule_filter)

        self.level_combo = QComboBox()
        self.level_combo.addItem("Все уровни", "all")
        self.level_combo.addItem("Ошибки", "errors")
        self.level_combo.addItem("Предупреждения", "warning")
        self.level_combo.addItem("Информация", "info")
        self.level_combo.addItem("Отладка", "debug")
        self.level_combo.currentIndexChanged.connect(
            self._apply_filter_immediately
        )

        self.source_combo = QComboBox()
        self.source_combo.addItem("Все источники", "all")
        self.source_combo.currentIndexChanged.connect(
            self._apply_filter_immediately
        )

        self.category_combo = QComboBox()
        self.category_combo.addItem("Все категории", "all")
        for category, label in JournalEventAnalyzer.CATEGORY_LABELS.items():
            self.category_combo.addItem(label, category)
        self.category_combo.currentIndexChanged.connect(
            self._apply_filter_immediately
        )

        self.period_combo = QComboBox()
        for label, seconds in (
            ("Всё время", 0),
            ("Последние 15 минут", 15 * 60),
            ("Последний час", 60 * 60),
            ("Последние 24 часа", 24 * 60 * 60),
            ("Последние 7 дней", 7 * 24 * 60 * 60),
        ):
            self.period_combo.addItem(label, seconds)
        self.period_combo.currentIndexChanged.connect(
            self._apply_filter_immediately
        )

        self.limit_combo = QComboBox()
        for value in (100, 250, 500, 1000, 2000):
            self.limit_combo.addItem(f"Последние {value}", value)
        self.limit_combo.setCurrentIndex(2)
        self.limit_combo.setToolTip(
            "Количество последних строк, запрашиваемых у роутера"
        )

        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.level_combo)
        controls.addWidget(self.category_combo)
        controls.addWidget(self.source_combo)
        controls.addWidget(self.period_combo)
        controls.addWidget(self.limit_combo)
        root.addLayout(controls)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("journalTable")
        self.table.setHorizontalHeaderLabels(
            ["Время", "Уровень", "Категория", "Источник", "Событие и пояснение"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.itemSelectionChanged.connect(
            self._selection_changed
        )
        header_view = self.table.horizontalHeader()
        for column, width in enumerate((170, 130, 130, 150)):
            header_view.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Interactive,
            )
            self.table.setColumnWidth(column, width)
        header_view.setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.Stretch,
        )
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("Журнал ещё не загружен.")
        self.status_label.setObjectName("journalStatus")
        self.visible_label = QLabel("Показано: 0")
        self.visible_label.setObjectName("journalVisibleCount")
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.visible_label)
        root.addLayout(footer)

        self.update_summary()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._entries]

    @property
    def visible_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._visible_entries]

    def set_router(self, router_id: int | None) -> None:
        normalized = int(router_id) if router_id is not None else None
        if normalized == self._router_id:
            return
        self._router_id = normalized
        self.set_router_log_supported(None)
        self.clear(reset_controls=False)

    def needs_initial_load(self) -> bool:
        return bool(
            self._router_id is not None
            and not self._loaded_once
            and not self._loading
        )

    def mark_stale(self) -> None:
        """Force a refresh on the next opening after an application event."""
        if self._router_id is None:
            return
        self._loaded_once = False
        if not self._loading:
            self.status_label.setText(
                "Появились новые события. Обновите журнал."
            )

    def requested_limit(self) -> int:
        return int(self.limit_combo.currentData() or 500)

    def request_refresh(self) -> None:
        if self._router_id is None:
            self.message_requested.emit("Сначала выберите роутер.")
            return
        if self._loading:
            return
        self.set_loading(True)
        self.refresh_requested.emit(self.requested_limit())

    def set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        self.refresh_button.setEnabled(not self._loading)
        self.refresh_button.setText(
            "Обновление..." if self._loading else "Обновить"
        )
        if self._loading:
            self.status_label.setText("Чтение журнала роутера...")

    def set_entries(
        self,
        entries: list[dict[str, Any]] | None,
        *,
        message: str = "",
    ) -> None:
        raw_entries = [
            dict(entry)
            for entry in (entries or [])
            if isinstance(entry, dict)
        ]
        support_values = {
            entry.get("router_log_supported")
            for entry in raw_entries
            if isinstance(entry.get("router_log_supported"), bool)
        }
        if support_values:
            self.set_router_log_supported(False not in support_values)
        if self._entries_are_analyzed(raw_entries):
            self._entries = raw_entries
        else:
            self._entries = JournalEventAnalyzer.analyze(raw_entries)
        self._rebuild_search_text_cache()
        self._loaded_once = True
        self._finish_loading_after_render = True
        self._pending_render_status = str(message or "Журнал обновлён.")
        self._refresh_sources()
        self.update_summary()
        self.apply_filter()

    def set_error(self, message: str) -> None:
        self._cancel_table_render(reset_pending=True)
        self.set_loading(False)
        text = str(message or "Не удалось загрузить журнал.")
        if "не поддерживается этой версией KeeneticOS" in text:
            self.set_router_log_supported(False)
        self.status_label.setText(text)

    def set_router_log_supported(self, supported: bool | None) -> None:
        self._router_log_supported = supported
        self.limit_combo.setEnabled(supported is not False)
        if supported is False:
            self.limit_combo.setToolTip(
                "Системный журнал недоступен на этой версии KeeneticOS"
            )
            self.subtitle_label.setText(
                "Операции Keenetic Manager; системный журнал этой модели недоступен."
            )
            return
        self.limit_combo.setToolTip(
            "Количество последних строк, запрашиваемых у роутера"
        )
        self.subtitle_label.setText(
            "Системные события роутера и операции Keenetic Manager."
        )

    def clear(self, *, reset_controls: bool = False) -> None:
        self._cancel_table_render(reset_pending=True)
        self._entries = []
        self._visible_entries = []
        self._search_text_cache.clear()
        self._filter_timer.stop()
        self._loaded_once = False
        self.set_loading(False)
        if reset_controls:
            self.search_input.clear()
            self.level_combo.setCurrentIndex(0)
            self.category_combo.setCurrentIndex(0)
            self.source_combo.setCurrentIndex(0)
            self.period_combo.setCurrentIndex(0)
            self.limit_combo.setCurrentIndex(2)
        self._refresh_sources()
        self.update_summary()
        self.apply_filter()
        self.status_label.setText("Журнал ещё не загружен.")

    def update_summary(self) -> None:
        counts = {
            "total": len(self._entries),
            "errors": sum(
                entry.get("level") in {"critical", "error"}
                for entry in self._entries
            ),
            "warnings": sum(
                entry.get("level") == "warning"
                for entry in self._entries
            ),
            "info": sum(
                entry.get("level") in {"notice", "info"}
                for entry in self._entries
            ),
        }
        for key, chip in self.summary_chips.items():
            chip.set_value(counts[key])

    def apply_filter(self, *_args: object) -> None:
        self._filter_timer.stop()
        query = self.search_input.text().strip().casefold()
        level_filter = str(self.level_combo.currentData() or "all")
        category_filter = str(
            self.category_combo.currentData() or "all"
        )
        source_filter = str(self.source_combo.currentData() or "all")
        period_seconds = int(self.period_combo.currentData() or 0)
        cutoff = time() - period_seconds if period_seconds > 0 else None
        visible: list[dict[str, Any]] = []
        for entry in self._entries:
            level = str(entry.get("level") or "unknown")
            if level_filter == "errors" and level not in {
                "critical",
                "error",
            }:
                continue
            if level_filter not in {"all", "errors"}:
                if level_filter == "info":
                    if level not in {"notice", "info"}:
                        continue
                elif level != level_filter:
                    continue
            if (
                category_filter != "all"
                and str(entry.get("category") or "other")
                != category_filter
            ):
                continue
            if (
                source_filter != "all"
                and str(entry.get("source") or "") != source_filter
            ):
                continue
            if cutoff is not None:
                timestamp = entry.get("timestamp_epoch")
                if timestamp is None or float(timestamp) < cutoff:
                    continue
            haystack = self._search_text_cache.get(id(entry))
            if haystack is None:
                haystack = self._entry_search_text(entry)
                self._search_text_cache[id(entry)] = haystack
            if query and query not in haystack:
                continue
            visible.append(entry)
        self._visible_entries = visible
        self._render_table()

    def _schedule_filter(self, *_args: object) -> None:
        if len(self._entries) <= self.ASYNC_RENDER_THRESHOLD:
            self.apply_filter()
            return
        self._cancel_table_render(reset_pending=False)
        self._filter_timer.start()
        self.status_label.setText("Подготовка фильтра журнала...")

    def _apply_filter_immediately(self, *_args: object) -> None:
        self._filter_timer.stop()
        self.apply_filter()

    @staticmethod
    def _entry_search_text(entry: dict[str, Any]) -> str:
        return " ".join(
            str(entry.get(key) or "")
            for key in (
                "timestamp",
                "level_label",
                "importance_label",
                "category_label",
                "source",
                "message",
                "explanation",
                "related_action",
            )
        ).casefold()

    def _rebuild_search_text_cache(self) -> None:
        self._search_text_cache = {
            id(entry): self._entry_search_text(entry)
            for entry in self._entries
        }

    @staticmethod
    def _entries_are_analyzed(entries: list[dict[str, Any]]) -> bool:
        if not entries:
            return False
        required_keys = {
            "timestamp_epoch",
            "category",
            "category_label",
            "importance",
            "importance_label",
            "display_message",
        }
        return all(required_keys.issubset(entry) for entry in entries)

    def _refresh_sources(self) -> None:
        current = str(self.source_combo.currentData() or "all")
        sources = sorted(
            {
                str(entry.get("source") or "").strip()
                for entry in self._entries
                if str(entry.get("source") or "").strip()
            },
            key=str.casefold,
        )
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("Все источники", "all")
        for source in sources:
            self.source_combo.addItem(source, source)
        index = self.source_combo.findData(current)
        self.source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.source_combo.blockSignals(False)

    def _render_table(self) -> None:
        scrollbar = self.table.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        previous_value = scrollbar.value()
        self._render_generation += 1
        generation = self._render_generation
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._visible_entries))
        finally:
            self.table.setUpdatesEnabled(True)

        total = len(self._visible_entries)
        if total <= self.ASYNC_RENDER_THRESHOLD:
            self._render_table_chunk(
                generation,
                0,
                was_at_bottom,
                previous_value,
            )
            return

        self.status_label.setText(
            f"Отображение журнала: 0 из {total}..."
        )
        QTimer.singleShot(
            0,
            lambda: self._render_table_chunk(
                generation,
                0,
                was_at_bottom,
                previous_value,
            ),
        )

    def _render_table_chunk(
        self,
        generation: int,
        start_row: int,
        was_at_bottom: bool,
        previous_value: int,
    ) -> None:
        if generation != self._render_generation:
            return

        total = len(self._visible_entries)
        end_row = min(total, start_row + self.RENDER_CHUNK_SIZE)
        self.table.setUpdatesEnabled(False)
        try:
            for row in range(start_row, end_row):
                self._populate_table_row(row, self._visible_entries[row])
        finally:
            self.table.setUpdatesEnabled(True)

        if end_row < total:
            self.status_label.setText(
                f"Отображение журнала: {end_row} из {total}..."
            )
            QTimer.singleShot(
                0,
                lambda: self._render_table_chunk(
                    generation,
                    end_row,
                    was_at_bottom,
                    previous_value,
                ),
            )
            return

        self._finish_table_render(
            generation,
            was_at_bottom,
            previous_value,
        )

    def _populate_table_row(
        self,
        row: int,
        entry: dict[str, Any],
    ) -> None:
        values = (
            str(entry.get("timestamp") or "—"),
            str(entry.get("level_label") or "Не определено"),
            str(entry.get("category_label") or "Прочее"),
            str(entry.get("source") or "—"),
            str(
                entry.get("display_message")
                or entry.get("message")
                or ""
            ),
        )
        color = QColor(
            self.LEVEL_COLORS.get(
                str(entry.get("level") or "unknown"),
                self.LEVEL_COLORS["unknown"],
            )
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(
                self._entry_tooltip(entry)
                if column == 4
                else value
            )
            if column == 1:
                item.setForeground(QBrush(color))
            self.table.setItem(row, column, item)

    def _finish_table_render(
        self,
        generation: int,
        was_at_bottom: bool,
        previous_value: int,
    ) -> None:
        if generation != self._render_generation:
            return
        scrollbar = self.table.verticalScrollBar()
        if was_at_bottom and self._visible_entries:
            self.table.scrollToBottom()
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        self.visible_label.setText(
            f"Показано: {len(self._visible_entries)} из {len(self._entries)}"
        )
        self.export_button.setEnabled(bool(self._visible_entries))
        self.archive_button.setEnabled(bool(self._visible_entries))
        self._selection_changed()

        if self._finish_loading_after_render:
            final_status = self._pending_render_status or "Журнал обновлён."
            self._finish_loading_after_render = False
            self._pending_render_status = None
            self.set_loading(False)
            self.status_label.setText(final_status)

    def _cancel_table_render(self, *, reset_pending: bool) -> None:
        self._render_generation += 1
        if reset_pending:
            self._finish_loading_after_render = False
            self._pending_render_status = None

    @staticmethod
    def _entry_tooltip(entry: dict[str, Any]) -> str:
        lines = [str(entry.get("message") or "").strip()]
        explanation = str(entry.get("explanation") or "").strip()
        if explanation:
            lines.append(f"Пояснение: {explanation}")
        importance = str(entry.get("importance_label") or "").strip()
        if importance:
            lines.append(f"Оценка: {importance}")
        related_action = str(entry.get("related_action") or "").strip()
        if related_action:
            lines.append(f"Связанное действие: {related_action}")
        repeat_count = max(1, int(entry.get("repeat_count") or 1))
        if repeat_count > 1:
            lines.append(f"Всего повторений: {repeat_count}")
        return "\n".join(line for line in lines if line)

    def _selection_changed(self) -> None:
        selection = self.table.selectionModel()
        self.copy_button.setEnabled(
            bool(selection and selection.selectedRows())
        )

    def copy_selected(self) -> None:
        selection = self.table.selectionModel()
        rows = sorted(
            index.row()
            for index in (selection.selectedRows() if selection else [])
        )
        if not rows:
            self.message_requested.emit("Выберите записи журнала.")
            return
        lines: list[str] = []
        for row in rows:
            if row >= len(self._visible_entries):
                continue
            entry = self._visible_entries[row]
            lines.append(
                " | ".join(
                    str(entry.get(key) or "")
                    for key in (
                        "timestamp",
                        "level_label",
                        "category_label",
                        "source",
                        "display_message",
                    )
                )
            )
        QApplication.clipboard().setText("\n".join(lines))
        self.message_requested.emit(
            f"Скопировано записей: {len(lines)}."
        )

    def export_csv(self) -> None:
        if not self._visible_entries:
            self.message_requested.emit("Нет записей для экспорта.")
            return
        default_name = (
            f"keenetic_journal_router_{self._router_id or 'unknown'}.csv"
        )
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Экспорт системного журнала",
            default_name,
            "CSV (*.csv)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                self._write_csv(stream, self._visible_entries)
        except OSError as error:
            self.message_requested.emit(f"Не удалось сохранить журнал: {error}")
            return
        self.message_requested.emit(f"Журнал сохранён: {path}")

    def export_diagnostic_archive(self) -> None:
        if not self._visible_entries:
            self.message_requested.emit("Нет записей для экспорта.")
            return
        default_name = (
            f"keenetic_journal_router_{self._router_id or 'unknown'}.zip"
        )
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Диагностический архив журнала",
            default_name,
            "ZIP (*.zip)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        try:
            self.write_diagnostic_archive(path)
        except (OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
            self.message_requested.emit(
                f"Не удалось сохранить архив журнала: {error}"
            )
            return
        self.message_requested.emit(f"Архив журнала сохранён: {path}")

    def write_diagnostic_archive(self, path: str | Path) -> Path:
        destination = Path(path)
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        entries = [self._export_entry(entry) for entry in self._visible_entries]
        csv_stream = io.StringIO(newline="")
        self._write_csv(csv_stream, entries)
        summary = {
            "router_id": self._router_id,
            "visible_entries": len(entries),
            "total_entries": len(self._entries),
            "filters": {
                "search": self.search_input.text().strip(),
                "level": str(self.level_combo.currentData() or "all"),
                "category": str(
                    self.category_combo.currentData() or "all"
                ),
                "source": str(self.source_combo.currentData() or "all"),
                "period_seconds": int(
                    self.period_combo.currentData() or 0
                ),
            },
            "router_log_supported": self._router_log_supported,
        }
        with zipfile.ZipFile(
            destination,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "journal.csv",
                "\ufeff" + csv_stream.getvalue(),
            )
            archive.writestr(
                "journal.json",
                json.dumps(entries, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "summary.json",
                json.dumps(summary, ensure_ascii=False, indent=2),
            )
        return destination

    @staticmethod
    def _write_csv(
        stream: Any,
        entries: list[dict[str, Any]],
    ) -> None:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            [
                "Время",
                "Уровень",
                "Важность",
                "Категория",
                "Источник",
                "Сообщение",
                "Пояснение",
                "Связанное действие",
                "Повторений",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry.get("timestamp") or "",
                    entry.get("level_label") or "",
                    entry.get("importance_label") or "",
                    entry.get("category_label") or "",
                    entry.get("source") or "",
                    entry.get("message") or "",
                    entry.get("explanation") or "",
                    entry.get("related_action") or "",
                    entry.get("repeat_count") or 1,
                ]
            )

    @staticmethod
    def _export_entry(entry: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id",
            "timestamp",
            "level",
            "level_label",
            "importance",
            "importance_label",
            "category",
            "category_label",
            "source",
            "message",
            "explanation",
            "related_action",
            "repeat_count",
            "last_timestamp",
            "application_event",
            "operation_type",
            "operation_state",
            "transient",
        )
        return {
            key: entry.get(key)
            for key in keys
            if key in entry
        }
