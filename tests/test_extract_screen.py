"""GUI smoke test of step 3 · 1D Extract on synthetic, already straightened stacks."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import synth
from pytestqt.qtbot import QtBot

from starextractor.core import geometry as g
from starextractor.core.extract import Bands
from starextractor.core.fitsio import read_header, write_float32
from starextractor.core.jobs import Ctx
from starextractor.core.session import Session, Status
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    p = synth.scaled(synth.PRESETS["nova"], 0.5)
    for img, which in (("Target", "target"), ("Reference", "reference")):
        straight = g.correct(synth.make_light(p, which).astype(np.float32), g.GeometryParams(), Ctx()).image
        write_float32(s.folder / f"work/{img}_S.fit", straight, {"OBJECT": img})
        write_float32(s.folder / f"work/{img}_G.fit", straight, {"OBJECT": img, "GEO_TILT": 0.0})
    s.mark_done("files", {"Target": "work/Target_S.fit", "Reference": "work/Reference_S.fit"}, "stacked")
    s.mark_done("geometry", {"Target": "work/Target_G.fit", "Reference": "work/Reference_G.fit"}, "straight")
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def _open(win: MainWindow, qtbot: QtBot):
    win.open_step(2)
    es = win.ensure_screen("extract")
    qtbot.waitUntil(lambda: es.viewer.current is not None and es.curve.yData is not None, timeout=10000)
    return es


def test_auto_bands_preview_and_run(win: MainWindow, qtbot: QtBot) -> None:
    es = _open(win, qtbot)
    b = es.bands["Target"]
    assert abs(b.spectrum.centre - 535) <= 2  # row 1070 at scale 0.5
    assert es.fields["spectrum"][0].text() == str(b.spectrum.top)
    assert es.centring.state == "ok"
    assert len(es.viewer.strip.marks) == 3
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "extract"):
        assert win.run_stage("extract")
    assert win.session.status["extract"].status == Status.VALID
    h = read_header(win.session.folder / "Target_X.fit")
    assert (h["NAXIS"], h["CRPIX1"], h["CRVAL1"], h["CDELT1"], h["BUNIT"]) == (1, 1, 1.0, 1.0, "ADU")
    assert h["EXT_SPT"] == b.spectrum.top and h["EXT_SKY"] == "mean"
    assert "sky subtracted" in win.rail.by_id["extract"].status


def test_band_drag_updates_preview_within_a_frame(win: MainWindow, qtbot: QtBot) -> None:
    es = _open(win, qtbot)
    before = np.array(es.curve.yData)
    region = es.overlay.regions["spectrum"]
    lo, hi = region.getRegion()
    t = time.perf_counter()
    region.setRegion((lo + 3, hi))  # drag the top edge down 3 rows
    qtbot.waitUntil(lambda: not np.array_equal(np.array(es.curve.yData), before), timeout=1000)
    assert time.perf_counter() - t < 0.05  # one frame at 20+ fps, typically a few ms
    assert es.fields["spectrum"][0].text() == str(round(lo) + 3)


def test_typed_rows_store_bands_and_copy(win: MainWindow, qtbot: QtBot) -> None:
    es = _open(win, qtbot)
    top, bot = es.fields["spectrum"]
    top.setText("525")
    bot.setText("545")
    es._fields_edited()
    stored = win.session.params["extract"]["per_image"]["Target"]["bands"]
    assert stored["spectrum"] == [525, 545]
    es.copy_bands()
    assert win.session.params["extract"]["per_image"]["Reference"]["bands"]["spectrum"] == [525, 545]
    assert Bands.from_json(stored).spectrum.rows == 21
