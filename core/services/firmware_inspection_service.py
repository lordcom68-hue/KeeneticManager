from __future__ import annotations

import hashlib
import lzma
import re
import struct
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


class FirmwareInspectionError(Exception):
    """The selected file cannot be inspected safely."""


class FirmwareInspectionCancelled(FirmwareInspectionError):
    """Inspection was interrupted by the user or application shutdown."""


@dataclass(frozen=True)
class SquashFsInfo:
    offset: int
    version: str
    compression: str
    block_size: int
    inode_count: int
    bytes_used: int


@dataclass(frozen=True)
class FirmwareCapabilityEvidence:
    key: str
    label: str
    state: str
    detail: str


@dataclass(frozen=True)
class FirmwareCapabilityProfile:
    wifi_management_mode: str
    confidence: str
    discovered_rci_endpoints: tuple[str, ...]
    features: tuple[FirmwareCapabilityEvidence, ...]


@dataclass(frozen=True)
class FirmwareInspectionResult:
    file_name: str
    file_path: str
    size_bytes: int
    sha256: str
    container: str
    internal_model: str
    compatible_models: tuple[str, ...]
    architecture: str
    firmware_version: str
    discovered_versions: tuple[str, ...]
    origin: str
    porting_marker_count: int
    rootfs: SquashFsInfo | None
    integrity_checked: bool
    integrity_valid: bool | None
    capability_profile: FirmwareCapabilityProfile
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any] | None,
    ) -> FirmwareInspectionResult | None:
        if not isinstance(value, dict):
            return None
        try:
            raw_rootfs = value.get("rootfs")
            rootfs = (
                SquashFsInfo(
                    offset=int(raw_rootfs.get("offset") or 0),
                    version=str(raw_rootfs.get("version") or ""),
                    compression=str(raw_rootfs.get("compression") or ""),
                    block_size=int(raw_rootfs.get("block_size") or 0),
                    inode_count=int(raw_rootfs.get("inode_count") or 0),
                    bytes_used=int(raw_rootfs.get("bytes_used") or 0),
                )
                if isinstance(raw_rootfs, dict)
                else None
            )
            raw_profile = value.get("capability_profile")
            if not isinstance(raw_profile, dict):
                return None
            raw_features = raw_profile.get("features")
            if not isinstance(raw_features, (list, tuple)):
                return None
            profile = FirmwareCapabilityProfile(
                wifi_management_mode=str(
                    raw_profile.get("wifi_management_mode") or "monitoring"
                ),
                confidence=str(raw_profile.get("confidence") or "low"),
                discovered_rci_endpoints=tuple(
                    str(item)
                    for item in raw_profile.get("discovered_rci_endpoints") or ()
                ),
                features=tuple(
                    FirmwareCapabilityEvidence(
                        key=str(item.get("key") or ""),
                        label=str(item.get("label") or ""),
                        state=str(item.get("state") or "unknown"),
                        detail=str(item.get("detail") or ""),
                    )
                    for item in raw_features
                    if isinstance(item, dict)
                ),
            )
            raw_integrity_valid = value.get("integrity_valid")
            integrity_valid = (
                bool(raw_integrity_valid)
                if raw_integrity_valid is not None
                else None
            )
            return cls(
                file_name=str(value.get("file_name") or ""),
                file_path=str(value.get("file_path") or ""),
                size_bytes=int(value.get("size_bytes") or 0),
                sha256=str(value.get("sha256") or ""),
                container=str(value.get("container") or ""),
                internal_model=str(value.get("internal_model") or ""),
                compatible_models=tuple(
                    str(item) for item in value.get("compatible_models") or ()
                ),
                architecture=str(value.get("architecture") or ""),
                firmware_version=str(value.get("firmware_version") or ""),
                discovered_versions=tuple(
                    str(item) for item in value.get("discovered_versions") or ()
                ),
                origin=str(value.get("origin") or "unverified"),
                porting_marker_count=int(
                    value.get("porting_marker_count") or 0
                ),
                rootfs=rootfs,
                integrity_checked=bool(value.get("integrity_checked")),
                integrity_valid=integrity_valid,
                capability_profile=profile,
                warnings=tuple(
                    str(item) for item in value.get("warnings") or ()
                ),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class FirmwareComparisonField:
    key: str
    label: str
    first_value: str
    second_value: str
    changed: bool


@dataclass(frozen=True)
class FirmwareComparisonResult:
    first: FirmwareInspectionResult
    second: FirmwareInspectionResult
    verdict: str
    verdict_label: str
    summary: str
    fields: tuple[FirmwareComparisonField, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FirmwareRouterCheckItem:
    key: str
    label: str
    status: str
    detail: str


@dataclass(frozen=True)
class FirmwareRouterCheckResult:
    firmware: FirmwareInspectionResult
    router_name: str
    router_model: str
    router_models: tuple[str, ...]
    current_version: str
    router_wifi_mode: str
    verdict: str
    verdict_label: str
    summary: str
    checks: tuple[FirmwareRouterCheckItem, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FirmwareInspectionService:
    """Read-only parser for Keenetic uImage and FIT update images."""

    UIMAGE_MAGIC = b"\x27\x05\x19\x56"
    FIT_MAGIC = b"\xd0\x0d\xfe\xed"
    SQUASHFS_MAGIC = b"hsqs"
    XZ_MAGIC = b"\xfd7zXZ\x00"
    PORTING_MARKER = b"t.me/keeneticported"

    RCI_ENDPOINT_SIGNATURES = (
        "show/system",
        "show/version",
        "show/interface",
        "show/interface/stat",
        "show/sc/mws/wlan",
        "show/mws/wlan",
        "show/ip/hotspot",
        "show/log",
        "show/ping-check",
    )

    MAX_FILE_SIZE = 256 * 1024 * 1024
    READ_CHUNK_SIZE = 1024 * 1024
    MAX_DECOMPRESSED_BLOCK = 4 * 1024 * 1024

    MODEL_PATTERN = re.compile(rb"KN-[0-9]{4}", re.IGNORECASE)
    VERSION_PATTERN = re.compile(
        rb"(?<![0-9])([45]\.\d{2}\.[A-Z]\.\d+(?:\.\d+){1,2}-\d+)(?![0-9])"
    )

    ARCHITECTURES = {
        2: "ARM",
        5: "MIPS",
        22: "ARM64",
    }
    UIMAGE_COMPRESSIONS = {
        0: "без сжатия",
        1: "gzip",
        2: "bzip2",
        3: "LZMA",
        4: "LZO",
        5: "LZ4",
        6: "Zstandard",
    }
    SQUASHFS_COMPRESSIONS = {
        1: "gzip",
        2: "LZMA",
        3: "LZO",
        4: "XZ",
        5: "LZ4",
        6: "Zstandard",
    }

    @classmethod
    def inspect(
        cls,
        path: str | Path,
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> FirmwareInspectionResult:
        source = Path(path).expanduser()
        if not source.is_file():
            raise FirmwareInspectionError("Файл прошивки не найден.")

        try:
            size = source.stat().st_size
        except OSError as error:
            raise FirmwareInspectionError(
                f"Не удалось прочитать свойства файла: {error}"
            ) from error

        if size < 64:
            raise FirmwareInspectionError("Файл слишком мал для образа прошивки.")
        if size > cls.MAX_FILE_SIZE:
            raise FirmwareInspectionError(
                "Размер файла превышает безопасный предел 256 МБ."
            )

        cls._check_cancelled(cancelled)
        cls._report(progress, 0, "Чтение файла...")
        digest = hashlib.sha256()
        buffer = bytearray()
        try:
            with source.open("rb") as stream:
                while True:
                    chunk = stream.read(cls.READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    digest.update(chunk)
                    cls._check_cancelled(cancelled)
                    cls._report(
                        progress,
                        min(20, int(len(buffer) * 20 / max(1, size))),
                        "Чтение файла...",
                    )
        except OSError as error:
            raise FirmwareInspectionError(
                f"Не удалось прочитать файл прошивки: {error}"
            ) from error

        data = bytes(buffer)
        cls._report(progress, 22, "Проверка контейнера...")

        warnings: list[str] = []
        compatible_models = cls._discover_models(data)
        raw_versions = cls._discover_versions(data)
        discovered_rci_endpoints = cls._discover_rci_endpoints(data)
        internal_model = ""
        architecture = "Не определена"
        integrity_checked = False
        integrity_valid: bool | None = None

        if data.startswith(cls.UIMAGE_MAGIC):
            container = "U-Boot uImage"
            uimage = cls._parse_uimage(data)
            internal_model = uimage["model"]
            architecture = uimage["architecture"]
            integrity_checked = True
            integrity_valid = bool(
                uimage["header_crc_valid"]
                and uimage["data_crc_valid"]
            )
            if uimage["compression"] not in {"LZMA", "без сжатия"}:
                warnings.append(
                    "Используется необычное для проверенных образов сжатие ядра: "
                    f"{uimage['compression']}."
                )
        elif data.startswith(cls.FIT_MAGIC):
            container = "FIT/FDT"
            fit = cls._parse_fit(data)
            internal_model = str(fit.get("model") or "")
            architecture = str(fit.get("architecture") or "Не определена")
            integrity_checked = bool(fit.get("hashes_checked"))
            integrity_valid = (
                bool(fit.get("hashes_valid"))
                if integrity_checked
                else None
            )
            compatible_models = tuple(
                sorted(
                    set(compatible_models)
                    | set(fit.get("compatible_models") or ()),
                    key=cls._model_sort_key,
                )
            )
        else:
            raise FirmwareInspectionError(
                "Формат не распознан: поддерживаются uImage и FIT/FDT."
            )

        rootfs = cls._find_squashfs(data)
        marker_count = data.count(cls.PORTING_MARKER)
        origin = "ported" if marker_count else "unverified"
        if marker_count:
            warnings.append(
                "В образе найдена метка портированной сборки. "
                "Совместимость и целостность нужно проверять особенно внимательно."
            )

        if not raw_versions and rootfs is not None:
            cls._report(progress, 28, "Поиск версии в SquashFS...")
            scanned_versions, scanned_models, scanned_endpoints = cls._scan_xz_blocks(
                data,
                rootfs,
                cancelled=cancelled,
                progress=progress,
            )
            raw_versions = tuple(
                sorted(
                    set(raw_versions) | set(scanned_versions),
                    key=cls._version_sort_key,
                )
            )
            compatible_models = tuple(
                sorted(
                    set(compatible_models) | set(scanned_models),
                    key=cls._model_sort_key,
                )
            )
            discovered_rci_endpoints = tuple(
                sorted(
                    set(discovered_rci_endpoints) | set(scanned_endpoints),
                    key=cls._rci_endpoint_sort_key,
                )
            )

        if internal_model:
            normalized_model = cls._normalize_model(internal_model)
            internal_model = normalized_model
            compatible_models = tuple(
                sorted(
                    set(compatible_models) | {normalized_model},
                    key=cls._model_sort_key,
                )
            )
        elif compatible_models:
            internal_model = compatible_models[-1]
        else:
            warnings.append("Внутренний идентификатор модели не найден.")

        firmware_version = (
            max(raw_versions, key=cls._version_sort_key)
            if raw_versions
            else ""
        )
        if not firmware_version:
            warnings.append("Версию KeeneticOS не удалось определить.")
        if rootfs is None:
            warnings.append("Корневая файловая система SquashFS не найдена.")
        if integrity_checked and integrity_valid is False:
            warnings.append(
                "Контрольные суммы контейнера не совпадают. "
                "Не используйте этот файл для обновления роутера."
            )
        if not integrity_checked:
            warnings.append(
                "В контейнере не найдены поддерживаемые контрольные суммы."
            )
        if "draft" in source.name.casefold():
            warnings.append(
                "Имя файла указывает на предварительную draft-сборку."
            )

        capability_profile = cls._build_capability_profile(
            firmware_version=firmware_version,
            discovered_rci_endpoints=discovered_rci_endpoints,
        )

        cls._check_cancelled(cancelled)
        cls._report(progress, 100, "Анализ завершён.")
        return FirmwareInspectionResult(
            file_name=source.name,
            file_path=str(source.resolve()),
            size_bytes=size,
            sha256=digest.hexdigest().upper(),
            container=container,
            internal_model=internal_model,
            compatible_models=compatible_models,
            architecture=architecture,
            firmware_version=firmware_version,
            discovered_versions=raw_versions,
            origin=origin,
            porting_marker_count=marker_count,
            rootfs=rootfs,
            integrity_checked=integrity_checked,
            integrity_valid=integrity_valid,
            capability_profile=capability_profile,
            warnings=tuple(warnings),
        )

    @classmethod
    def compare(
        cls,
        first: FirmwareInspectionResult,
        second: FirmwareInspectionResult,
    ) -> FirmwareComparisonResult:
        """Compare two completed inspections without reading files again."""
        fields = (
            cls._comparison_field(
                "sha256",
                "SHA-256",
                first.sha256,
                second.sha256,
            ),
            cls._comparison_field(
                "size",
                "Размер",
                cls._format_size(first.size_bytes),
                cls._format_size(second.size_bytes),
            ),
            cls._comparison_field(
                "container",
                "Контейнер",
                first.container,
                second.container,
            ),
            cls._comparison_field(
                "model",
                "Внутренняя модель",
                first.internal_model or "Не определена",
                second.internal_model or "Не определена",
            ),
            cls._comparison_field(
                "architecture",
                "Архитектура",
                first.architecture,
                second.architecture,
            ),
            cls._comparison_field(
                "version",
                "Версия KeeneticOS",
                first.firmware_version or "Не определена",
                second.firmware_version or "Не определена",
            ),
            cls._comparison_field(
                "origin",
                "Происхождение",
                cls._origin_label(first.origin),
                cls._origin_label(second.origin),
            ),
            cls._comparison_field(
                "integrity",
                "Целостность",
                cls._integrity_label(first),
                cls._integrity_label(second),
            ),
            cls._comparison_field(
                "wifi_mode",
                "Предварительный режим Wi-Fi",
                cls._wifi_mode_label(
                    first.capability_profile.wifi_management_mode
                ),
                cls._wifi_mode_label(
                    second.capability_profile.wifi_management_mode
                ),
            ),
            cls._comparison_field(
                "rci_endpoints",
                "Найдено RCI-признаков",
                str(len(first.capability_profile.discovered_rci_endpoints)),
                str(len(second.capability_profile.discovered_rci_endpoints)),
            ),
        )

        warnings: list[str] = []
        same_hash = first.sha256 == second.sha256
        first_model = first.internal_model.strip().upper()
        second_model = second.internal_model.strip().upper()
        first_arch = first.architecture.strip().upper()
        second_arch = second.architecture.strip().upper()
        known_architectures = {
            value
            for value in (first_arch, second_arch)
            if value and not value.startswith("НЕ ОПРЕДЕЛЕНА")
        }

        if same_hash:
            verdict = "identical"
            verdict_label = "Файлы идентичны"
            summary = "SHA-256 обоих файлов совпадает."
        elif any(
            result.integrity_checked and result.integrity_valid is False
            for result in (first, second)
        ):
            verdict = "invalid_integrity"
            verdict_label = "Обнаружено повреждение"
            summary = (
                "Хотя бы один контейнер не прошёл проверку контрольных сумм."
            )
            warnings.append(
                "Не используйте образ с неподтверждённой целостностью для обновления."
            )
        elif len(known_architectures) > 1:
            verdict = "architecture_mismatch"
            verdict_label = "Архитектуры не совпадают"
            summary = "Образы собраны для разных аппаратных архитектур."
            warnings.append(
                "Такие файлы нельзя считать взаимозаменяемыми."
            )
        elif first_model and second_model and first_model != second_model:
            verdict = "model_mismatch"
            verdict_label = "Модели не совпадают"
            summary = (
                f"Первый образ содержит {first_model}, второй — {second_model}."
            )
            warnings.append(
                "Совпадение архитектуры не означает совместимость разных моделей."
            )
        elif first_model and first_model == second_model:
            first_version = cls._version_sort_key(first.firmware_version)
            second_version = cls._version_sort_key(second.firmware_version)
            if second_version > first_version:
                verdict = "same_model_update"
                verdict_label = "Более новая версия для той же модели"
                summary = (
                    f"{second.firmware_version} новее {first.firmware_version}."
                )
            elif second_version < first_version:
                verdict = "same_model_downgrade"
                verdict_label = "Более старая версия для той же модели"
                summary = (
                    f"{second.firmware_version} старее {first.firmware_version}."
                )
                warnings.append(
                    "Понижение версии может быть ограничено загрузчиком или схемой разделов."
                )
            else:
                verdict = "same_model_build"
                verdict_label = "Разные сборки для той же модели"
                summary = "Модель и определённая версия совпадают, но файлы различаются."
        else:
            verdict = "indeterminate"
            verdict_label = "Совместимость не определена"
            summary = "В одном или обоих файлах не удалось надёжно определить модель."

        if first.origin != second.origin:
            warnings.append(
                "Сравниваются образы разного происхождения: метка портирования "
                "обнаружена только в одном файле."
            )
        if second.origin == "ported":
            warnings.append(
                "Во втором образе обнаружена метка портированной сборки."
            )

        return FirmwareComparisonResult(
            first=first,
            second=second,
            verdict=verdict,
            verdict_label=verdict_label,
            summary=summary,
            fields=fields,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @classmethod
    def check_for_router(
        cls,
        firmware: FirmwareInspectionResult,
        *,
        router_name: str,
        router_model: str,
        current_version: str,
        router_wifi_mode: str = "",
    ) -> FirmwareRouterCheckResult:
        """Build a read-only preflight report for one connected router."""
        normalized_router_model = str(router_model or "").strip()
        router_models = cls._extract_model_ids_from_text(
            normalized_router_model
        )
        firmware_models = tuple(
            sorted(
                {
                    *firmware.compatible_models,
                    *cls._extract_model_ids_from_text(
                        firmware.internal_model
                    ),
                },
                key=cls._model_sort_key,
            )
        )
        model_match = bool(
            set(router_models) & set(firmware_models)
        )
        checks: list[FirmwareRouterCheckItem] = []
        warnings: list[str] = []

        if firmware.integrity_checked:
            integrity_status = (
                "passed" if firmware.integrity_valid else "failed"
            )
            integrity_detail = (
                "Контрольные суммы контейнера подтверждены."
                if firmware.integrity_valid
                else "Контрольные суммы контейнера не совпадают."
            )
        else:
            integrity_status = "unknown"
            integrity_detail = "Поддерживаемые контрольные суммы не найдены."
        checks.append(
            FirmwareRouterCheckItem(
                key="integrity",
                label="Целостность образа",
                status=integrity_status,
                detail=integrity_detail,
            )
        )

        if router_models and firmware_models:
            model_status = "passed" if model_match else "failed"
            model_detail = (
                f"Совпавший внутренний код: "
                f"{', '.join(sorted(set(router_models) & set(firmware_models)))}."
                if model_match
                else (
                    f"Роутер: {', '.join(router_models)}; "
                    f"прошивка: {', '.join(firmware_models)}."
                )
            )
        else:
            model_status = "unknown"
            model_detail = (
                "Не удалось надёжно извлечь внутренний код KN‑xxxx "
                "из данных роутера или образа."
            )
        checks.append(
            FirmwareRouterCheckItem(
                key="model",
                label="Модель устройства",
                status=model_status,
                detail=model_detail,
            )
        )

        installed_version = cls._canonical_version(current_version)
        image_version = cls._canonical_version(firmware.firmware_version)
        version_direction = "unknown"
        if installed_version and image_version:
            installed_key = cls._version_sort_key(installed_version)
            image_key = cls._version_sort_key(image_version)
            if image_key > installed_key:
                version_direction = "update"
                version_detail = (
                    f"Обновление: {installed_version} → {image_version}."
                )
            elif image_key < installed_key:
                version_direction = "downgrade"
                version_detail = (
                    f"Понижение: {installed_version} → {image_version}."
                )
                warnings.append(
                    "Понижение версии может быть запрещено загрузчиком "
                    "или несовместимо со схемой разделов."
                )
            else:
                version_direction = "same"
                version_detail = f"Установлена та же версия {installed_version}."
            version_status = "passed"
        else:
            version_status = "unknown"
            version_detail = (
                "Текущую версию роутера или версию образа определить не удалось."
            )
        checks.append(
            FirmwareRouterCheckItem(
                key="version",
                label="Направление обновления",
                status=version_status,
                detail=version_detail,
            )
        )

        normalized_router_wifi = str(router_wifi_mode or "").strip().lower()
        firmware_wifi = firmware.capability_profile.wifi_management_mode
        if normalized_router_wifi and firmware_wifi:
            wifi_changed = normalized_router_wifi != firmware_wifi
            wifi_status = "warning" if wifi_changed else "passed"
            wifi_detail = (
                f"Режим может измениться: "
                f"{cls._wifi_mode_label(normalized_router_wifi)} → "
                f"{cls._wifi_mode_label(firmware_wifi)}."
                if wifi_changed
                else (
                    f"Предполагаемый режим совпадает: "
                    f"{cls._wifi_mode_label(firmware_wifi)}."
                )
            )
        else:
            wifi_status = "unknown"
            wifi_detail = "Недостаточно данных для сопоставления режима Wi-Fi."
        checks.append(
            FirmwareRouterCheckItem(
                key="wifi_mode",
                label="Режим управления Wi-Fi",
                status=wifi_status,
                detail=wifi_detail,
            )
        )

        if firmware.origin == "ported":
            origin_status = "warning"
            origin_detail = "В образе найдена метка портированной сборки."
            warnings.append(
                "Портированная сборка требует отдельной проверки источника "
                "и аппаратной ревизии устройства."
            )
        else:
            origin_status = "unknown"
            origin_detail = (
                "Метка портирования не найдена; это не подтверждает "
                "официальное происхождение файла."
            )
        checks.append(
            FirmwareRouterCheckItem(
                key="origin",
                label="Происхождение образа",
                status=origin_status,
                detail=origin_detail,
            )
        )

        if firmware.integrity_checked and firmware.integrity_valid is False:
            verdict = "blocked_integrity"
            verdict_label = "Использование образа опасно"
            summary = "Файл не прошёл проверку целостности контейнера."
        elif router_models and firmware_models and not model_match:
            verdict = "model_mismatch"
            verdict_label = "Прошивка не соответствует модели"
            summary = (
                "Внутренний код модели выбранного роутера не совпадает "
                "с кодом в прошивке."
            )
        elif not router_models or not firmware_models:
            verdict = "indeterminate"
            verdict_label = "Недостаточно данных"
            summary = (
                "Нельзя безопасно подтвердить соответствие файла выбранному роутеру."
            )
        elif not firmware.integrity_checked:
            verdict = "matching_unverified_integrity"
            verdict_label = "Модель совпадает, целостность не подтверждена"
            summary = (
                "Код модели совпадает, но контейнер не содержит "
                "поддерживаемой проверки целостности."
            )
        elif version_direction == "downgrade":
            verdict = "matching_downgrade"
            verdict_label = "Модель совпадает, но это понижение версии"
            summary = "Образ относится к этой модели, однако версия старее текущей."
        elif version_direction == "update":
            verdict = "matching_update"
            verdict_label = "Модель совпадает; обнаружено обновление"
            summary = (
                "Код модели совпадает, целостность контейнера подтверждена, "
                "версия новее текущей."
            )
        elif version_direction == "same":
            verdict = "matching_same_version"
            verdict_label = "Та же версия для той же модели"
            summary = "Модель и версия совпадают, но файл может отличаться сборкой."
        else:
            verdict = "matching_unknown_version"
            verdict_label = "Модель совпадает"
            summary = "Модель совпадает, направление обновления определить нельзя."

        return FirmwareRouterCheckResult(
            firmware=firmware,
            router_name=str(router_name or "Выбранный роутер"),
            router_model=normalized_router_model,
            router_models=router_models,
            current_version=installed_version or str(current_version or "").strip(),
            router_wifi_mode=normalized_router_wifi,
            verdict=verdict,
            verdict_label=verdict_label,
            summary=summary,
            checks=tuple(checks),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @classmethod
    def _parse_uimage(cls, data: bytes) -> dict[str, Any]:
        if len(data) < 64:
            raise FirmwareInspectionError("Заголовок uImage повреждён.")
        (
            _magic,
            stored_header_crc,
            _timestamp,
            payload_size,
            _load,
            _entry,
            stored_data_crc,
            _os_id,
            architecture_id,
            _image_type,
            compression_id,
            raw_name,
        ) = struct.unpack(">7I4B32s", data[:64])
        payload_end = 64 + payload_size
        if payload_size <= 0 or payload_end > len(data):
            raise FirmwareInspectionError(
                "Размер ядра в заголовке uImage выходит за границы файла."
            )

        header = bytearray(data[:64])
        header[4:8] = b"\0\0\0\0"
        calculated_header_crc = zlib.crc32(header) & 0xFFFFFFFF
        calculated_data_crc = zlib.crc32(data[64:payload_end]) & 0xFFFFFFFF
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace")
        model_match = cls.MODEL_PATTERN.search(name.encode("ascii", errors="ignore"))
        model = (
            cls._normalize_model(model_match.group(0).decode("ascii"))
            if model_match
            else ""
        )
        return {
            "model": model,
            "architecture": cls.ARCHITECTURES.get(
                architecture_id,
                f"Неизвестная ({architecture_id})",
            ),
            "compression": cls.UIMAGE_COMPRESSIONS.get(
                compression_id,
                f"неизвестное ({compression_id})",
            ),
            "header_crc_valid": calculated_header_crc == stored_header_crc,
            "data_crc_valid": calculated_data_crc == stored_data_crc,
        }

    @classmethod
    def _parse_fit(cls, data: bytes) -> dict[str, Any]:
        document = cls._parse_fdt_document(data)
        properties = document["properties"]
        architecture = ""
        compatible_models: set[str] = set()
        model = ""
        hashes_checked = 0
        hashes_valid = True

        for path, values in properties.items():
            if path.startswith("/images/kernel") and not architecture:
                architecture = cls._decode_c_string(values.get("arch", b""))

            image_data = values.get("data")
            if not path.startswith("/images/") or not image_data:
                continue
            if "/hash" in path:
                continue
            prefix = f"{path}/hash"
            for hash_path, hash_values in properties.items():
                if not hash_path.startswith(prefix):
                    continue
                algorithm = cls._decode_c_string(hash_values.get("algo", b""))
                expected = hash_values.get("value", b"")
                if algorithm == "crc32" and len(expected) == 4:
                    actual = struct.pack(">I", zlib.crc32(image_data) & 0xFFFFFFFF)
                elif algorithm == "sha1" and len(expected) == 20:
                    actual = hashlib.sha1(image_data).digest()
                elif algorithm == "sha256" and len(expected) == 32:
                    actual = hashlib.sha256(image_data).digest()
                else:
                    continue
                hashes_checked += 1
                hashes_valid = hashes_valid and actual == expected

            if "fdt" not in path.casefold() or not image_data.startswith(cls.FIT_MAGIC):
                continue
            try:
                nested = cls._parse_fdt_document(image_data)["properties"]
            except FirmwareInspectionError:
                continue
            root = nested.get("/", {})
            model_text = cls._decode_c_string(root.get("model", b""))
            compatible_text = cls._decode_c_strings(root.get("compatible", b""))
            match = cls.MODEL_PATTERN.search(model_text.encode("ascii", errors="ignore"))
            if match:
                model = cls._normalize_model(match.group(0).decode("ascii"))
            for item in compatible_text:
                match = cls.MODEL_PATTERN.search(item.encode("ascii", errors="ignore"))
                if match:
                    compatible_models.add(
                        cls._normalize_model(match.group(0).decode("ascii"))
                    )

        return {
            "model": model,
            "compatible_models": tuple(compatible_models),
            "architecture": architecture.upper() if architecture else "Не определена",
            "hashes_checked": hashes_checked,
            "hashes_valid": hashes_valid if hashes_checked else None,
        }

    @classmethod
    def _parse_fdt_document(cls, data: bytes) -> dict[str, Any]:
        if len(data) < 40 or not data.startswith(cls.FIT_MAGIC):
            raise FirmwareInspectionError("Заголовок FIT/FDT повреждён.")
        (
            _magic,
            total_size,
            structure_offset,
            strings_offset,
            _reserve_offset,
            _version,
            _last_version,
            _boot_cpu,
            strings_size,
            structure_size,
        ) = struct.unpack(">10I", data[:40])
        if (
            total_size > len(data)
            or structure_offset + structure_size > total_size
            or strings_offset + strings_size > total_size
        ):
            raise FirmwareInspectionError("Секции FIT/FDT выходят за границы файла.")

        strings = data[strings_offset : strings_offset + strings_size]
        position = structure_offset
        structure_end = structure_offset + structure_size
        stack: list[str] = []
        properties: dict[str, dict[str, bytes]] = {}

        while position + 4 <= structure_end:
            token = struct.unpack_from(">I", data, position)[0]
            position += 4
            if token == 1:
                terminator = data.find(b"\0", position, structure_end)
                if terminator < 0:
                    raise FirmwareInspectionError("Имя узла FIT/FDT повреждено.")
                stack.append(
                    data[position:terminator].decode("ascii", errors="replace")
                )
                position = cls._align4(terminator + 1)
            elif token == 2:
                if stack:
                    stack.pop()
            elif token == 3:
                if position + 8 > structure_end:
                    raise FirmwareInspectionError("Свойство FIT/FDT повреждено.")
                length, name_offset = struct.unpack_from(">II", data, position)
                position += 8
                value_end = position + length
                if value_end > structure_end or name_offset >= len(strings):
                    raise FirmwareInspectionError("Свойство FIT/FDT выходит за границы.")
                name_end = strings.find(b"\0", name_offset)
                if name_end < 0:
                    raise FirmwareInspectionError("Таблица строк FIT/FDT повреждена.")
                name = strings[name_offset:name_end].decode("ascii", errors="replace")
                path = "/" + "/".join(item for item in stack if item)
                properties.setdefault(path or "/", {})[name] = data[position:value_end]
                position = cls._align4(value_end)
            elif token == 4:
                continue
            elif token == 9:
                break
            else:
                raise FirmwareInspectionError(
                    f"Неизвестный токен FIT/FDT: {token}."
                )

        return {
            "total_size": total_size,
            "properties": properties,
        }

    @classmethod
    def _find_squashfs(cls, data: bytes) -> SquashFsInfo | None:
        position = 0
        header_size = struct.calcsize("<5I6H8Q")
        while True:
            position = data.find(cls.SQUASHFS_MAGIC, position)
            if position < 0:
                return None
            if position + header_size > len(data):
                return None
            values = struct.unpack_from("<5I6H8Q", data, position)
            (
                _magic,
                inode_count,
                _modification_time,
                block_size,
                _fragments,
                compression_id,
                _block_log,
                _flags,
                _id_count,
                major,
                minor,
                _root_inode,
                bytes_used,
                *_table_offsets,
            ) = values
            if (
                major == 4
                and minor == 0
                and block_size > 0
                and bytes_used >= header_size
                and position + bytes_used <= len(data)
            ):
                return SquashFsInfo(
                    offset=position,
                    version=f"{major}.{minor}",
                    compression=cls.SQUASHFS_COMPRESSIONS.get(
                        compression_id,
                        f"неизвестное ({compression_id})",
                    ),
                    block_size=block_size,
                    inode_count=inode_count,
                    bytes_used=bytes_used,
                )
            position += len(cls.SQUASHFS_MAGIC)

    @classmethod
    def _scan_xz_blocks(
        cls,
        data: bytes,
        rootfs: SquashFsInfo,
        *,
        cancelled: Callable[[], bool] | None,
        progress: Callable[[int, str], None] | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        end = min(len(data), rootfs.offset + rootfs.bytes_used)
        positions: list[int] = []
        position = rootfs.offset
        while True:
            position = data.find(cls.XZ_MAGIC, position, end)
            if position < 0:
                break
            positions.append(position)
            position += len(cls.XZ_MAGIC)

        versions: set[str] = set()
        models: set[str] = set()
        endpoints: set[str] = set()
        window_size = min(
            2 * 1024 * 1024,
            max(384 * 1024, rootfs.block_size + 256 * 1024),
        )
        for index, offset in enumerate(positions):
            cls._check_cancelled(cancelled)
            try:
                decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
                decoded = decompressor.decompress(
                    data[offset : min(end, offset + window_size)],
                    max_length=cls.MAX_DECOMPRESSED_BLOCK,
                )
            except (EOFError, lzma.LZMAError):
                continue
            versions.update(cls._discover_versions(decoded))
            models.update(cls._discover_models(decoded))
            endpoints.update(cls._discover_rci_endpoints(decoded))
            if index % 8 == 0 or index + 1 == len(positions):
                percentage = 30 + int((index + 1) * 65 / max(1, len(positions)))
                cls._report(
                    progress,
                    percentage,
                    f"Проверка SquashFS: {index + 1} из {len(positions)}...",
                )

        return (
            tuple(sorted(versions, key=cls._version_sort_key)),
            tuple(sorted(models, key=cls._model_sort_key)),
            tuple(sorted(endpoints, key=cls._rci_endpoint_sort_key)),
        )

    @classmethod
    def _build_capability_profile(
        cls,
        *,
        firmware_version: str,
        discovered_rci_endpoints: tuple[str, ...],
    ) -> FirmwareCapabilityProfile:
        endpoints = set(discovered_rci_endpoints)
        version_match = re.match(r"^(\d+)\.(\d+)", firmware_version)
        major = int(version_match.group(1)) if version_match else 0
        minor = int(version_match.group(2)) if version_match else 0

        has_mws = bool(
            endpoints
            & {
                "show/sc/mws/wlan",
                "show/mws/wlan",
            }
        )
        if has_mws:
            wifi_mode = "mws"
        elif major == 4:
            wifi_mode = "legacy"
        elif major >= 5 and minor >= 1:
            wifi_mode = "mws"
        else:
            wifi_mode = "monitoring"

        if len(endpoints) >= 3:
            confidence = "high"
        elif firmware_version:
            confidence = "medium"
        else:
            confidence = "low"

        def endpoint_feature(
            key: str,
            label: str,
            candidates: set[str],
            detail: str,
        ) -> FirmwareCapabilityEvidence:
            found = tuple(
                sorted(
                    endpoints & candidates,
                    key=cls._rci_endpoint_sort_key,
                )
            )
            if found:
                return FirmwareCapabilityEvidence(
                    key=key,
                    label=label,
                    state="confirmed",
                    detail=f"Найдены статические признаки: {', '.join(found)}.",
                )
            return FirmwareCapabilityEvidence(
                key=key,
                label=label,
                state="unknown",
                detail=(
                    f"{detail} Отсутствие строки в образе не доказывает "
                    "отсутствие функции."
                ),
            )

        if has_mws:
            wifi_feature = FirmwareCapabilityEvidence(
                key="wifi_management",
                label="Управление Wi-Fi",
                state="confirmed",
                detail="В образе найдены RCI-признаки логических сетей MWS.",
            )
        elif wifi_mode == "legacy":
            wifi_feature = FirmwareCapabilityEvidence(
                key="wifi_management",
                label="Управление Wi-Fi",
                state="inferred",
                detail=(
                    "Для KeeneticOS 4 предполагается legacy-управление "
                    "физическими AccessPoint; требуется проверка на роутере."
                ),
            )
        elif wifi_mode == "mws":
            wifi_feature = FirmwareCapabilityEvidence(
                key="wifi_management",
                label="Управление Wi-Fi",
                state="inferred",
                detail=(
                    "Режим MWS предполагается по версии KeeneticOS; "
                    "статическая строка команды не найдена."
                ),
            )
        else:
            wifi_feature = FirmwareCapabilityEvidence(
                key="wifi_management",
                label="Управление Wi-Fi",
                state="unknown",
                detail="Режим управления Wi-Fi по образу не определён.",
            )

        features = (
            endpoint_feature(
                "system",
                "Состояние системы",
                {"show/system", "show/version"},
                "Команды состояния системы статически не найдены.",
            ),
            endpoint_feature(
                "interfaces",
                "Интерфейсы и порты",
                {"show/interface", "show/interface/stat"},
                "Команды интерфейсов статически не найдены.",
            ),
            wifi_feature,
            endpoint_feature(
                "clients",
                "Подключённые клиенты",
                {"show/ip/hotspot"},
                "Команда списка клиентов статически не найдена.",
            ),
            endpoint_feature(
                "system_log",
                "Системный журнал",
                {"show/log"},
                "Команда журнала статически не найдена.",
            ),
            endpoint_feature(
                "ping_check",
                "Проверка доступности",
                {"show/ping-check"},
                "Команда ping-check статически не найдена.",
            ),
        )
        return FirmwareCapabilityProfile(
            wifi_management_mode=wifi_mode,
            confidence=confidence,
            discovered_rci_endpoints=tuple(
                sorted(endpoints, key=cls._rci_endpoint_sort_key)
            ),
            features=features,
        )

    @classmethod
    def _discover_rci_endpoints(cls, data: bytes) -> tuple[str, ...]:
        return tuple(
            endpoint
            for endpoint in cls.RCI_ENDPOINT_SIGNATURES
            if endpoint.encode("ascii") in data
        )

    @classmethod
    def _discover_models(cls, data: bytes) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    cls._normalize_model(match.group(0).decode("ascii"))
                    for match in cls.MODEL_PATTERN.finditer(data)
                },
                key=cls._model_sort_key,
            )
        )

    @classmethod
    def _extract_model_ids_from_text(cls, value: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    f"KN-{match.group(1)}"
                    for match in re.finditer(
                        r"\bKN[\s_-]?(\d{4})\b",
                        str(value or ""),
                        flags=re.IGNORECASE,
                    )
                },
                key=cls._model_sort_key,
            )
        )

    @classmethod
    def _discover_versions(cls, data: bytes) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    match.group(1).decode("ascii")
                    for match in cls.VERSION_PATTERN.finditer(data)
                },
                key=cls._version_sort_key,
            )
        )

    @staticmethod
    def _canonical_version(value: str) -> str:
        match = re.search(
            r"([45]\.\d{2}\.[A-Z]\.\d+(?:\.\d+){1,2}-\d+)",
            str(value or "").upper(),
        )
        return match.group(1) if match else ""

    @classmethod
    def _comparison_field(
        cls,
        key: str,
        label: str,
        first_value: str,
        second_value: str,
    ) -> FirmwareComparisonField:
        return FirmwareComparisonField(
            key=key,
            label=label,
            first_value=str(first_value),
            second_value=str(second_value),
            changed=str(first_value) != str(second_value),
        )

    @staticmethod
    def _format_size(value: int) -> str:
        size = max(0, int(value))
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.2f} МБ"
        if size >= 1024:
            return f"{size / 1024:.1f} КБ"
        return f"{size} байт"

    @staticmethod
    def _origin_label(value: str) -> str:
        if value == "ported":
            return "Есть метка портированной сборки"
        return "Метка портирования не найдена"

    @staticmethod
    def _integrity_label(result: FirmwareInspectionResult) -> str:
        if not result.integrity_checked:
            return "Не проверена"
        return "Подтверждена" if result.integrity_valid else "Не подтверждена"

    @staticmethod
    def _wifi_mode_label(value: str) -> str:
        return {
            "mws": "MWS",
            "legacy": "Legacy AccessPoint",
            "monitoring": "Только мониторинг / не определён",
        }.get(str(value or ""), "Не определён")

    @classmethod
    def _rci_endpoint_sort_key(cls, value: str) -> tuple[int, str]:
        try:
            return cls.RCI_ENDPOINT_SIGNATURES.index(value), value
        except ValueError:
            return len(cls.RCI_ENDPOINT_SIGNATURES), value

    @staticmethod
    def _version_sort_key(value: str) -> tuple[int, int, int, tuple[int, ...]]:
        match = re.match(
            r"^(\d+)\.(\d+)\.([A-Z])\.(\d+(?:\.\d+){1,2})-(\d+)$",
            str(value or ""),
        )
        if not match:
            return 0, 0, 0, ()
        numbers = tuple(
            int(item) for item in match.group(4).split(".")
        ) + (int(match.group(5)),)
        return (
            int(match.group(1)),
            int(match.group(2)),
            ord(match.group(3)) - ord("A") + 1,
            numbers,
        )

    @staticmethod
    def _model_sort_key(value: str) -> tuple[int, str]:
        match = re.search(r"(\d{4})", str(value or ""))
        return (int(match.group(1)) if match else 0, str(value or ""))

    @staticmethod
    def _normalize_model(value: str) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _decode_c_string(value: bytes) -> str:
        return value.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()

    @staticmethod
    def _decode_c_strings(value: bytes) -> tuple[str, ...]:
        return tuple(
            item.decode("ascii", errors="replace").strip()
            for item in value.split(b"\0")
            if item
        )

    @staticmethod
    def _align4(value: int) -> int:
        return (int(value) + 3) & ~3

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
        if cancelled is not None and cancelled():
            raise FirmwareInspectionCancelled("Анализ прошивки отменён.")

    @staticmethod
    def _report(
        progress: Callable[[int, str], None] | None,
        percentage: int,
        message: str,
    ) -> None:
        if progress is not None:
            progress(max(0, min(100, int(percentage))), str(message))
