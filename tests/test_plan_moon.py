"""Plan › Observing conditions: the Moon's path on the polar sky plot (ui/widgets/sky_polar.py). One computation,
two views: the polar plot draws the altitude chart's Moon samples."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starextractor.core import sky
from starextractor.ui import theme
from starextractor.ui.widgets.alt_chart import SERIES, AltitudeChart
from starextractor.ui.widgets.sky_polar import MOON_BELOW, SkyPolar, moon_pen

LAT, LON = 30.6714, -104.0225  # McDonald Observatory, a public site
TZ = timezone(timedelta(hours=-6))
VEGA = (279.2347, 38.7837)
MOON_UP = datetime(2026, 11, 20)  # the Moon is up for most of 18:00-08:00
MOON_DOWN = datetime(2026, 11, 9)  # new Moon: below the horizon over 20:00-04:00


def _plan(day: datetime, a=dtime(20, 0), b=dtime(6, 0), target=True) -> sky.SkyPlan:
    w = sky.Window.from_local(day, a, b, TZ)
    return sky.compute(LAT, LON, w, 25.0, *(VEGA if target else (None, None)))


def _views(qtbot: QtBot, plan: sky.SkyPlan, now: float | None = None) -> tuple[SkyPolar, AltitudeChart]:
    polar, chart = SkyPolar(), AltitudeChart()
    for w in (polar, chart):
        qtbot.addWidget(w)
    polar.set_plan(plan, now, True, TZ)
    chart.set_plan(plan, now)
    return polar, chart


def _rgb(w) -> np.ndarray:
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, : img.width(), [2, 1, 0]].astype(int)


def _near(rgb: np.ndarray, colour: QColor, tol: int = 18) -> np.ndarray:
    c = np.array([colour.red(), colour.green(), colour.blue()])
    return np.abs(rgb - c).max(axis=2) <= tol


def test_polar_moon_samples_are_the_altitude_charts(qtbot: QtBot) -> None:
    plan = _plan(MOON_UP)
    polar, chart = _views(qtbot, plan)
    x, y = chart.items["moon"].getData()
    drawn_chart = x[np.isfinite(y)]
    idx = np.concatenate(polar.moon_indices())
    assert np.array_equal(plan.unix[idx], drawn_chart)  # the same samples, not a second computation
    pts = np.concatenate(polar.moon_points())
    assert np.array_equal(pts[:, 0], y[np.isfinite(y)]) and np.array_equal(pts[:, 1], plan.moon_az[idx])
    assert all(np.all(r[:, 0] > 0) for r in polar.moon_points())  # only above the horizon
    assert ("moon", "moon") in polar.legend and ("moon now", "moon now") in polar.legend


def test_moon_ring_at_now_or_at_the_window_middle(qtbot: QtBot) -> None:
    plan = _plan(MOON_UP)
    polar, _ = _views(qtbot, plan)  # now = None: the window middle
    mid = (plan.window[0] + plan.window[1]) / 2
    assert polar.moon_mark is not None and polar.moon_mark[0] == pytest.approx(mid)
    assert polar.moon_mark[1] == pytest.approx(float(np.interp(mid, plan.unix, plan.moon_alt)))
    inside = float(plan.unix[40])
    polar.set_now(inside)
    assert polar.moon_mark[0] == inside and polar.moon_mark[1] == pytest.approx(float(plan.moon_alt[40]))
    polar.set_now(plan.unix[-1] + 86400.0)  # outside the range: back to the window middle
    assert polar.moon_mark[0] == pytest.approx(mid)


def test_moon_below_the_horizon_draws_nothing_and_says_so(qtbot: QtBot) -> None:
    plan = _plan(MOON_DOWN, dtime(22, 0), dtime(2, 0))
    assert not np.any(plan.moon_alt > 0)
    polar, chart = _views(qtbot, plan)
    assert polar.moon_points() == [] and polar.moon_mark is None
    assert ("note", MOON_BELOW) in polar.legend and all(k not in ("moon", "moon now") for k, _t in polar.legend)
    assert not np.any(np.isfinite(chart.items["moon"].getData()[1]))
    plot = _rgb(polar)[: polar.height() - 50]  # the plot, not the legend
    assert _near(plot, QColor(getattr(theme, SERIES["moon"][0]))).sum() == 0


def test_moon_path_without_a_target(qtbot: QtBot) -> None:
    polar, _ = _views(qtbot, _plan(MOON_UP, target=False))
    assert polar.path_points() == [] and polar.markers == []
    assert polar.moon_points() and ("moon", "moon") in polar.legend
    plot = _rgb(polar)[: polar.height() - 50]
    assert _near(plot, QColor(getattr(theme, SERIES["moon"][0]))).sum() > 40  # drawn


def test_target_keeps_its_dots_and_the_styles_differ(qtbot: QtBot) -> None:
    plan = _plan(MOON_UP)
    polar, _ = _views(qtbot, plan, now=float(plan.unix[30]))
    kinds = [k for k, _t in polar.legend]
    assert kinds[: kinds.index("moon")] == [k for k in kinds if k in ("event", "now")]  # existing entries first
    assert "now" in kinds and kinds[-2:] == ["moon", "moon now"]
    from PySide6.QtCore import Qt

    assert moon_pen().style() == Qt.PenStyle.DashLine == SERIES["moon"][1]  # target: solid
    assert SERIES["target"][1] == Qt.PenStyle.SolidLine


@pytest.mark.parametrize("palette", ["night", "red"])
def test_dark_room_palettes(qtbot: QtBot, palette: str) -> None:
    theme.apply(QApplication.instance(), palette)
    try:
        plan = _plan(MOON_UP)
        polar, _ = _views(qtbot, plan, now=float(plan.unix[30]))
        rgb = _rgb(polar)
        lit = rgb.sum(axis=2) > 30
        r, g, b = (rgb[..., i][lit] for i in range(3))
        if palette == "red":
            assert np.all(g <= 2) and np.all(b <= 2)
        else:
            assert np.all(g <= r + 2) and np.all(b <= r + 2)
        assert _near(rgb, QColor(getattr(theme, SERIES["moon"][0])), 10).sum() > 40  # the Moon is there
    finally:
        theme.apply(QApplication.instance(), "default")


def test_recalculation_still_under_budget(qtbot: QtBot) -> None:
    polar = SkyPolar()
    qtbot.addWidget(polar)
    w = sky.Window.from_local(MOON_UP, dtime(20, 0), dtime(6, 0), TZ)
    best = 1.0
    for _ in range(3):
        t = time.perf_counter()
        plan = sky.compute(LAT, LON, w, 25.0, *VEGA)
        polar.set_plan(plan, None, True, TZ)
        best = min(best, time.perf_counter() - t)
    assert best < 0.030
