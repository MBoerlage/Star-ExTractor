"""The prism logo (starex_reduce/resources): header widget, window/taskbar icon. The brand colours in the default
palette; night / red draw it monochrome (prism and beam in the text colour, the fan in tints of the accent). The small
variant (thicker strokes) is used for anything drawn at 32 px or less."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .taskbar import APP_USER_MODEL_ID as APP_USER_MODEL_ID  # re-exported (tests, tools)
from .taskbar import set_taskbar_id as set_taskbar_id

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
LOGO = RESOURCES / "logo.svg"
LOGO_SMALL = RESOURCES / "logo_small.svg"
ICO = RESOURCES / "starex.ico"
LOGO_ICON = RESOURCES / "logo_icon.svg"  # dark tile, for the .ico / window / taskbar
LOGO_ICON_SMALL = RESOURCES / "logo_icon_small.svg"  # 16 and 24 px: thicker, four stripes
ICON_SMALL_MAX_PX = 24
SMALL_MAX_PX = 32


def svg_for(logical_px: int) -> Path:
    return LOGO_SMALL if logical_px <= SMALL_MAX_PX else LOGO


def icon_svg_for(px: int) -> Path:
    return LOGO_ICON_SMALL if px <= ICON_SMALL_MAX_PX else LOGO_ICON


PRISM_COLOURS = ("#B5D4F4", "#E6F1FB", "#FFFFFF")  # the prism fill and stroke, the incoming beam
FAN_COLOURS = ("#E24B4A", "#EF9F27", "#FAC775", "#639922", "#378ADD", "#7F77DD")  # the spectrum fan, red first


def svg_text(svg: Path) -> str:
    """The logo in the active palette: unchanged in default; night / red monochrome."""
    from PySide6.QtGui import QColor

    from . import theme

    text = svg.read_text(encoding="utf-8")
    if not theme.is_dark_room():
        return text
    for c in PRISM_COLOURS:
        text = re.sub(c, theme.TEXT, text, flags=re.IGNORECASE)
    a, b = QColor(theme.AMBER), QColor(theme.BG)
    for i, c in enumerate(FAN_COLOURS):  # the accent fading towards the background: brightest first
        t = 1.0 - 0.13 * i
        tint = QColor.fromRgbF(a.redF() * t + b.redF() * (1 - t), a.greenF() * t + b.greenF() * (1 - t),
                               a.blueF() * t + b.blueF() * (1 - t))
        text = re.sub(c, tint.name(), text, flags=re.IGNORECASE)
    return text


def render(svg: Path, px: int) -> QImage:
    """The SVG drawn on a transparent px×px image."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(svg_text(svg).encode("utf-8")))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    return img


def app_icon() -> QIcon:
    return QIcon(str(ICO))


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
        self._cache: tuple[tuple[float, str], QPixmap] | None = None

    def pixmap(self, dpr: float) -> QPixmap:
        from . import theme

        if self._cache is None or self._cache[0] != (dpr, theme.palette):
            pm = QPixmap.fromImage(render(svg_for(self.SIZE), round(self.SIZE * dpr)))
            pm.setDevicePixelRatio(dpr)
            self._cache = ((dpr, theme.palette), pm)
        return self._cache[1]

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pixmap(self.devicePixelRatioF()))
        p.end()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
