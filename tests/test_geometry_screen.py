"""GUI smoke test of step 2 · Geometry on synthetic stacks."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import synth
from PySide6.QtCore import QPointF, Qt
from pytestqt.qtbot import QtBot

from starex_reduce.core.fitsio import read_header, write_float32
from starex_reduce.core.session import Session, Status
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

SCALE = 0.25  # 960 × 540


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    p = synth.scaled(synth.PRESETS["nova"], SCALE)
    for img, which in (("Target", "target"), ("Reference", "reference")):
        write_float32(s.folder / f"{img}_S.fit", synth.make_light(p, which).astype(np.float32), {"OBJECT": img})
    s.mark_done("files", {"Target": "Target_S.fit", "Reference": "Reference_S.fit"}, "stacked")
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def _open(win: MainWindow, qtbot: QtBot):
    win.open_step(1)
    gs = win.geometry_screen
    qtbot.waitUntil(lambda: gs.viewer.current is not None, timeout=10000)
    return gs


def test_detect_runs_and_corrects(win: MainWindow, qtbot: QtBot) -> None:
    gs = _open(win, qtbot)
    assert gs.viewer.shape == (540, 960)
    with qtbot.waitSignal(win.runner.finished, timeout=10000,
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        gs.detect()
    assert abs(gs.angle.value() - (-0.67)) < 0.02
    assert win.session.image_done("geometry", "Target")
    h = read_header(win.session.folder / "Target_G.fit")
    assert abs(h["GEO_TILT"] - (-0.67)) < 0.02 and h["GEO_TSRC"] == "detect"
    qtbot.waitUntil(lambda: gs.viewer.images["corr"] is not None, timeout=10000)
    assert gs.viewer.which == "corr" and gs.view_toggle.index() == 1
    assert "tilt -0.67° removed" in gs.result_note.text()

    with qtbot.waitSignal(win.runner.finished, timeout=10000,
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        win.run_stage("geometry", ["Reference"])
    assert win.session.status["geometry"].status == Status.VALID
    assert win.ribbon.steps[1].status.startswith("Tilt")


class _Drag:
    """Minimal stand-in for pyqtgraph's MouseDragEvent."""

    def __init__(self, vb, start: QPointF, pos: QPointF, start_ev: bool, finish: bool) -> None:
        self._start, self._pos, self._s, self._f = vb.mapViewToScene(start), vb.mapViewToScene(pos), start_ev, finish

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def isStart(self) -> bool:
        return self._s

    def isFinish(self) -> bool:
        return self._f

    def scenePos(self) -> QPointF:
        return self._pos

    def buttonDownScenePos(self) -> QPointF:
        return self._start

    def accept(self) -> None:
        pass


@pytest.mark.parametrize("zoom", [1, 16])
def test_draw_angle_independent_of_zoom(win: MainWindow, qtbot: QtBot, zoom: int) -> None:
    gs = _open(win, qtbot)
    gs.viewer.set_zoom("fit")
    gs.viewer.set_zoom(zoom)
    qtbot.wait(20)
    drawn: list[tuple[float, ...]] = []
    gs.viewer.line_drawn.connect(lambda *a: drawn.append(a))
    gs.viewer.set_draw_mode(True)
    vb = gs.viewer.vb
    y0, y1 = gs.viewer.y_range()
    a, b = QPointF(100, (y0 + y1) / 2), QPointF(860, (y0 + y1) / 2 + 760 * math.tan(math.radians(-0.67)))
    vb.mouseDragEvent(_Drag(vb, a, a, True, False))
    vb.mouseDragEvent(_Drag(vb, a, b, False, True))
    assert len(drawn) == 1
    from starex_reduce.core.geometry import drawn_angle

    assert drawn_angle(*drawn[0]) == pytest.approx(-0.67, abs=0.01)


def test_views_do_not_recompute(win: MainWindow, qtbot: QtBot) -> None:
    gs = _open(win, qtbot)
    submitted: list[str] = []
    orig = win.runner.submit
    win.runner.submit = lambda slot, *a, **k: submitted.append(slot) or orig(slot, *a, **k)  # type: ignore[method-assign]
    for z in ("full", "fit", 2, 4, 8, 16):
        gs.viewer.set_zoom(z)
    gs.viewer.centre_on(200)
    gs.viewer.zoom_by(1.5)
    gs.view_toggle.set_index(1)
    gs.view_toggle.set_index(0)
    assert submitted == []


@pytest.fixture
def slant_win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    from starex_reduce.synth import slanted_frame

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    for img, seed in (("Target", 1), ("Reference", 2)):
        write_float32(s.folder / f"{img}_S.fit", slanted_frame(-0.67, 1.0, seed=seed, width=1600, height=1100,
                                                               row0=550), {"OBJECT": img})
    s.mark_done("files", {"Target": "Target_S.fit", "Reference": "Reference_S.fit"}, "stacked")
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def test_slant_card_detects_but_applies_only_when_ticked(slant_win: MainWindow, qtbot: QtBot) -> None:
    win = slant_win
    gs = _open(win, qtbot)
    assert not gs.slant_on.isChecked()  # default OFF
    with qtbot.waitSignal(win.runner.finished, timeout=20000,
                          check_params_cb=lambda slot, _r: slot == "slant.Target"):
        gs.detect_slant()
    assert abs(gs.slant_angle.value() - 1.0) < 0.05
    assert "block pairs" in gs.slant_status.text.text() and "not applied" in gs.slant_status.text.text()
    assert "px across a 21-row band" in gs.slant_impact.text(), gs.slant_impact.text()
    assert any("Slant Target: detected +" in line and "block pairs" in line for line in win.session.log_lines)
    with qtbot.waitSignal(win.runner.finished, timeout=20000,
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        win.run_stage("geometry", ["Target"])
    assert read_header(win.session.folder / "Target_G.fit")["GEO_SLNT"] == 0  # unticked: tilt only
    gs.slant_on.setChecked(True)
    assert not win.session.image_done("geometry", "Target")  # the setting is part of the stage key
    with qtbot.waitSignal(win.runner.finished, timeout=20000,
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        win.run_stage("geometry", ["Target"])
    h = read_header(win.session.folder / "Target_G.fit")
    assert abs(h["GEO_SLNT"] - 1.0) < 0.05 and h["GEO_SSRC"] == "detect"
    assert any("tilt -0.6" in line and "slant +" in line and "(detect)" in line for line in win.session.log_lines)
    qtbot.waitUntil(lambda: "slant +" in gs.result_note.text(), timeout=5000)
    gs.set_values()
    assert win.profile.slant_deg is not None and abs(win.profile.slant_deg - 1.0) < 0.05
    assert win.session.params["geometry"]["slant"] == pytest.approx(gs.slant_angle.value(), abs=1e-3)  # both images

    # Draw angle adds what the shown view already removed
    lean = 0.4
    x1 = 100 + math.tan(math.radians(lean)) * 400
    with qtbot.waitSignal(win.runner.finished, timeout=20000,  # Set values changed the params: run again
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        win.run_stage("geometry", ["Target"])
    qtbot.waitUntil(lambda: (gs.display.get("Target") or {}).get("corr") is not None, timeout=20000)
    gs.viewer.show_corrected(True)
    applied = float(gs.display["Target"]["corr_slant"])
    gs._slant_drawn(100, 100, x1, 500)
    assert gs.slant_angle.value() == pytest.approx(lean + applied, abs=0.002)
    gs.viewer.show_corrected(False)
    tilt = gs.results["Target"]["tilt"]
    gs._slant_drawn(100, 100, x1, 500)
    assert gs.slant_angle.value() == pytest.approx(lean + tilt, abs=0.002)
    assert "original" in gs.slant_status.text.text()


def test_set_values_saves_geometry_and_reuses_it(win: MainWindow, qtbot: QtBot) -> None:
    gs = _open(win, qtbot)
    gs.angle.setValue(-0.67)
    gs.set_values()
    prof = win.profile
    assert prof.tilt_deg == -0.67 and prof.tilt_date
    assert "Use saved geometry · " in gs.use_saved.text()
    from starex_reduce.core.instrument import InstrumentProfile

    assert InstrumentProfile.load().tilt_deg == -0.67  # persisted in the (test) app folder
    with qtbot.waitSignal(win.runner.finished, timeout=15000,
                          check_params_cb=lambda slot, _r: slot == "geometry"):
        assert win.run_stage("geometry")
    for img in ("Target", "Reference"):
        assert read_header(win.session.folder / f"{img}_G.fit")["GEO_TSRC"] == "saved"
    assert gs.saved_status.state == "ok" and "Saved values still fit" in gs.saved_status.text.text()
