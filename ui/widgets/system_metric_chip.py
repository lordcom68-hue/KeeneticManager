from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SystemMetricChip(QFrame):
    """Компактная системная метрика с тонким индикатором."""

    def __init__(
        self,
        title: str,
        metric_kind: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.title = title
        self.setObjectName("systemMetricChip")
        self.setProperty("metricKind", metric_kind)
        self.setMinimumWidth(148)
        self.setMaximumWidth(184)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 10)
        root.setSpacing(7)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("systemMetricTitle")

        self.value_label = QLabel("—")
        self.value_label.setObjectName("systemMetricValue")
        self.value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        heading.addWidget(self.title_label)
        heading.addStretch()
        heading.addWidget(self.value_label)

        self.progress = QProgressBar()
        progress_name = (
            "cpuMetricProgress"
            if metric_kind == "cpu"
            else "memoryMetricProgress"
        )
        self.progress.setObjectName(progress_name)
        self.progress.setProperty("metricKind", metric_kind)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("—")
        self.progress.setTextVisible(False)
        accent = (
            "#34d399"
            if metric_kind == "cpu"
            else "#60a5fa"
        )
        self.progress.setStyleSheet(
            "QProgressBar::chunk {"
            f" background-color: {accent};"
            " border-radius: 2px;"
            "}"
        )

        root.addLayout(heading)
        root.addWidget(self.progress)

        self.set_metric("—", 0)

    def set_metric(
        self,
        text: str,
        value: int,
    ) -> None:
        display_text = str(text or "—")
        safe_value = max(0, min(100, int(value)))
        tooltip = f"{self.title}: {display_text}"

        self.value_label.setText(display_text)
        self.progress.setValue(safe_value)
        self.progress.setFormat(display_text)
        self.progress.setToolTip(tooltip)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def clear_metric(self) -> None:
        self.set_metric("—", 0)
