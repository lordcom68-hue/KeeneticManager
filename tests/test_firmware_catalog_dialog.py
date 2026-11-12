from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.services.firmware_catalog_service import (  # noqa: E402
    FirmwareCatalogService,
)
from core.services.firmware_inspection_service import (  # noqa: E402
    FirmwareInspectionService,
)
from tests.test_firmware_inspection_service import build_uimage  # noqa: E402
from ui.dialogs.firmware_catalog_dialog import (  # noqa: E402
    FirmwareCatalogDialog,
)


class FirmwareCatalogDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def inspect(
        path: Path,
        *,
        ported: bool = False,
        version: bytes = b"5.01.C.2.0-2",
    ):
        path.write_bytes(build_uimage(ported=ported, version=version))
        return FirmwareInspectionService.inspect(path)

    def make_catalog(self, root: Path) -> tuple[FirmwareCatalogService, list]:
        root.mkdir(parents=True, exist_ok=True)
        service = FirmwareCatalogService(path=root / "catalog.json")
        records = []
        for filename, ported, version in (
            ("official.bin", False, b"5.01.C.2.0-2"),
            ("ported.bin", True, b"5.01.C.3.0-1"),
        ):
            inspection = self.inspect(
                root / filename,
                ported=ported,
                version=version,
            )
            record = service.upsert_inspection(inspection)
            self.assertIsNotNone(record)
            records.append(record)
        return service, records

    def test_search_and_type_filters_update_visible_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _records = self.make_catalog(Path(directory))
            dialog = FirmwareCatalogDialog(service)
            self.addCleanup(dialog.deleteLater)

            self.assertEqual(dialog.table.rowCount(), 2)
            dialog.search_input.setText("5.01.C.3")
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 0).text(), "ported.bin")

            dialog.search_input.clear()
            dialog.filter_combo.setCurrentIndex(
                dialog.filter_combo.findData("ported")
            )
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 4).text(), "Портированная")

            dialog.filter_combo.setCurrentIndex(
                dialog.filter_combo.findData("unverified")
            )
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 0).text(), "official.bin")

    def test_imported_missing_record_has_explicit_state_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _records = self.make_catalog(root / "source")
            export_path = root / "portable.json"
            self.assertTrue(source.export_to_file(export_path))
            target = FirmwareCatalogService(path=root / "target.json")
            result = target.import_from_file(export_path)
            self.assertTrue(result.success)
            dialog = FirmwareCatalogDialog(target)
            self.addCleanup(dialog.deleteLater)

            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(
                dialog.table.item(0, 6).text(),
                "Импортировано, файл не привязан",
            )
            dialog.filter_combo.setCurrentIndex(
                dialog.filter_combo.findData("file_problem")
            )
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertIn(
                "Недоступных или изменённых файлов: 2",
                dialog.status_label.text(),
            )

    def test_selection_and_busy_state_control_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _records = self.make_catalog(Path(directory))
            dialog = FirmwareCatalogDialog(service)
            self.addCleanup(dialog.deleteLater)

            dialog.table.selectRow(0)
            dialog.update_actions()
            self.assertTrue(dialog.open_button.isEnabled())
            self.assertTrue(dialog.reanalyze_button.isEnabled())
            self.assertFalse(dialog.compare_button.isEnabled())

            selection = dialog.table.selectionModel()
            selection.select(
                dialog.table.model().index(1, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            dialog.update_actions()
            self.assertEqual(len(dialog.selected_records()), 2)
            self.assertTrue(dialog.compare_button.isEnabled())

            dialog.worker = Mock()
            dialog.update_actions()
            self.assertFalse(dialog.open_button.isEnabled())
            self.assertFalse(dialog.add_files_button.isEnabled())
            self.assertFalse(dialog.close_button.isEnabled())
            dialog.worker = None

    def test_completed_batch_is_saved_and_progress_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FirmwareCatalogService(path=root / "catalog.json")
            inspection = self.inspect(root / "firmware.bin")
            dialog = FirmwareCatalogDialog(service)
            self.addCleanup(dialog.deleteLater)
            dialog.worker_mode = "batch"

            dialog._worker_progress(140, "Готово")
            self.assertEqual(dialog.progress_bar.value(), 100)
            dialog._worker_completed(
                {"results": [inspection], "errors": []}
            )

            self.assertEqual(len(service.list_records()), 1)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertIn("записей обновлено: 1", dialog.status_label.text())

    def test_relink_hash_mismatch_keeps_original_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FirmwareCatalogService(path=root / "catalog.json")
            original = self.inspect(root / "original.bin")
            different = self.inspect(
                root / "different.bin",
                version=b"5.01.C.3.0-1",
            )
            service.upsert_inspection(original)
            dialog = FirmwareCatalogDialog(service)
            self.addCleanup(dialog.deleteLater)
            dialog.worker_mode = "relink"
            dialog.worker_expected_sha256 = original.sha256

            with patch(
                "ui.dialogs.firmware_catalog_dialog.QMessageBox.warning"
            ) as warning:
                dialog._worker_completed(
                    {"results": [different], "errors": []}
                )

            self.assertEqual(warning.call_count, 1)
            record = service.list_records()[0]
            self.assertEqual(
                record.inspection.file_path,
                str((root / "original.bin").resolve()),
            )

    def test_cancel_and_close_are_guarded_while_worker_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = FirmwareCatalogDialog(
                FirmwareCatalogService(path=Path(directory) / "catalog.json")
            )
            self.addCleanup(dialog.deleteLater)
            worker = Mock()
            dialog.worker = worker

            dialog.cancel_worker()
            worker.stop.assert_called_once_with()
            self.assertFalse(dialog.cancel_worker_button.isEnabled())
            event = QCloseEvent()
            with patch(
                "ui.dialogs.firmware_catalog_dialog.QMessageBox.information"
            ) as information:
                dialog.closeEvent(event)

            self.assertFalse(event.isAccepted())
            information.assert_called_once()
            dialog.worker = None


if __name__ == "__main__":
    unittest.main()
