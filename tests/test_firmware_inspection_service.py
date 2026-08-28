from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from core.services.firmware_inspection_service import (
    FirmwareInspectionCancelled,
    FirmwareInspectionService,
)
from ui.dialogs.firmware_inspection_dialog import FirmwareInspectionDialog
from ui.dialogs.firmware_inspection_dialog import FirmwareComparisonDialog
from ui.dialogs.firmware_inspection_dialog import FirmwareRouterCheckDialog


def build_squashfs_header() -> bytes:
    header_size = struct.calcsize("<5I6H8Q")
    return struct.pack(
        "<5I6H8Q",
        int.from_bytes(b"hsqs", "little"),
        12,
        0,
        131072,
        0,
        4,
        17,
        0,
        1,
        4,
        0,
        0,
        header_size,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def build_uimage(
    *,
    model: bytes = b"KN-2311",
    version: bytes = b"5.01.C.2.0-2",
    architecture_id: int = 5,
    ported: bool = True,
    endpoints: bytes = (
        b"show/system show/interface show/sc/mws/wlan "
        b"show/ip/hotspot show/log"
    ),
) -> bytes:
    payload = b"synthetic read-only kernel payload"
    name = model.ljust(32, b"\0")
    header = bytearray(
        struct.pack(
            ">7I4B32s",
            0x27051956,
            0,
            0,
            len(payload),
            0x81000000,
            0x81000000,
            zlib.crc32(payload) & 0xFFFFFFFF,
            architecture_id,
            5,
            2,
            0,
            name,
        )
    )
    struct.pack_into(
        ">I",
        header,
        4,
        zlib.crc32(header) & 0xFFFFFFFF,
    )
    padding = b"\xFF" * (512 - len(header) - len(payload))
    image = (
        bytes(header)
        + payload
        + padding
        + build_squashfs_header()
        + model
        + b" "
        + version
        + b"\0"
        + endpoints
        + b"\0"
    )
    if ported:
        image += b"t.me/keeneticported\0"
    return image


def build_fdt(nodes: list[tuple[str, list[tuple[str, bytes]]]]) -> bytes:
    strings = bytearray()
    name_offsets: dict[str, int] = {}

    def name_offset(name: str) -> int:
        if name not in name_offsets:
            name_offsets[name] = len(strings)
            strings.extend(name.encode("ascii") + b"\0")
        return name_offsets[name]

    structure = bytearray()

    def append_aligned(value: bytes) -> None:
        structure.extend(value)
        structure.extend(b"\0" * ((-len(structure)) % 4))

    structure.extend(struct.pack(">I", 1))
    append_aligned(b"\0")
    open_nodes: list[str] = []
    for path, properties in nodes:
        parts = [item for item in path.split("/") if item]
        common = 0
        while (
            common < len(parts)
            and common < len(open_nodes)
            and parts[common] == open_nodes[common]
        ):
            common += 1
        for _item in range(len(open_nodes) - common):
            structure.extend(struct.pack(">I", 2))
        open_nodes = open_nodes[:common]
        for part in parts[common:]:
            structure.extend(struct.pack(">I", 1))
            append_aligned(part.encode("ascii") + b"\0")
            open_nodes.append(part)
        for name, value in properties:
            structure.extend(
                struct.pack(">III", 3, len(value), name_offset(name))
            )
            append_aligned(value)

    for _item in open_nodes:
        structure.extend(struct.pack(">I", 2))
    structure.extend(struct.pack(">I", 2))
    structure.extend(struct.pack(">I", 9))

    reserve = b"\0" * 16
    structure_offset = 40 + len(reserve)
    strings_offset = structure_offset + len(structure)
    total_size = strings_offset + len(strings)
    header = struct.pack(
        ">10I",
        0xD00DFEED,
        total_size,
        structure_offset,
        strings_offset,
        40,
        17,
        16,
        0,
        len(strings),
        len(structure),
    )
    return header + reserve + bytes(structure) + bytes(strings)


def build_fit(*, include_hashes: bool = True) -> bytes:
    nested_fdt = build_fdt(
        [
            (
                "/",
                [
                    ("model", b"Keenetic KN-3812\0"),
                    ("compatible", b"keenetic,kn-3812\0mediatek,mt7981\0"),
                ],
            ),
        ]
    )
    kernel = b"synthetic arm64 kernel"
    nodes = [
            ("/", []),
            ("/images", []),
            (
                "/images/kernel@1",
                [
                    ("type", b"kernel\0"),
                    ("arch", b"arm64\0"),
                    ("data", kernel),
                ],
            ),
            (
                "/images/fdt@1",
                [
                    ("type", b"flat_dt\0"),
                    ("arch", b"arm64\0"),
                    ("data", nested_fdt),
                ],
            ),
        ]
    if include_hashes:
        nodes.extend(
            (
                (
                    "/images/kernel@1/hash@1",
                    [
                        ("algo", b"sha1\0"),
                        ("value", hashlib.sha1(kernel).digest()),
                    ],
                ),
                (
                    "/images/fdt@1/hash@1",
                    [
                        ("algo", b"sha256\0"),
                        ("value", hashlib.sha256(nested_fdt).digest()),
                    ],
                ),
            )
        )
    fit = build_fdt(nodes)
    padding = b"\0" * (4096 - len(fit))
    return (
        fit
        + padding
        + build_squashfs_header()
        + b"KN-3812 5.02.A.5.0-1\0"
    )


class FirmwareInspectionServiceTests(unittest.TestCase):
    def inspect_bytes(self, data: bytes, name: str):  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(data)
            progress: list[int] = []
            result = FirmwareInspectionService.inspect(
                path,
                progress=lambda value, _message: progress.append(value),
            )
        self.assertEqual(progress[-1], 100)
        return result

    def test_uimage_reports_profile_rootfs_origin_and_crc(self) -> None:
        result = self.inspect_bytes(build_uimage(), "ported.bin")

        self.assertEqual(result.container, "U-Boot uImage")
        self.assertEqual(result.internal_model, "KN-2311")
        self.assertEqual(result.architecture, "MIPS")
        self.assertEqual(result.firmware_version, "5.01.C.2.0-2")
        self.assertEqual(result.origin, "ported")
        self.assertTrue(result.integrity_valid)
        self.assertEqual(result.rootfs.compression, "XZ")
        self.assertEqual(
            result.capability_profile.wifi_management_mode,
            "mws",
        )
        self.assertEqual(result.capability_profile.confidence, "high")
        self.assertIn(
            "show/log",
            result.capability_profile.discovered_rci_endpoints,
        )

    def test_fit_reports_nested_model_architecture_and_hashes(self) -> None:
        result = self.inspect_bytes(build_fit(), "draft.bin")

        self.assertEqual(result.container, "FIT/FDT")
        self.assertEqual(result.internal_model, "KN-3812")
        self.assertEqual(result.architecture, "ARM64")
        self.assertEqual(result.firmware_version, "5.02.A.5.0-1")
        self.assertTrue(result.integrity_checked)
        self.assertTrue(result.integrity_valid)

    def test_modified_uimage_payload_is_reported_as_invalid(self) -> None:
        image = bytearray(build_uimage())
        image[70] ^= 0x01
        result = self.inspect_bytes(bytes(image), "modified.bin")

        self.assertFalse(result.integrity_valid)
        self.assertTrue(
            any("не совпадают" in warning for warning in result.warnings)
        )

    def test_cancellation_stops_before_file_processing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cancel.bin"
            path.write_bytes(build_uimage())
            with self.assertRaises(FirmwareInspectionCancelled):
                FirmwareInspectionService.inspect(
                    path,
                    cancelled=lambda: True,
                )

    def test_report_explicitly_states_read_only_limits(self) -> None:
        result = self.inspect_bytes(build_uimage(), "firmware.bin")
        report = FirmwareInspectionDialog.build_report(result)

        self.assertIn("SHA-256", report)
        self.assertIn("Целостность контейнера: Подтверждена", report)
        self.assertIn("не является разрешением на прошивку", report)
        self.assertIn("Предварительный профиль возможностей", report)
        self.assertIn("Системный журнал: найдено в образе", report)

    def test_comparison_reports_update_for_same_model(self) -> None:
        old = self.inspect_bytes(
            build_uimage(version=b"5.01.C.2.0-2", ported=False),
            "old.bin",
        )
        new = self.inspect_bytes(
            build_uimage(version=b"5.01.C.3.0-1", ported=False),
            "new.bin",
        )

        comparison = FirmwareInspectionService.compare(old, new)

        self.assertEqual(comparison.verdict, "same_model_update")
        self.assertIn("новее", comparison.summary)
        version_field = next(
            field for field in comparison.fields if field.key == "version"
        )
        self.assertTrue(version_field.changed)

    def test_comparison_rejects_different_architectures_first(self) -> None:
        mips = self.inspect_bytes(
            build_uimage(ported=False),
            "mips.bin",
        )
        arm64 = self.inspect_bytes(build_fit(), "arm64.bin")

        comparison = FirmwareInspectionService.compare(mips, arm64)

        self.assertEqual(comparison.verdict, "architecture_mismatch")
        self.assertTrue(comparison.warnings)

    def test_comparison_report_contains_both_files_and_safety_limit(self) -> None:
        first = self.inspect_bytes(
            build_uimage(version=b"5.01.C.2.0-2", ported=False),
            "first.bin",
        )
        second = self.inspect_bytes(
            build_uimage(version=b"5.01.C.3.0-1", ported=False),
            "second.bin",
        )
        comparison = FirmwareInspectionService.compare(first, second)

        report = FirmwareComparisonDialog.build_report(comparison)

        self.assertIn("Первый файл: first.bin", report)
        self.assertIn("Второй файл: second.bin", report)
        self.assertIn("ИЗМЕНЕНО", report)
        self.assertIn("не является разрешением на прошивку", report)

    def test_router_check_accepts_newer_version_for_same_internal_model(
        self,
    ) -> None:
        firmware = self.inspect_bytes(
            build_uimage(version=b"5.01.C.3.0-1", ported=False),
            "update.bin",
        )

        result = FirmwareInspectionService.check_for_router(
            firmware,
            router_name="Домашний роутер",
            router_model="N6 AX1800 (KN-2311)",
            current_version="5.01.C.2.0-2",
            router_wifi_mode="mws",
        )

        self.assertEqual(result.verdict, "matching_update")
        self.assertEqual(result.router_models, ("KN-2311",))
        self.assertEqual(
            next(item for item in result.checks if item.key == "model").status,
            "passed",
        )

    def test_router_check_rejects_different_internal_model(self) -> None:
        firmware = self.inspect_bytes(
            build_uimage(ported=False),
            "other-model.bin",
        )

        result = FirmwareInspectionService.check_for_router(
            firmware,
            router_name="Sprinter",
            router_model="Sprinter (KN-3711)",
            current_version="5.01.C.1.0-0",
        )

        self.assertEqual(result.verdict, "model_mismatch")
        self.assertEqual(
            next(item for item in result.checks if item.key == "model").status,
            "failed",
        )

    def test_router_check_blocks_corrupted_container_before_version(self) -> None:
        image = bytearray(build_uimage(ported=False))
        image[70] ^= 0x01
        firmware = self.inspect_bytes(bytes(image), "corrupted.bin")

        result = FirmwareInspectionService.check_for_router(
            firmware,
            router_name="N6",
            router_model="KN_2311",
            current_version="5.01.C.1.0-0",
        )

        self.assertEqual(result.verdict, "blocked_integrity")
        self.assertEqual(result.router_models, ("KN-2311",))

    def test_router_check_report_contains_router_and_read_only_limit(self) -> None:
        firmware = self.inspect_bytes(
            build_uimage(version=b"5.01.C.3.0-1", ported=False),
            "candidate.bin",
        )
        result = FirmwareInspectionService.check_for_router(
            firmware,
            router_name="test2",
            router_model="N6 AX1800 (KN-2311)",
            current_version="5.01.C.2.0-2",
            router_wifi_mode="legacy",
        )

        report = FirmwareRouterCheckDialog.build_report(result)

        self.assertIn("Роутер: test2", report)
        self.assertIn("[ПРОЙДЕНО] Модель устройства", report)
        self.assertIn("не является разрешением на прошивку", report)

    def test_router_check_does_not_approve_unverified_fit_integrity(self) -> None:
        firmware = self.inspect_bytes(
            build_fit(include_hashes=False),
            "unverified-fit.bin",
        )

        result = FirmwareInspectionService.check_for_router(
            firmware,
            router_name="RAX3000M",
            router_model="KN-3812",
            current_version="5.01.C.1.0-0",
        )

        self.assertEqual(result.verdict, "matching_unverified_integrity")


if __name__ == "__main__":
    unittest.main()
