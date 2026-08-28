from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.services.firmware_inspection_service import (
    FirmwareInspectionResult,
    FirmwareRouterCheckResult,
)
from core.services.runtime_paths import firmware_catalog_path


NowFunction = Callable[[], datetime]


@dataclass(frozen=True)
class FirmwareRouterCheckSummary:
    checked_at: str
    router_name: str
    router_model: str
    current_version: str
    verdict: str
    verdict_label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any] | None,
    ) -> FirmwareRouterCheckSummary | None:
        if not isinstance(value, dict):
            return None
        return cls(
            checked_at=str(value.get("checked_at") or ""),
            router_name=str(value.get("router_name") or ""),
            router_model=str(value.get("router_model") or ""),
            current_version=str(value.get("current_version") or ""),
            verdict=str(value.get("verdict") or ""),
            verdict_label=str(value.get("verdict_label") or ""),
        )


@dataclass(frozen=True)
class FirmwareCatalogRecord:
    sha256: str
    inspection: FirmwareInspectionResult
    first_analyzed_at: str
    last_analyzed_at: str
    file_mtime_ns: int
    last_router_check: FirmwareRouterCheckSummary | None = None
    record_source: str = "local_analysis"
    file_state: str = "available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "inspection": self.inspection.to_dict(),
            "first_analyzed_at": self.first_analyzed_at,
            "last_analyzed_at": self.last_analyzed_at,
            "file_mtime_ns": self.file_mtime_ns,
            "last_router_check": (
                self.last_router_check.to_dict()
                if self.last_router_check is not None
                else None
            ),
            "record_source": self.record_source,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any] | None,
    ) -> FirmwareCatalogRecord | None:
        if not isinstance(value, dict):
            return None
        inspection = FirmwareInspectionResult.from_dict(
            value.get("inspection")
            if isinstance(value.get("inspection"), dict)
            else None
        )
        if inspection is None or not inspection.sha256:
            return None
        try:
            return cls(
                sha256=inspection.sha256.upper(),
                inspection=inspection,
                first_analyzed_at=str(value.get("first_analyzed_at") or ""),
                last_analyzed_at=str(value.get("last_analyzed_at") or ""),
                file_mtime_ns=int(value.get("file_mtime_ns") or 0),
                last_router_check=FirmwareRouterCheckSummary.from_dict(
                    value.get("last_router_check")
                    if isinstance(value.get("last_router_check"), dict)
                    else None
                ),
                record_source=(
                    "imported"
                    if str(value.get("record_source") or "") == "imported"
                    else "local_analysis"
                ),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class FirmwareCatalogImportResult:
    imported: int
    updated: int
    skipped: int
    errors: tuple[str, ...]

    @property
    def success(self) -> bool:
        return not self.errors


class FirmwareCatalogService:
    """Persist bounded firmware metadata without copying firmware files."""

    SCHEMA_VERSION = 1
    MAX_RECORDS = 500
    EXPORT_FORMAT = "keenetic-manager-firmware-catalog"
    MAX_IMPORT_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        now: NowFunction | None = None,
    ) -> None:
        self.path = (
            Path(path)
            if path is not None
            else firmware_catalog_path()
        )
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = threading.RLock()

    def list_records(
        self,
        *,
        refresh_file_state: bool = True,
    ) -> list[FirmwareCatalogRecord]:
        with self._lock:
            records = self._load_unlocked()
        if refresh_file_state:
            records = [self._with_file_state(record) for record in records]
        records.sort(
            key=lambda record: record.last_analyzed_at,
            reverse=True,
        )
        return records

    def upsert_inspection(
        self,
        inspection: FirmwareInspectionResult,
    ) -> FirmwareCatalogRecord | None:
        if not inspection.sha256:
            return None
        with self._lock:
            records = self._load_unlocked()
            updated = self._merge_inspection(
                records,
                inspection,
                timestamp=self._now().isoformat(),
            )
            if not self._save_unlocked(records):
                return None
        return self._with_file_state(updated)

    def record_router_check(
        self,
        result: FirmwareRouterCheckResult,
    ) -> FirmwareCatalogRecord | None:
        timestamp = self._now().isoformat()
        summary = FirmwareRouterCheckSummary(
            checked_at=timestamp,
            router_name=result.router_name,
            router_model=result.router_model,
            current_version=result.current_version,
            verdict=result.verdict,
            verdict_label=result.verdict_label,
        )
        with self._lock:
            records = self._load_unlocked()
            updated = self._merge_inspection(
                records,
                result.firmware,
                timestamp=timestamp,
            )
            updated = replace(updated, last_router_check=summary)
            for index, record in enumerate(records):
                if record.sha256 == updated.sha256:
                    records[index] = updated
                    break
            if not self._save_unlocked(records):
                return None
        return self._with_file_state(updated)

    def relink_inspection(
        self,
        expected_sha256: str,
        inspection: FirmwareInspectionResult,
    ) -> FirmwareCatalogRecord | None:
        """Update a stored path only after a full SHA-256 match."""
        expected = str(expected_sha256 or "").strip().upper()
        actual = str(inspection.sha256 or "").strip().upper()
        if not expected or expected != actual:
            return None
        with self._lock:
            records = self._load_unlocked()
            if not any(record.sha256.upper() == expected for record in records):
                return None
            updated = self._merge_inspection(
                records,
                inspection,
                timestamp=self._now().isoformat(),
            )
            if not self._save_unlocked(records):
                return None
        return self._with_file_state(updated)

    def remove(self, sha256: str) -> bool:
        normalized = str(sha256 or "").strip().upper()
        if not normalized:
            return False
        with self._lock:
            records = self._load_unlocked()
            filtered = [
                record
                for record in records
                if record.sha256.upper() != normalized
            ]
            if len(filtered) == len(records):
                return False
            return self._save_unlocked(filtered)

    def clear(self) -> bool:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                temporary.unlink(missing_ok=True)
                return True
            except OSError:
                return False

    def export_payload(
        self,
        records: list[FirmwareCatalogRecord] | None = None,
    ) -> dict[str, Any]:
        selected = (
            list(records)
            if records is not None
            else self.list_records(refresh_file_state=False)
        )
        portable_records = [
            self._portable_record(record).to_dict()
            for record in selected[: self.MAX_RECORDS]
        ]
        return {
            "format": self.EXPORT_FORMAT,
            "schema_version": self.SCHEMA_VERSION,
            "exported_at": self._now().isoformat(),
            "record_count": len(portable_records),
            "records": portable_records,
        }

    def export_to_file(
        self,
        destination: str | Path,
        *,
        records: list[FirmwareCatalogRecord] | None = None,
    ) -> bool:
        target = Path(destination)
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            encoded = json.dumps(
                self.export_payload(records),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded + "\n", encoding="utf-8")
            temporary.replace(target)
            return True
        except (OSError, TypeError, ValueError):
            temporary.unlink(missing_ok=True)
            return False

    def import_from_file(
        self,
        source: str | Path,
    ) -> FirmwareCatalogImportResult:
        path = Path(source)
        try:
            if path.stat().st_size > self.MAX_IMPORT_BYTES:
                return FirmwareCatalogImportResult(
                    0,
                    0,
                    0,
                    ("Размер файла импорта превышает 8 МБ.",),
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                (f"Не удалось прочитать файл: {error}",),
            )
        except json.JSONDecodeError:
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                ("Файл не является корректным JSON.",),
            )
        if not isinstance(payload, dict):
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                ("Корневой элемент JSON должен быть объектом.",),
            )
        if str(payload.get("format") or "") != self.EXPORT_FORMAT:
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                ("Формат файла каталога не поддерживается.",),
            )
        try:
            schema_version = int(payload.get("schema_version") or 0)
        except (TypeError, ValueError):
            schema_version = 0
        if schema_version != self.SCHEMA_VERSION:
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                (f"Версия схемы {schema_version} не поддерживается.",),
            )
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            return FirmwareCatalogImportResult(
                0,
                0,
                0,
                ("В файле отсутствует список records.",),
            )

        imported_records: list[FirmwareCatalogRecord] = []
        invalid_count = 0
        for raw_record in raw_records[: self.MAX_RECORDS]:
            record = FirmwareCatalogRecord.from_dict(
                raw_record if isinstance(raw_record, dict) else None
            )
            if record is None:
                invalid_count += 1
                continue
            imported_records.append(
                replace(
                    self._portable_record(record),
                    record_source="imported",
                )
            )
        overflow = max(0, len(raw_records) - self.MAX_RECORDS)

        with self._lock:
            existing = self._load_unlocked()
            by_hash = {record.sha256.upper(): record for record in existing}
            imported = 0
            updated = 0
            skipped = invalid_count + overflow
            for candidate in imported_records:
                identity = candidate.sha256.upper()
                current = by_hash.get(identity)
                if current is None:
                    existing.append(candidate)
                    by_hash[identity] = candidate
                    imported += 1
                    continue
                if (
                    current.last_router_check is None
                    and candidate.last_router_check is not None
                ):
                    merged = replace(
                        current,
                        last_router_check=candidate.last_router_check,
                    )
                    existing[existing.index(current)] = merged
                    by_hash[identity] = merged
                    updated += 1
                else:
                    skipped += 1
            existing.sort(
                key=lambda record: record.last_analyzed_at,
                reverse=True,
            )
            if len(existing) > self.MAX_RECORDS:
                skipped += len(existing) - self.MAX_RECORDS
                del existing[self.MAX_RECORDS :]
            if not self._save_unlocked(existing):
                return FirmwareCatalogImportResult(
                    0,
                    0,
                    skipped,
                    ("Не удалось сохранить объединённый каталог.",),
                )
        return FirmwareCatalogImportResult(
            imported,
            updated,
            skipped,
            (),
        )

    def diagnostic_summary(self) -> dict[str, Any]:
        records = self.list_records(refresh_file_state=False)
        summaries: list[dict[str, Any]] = []
        for record in records:
            inspection = record.inspection
            if inspection.integrity_checked:
                integrity = (
                    "valid" if inspection.integrity_valid else "invalid"
                )
            else:
                integrity = "unchecked"
            summaries.append(
                {
                    "file_name": self._safe_file_name(inspection.file_name),
                    "sha256": inspection.sha256,
                    "internal_model": inspection.internal_model,
                    "compatible_models": list(inspection.compatible_models),
                    "firmware_version": inspection.firmware_version,
                    "architecture": inspection.architecture,
                    "container": inspection.container,
                    "origin": inspection.origin,
                    "integrity": integrity,
                    "wifi_management_mode": (
                        inspection.capability_profile.wifi_management_mode
                    ),
                    "last_router_verdict": (
                        record.last_router_check.verdict
                        if record.last_router_check is not None
                        else ""
                    ),
                    "last_router_verdict_label": (
                        record.last_router_check.verdict_label
                        if record.last_router_check is not None
                        else ""
                    ),
                    "record_source": record.record_source,
                }
            )
        return {
            "schema_version": self.SCHEMA_VERSION,
            "record_count": len(summaries),
            "privacy": {
                "contains_firmware_files": False,
                "contains_local_paths": False,
                "contains_router_names": False,
            },
            "records": summaries,
        }

    def _merge_inspection(
        self,
        records: list[FirmwareCatalogRecord],
        inspection: FirmwareInspectionResult,
        *,
        timestamp: str,
    ) -> FirmwareCatalogRecord:
        existing = next(
            (
                record
                for record in records
                if record.sha256.upper() == inspection.sha256.upper()
            ),
            None,
        )
        mtime_ns = self._file_mtime_ns(inspection.file_path)
        if existing is None:
            updated = FirmwareCatalogRecord(
                sha256=inspection.sha256.upper(),
                inspection=inspection,
                first_analyzed_at=timestamp,
                last_analyzed_at=timestamp,
                file_mtime_ns=mtime_ns,
                record_source="local_analysis",
            )
            records.append(updated)
        else:
            updated = replace(
                existing,
                inspection=inspection,
                last_analyzed_at=timestamp,
                file_mtime_ns=mtime_ns,
                record_source="local_analysis",
                file_state="available",
            )
            records[records.index(existing)] = updated

        records.sort(
            key=lambda record: record.last_analyzed_at,
            reverse=True,
        )
        del records[self.MAX_RECORDS :]
        return updated

    @classmethod
    def _portable_record(
        cls,
        record: FirmwareCatalogRecord,
    ) -> FirmwareCatalogRecord:
        inspection = replace(
            record.inspection,
            file_name=cls._safe_file_name(record.inspection.file_name),
            file_path="",
        )
        router_check = record.last_router_check
        if router_check is not None:
            router_check = replace(
                router_check,
                router_name="",
                router_model="",
            )
        return replace(
            record,
            inspection=inspection,
            file_mtime_ns=0,
            last_router_check=router_check,
            file_state="missing",
        )

    @staticmethod
    def _safe_file_name(value: str) -> str:
        normalized = str(value or "").replace("\\", "/")
        return normalized.rsplit("/", 1)[-1]

    def _load_unlocked(self) -> list[FirmwareCatalogRecord]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            return []
        result: list[FirmwareCatalogRecord] = []
        seen: set[str] = set()
        for value in raw_records:
            record = FirmwareCatalogRecord.from_dict(
                value if isinstance(value, dict) else None
            )
            if record is None:
                continue
            identity = record.sha256.upper()
            if identity in seen:
                continue
            seen.add(identity)
            result.append(record)
            if len(result) >= self.MAX_RECORDS:
                break
        return result

    def _save_unlocked(self, records: list[FirmwareCatalogRecord]) -> bool:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "records": [record.to_dict() for record in records],
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded + "\n", encoding="utf-8")
            temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            temporary.unlink(missing_ok=True)
            return False

    @staticmethod
    def _file_mtime_ns(path: str) -> int:
        try:
            return Path(path).stat().st_mtime_ns
        except OSError:
            return 0

    @classmethod
    def _with_file_state(
        cls,
        record: FirmwareCatalogRecord,
    ) -> FirmwareCatalogRecord:
        path_text = str(record.inspection.file_path or "").strip()
        if not path_text:
            return replace(record, file_state="missing")
        path = Path(path_text)
        try:
            stat = path.stat()
        except OSError:
            return replace(record, file_state="missing")
        if not path.is_file():
            return replace(record, file_state="missing")
        changed = bool(
            stat.st_size != record.inspection.size_bytes
            or (
                record.file_mtime_ns > 0
                and stat.st_mtime_ns != record.file_mtime_ns
            )
        )
        return replace(
            record,
            file_state="changed" if changed else "available",
        )
