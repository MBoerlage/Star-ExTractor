"""Rebuild starextractor/resources/starex.ico from the logo SVGs (Qt only, no Pillow).

    python tools/make_icon.py [--preview out.png]

Seven PNG-compressed entries on the dark tile: 16 and 24 px from logo_icon_small.svg (thicker strokes, four
stripes), 32-256 px from logo_icon.svg. `--preview` writes 16/24/32/48/256 at 1:1 on a white and on a #202020
row (light and dark taskbar), plus the small sizes enlarged ×4."""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(img) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def build_ico(path: Path) -> list[int]:
    from starextractor.ui.brand import icon_svg_for, render

    entries = [(s, png_bytes(render(icon_svg_for(s), s))) for s in SIZES]
    head = struct.pack("<HHH", 0, 1, len(entries))  # reserved, type 1 = icon, count
    offset = len(head) + 16 * len(entries)
    table, blobs = b"", b""
    for s, png in entries:
        wh = 0 if s >= 256 else s  # 0 means 256 in the directory
        table += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    path.write_bytes(head + table + blobs)
    return [s for s, _ in entries]


def ico_sizes(path: Path) -> list[int]:
    data = path.read_bytes()
    _r, kind, n = struct.unpack_from("<HHH", data, 0)
    assert kind == 1
    return [(struct.unpack_from("<B", data, 6 + 16 * i)[0] or 256) for i in range(n)]


def preview(ico: Path, out: Path) -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter

    icon = QIcon(str(ico))
    sizes, zoomed, zoom, gap = (16, 24, 32, 48, 256), (16, 24, 32), 4, 20
    row_w = sum(sizes) + gap * (len(sizes) + 1) + sum(s * zoom for s in zoomed) + gap * len(zoomed)
    row_h = 256 + 2 * gap
    img = QImage(row_w + 150, 2 * row_h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    font = QFont("Segoe UI")
    font.setPixelSize(14)
    p.setFont(font)
    rows = (("#FFFFFF", "#202020", "light taskbar"), ("#202020", "#E0E0E0", "dark taskbar"))
    for r, (bg, fg, name) in enumerate(rows):
        y0 = r * row_h
        p.fillRect(0, y0, img.width(), row_h, QColor(bg))
        p.setPen(QColor(fg))
        p.drawText(10, y0 + gap + 14, name)
        x = 140
        for s in sizes:  # 1:1, bottom-aligned like icons on a bar
            p.drawPixmap(x, y0 + gap + 256 - s, icon.pixmap(QSize(s, s)))
            p.drawText(x, y0 + row_h - 4, f"{s}")
            x += s + gap
        for s in zoomed:  # hard pixels, to judge the small entries
            big = icon.pixmap(QSize(s, s)).toImage().scaled(s * zoom, s * zoom)
            p.drawImage(x, y0 + gap + 256 - s * zoom, big)
            p.drawText(x, y0 + row_h - 4, f"{s} ×{zoom}")
            x += s * zoom + gap
    p.end()
    img.save(str(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":  # offscreen Qt has no fonts of its own on Windows (preview labels)
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - Qt needs it for images
    from starextractor.ui.brand import ICO

    print("wrote", ICO, build_ico(ICO))
    if a.preview:
        preview(ICO, a.preview)
        print("preview", a.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
