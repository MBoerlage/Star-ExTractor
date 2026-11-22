"""About: the splash image with a credit roll under it. Opens from the logo in the top row; Esc or a click
closes it. The roll scrolls slowly upwards and loops; with reduced motion it is a plain scrollable list."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

from starex_reduce import APP_NAME

from . import theme

SPLASH = Path(__file__).parent / "about" / "splash.jpg"
WIDTH = 840
ROLL_H = 230
SPEED_PX = 1  # per tick
TICK_MS = 30  # ~33 px/s

# (style, text): title | sub | head | text | gap
CREDITS: tuple[tuple[str, str], ...] = (
    ("title", "STAR EXTRACTOR"),
    ("sub", "Spectroscopy reduction for low-resolution slit spectrographs"),
    ("gap", ""),
    ("head", "CREDITS"),
    ("gap", ""),
    ("label", "Stellar flux library"),
    ("text", "Pickles, A. J. (1998). “A Stellar Spectral Flux Library: 1150–25000 Å.”"),
    ("text", "Publications of the Astronomical Society of the Pacific, 110, 863–878."),
    ("gap", ""),
    ("label", "Reference star list"),
    ("text", "A subset, with amendments, of the Reference Star Finder database"),
    ("text", "by François Teyssier and Paolo Berardi (data from SIMBAD)"),
    ("gap", ""),
    ("label", "Spectrum submission and variable star data"),
    ("text", "AAVSO Spectroscopy Database (AVSpec) and Variable Star Index (VSX)"),
    ("gap", ""),
    ("label", "Target lookup"),
    ("text", "This application has made use of the SIMBAD database,"),
    ("text", "operated at CDS, Strasbourg, France."),
    ("gap", ""),
    ("label", "Built with"),
    ("text", "Python, PySide6 (Qt), NumPy, SciPy, Astropy, pyqtgraph"),
    ("gap", ""),
    ("label", "With thanks and inspiration"),
    ("text", "The STAROS Projects community, the AAVSO Spectroscopy SIG,"),
    ("text", "the Society for Astronomical Sciences (SAS), the BASS Project,"),
    ("text", "Tom Field (RSpec), the French amateur spectroscopy community,"),
    ("text", "and many others."),
    ("gap", ""),
    ("gap", ""),
    ("text", "Thank you to everyone who shares their data, tools and knowledge"),
    ("text", "with the amateur spectroscopy community – to the stars and beyond!"),
)


def _font(style: str) -> QFont:
    f = QFont(theme.UI_FAMILIES[0])
    f.setFamilies(list(theme.UI_FAMILIES))
    size = {"title": 22, "sub": 13, "head": 15, "label": 11, "text": 13}.get(style, 13)
    f.setPixelSize(size)
    f.setBold(style in ("title", "head"))
    if style in ("title", "head", "label"):
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2 if style != "label" else 1)
    return f


def colours() -> dict[str, str]:
    return {"title": theme.TEXT, "sub": theme.MUTED, "head": theme.AMBER, "label": theme.BLUE, "text": theme.TEXT}
LINE_H = {"title": 34, "sub": 22, "head": 26, "label": 20, "text": 21, "gap": 14}


class CreditRoll(QWidget):
    """The credits rolling upwards, centred, fading in at the bottom and out at the top; loops."""

    def __init__(self, animate: bool = True) -> None:
        super().__init__()
        self.setFixedSize(WIDTH, ROLL_H)
        self.content_h = sum(LINE_H.get(s, 21) for s, _ in CREDITS)
        self.offset = 0.0  # px the text has moved up, starting just below the view
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        if animate:
            self.timer.start()
        self.setToolTip("Hover to pause")

    def _tick(self) -> None:
        if self.underMouse():
            return
        self.offset += SPEED_PX
        if self.offset > ROLL_H + self.content_h:  # all of it has passed: start again from the bottom
            self.offset = 0.0
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        y = ROLL_H - self.offset
        for style, text in CREDITS:
            h = LINE_H.get(style, 21)
            if text and -h < y < ROLL_H:
                p.setFont(_font(style))
                p.setPen(QColor(colours().get(style, theme.TEXT)))
                p.drawText(QRectF(0, y, WIDTH, h), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)
            y += h
        for top in (True, False):  # soft edges
            g = QLinearGradient(0, 0 if top else ROLL_H, 0, 36 if top else ROLL_H - 36)
            c = QColor(theme.BG)
            g.setColorAt(0, c)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.fillRect(QRectF(0, 0 if top else ROLL_H - 36, WIDTH, 36), g)
        p.end()


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, animate: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setModal(True)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(0)
        img = QLabel()
        from PySide6.QtGui import QImage

        pm = QPixmap.fromImage(theme.tinted(QImage(str(SPLASH))))  # night / red: a black → accent ramp
        if not pm.isNull():
            img.setPixmap(pm.scaledToWidth(WIDTH, Qt.TransformationMode.SmoothTransformation))
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(img)
        if animate:
            self.roll: QWidget = CreditRoll()
            lay.addWidget(self.roll)
        else:  # reduced motion: the same credits as a still, scrollable list
            self.roll = self._static_list()
            lay.addWidget(self.roll)
        from starex_reduce import version

        self.version = QLabel(f"Version {version()}")  # for bug reports: the one place it shows after start-up
        self.version.setObjectName("aboutVersion")
        self.version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.version.setStyleSheet(f"color: {theme.MUTED}; background: transparent; padding-top: 8px;")
        lay.addWidget(self.version)
        self.setFixedWidth(WIDTH)

    @staticmethod
    def _static_list() -> QScrollArea:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setSpacing(2)
        for style, text in CREDITS:
            lbl = QLabel(text)
            lbl.setFont(_font(style))
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setStyleSheet(f"color: {colours().get(style, theme.TEXT)}; background: transparent;")
            lbl.setFixedHeight(LINE_H.get(style, 21))
            v.addWidget(lbl)
        sa = QScrollArea()
        sa.setWidget(box)
        sa.setWidgetResizable(True)
        sa.setFixedHeight(ROLL_H)
        sa.setFrameShape(QScrollArea.Shape.NoFrame)
        return sa

    def mousePressEvent(self, _e) -> None:  # a click anywhere closes it, like a splash
        self.accept()
