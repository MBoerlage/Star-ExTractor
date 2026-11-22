"""Altitude chart for Plan › Observing conditions (pyqtgraph), palette tokens only.

X: local time over the window ± 2 h, ticks every 2 h (24-hour clock); Y: 0-90° with grid lines at 30° and 60°.
Series, told apart by line style as well as colour (needed in the red-filter palettes): target (solid, thick,
lightly filled below), Moon (dashed), minimum altitude (dotted), now (thin, only inside the range). Curves only
above the horizon. Background bands by Sun altitude: daylight, civil, nautical, astronomical twilight (in the night
palettes brightness steps only). The legend under the chart is `ChartLegend`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

from .. import theme

if TYPE_CHECKING:
    from starex_reduce.core.sky import SkyPlan

TICK_H = 2
CHART_H = 250
# series → (token, pen style, width); the legend draws the same
SERIES = {
    "target": ("BLUE", Qt.PenStyle.SolidLine, 2.2),
    "moon": ("SKY_TEXT", Qt.PenStyle.DashLine, 1.5),
    "min alt": ("RED", Qt.PenStyle.DotLine, 1.4),
    "now": ("WHITE", Qt.PenStyle.SolidLine, 0.8),
}
BAND_ALPHA = {"default": {"day": ("AMBER", 34), "civil": ("BLUE", 46), "nautical": ("BLUE", 30),
                          "astronomical": ("BLUE", 16)},
              "dark": {"day": ("TEXT", 40), "civil": ("TEXT", 28), "nautical": ("TEXT", 18),
                       "astronomical": ("TEXT", 9)}}  # night palettes: brightness steps only


def pen_for(series: str) -> QPen:
    token, style, width = SERIES[series]
    pen = pg.mkPen(theme.with_alpha(getattr(theme, token), 255), width=width)
    pen.setStyle(style)
    pen.setCosmetic(True)
    return pen


class TimeAxis(pg.AxisItem):
    """Unix seconds shown as local 24-hour clock times, a tick every TICK_H hours on the hour (every 3, 4 or 6 hours
    when the chart is too narrow for the labels)."""

    LABEL_PX = 46  # room one "HH:MM" label needs

    def __init__(self, tz=None) -> None:
        super().__init__("bottom")
        self.tz = tz

    # Called by Qt while painting: they must never raise (an exception there ends the process). An empty chart has
    # the range 0..1, i.e. 1970, which datetime cannot localise on Windows: no ticks outside a sane span.
    MIN_UNIX, MAX_UNIX, MAX_SPAN = 86400.0 * 365 * 20, 86400.0 * 365 * 200, 86400.0 * 4

    def tickValues(self, minVal, maxVal, size):
        try:
            if not (np.isfinite(minVal) and np.isfinite(maxVal) and self.MIN_UNIX < minVal < maxVal < self.MAX_UNIX
                    and maxVal - minVal < self.MAX_SPAN):
                return []
            step = TICK_H
            for step in sorted({TICK_H, 3, 4, 6}):
                if step >= TICK_H and (maxVal - minVal) / (step * 3600.0) * self.LABEL_PX <= max(size, 1):
                    break
            t = datetime.fromtimestamp(minVal).astimezone(self.tz).replace(minute=0, second=0, microsecond=0)
            t += timedelta(hours=(step - t.hour % step) % step)
            ticks = []
            while t.timestamp() <= maxVal:
                if t.timestamp() >= minVal:
                    ticks.append(t.timestamp())
                t += timedelta(hours=step)
            return [(step * 3600.0, ticks)]
        except (OSError, OverflowError, ValueError):
            return []

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            try:
                out.append(datetime.fromtimestamp(v).astimezone(self.tz).strftime("%H:%M"))
            except (OSError, OverflowError, ValueError):
                out.append("")
        return out


class AltitudeChart(pg.PlotWidget):
    def __init__(self, parent: QWidget | None = None, tz=None) -> None:
        axis = TimeAxis(tz)  # never set attributes before the Qt base class exists
        super().__init__(parent, axisItems={"bottom": axis}, enableMenu=False)
        self.axis = axis
        self.setFixedHeight(CHART_H)  # compact: as tall as the polar plot
        pi = self.getPlotItem()
        pi.hideButtons()
        pi.setMouseEnabled(x=False, y=False)
        pi.getViewBox().setDefaultPadding(0.0)
        pi.setYRange(0, 90, padding=0)
        pi.getAxis("left").setTicks([[(0, "0°"), (30, "30°"), (60, "60°"), (90, "90°")]])
        self.plan: SkyPlan | None = None
        self.now: float | None = None
        self.items: dict[str, object] = {}  # series → item, as drawn (tests, legend)
        self.bands: list[tuple[float, float, str]] = []
        self._style()

    def _style(self) -> None:
        self.setBackground(theme.with_alpha(theme.BG, 255))
        for side in ("left", "bottom"):
            ax = self.getPlotItem().getAxis(side)
            ax.setPen(pg.mkPen(theme.with_alpha(theme.LINE, 255)))
            ax.setTextPen(pg.mkPen(theme.with_alpha(theme.MUTED, 255)))
        self.getPlotItem().showGrid(x=False, y=True, alpha=0.25)

    def retheme(self) -> None:
        """The palette changed: pens, bands and background from the new tokens (no recompute)."""
        self._style()
        self.set_plan(self.plan, self.now)

    def set_plan(self, plan: SkyPlan | None, now: float | None) -> None:
        self.plan, self.now = plan, now
        pi = self.getPlotItem()
        for it in list(self.items.values()) + getattr(self, "_band_items", []):
            pi.removeItem(it)
        self.items, self._band_items, self.bands = {}, [], []
        if plan is None:
            return
        from starex_reduce.core.sky import twilight_bands

        x = plan.unix
        pi.setXRange(float(x[0]), float(x[-1]), padding=0)
        dark = "dark" if theme.is_dark_room() else "default"
        for a, b, kind in twilight_bands(x, plan.sun_alt):
            if kind == "night":
                continue
            token, alpha = BAND_ALPHA[dark][kind]
            region = pg.LinearRegionItem(values=(a, b), movable=False,
                                         brush=theme.with_alpha(getattr(theme, token), alpha))
            for line in region.lines:
                line.setPen(pg.mkPen(None))
            region.setZValue(-20)
            pi.addItem(region)
            self._band_items.append(region)
            self.bands.append((a, b, kind))
        moon = np.where(plan.moon_alt > 0, plan.moon_alt, np.nan)
        self.items["moon"] = pi.plot(x, moon, pen=pen_for("moon"), connect="finite")
        if plan.target_alt is not None:
            fill = pg.PlotDataItem(x, np.clip(plan.target_alt, 0, None), pen=pg.mkPen(None), fillLevel=0,
                                   brush=theme.with_alpha(theme.BLUE, 38))
            fill.setZValue(-5)
            pi.addItem(fill)
            self.items["target fill"] = fill
            alt = np.where(plan.target_alt > 0, plan.target_alt, np.nan)
            self.items["target"] = pi.plot(x, alt, pen=pen_for("target"), connect="finite")
        line = pg.InfiniteLine(pos=plan.min_alt, angle=0, movable=False, pen=pen_for("min alt"))
        pi.addItem(line)
        self.items["min alt"] = line
        self._place_now()

    def set_now(self, now: float | None) -> None:
        self.now = now
        self._place_now()

    def _place_now(self) -> None:
        pi = self.getPlotItem()
        old = self.items.pop("now", None)
        if old is not None:
            pi.removeItem(old)
        p = self.plan
        if p is not None and self.now is not None and p.unix[0] <= self.now <= p.unix[-1]:
            line = pg.InfiniteLine(pos=self.now, angle=90, movable=False, pen=pen_for("now"))
            pi.addItem(line)
            self.items["now"] = line

    def series_shown(self) -> list[str]:
        return [s for s in SERIES if s in self.items]


class Swatch(QWidget):
    """A short line in a series' colour and style (the legend)."""

    def __init__(self, series: str) -> None:
        super().__init__()
        self.series = series
        self.setFixedSize(26, 12)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = pen_for(self.series)
        pen.setCosmetic(False)
        p.setPen(pen)
        p.drawLine(QPointF(1, 6), QPointF(25, 6))
        p.end()


class ChartLegend(QWidget):
    """target, moon, min alt, now: a swatch and a name each (labels from LABELS)."""

    LABELS: ClassVar[dict[str, str]] = {"target": "Target", "moon": "Moon", "min alt": "Min altitude", "now": "Now"}

    def __init__(self) -> None:
        super().__init__()
        from .flow import FlowLayout, add, unit

        lay = FlowLayout(self, hspacing=14, vspacing=4)  # wraps when the chart card is narrow
        self.entries: list[tuple[str, str]] = []
        for series, text in self.LABELS.items():
            lbl = QLabel(text)
            lbl.setProperty("role", "muted")
            add(lay, unit(Swatch(series), lbl))
            self.entries.append((series, text))

    def retheme(self) -> None:
        for sw in self.findChildren(Swatch):
            sw.update()
