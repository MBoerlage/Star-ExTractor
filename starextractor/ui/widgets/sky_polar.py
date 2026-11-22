"""Polar sky plot for Plan › Observing conditions: a QPainter widget, palette tokens only (read at paint time, so a
palette switch needs only update()).

N at the top, S at the bottom, E at the right, W at the left; zenith in the middle, the outer ring is the horizon;
rings at 30° and 60° with small labels; the minimum altitude as a dashed ring. The target's path over the window
(every 5 min, only above the horizon) as a solid line; an accent dot with its time where it rises above or sets
below the minimum altitude in the window, and a bright dot for now when now is inside the plotted range. The Moon's
track over the whole plotted range as a dashed line (the altitude chart's Moon samples: one computation, two views;
only above the horizon) and a hollow ring where it is now (or at the window middle when now is outside the range).
The legend (rises / sets / current pos / moon / moon now) is drawn under the plot. The reference star is not shown.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from .alt_chart import SERIES

if TYPE_CHECKING:
    from starextractor.core.sky import SkyPlan

SIDE = 240  # the preferred side; the plot is square and follows its card's width (SIDE_MIN at least)
SIDE_MIN = 180
LEGEND_H = 50
NO_LOCATION = "Set your location to see the sky plots"
NO_TARGET = "Enter the target position to see its path"
MOON_BELOW = "Moon below the horizon"


def moon_colour():
    return theme.with_alpha(getattr(theme, SERIES["moon"][0]), 255)


def moon_pen(width: float = 1.6) -> QPen:
    """The altitude chart's Moon series: the same token and the same dashed style."""
    return QPen(moon_colour(), width, SERIES["moon"][1], Qt.PenCapStyle.FlatCap)


class SkyPolar(QWidget):
    side_changed = Signal(int)  # the altitude chart beside it takes the same height

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(SIDE_MIN)
        self.setFixedHeight(SIDE + LEGEND_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.plan: SkyPlan | None = None
        self.now: float | None = None
        self.has_location = False
        self.tz = None
        # (kind: event | now | moon | moon now | note, text) as drawn, for tests and screenshots
        self.legend: list[tuple[str, str]] = []
        self.markers: list[tuple[str, float, float]] = []  # (kind, alt, az) of the target
        self.moon_mark: tuple[float, float, float] | None = None  # (unix, alt, az) of the hollow ring

    def sizeHint(self) -> QSize:
        return QSize(SIDE, SIDE + LEGEND_H)

    def side(self) -> int:
        """The plot is a square as wide as the widget; the legend sits under it."""
        return self.width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.height() != self.side() + LEGEND_H:
            self.setFixedHeight(self.side() + LEGEND_H)
        self.side_changed.emit(self.side())

    def set_plan(self, plan: SkyPlan | None, now: float | None, has_location: bool, tz=None) -> None:
        self.plan, self.now, self.has_location, self.tz = plan, now, has_location, tz
        self._build_markers()
        self.update()

    def set_now(self, now: float | None) -> None:
        self.now = now
        self._build_markers()
        self.update()

    # the model of what is drawn -----------------------------------------------------------------------------------
    def path_points(self) -> list[np.ndarray]:
        """(alt, az) runs of the target inside the window and above the horizon."""
        p = self.plan
        if p is None or p.target_alt is None:
            return []
        keep = p.in_window() & (p.target_alt > 0)
        idx = np.nonzero(keep)[0]
        if not len(idx):
            return []
        runs = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
        return [np.column_stack([p.target_alt[r], p.target_az[r]]) for r in runs if len(r) > 1]

    def moon_indices(self) -> list[np.ndarray]:
        """Runs of sample indices where the Moon is above the horizon over the plotted range: the same samples
        (plan.unix, moon_alt > 0) as the altitude chart's Moon curve."""
        p = self.plan
        if p is None or not self.has_location:
            return []
        idx = np.nonzero(p.moon_alt > 0)[0]
        if not len(idx):
            return []
        runs = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
        return [r for r in runs if len(r) > 1]

    def moon_points(self) -> list[np.ndarray]:
        """(alt, az) runs of the Moon above the horizon."""
        p = self.plan
        if p is None:
            return []
        return [np.column_stack([p.moon_alt[r], p.moon_az[r]]) for r in self.moon_indices()]

    def _build_markers(self) -> None:
        self.legend, self.markers, self.moon_mark = [], [], None
        p = self.plan
        if p is None or not self.has_location:
            return
        if p.target_alt is not None:
            self._target_markers()
        if not self.moon_indices():
            self.legend.append(("note", MOON_BELOW))
            return
        self.legend.append(("moon", "moon"))
        inside = self.now is not None and p.unix[0] <= self.now <= p.unix[-1]
        t = float(self.now) if inside else (p.window[0] + p.window[1]) / 2.0  # type: ignore[arg-type]
        alt, az = self._interp(t, p.moon_alt, p.moon_az)
        if alt > 0:
            self.moon_mark = (t, alt, az)
            self.legend.append(("moon now", "moon now"))

    def _target_markers(self) -> None:
        from starextractor.core.sky import hhmm

        p = self.plan
        assert p is not None and p.target_alt is not None and p.target_az is not None
        for t, kind in p.events:
            alt, az = self._interp(t, p.target_alt, p.target_az)
            word = "rises" if kind == "rise" else "sets"
            arrow = "↑" if kind == "rise" else "↓"
            self.markers.append(("event", alt, az))
            self.legend.append(("event", f"{arrow} {word} {hhmm(t, self.tz)}"))
        if self.now is not None and p.unix[0] <= self.now <= p.unix[-1]:
            alt, az = self._interp(self.now, p.target_alt, p.target_az)
            if alt > 0:
                self.markers.append(("now", alt, az))
                self.legend.append(("now", "current pos"))

    def _interp(self, t: float, alt_s: np.ndarray, az_s: np.ndarray) -> tuple[float, float]:
        p = self.plan
        assert p is not None
        alt = float(np.interp(t, p.unix, alt_s))
        s = float(np.interp(t, p.unix, np.sin(np.radians(az_s))))
        c = float(np.interp(t, p.unix, np.cos(np.radians(az_s))))
        return alt, math.degrees(math.atan2(s, c)) % 360.0

    # drawing ------------------------------------------------------------------------------------------------------
    def _geometry(self) -> tuple[QPointF, float]:
        s = self.side()
        return QPointF(s / 2, s / 2), s / 2 - 18

    def to_xy(self, alt: float, az: float) -> QPointF:
        c, r = self._geometry()
        rr = r * (90.0 - max(alt, 0.0)) / 90.0
        a = math.radians(az)
        return QPointF(c.x() + rr * math.sin(a), c.y() - rr * math.cos(a))  # E right, W left

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, r = self._geometry()
        small = QFont(theme.ui_font_family())
        small.setPixelSize(10)
        p.setFont(small)
        p.setPen(QPen(theme.with_alpha(theme.BORDER, 255), 1.2))
        p.setBrush(theme.with_alpha(theme.BG, 255))
        p.drawEllipse(c, r, r)  # the horizon
        p.setBrush(Qt.BrushStyle.NoBrush)
        for alt in (30, 60):
            rr = r * (90 - alt) / 90
            p.setPen(QPen(theme.with_alpha(theme.LINE, 255), 1))
            p.drawEllipse(c, rr, rr)
            p.setPen(theme.with_alpha(theme.MUTED, 200))
            p.drawText(QPointF(c.x() + 3, c.y() - rr - 2), f"{alt}°")
        if self.plan is not None and self.has_location:
            rr = r * (90 - self.plan.min_alt) / 90
            pen = QPen(theme.with_alpha(theme.RED, 230), 1.2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawEllipse(c, rr, rr)
        p.setPen(QPen(theme.with_alpha(theme.LINE, 255), 1))
        p.drawLine(QPointF(c.x(), c.y() - 3), QPointF(c.x(), c.y() + 3))
        p.drawLine(QPointF(c.x() - 3, c.y()), QPointF(c.x() + 3, c.y()))
        big = QFont(theme.ui_font_family())
        big.setPixelSize(12)
        big.setBold(True)
        p.setFont(big)
        p.setPen(theme.with_alpha(theme.TEXT, 255))
        for letter, (dx, dy) in {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}.items():
            box = QRectF(c.x() + dx * (r + 9) - 8, c.y() + dy * (r + 9) - 8, 16, 16)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, letter)
        p.setFont(small)
        if not self.has_location:
            self._prompt(p, NO_LOCATION)
        else:
            self._draw_moon(p)  # under the target: the target path stays on top
            if self.plan is None or self.plan.target_alt is None:
                self._prompt(p, NO_TARGET)
            else:
                self._draw_path(p)
        self._draw_legend(p)
        p.end()

    def _prompt(self, p: QPainter, text: str) -> None:
        p.setPen(theme.with_alpha(theme.MUTED, 255))
        c, r = self._geometry()
        p.drawText(QRectF(c.x() - r * 0.7, c.y() + 8, r * 1.4, 40),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, text)

    def _draw_moon(self, p: QPainter) -> None:
        p.setPen(moon_pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        for run in self.moon_points():
            path = QPainterPath(self.to_xy(*run[0]))
            for alt, az in run[1:]:
                path.lineTo(self.to_xy(alt, az))
            p.drawPath(path)
        if self.moon_mark is not None:
            self._moon_ring(p, self.to_xy(self.moon_mark[1], self.moon_mark[2]), 5.5)

    @staticmethod
    def _moon_ring(p: QPainter, pt: QPointF, r: float) -> None:
        """A hollow ring (no fill), unlike the target's filled dots; a bg halo keeps it apart from the lines."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.with_alpha(theme.BG, 255), 4.0))
        p.drawEllipse(pt, r, r)
        p.setPen(QPen(moon_colour(), 2.0))
        p.drawEllipse(pt, r, r)

    def _draw_path(self, p: QPainter) -> None:
        p.setPen(QPen(theme.with_alpha(theme.BLUE, 255), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for run in self.path_points():
            path = QPainterPath(self.to_xy(*run[0]))
            for alt, az in run[1:]:
                path.lineTo(self.to_xy(alt, az))
            p.drawPath(path)
        for kind, alt, az in self.markers:
            pt = self.to_xy(alt, az)
            colour = theme.AMBER if kind == "event" else theme.WHITE
            p.setPen(QPen(theme.with_alpha(theme.BG, 255), 1.5))
            p.setBrush(theme.with_alpha(colour, 255))
            p.drawEllipse(pt, 4.5, 4.5)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_legend(self, p: QPainter) -> None:
        x, y, side = 6.0, self.side() + 6.0, self.side()
        for kind, text in self.legend:
            w = p.fontMetrics().horizontalAdvance(text)
            sample = {"moon": 18.0, "note": 0.0}.get(kind, 8.0)
            if x > 6.0 and x + sample + 4 + w > side - 4:  # wrap to the next line
                x, y = 6.0, y + 14
            if kind == "moon":
                p.setPen(moon_pen())
                p.drawLine(QPointF(x, y + 7), QPointF(x + sample, y + 7))
            elif kind == "moon now":
                self._moon_ring(p, QPointF(x + 4, y + 7), 3.5)
            elif kind in ("event", "now"):
                colour = theme.AMBER if kind == "event" else theme.WHITE
                p.setPen(QPen(theme.with_alpha(theme.BG, 255), 1))
                p.setBrush(theme.with_alpha(colour, 255))
                p.drawEllipse(QPointF(x + 4, y + 7), 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(theme.with_alpha(theme.MUTED, 255))
            tx = x + sample + (4 if sample else 0)
            p.drawText(QPointF(tx, y + 11), text)
            x = tx + w + 12
