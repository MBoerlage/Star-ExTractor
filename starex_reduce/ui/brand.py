"""The prism logo (starex_reduce/resources): header widget, window/taskbar icon. Fixed brand colours, not
theme tokens. The small variant (thicker strokes) is used for anything drawn at 32 px or less."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
LOGO = RESOURCES / "logo.svg"
LOGO_SMALL = RESOURCES / "logo_small.svg"
ICO = RESOURCES / "starex.ico"
LOGO_ICON = RESOURCES / "logo_icon.svg"  # dark tile, for the .ico / window / taskbar
LOGO_ICON_SMALL = RESOURCES / "logo_icon_small.svg"  # 16 and 24 px: thicker, four stripes
ICON_SMALL_MAX_PX = 24
APP_USER_MODEL_ID = "StarExTractor.App"
SMALL_MAX_PX = 32


def svg_for(logical_px: int) -> Path:
    return LOGO_SMALL if logical_px <= SMALL_MAX_PX else LOGO


def icon_svg_for(px: int) -> Path:
    return LOGO_ICON_SMALL if px <= ICON_SMALL_MAX_PX else LOGO_ICON


def render(svg: Path, px: int) -> QImage:
    """The SVG drawn on a transparent px×px image."""
    from PySide6.QtSvg import QSvgRenderer

    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(str(svg))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    return img


def app_icon() -> QIcon:
    return QIcon(str(ICO))


def set_taskbar_id() -> None:
    """Windows: group the app under its own taskbar entry (and icon) instead of python.exe's."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass


class Logo(QWidget):
    """Header logo, 28×28 logical px, rendered at the screen's device pixel ratio. Click: About."""

    SIZE = 28
    clicked = Signal()

    def __init__(self, tooltip: str = "") -> None:
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._cache: tuple[float, QPixmap] | None = None

    def pixmap(self, dpr: float) -> QPixmap:
        if self._cache is None or self._cache[0] != dpr:
            pm = QPixmap.fromImage(render(svg_for(self.SIZE), round(self.SIZE * dpr)))
            pm.setDevicePixelRatio(dpr)
            self._cache = (dpr, pm)
        return self._cache[1]

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pixmap(self.devicePixelRatioF()))
        p.end()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
