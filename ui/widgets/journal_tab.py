from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
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

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Журнал")
        title.setObjectName("journalTitle")
        subtitle = QLabel(
            "Системные события роутера и операции Keenetic Manager."
        )
        subtitle.setObjectName("journalSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)

        self.copy_button = QPushButton("Копировать")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_selected)
        self.export_button = QPushButton("Экспорт CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_csv)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.request_refresh)

        header.addLayout(titles, 1)
        header.addWidget(self.copy_button)
        header.addWidget(self.export_button)
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
        self.search_input.textChanged.connect(self.apply_filter)

        self.level_combo = QComboBox()
        self.level_combo.addItem("Все уровни", "all")
        self.level_combo.addItem("Ошибки", "errors")
        self.level_combo.addItem("Предупреждения", "warning")
        self.level_combo.addItem("Информация", "info")
        self.level_combo.addItem("Отладка", "debug")
        self.level_combo.currentIndexChanged.connect(self.apply_filter)

        self.source_combo = QComboBox()
        self.source_combo.addItem("Все источники", "all")
        self.source_combo.currentIndexChanged.connect(self.apply_filter)

        self.limit_combo = QComboBox()
        for value in (100, 250, 500, 1000, 2000):
            self.limit_combo.addItem(f"Последние {value}", value)
        self.limit_combo.setCurrentIndex(2)
        self.limit_combo.setToolTip(
            "Количество последних строк, запрашиваемых у роутера"
        )

        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.level_combo)
        controls.addWidget(self.source_combo)
        controls.addWidget(self.limit_combo)
        root.addLayout(controls)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("journalTable")
        self.table.setHorizontalHeaderLabels(
            ["Время", "Уровень", "Источник", "Сообщение"]
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
        header_view.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        header_view.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        header_view.setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        header_view.setSectionResizeMode(
            3,
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
        self._entries = [
            dict(entry)
            for entry in (entries or [])
            if isinstance(entry, dict)
        ]
        self._loaded_once = True
        self.set_loading(False)
        self._refresh_sources()
        self.update_summary()
        self.apply_filter()
        self.status_label.setText(
            str(message or "Журнал обновлён.")
        )

    def set_error(self, message: str) -> None:
        self.set_loading(False)
        self.status_label.setText(str(message or "Не удалось загрузить журнал."))

    def clear(self, *, reset_controls: bool = False) -> None:
        self._entries = []
        self._visible_entries = []
        self._loaded_once = False
        self.set_loading(False)
        if reset_controls:
            self.search_input.clear()
            self.level_combo.setCurrentIndex(0)
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
        query = self.search_input.text().strip().casefold()
        level_filter = str(self.level_combo.currentData() or "all")
        source_filter = str(self.source_combo.currentData() or "all")
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
                source_filter != "all"
                and str(entry.get("source") or "") != source_filter
            ):
                continue
            haystack = " ".join(
                str(entry.get(key) or "")
                for key in (
                    "timestamp",
                    "level_label",
                    "source",
                    "message",
                )
            ).casefold()
            if query and query not in haystack:
                continue
            visible.append(entry)
        self._visible_entries = visible
        self._render_table()

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
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(self._visible_entries))
            for row, entry in enumerate(self._visible_entries):
                values = (
                    str(entry.get("timestamp") or "—"),
                    str(entry.get("level_label") or "Не определено"),
                    str(entry.get("source") or "—"),
                    str(entry.get("message") or ""),
                )
                color = QColor(
                    self.LEVEL_COLORS.get(
                        str(entry.get("level") or "unknown"),
                        self.LEVEL_COLORS["unknown"],
                    )
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 1:
                        item.setForeground(QBrush(color))
                    self.table.setItem(row, column, item)
        finally:
            self.table.setUpdatesEnabled(True)

        if was_at_bottom and self._visible_entries:
            self.table.scrollToBottom()
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        self.visible_label.setText(
            f"Показано: {len(self._visible_entries)} из {len(self._entries)}"
        )
        self.export_button.setEnabled(bool(self._visible_entries))
        self._selection_changed()

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
                        "source",
                        "message",
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
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["Время", "Уровень", "Источник", "Сообщение"])
                for entry in self._visible_entries:
                    writer.writerow(
                        [
                            entry.get("timestamp") or "",
                            entry.get("level_label") or "",
                            entry.get("source") or "",
                            entry.get("message") or "",
                        ]
                    )
        except OSError as error:
            self.message_requested.emit(f"Не удалось сохранить журнал: {error}")
            return
        self.message_requested.emit(f"Журнал сохранён: {path}")
