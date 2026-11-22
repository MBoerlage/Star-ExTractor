"""A short confetti burst over one widget after a successful save: a transparent child overlay painted with
QPainter, driven by a QTimer, removed after about a second. Clicks pass through; only the overlay area
repaints. Colours from the theme (default: accent blue, amber, success green, white; night / red: accent tints)."""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .. import theme
from ..motion import reduced_motion as reduced_motion  # re-exported: older callers import it from here

DURATION_S = 1.0
FRAME_MS = 16
N_PARTICLES = 50

GRAVITY = 900.0  # px/s²


def colours() -> tuple[str, ...]:
    """Default: blue, amber, green, white. Night / red: tints of the accent (no light, no other hue)."""
    if theme.is_dark_room():
        return (theme.AMBER, theme.AMBER_HOVER, theme.SPECTRUM_TEXT, theme.DONE_TEXT)
    return (theme.BLUE, theme.AMBER, theme.GREEN, theme.WHITE)


class ConfettiOverlay(QWidget):
    """Covers `host`; the burst starts at `origin` (host coordinates). Deletes itself when done."""

    def __init__(self, host: QWidget, origin: QPointF, seed: int | None = None) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(host.rect())
        rng = random.Random(seed)
        self.particles = []
        for _ in range(N_PARTICLES):
            a = rng.uniform(-math.pi * 0.95, -math.pi * 0.05)  # upwards fan
            v = rng.uniform(180, 420)
            self.particles.append({
                "x": origin.x(), "y": origin.y(), "vx": v * math.cos(a), "vy": v * math.sin(a),
                "size": rng.uniform(3, 6), "rot": rng.uniform(0, 360), "spin": rng.uniform(-540, 540),
                "colour": QColor(rng.choice(colours()))})
        self.frame_times: list[float] = []  # for the perf check
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._step)
        self.show()
        self.raise_()
        self._timer.start()

    def _step(self) -> None:
        now = time.perf_counter()
        dt = now - self._last
        self._last = now
        self.frame_times.append(now)
        if now - self._t0 >= DURATION_S:
            self._timer.stop()
            self.hide()
            self.deleteLater()
            return
        for p in self.particles:
            p["vy"] += GRAVITY * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["rot"] += p["spin"] * dt
        self.update()

    def paintEvent(self, _e) -> None:
        fade = max(0.0, 1.0 - (time.perf_counter() - self._t0) / DURATION_S)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for q in self.particles:
            c = QColor(q["colour"])
            c.setAlphaF(fade)
            p.save()
            p.translate(q["x"], q["y"])
            p.rotate(q["rot"])
            p.setBrush(c)
            s = q["size"]
            p.drawRect(QRectF(-s / 2, -s / 4, s, s / 2))
            p.restore()
        p.end()
