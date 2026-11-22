"""Start-up splash (docs/SPEC.md › Start-up). Imports only the stdlib and PySide6, never an app module that pulls in
numpy, scipy, astropy or pyqtgraph: app.py shows it right after QApplication exists, then imports the rest.

The art is drawn at its native pixel size from pre-scaled files (tools/make_splash.py): 1x below a device pixel
ratio of 1.5, else 2x. The version and one status line are painted over the bottom. In the night and red
palettes the image is dimmed. The splash never takes focus and closes as soon as the main window is on screen
(150 ms fade, none with reduced motion). The frozen build also has the PyInstaller bootloader splash, which shows
the same 1x image before Python starts; `close_bootloader_splash` ends it once this one is visible.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPointF, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGlyphRun,
    QGuiApplication,
    QPainter,
    QPixmap,
    QRawFont,
    QScreen,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import theme  # tokens only (Qt + re, a few ms); set_palette is not called here

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
IMAGES = {1: RESOURCES / "splash.png", 2: RESOURCES / "splash@2x.png"}
FADE_MS = 150
DIM_ALPHA = 0.6  # night / red: a black overlay at this opacity, so a dark-adapted eye is not hit by the art
MAX_PROCESS_EVENTS = 4  # the splash never pumps the event loop more often than this
ENV_OFF = "STAREX_NO_SPLASH"  # tools (screenshots, perf) set this: no splash
ENV_FORCE = "STAREX_SPLASH_FORCE"  # tests set this to show it on the offscreen platform
OFF_ARGS = frozenset({"--no-splash", "--selftest", "--selftest-gui", "--make-example", "--version", "-h", "--help"})
STATUS_LOADING, STATUS_WINDOW, STATUS_READY = "Loading libraries", "Preparing window", "Ready"
WARNINGS: list[str] = []  # shown in the Log panel once the window exists (a missing image, …)
TEXT_PX = 12
# The text is drawn from the font file itself (QRawFont): a QFont would make Qt enumerate every installed font
# first (~50 ms here), which theme.apply pays later anyway. Without the file, a plain QFont is used.
FONT_FILES = (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",)
_raw_font: QRawFont | None = None


class SplashImageMissing(OSError):
    pass


# ---- the cheap settings read ------------------------------------------------------------------------------------
def settings_file() -> Path:
    """settings.json as core/settings.py places it, without importing it or creating any folder."""
    override = os.environ.get("STAREXTRACTOR_HOME")
    if override:
        return Path(override) / "settings.json"
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    path = appdata / "StarExtractor" / "settings.json"
    legacy = appdata / "starex_reduce" / "settings.json"  # before the 2026-11-18 rename; copied on first start
    return legacy if not path.exists() and legacy.exists() else path


def read_prefs(path: Path | None = None) -> tuple[str, bool]:
    """(palette, show splash) from the settings file: only ui.palette and ui.splash are looked at. A missing or
    corrupt file gives ("default", True)."""
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
        ui = data.get("ui") if isinstance(data, dict) else None
        if not isinstance(ui, dict):
            return "default", True
        palette = ui.get("palette")
        splash = ui.get("splash", True)
        return (palette if isinstance(palette, str) and palette else "default",
                splash if isinstance(splash, bool) else True)
    except (OSError, ValueError, TypeError, AttributeError):
        return "default", True


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def wanted(argv: Sequence[str], setting_on: bool) -> tuple[bool, str]:
    """Show the splash? (yes/no, the reason when not). Never under tests, the offscreen platform (unless forced),
    the command-line modes, --no-splash, the tools' variable or the Settings switch."""
    if OFF_ARGS.intersection(argv):
        return False, "command-line option"
    if not _truthy(ENV_FORCE):  # the tests' switch wins over the automatic ones below
        if _truthy(ENV_OFF):
            return False, ENV_OFF
        if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
            return False, "offscreen platform"
        if "PYTEST_CURRENT_TEST" in os.environ:
            return False, "test run"
    if not setting_on:
        return False, "turned off in Settings"
    return True, ""


# ---- the widget ------------------------------------------------------------------------------------------------
def screen_for_cursor() -> QScreen:
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def scale_for(ratio: float) -> int:
    return 2 if ratio >= 1.5 else 1


class Splash(QWidget):
    """Frameless, on top, never focused. `set_status` repaints in place; `finish` fades it out."""

    closed = Signal()

    def __init__(self, version: str, palette: str = "default", ratio: float | None = None,
                 screen: QScreen | None = None, scale: int | None = None) -> None:
        flags = (Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.version = version
        self.status = STATUS_LOADING
        self.palette_name = palette
        self.dimmed = palette != "default"
        self.pumps = 0  # processEvents calls made (≤ MAX_PROCESS_EVENTS)
        self._fade: QPropertyAnimation | None = None
        self._closed = False
        scr = screen or screen_for_cursor()
        self.ratio = float(ratio if ratio is not None else (scr.devicePixelRatio() if scr else 1.0))
        self.scale = scale or scale_for(self.ratio)  # scale=1 matches the bootloader splash pixel for pixel
        path = IMAGES[self.scale]
        pm = QPixmap(str(path))
        if pm.isNull():
            raise SplashImageMissing(f"splash image missing or unreadable: {path}")
        pm.setDevicePixelRatio(self.ratio)  # one image pixel per device pixel: no scaling at paint time
        self.pixmap = pm
        self.setFixedSize(math.ceil(pm.width() / self.ratio), math.ceil(pm.height() / self.ratio))
        if scr is not None:
            g = self.frameGeometry()
            g.moveCenter(scr.geometry().center())  # the whole screen, as the bootloader splash centres
            self.move(g.topLeft())

    # painting
    def paintEvent(self, _e) -> None:
        tok = theme.PALETTES.get(self.palette_name, theme.PALETTES["default"])
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(tok["BG"]))
        p.drawPixmap(0, 0, self.pixmap)
        if self.dimmed:
            p.fillRect(self.rect(), theme.with_alpha(tok["BG"], round(255 * DIM_ALPHA)))
        text = theme.with_alpha(tok["MUTED"] if self.dimmed else tok["TEXT"], 190 if self.dimmed else 170)
        margin, baseline = 14.0, self.height() - 13.0
        for colour, d in ((theme.with_alpha(tok["BG"], 170), 1.0), (text, 0.0)):  # 1 px shadow: legible on the art
            p.setPen(colour)
            self._text(p, f"Version {self.version}", margin + d, baseline + d, right=False)
            self._text(p, self.status, self.width() - margin + d, baseline + d, right=True)
        p.end()

    def _text(self, p: QPainter, text: str, x: float, baseline: float, right: bool) -> None:
        rf = raw_font()
        if rf is None:
            f = QFont("Segoe UI")
            f.setPixelSize(TEXT_PX)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QPointF(x - w if right else x, baseline), text)
            return
        idx = rf.glyphIndexesForString(text)
        pos, cx = [], 0.0
        for a in rf.advancesForGlyphIndexes(idx):
            pos.append(QPointF(cx, 0.0))
            cx += a.x()
        run = QGlyphRun()
        run.setRawFont(rf)
        run.setGlyphIndexes(idx)
        run.setPositions(pos)
        p.drawGlyphRun(QPointF(x - cx if right else x, baseline), run)

    def _pump(self) -> None:
        if self.pumps < MAX_PROCESS_EVENTS:
            self.pumps += 1
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    # life cycle
    def show_now(self) -> None:
        """Show and paint it now (one processEvents), before anything heavy is imported."""
        self.show()
        self.raise_()
        self.repaint()
        self._pump()

    def set_status(self, text: str, pump: bool = True) -> None:
        self.status = text
        if self.isVisible():
            self.repaint()  # synchronous: no event loop needed for the text itself
            if pump:
                self._pump()

    def finish(self, fade: bool) -> None:
        """The main window is on screen: "Ready", then fade out (or close at once). Never blocks."""
        if self._closed:
            return
        self.set_status(STATUS_READY, pump=False)
        if not fade:
            self.close_now()
            return
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close_now)
        self._fade.start()

    def close_now(self) -> None:
        """Close immediately (also before any dialog, so the splash never covers one)."""
        if self._closed:
            return
        self._closed = True
        if self._fade is not None:
            self._fade.stop()
        self.close()
        self.closed.emit()

    def is_closed(self) -> bool:
        return self._closed

    def after_close(self, fn: Callable[[], None]) -> None:
        """Run fn once the splash is gone (at once when it already is)."""
        if self._closed:
            fn()
        else:
            self.closed.connect(fn)


def raw_font() -> QRawFont | None:
    """Segoe UI straight from its file (cached), or None when it is not there."""
    global _raw_font
    if _raw_font is None:
        for f in FONT_FILES:
            if f.is_file():
                rf = QRawFont(str(f), TEXT_PX)
                if rf.isValid():
                    _raw_font = rf
                    break
    return _raw_font


def start(argv: Sequence[str], version: str) -> Splash | None:
    """Show the splash when wanted; None when not (or when the image is missing: a warning, no crash)."""
    palette, on = read_prefs()
    ok, _why = wanted(argv, on)
    if not ok:
        return None
    try:
        s = Splash(version, palette, scale=1 if bootloader_splash_alive() else None)
    except SplashImageMissing as e:
        WARNINGS.append(str(e))
        print(f"warning: {e}", file=sys.stderr) if sys.stderr else None
        return None
    s.show_now()
    return s


def bootloader_splash_alive() -> bool:
    """The frozen build's PyInstaller splash (stage 1) is on screen."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        import pyi_splash  # type: ignore[import-not-found]

        return bool(pyi_splash.is_alive())
    except Exception:  # noqa: BLE001 - built without it
        return False


def close_bootloader_splash() -> None:
    """The frozen build's PyInstaller splash (stage 1): close it. No-op in a source run or without it."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import pyi_splash  # type: ignore[import-not-found]

        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:  # noqa: BLE001 - built without the bootloader splash, or it is already gone
        pass
