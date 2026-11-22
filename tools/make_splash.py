"""Rebuild the start-up splash images from the source art (Qt only, no Pillow).

    python tools/make_splash.py [--source "Prism casting a horizontal stellar spectrum.png"]

Writes starextractor/resources/splash.png (1x, 800 px wide) and splash@2x.png (2x, 1600 px wide): the art scaled
once here with a smooth filter, never at start-up. The 1x file is also the PyInstaller bootloader splash
(packaging/starex_extractor.spec). The artwork itself is not changed; version and status text are drawn over it
at run time (ui/splash.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Prism casting a horizontal stellar spectrum.png"  # the same art as Help › About
OUT = ROOT / "starextractor" / "resources"
WIDTH_1X = 800


def build(source: Path = SOURCE, out: Path = OUT) -> list[Path]:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    img = QImage(str(source))
    if img.isNull():
        raise SystemExit(f"cannot read {source}")
    img = img.convertToFormat(QImage.Format.Format_RGB32)  # opaque art: no alpha channel in the files
    written = []
    for scale, name in ((1, "splash.png"), (2, "splash@2x.png")):
        w = WIDTH_1X * scale
        if w > img.width():
            raise SystemExit(f"{source.name} is {img.width()} px wide; {w} px needed for the {scale}x image")
        s = img.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        if s.height() % scale:  # 2x must be exactly twice 1x so both draw at the same logical size
            s = s.copy(0, 0, s.width(), s.height() - s.height() % scale)
        path = out / name
        if not s.save(str(path), "PNG", 0):  # quality 0 = strongest zlib compression
            raise SystemExit(f"cannot write {path}")
        written.append(path)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, default=SOURCE)
    args = ap.parse_args()
    for p in build(args.source):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
