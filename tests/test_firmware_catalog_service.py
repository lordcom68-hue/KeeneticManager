from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.services.firmware_catalog_service import FirmwareCatalogService
from core.services.firmware_inspection_service import FirmwareInspectionService
from tests.test_firmware_inspection_service import build_uimage
from ui.dialogs.firmware_catalog_dialog import FirmwareCatalogBatchWorker


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(minutes=1)
        return current


class FirmwareCatalogServiceTests(unittest.TestCase):
    @staticmethod
    def inspect(path: Path, *, version: bytes = b"5.01.C.2.0-2"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_uimage(version=version, ported=False))
        return FirmwareInspectionService.inspect(path)

    def test_catalog_persists_full_report_without_copying_firmware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_path = root / "firmware.bin"
            catalog_path = root / "cache" / "catalog.json"
            inspection = self.inspect(firmware_path)
            service = FirmwareCatalogService(path=catalog_path, now=Clock())

            saved = service.upsert_inspection(inspection)
            loaded = FirmwareCatalogService(path=catalog_path).list_records()

            self.assertIsNotNone(saved)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].inspection.sha256, inspection.sha256)
            self.assertEqual(loaded[0].inspection.internal_model, "KN-2311")
            self.assertEqual(loaded[0].file_state, "available")
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {"firmware.bin", "cache"},
            )

    def test_same_hash_updates_path_without_duplicate_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "first.bin"
            second_path = root / "second.bin"
            data = build_uimage(ported=False)
            first_path.write_bytes(data)
            second_path.write_bytes(data)
            first = FirmwareInspectionService.inspect(first_path)
            second = FirmwareInspectionService.inspect(second_path)
            service = FirmwareCatalogService(
                path=root / "catalog.json",
                now=Clock(),
            )

            service.upsert_inspection(first)
            service.upsert_inspection(second)
            records = service.list_records()

            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0].inspection.file_path,
                str(second_path.resolve()),
            )
            self.assertNotEqual(
                records[0].first_analyzed_at,
                records[0].last_analyzed_at,
            )

    def test_file_state_detects_changed_and_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_path = root / "firmware.bin"
            inspection = self.inspect(firmware_path)
            service = FirmwareCatalogService(path=root / "catalog.json")
            service.upsert_inspection(inspection)

            firmware_path.write_bytes(firmware_path.read_bytes() + b"changed")
            changed = service.list_records()[0]
            firmware_path.unlink()
            missing = service.list_records()[0]

            self.assertEqual(changed.file_state, "changed")
            self.assertEqual(missing.file_state, "missing")

    def test_router_verdict_is_saved_with_catalog_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection = self.inspect(
                root / "update.bin",
                version=b"5.01.C.3.0-1",
            )
            check = FirmwareInspectionService.check_for_router(
                inspection,
                router_name="test2",
                router_model="N6 AX1800 (KN-2311)",
                current_version="5.01.C.2.0-2",
                router_wifi_mode="mws",
            )
            service = FirmwareCatalogService(
                path=root / "catalog.json",
                now=Clock(),
            )

            service.record_router_check(check)
            loaded = service.list_records()[0]

            self.assertIsNotNone(loaded.last_router_check)
            self.assertEqual(
                loaded.last_router_check.verdict,
                "matching_update",
            )
            self.assertEqual(loaded.last_router_check.router_name, "test2")

    def test_relink_requires_exact_hash_and_preserves_wrong_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_path = root / "original.bin"
            correct_path = root / "moved.bin"
            wrong_path = root / "wrong.bin"
            original = self.inspect(original_path)
            correct_path.write_bytes(original_path.read_bytes())
            wrong = self.inspect(
                wrong_path,
                version=b"5.01.C.3.0-1",
            )
            correct = FirmwareInspectionService.inspect(correct_path)
            service = FirmwareCatalogService(
                path=root / "catalog.json",
                now=Clock(),
            )
            service.upsert_inspection(original)

            rejected = service.relink_inspection(original.sha256, wrong)
            unchanged = service.list_records()[0]
            accepted = service.relink_inspection(original.sha256, correct)
            updated = service.list_records()[0]

            self.assertIsNone(rejected)
            self.assertEqual(
                unchanged.inspection.file_path,
                str(original_path.resolve()),
            )
            self.assertIsNotNone(accepted)
            self.assertEqual(
                updated.inspection.file_path,
                str(correct_path.resolve()),
            )

    def test_remove_and_clear_never_delete_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_path = root / "firmware.bin"
            inspection = self.inspect(firmware_path)
            catalog_path = root / "catalog.json"
            service = FirmwareCatalogService(path=catalog_path)
            service.upsert_inspection(inspection)

            removed = service.remove(inspection.sha256)
            service.upsert_inspection(inspection)
            cleared = service.clear()

            self.assertTrue(removed)
            self.assertTrue(cleared)
            self.assertTrue(firmware_path.is_file())
            self.assertFalse(catalog_path.exists())

    def test_malformed_or_partial_records_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text("not-json", encoding="utf-8")
            service = FirmwareCatalogService(path=path)

            self.assertEqual(service.list_records(), [])

            path.write_text(
                json.dumps({"schema_version": 1, "records": [{"bad": True}]}),
                encoding="utf-8",
            )
            self.assertEqual(service.list_records(), [])

    def test_batch_worker_keeps_valid_results_when_one_file_is_invalid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid_path = root / "valid.bin"
            invalid_path = root / "invalid.bin"
            valid_path.write_bytes(build_uimage(ported=False))
            invalid_path.write_bytes(b"not a firmware image")
            payloads: list[dict[str, object]] = []
            progress: list[int] = []
            worker = FirmwareCatalogBatchWorker(
                [valid_path, invalid_path]
            )
            worker.completed.connect(payloads.append)
            worker.progress_changed.connect(
                lambda value, _message: progress.append(value)
            )

            worker.run()

            self.assertEqual(len(payloads), 1)
            self.assertEqual(len(payloads[0]["results"]), 1)
            self.assertEqual(len(payloads[0]["errors"]), 1)
            self.assertEqual(progress[-1], 100)

    def test_portable_export_omits_paths_and_router_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_path = root / "private" / "firmware.bin"
            firmware_path.parent.mkdir()
            inspection = self.inspect(firmware_path)
            check = FirmwareInspectionService.check_for_router(
                inspection,
                router_name="Secret Router Name",
                router_model="N6 AX1800 (KN-2311)",
                current_version="5.01.C.1.0-0",
            )
            service = FirmwareCatalogService(path=root / "catalog.json")
            service.record_router_check(check)

            payload = service.export_payload()
            encoded = json.dumps(payload, ensure_ascii=False)

            self.assertEqual(
                payload["format"],
                FirmwareCatalogService.EXPORT_FORMAT,
            )
            self.assertNotIn(str(firmware_path), encoded)
            self.assertNotIn("Secret Router Name", encoded)
            self.assertNotIn("N6 AX1800", encoded)
            self.assertEqual(payload["records"][0]["inspection"]["file_path"], "")

    def test_exported_catalog_imports_without_paths_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection = self.inspect(root / "firmware.bin")
            source = FirmwareCatalogService(path=root / "source.json")
            source.upsert_inspection(inspection)
            export_path = root / "portable.json"
            source.export_to_file(export_path)
            target = FirmwareCatalogService(path=root / "target.json")

            first = target.import_from_file(export_path)
            second = target.import_from_file(export_path)
            records = target.list_records()

            self.assertEqual(first.imported, 1)
            self.assertTrue(first.success)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].inspection.file_path, "")
            self.assertEqual(records[0].file_state, "missing")
            self.assertEqual(records[0].record_source, "imported")

    def test_import_rejects_unknown_format_without_changing_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wrong.json"
            source.write_text(
                json.dumps({"format": "something-else", "records": []}),
                encoding="utf-8",
            )
            service = FirmwareCatalogService(path=root / "catalog.json")

            result = service.import_from_file(source)

            self.assertFalse(result.success)
            self.assertEqual(service.list_records(), [])

    def test_diagnostic_summary_contains_no_paths_or_router_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection = self.inspect(root / "secret" / "firmware.bin")
            check = FirmwareInspectionService.check_for_router(
                inspection,
                router_name="Private test2",
                router_model="KN-2311",
                current_version="5.01.C.1.0-0",
            )
            service = FirmwareCatalogService(path=root / "catalog.json")
            service.record_router_check(check)

            summary = service.diagnostic_summary()
            encoded = json.dumps(summary, ensure_ascii=False)

            self.assertEqual(summary["record_count"], 1)
            self.assertFalse(summary["privacy"]["contains_local_paths"])
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("Private test2", encoded)
            self.assertNotIn('"router_name":', encoded)


if __name__ == "__main__":
    unittest.main()
