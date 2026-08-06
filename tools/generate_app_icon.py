from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = PROJECT_ROOT / "assets" / "keenetic_manager.svg"
ICO_PATH = PROJECT_ROOT / "assets" / "keenetic_manager.ico"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(
        size,
        size,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(0)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Не удалось создать PNG {size}x{size}")
    buffer.close()
    return bytes(data)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    directory = bytearray()
    payload = bytearray()
    offset = 6 + 16 * len(images)

    for size, image_data in images:
        encoded_size = 0 if size >= 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(image_data),
                offset,
            )
        )
        payload.extend(image_data)
        offset += len(image_data)

    return header + bytes(directory) + bytes(payload)


def main() -> None:
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        raise RuntimeError(f"Некорректный SVG: {SVG_PATH}")

    images = [
        (size, render_png(renderer, size))
        for size in ICON_SIZES
    ]
    ICO_PATH.write_bytes(build_ico(images))
    print(f"Создано: {ICO_PATH}")


if __name__ == "__main__":
    main()
