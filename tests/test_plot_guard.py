"""Wheel zoom can never drive a plot to a zero/NaN range ("Cannot set range [nan, nan]")."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starex_reduce.ui.widgets.plot_guard import pg, span_ok


def _wheel(pw: pg.PlotWidget, item, delta: int, n: int) -> None:
    c = pw.mapFromScene(item.sceneBoundingRect().center())
    for _ in range(n):
        ev = QWheelEvent(QPointF(c), QPointF(pw.mapToGlobal(c)), QPoint(0, 0), QPoint(0, delta),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(pw.viewport(), ev)


def _finite(vb: pg.ViewBox) -> bool:
    return all(math.isfinite(v) for r in vb.viewRange() for v in r) and all(
        span_ok(*r) for r in vb.viewRange())


def test_span_limits() -> None:
    assert span_ok(0, 1) and span_ok(6500, 6500.01)
    assert not span_ok(0, 0) and not span_ok(6500, 6500.000001) and not span_ok(0, 1e12)
    assert not span_ok(float("nan"), 1)


def test_endless_wheel_on_plot_and_axes_stays_finite(qtbot: QtBot) -> None:
    pw = pg.PlotWidget()
    qtbot.addWidget(pw)
    pw.plot([0, 1000, 2000], [10, 50000, 20])
    pw.resize(500, 300)
    pw.show()
    qtbot.waitExposed(pw)
    pi = pw.getPlotItem()
    for item in (pi.vb, pi.getAxis("left"), pi.getAxis("bottom")):
        for delta in (120, -120):
            _wheel(pw, item, delta, 400)  # 400 steps either way: far past the old underflow/overflow
            assert _finite(pi.vb), pi.vb.viewRange()


def test_acquisition_empty_plot_has_a_sane_range(qtbot: QtBot, tmp_path) -> None:
    """The latest-sub plot used to auto-range itself to ~1e-91 (label TextItem in the bounds)."""
    from starex_reduce.core.session import Session
    from starex_reduce.ui import theme
    from starex_reduce.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    w.show()
    a = w.ensure_acquisition()
    w.show_tab("acquisition")
    qtbot.wait(200)
    vb = a.plot.getPlotItem().vb
    (_, _), (y0, y1) = vb.viewRange()
    assert y1 - y0 >= 0.5
    _wheel(a.plot, a.plot.getPlotItem().getAxis("left"), 120, 50)
    assert _finite(vb)
