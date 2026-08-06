from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.system_metric_chip import SystemMetricChip  # noqa: E402


class SystemMetricChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_updates_value_progress_and_accessible_text(self) -> None:
        metric = SystemMetricChip("Память", "memory")

        metric.set_metric("35.5%", 36)

        self.assertEqual(metric.value_label.text(), "35.5%")
        self.assertEqual(metric.progress.value(), 36)
        self.assertEqual(metric.progress.format(), "35.5%")
        self.assertEqual(metric.toolTip(), "Память: 35.5%")
        self.assertEqual(metric.accessibleName(), "Память: 35.5%")

    def test_clamps_progress_without_changing_router_text(self) -> None:
        metric = SystemMetricChip("CPU", "cpu")

        metric.set_metric("105%", 105)

        self.assertEqual(metric.value_label.text(), "105%")
        self.assertEqual(metric.progress.value(), 100)

    def test_clear_restores_compact_empty_state(self) -> None:
        metric = SystemMetricChip("CPU", "cpu")
        metric.set_metric("18%", 18)

        metric.clear_metric()

        self.assertEqual(metric.value_label.text(), "—")
        self.assertEqual(metric.progress.value(), 0)
        self.assertFalse(metric.progress.isTextVisible())

    def test_cpu_and_memory_have_distinct_progress_accents(self) -> None:
        cpu = SystemMetricChip("CPU", "cpu")
        memory = SystemMetricChip("Память", "memory")

        self.assertEqual(cpu.progress.objectName(), "cpuMetricProgress")
        self.assertEqual(
            memory.progress.objectName(),
            "memoryMetricProgress",
        )


if __name__ == "__main__":
    unittest.main()
