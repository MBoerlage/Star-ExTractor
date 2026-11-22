"""Acquisition → Sub view: ADU x range, peak label, Y zoom chips, row profile aligned with the image.
Deneb-like synthetic frames (3840×2160, 20 s, peak ≈ 23 % of the limit, tilt +0.26°)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from pytestqt.qtbot import QtBot

from starextractor import synth
from starextractor.core import subs as sb
from starextractor.core.session import Session
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow
from starextractor.ui.screens.acquisition import IMG_ZOOMS

ROW0 = 1100.0


def deneb_frame(folder: Path, tilt: float = 0.26, name: str = "Deneb_00001.fits", lit=(876, 3145)) -> Path:
    p0 = synth.PRESETS["nova"]
    star = replace(p0.target, name="Deneb", tilt_deg=tilt, exptime=20.0, peak=75000.0, row0=ROW0,
                   emission=False, lit=lit)
    p = replace(p0, target=star)
    f = folder / name
    synth.write_frame(f, synth.make_light(p, "target"), synth.light_header(p, "target", datetime(2025, 9, 30, 22)))
    return f


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    w.show_tab("acquisition")
    return w


def _load(a, qtbot: QtBot, files: list[Path]) -> None:
    a.enqueue(files)
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=30000)


def _image_mode(a, qtbot: QtBot) -> None:
    a.view_mode.set_index(1)
    a._show_sub()
    qtbot.waitUntil(lambda: a._shown_view is not None, timeout=30000)


def test_adu_x_range_is_the_frame(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    import pyqtgraph as pg

    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path)])
    rec = a.records[-1]
    assert 0.18 < rec.peak_fill < 0.28  # about the DenebLate peak
    vb = a.plot.getPlotItem().vb
    x0, x1 = vb.viewRange()[0]
    assert x0 >= -0.02 * 3840 and abs(x1 - 3840) <= 0.02 * 3840
    far = pg.InfiniteLine(pos=190000, angle=90)  # a reference line far away does not widen it
    a.plot.getPlotItem().addItem(far)
    a.refresh()
    qtbot.wait(50)
    x0, x1 = vb.viewRange()[0]
    assert x0 >= -0.02 * 3840 and abs(x1 - 3840) <= 0.02 * 3840
    vb.scaleBy((50, 1))  # a wheel zoom out cannot go past the frame either
    assert vb.viewRange()[0][1] <= 3840 * 1.02


@pytest.mark.parametrize("lit", [(0, 500), (1500, 2400), (3300, 3840)])
def test_peak_label_inside_the_plot(win: MainWindow, qtbot: QtBot, tmp_path: Path, lit) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path, lit=lit)])
    qtbot.wait(50)
    a._place_peak_label()
    vb = a.plot.getPlotItem().vb
    t = a.peak_text
    box = t.mapRectToScene(t.boundingRect())
    plot = vb.sceneBoundingRect()
    assert plot.adjusted(-1, -1, 1, 1).contains(box), (lit, a.records[-1].peak_col, box, plot)
    # same test with the peak close to the top of the view
    vb.setYRange(0, a.records[-1].peak_adu * 1.01, padding=0)
    a._place_peak_label()
    box = t.mapRectToScene(t.boundingRect())
    assert vb.sceneBoundingRect().adjusted(-1, -1, 1, 1).contains(box)


def test_zoom_chips_show_the_promised_rows(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path)])
    _image_mode(a, qtbot)
    bands = a.band_overlay.bands()
    centre = (bands.spectrum.top + bands.spectrum.bottom + 1) / 2
    expect = {"full": 2160, 2: 1080, 4: 540, 8: 270, 16: 135}
    for i, (_t, mode) in enumerate(IMG_ZOOMS):
        a.zoom_chips.set_index(i)
        qtbot.wait(20)
        y0, y1 = a.viewer.y_range()
        if mode == "bands":
            assert abs(y0 - (bands.sky_a.top - 40)) <= 1 and abs(y1 - (bands.sky_b.bottom + 1 + 40)) <= 1
        else:
            assert abs((y1 - y0) - expect[mode]) <= 1, (mode, y0, y1)
            if mode != "full":
                assert abs((y0 + y1) / 2 - centre) <= 1
        assert a.img_rows_label.text() == f"rows {round(y0)}–{round(y1) - 1} of 2160"


def test_zoom_is_clamped_at_the_frame_edge(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path)])
    _image_mode(a, qtbot)
    a.zoom_chips.set_index(2)  # ×2
    qtbot.wait(20)
    y0, y1 = a.viewer.y_range()
    assert 0 <= y0 and y1 <= 2160 and abs((y1 - y0) - 1080) <= 1


@pytest.mark.parametrize("tilt", [0.26, 0.0])
def test_row_profile_lines_up_with_the_image(win: MainWindow, qtbot: QtBot, tmp_path: Path, tilt: float) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path, tilt=tilt)])
    _image_mode(a, qtbot)
    a.zoom_chips.set_index(3)  # ×4
    qtbot.wait(50)
    v = a._shown_view
    d = v["display"]
    prof_peak = float(np.argmax(v["profile"])) + 0.5  # row centres at k + 0.5 everywhere
    lo, hi = 1000, 3000
    img_peak = float(np.argmax(np.median(np.asarray(d.raw)[:, lo:hi], axis=1))) + 0.5
    b = a.band_overlay.bands().spectrum
    band_centre = (b.top + b.bottom + 1) / 2
    assert abs(prof_peak - img_peak) <= 0.5 and abs(prof_peak - band_centre) <= 0.5
    assert abs(prof_peak - (ROW0 + 0.5)) <= 1.5  # the frame's real spectrum row (centre column)
    # one row → the same screen pixel in the image, in the band rectangle and in the profile plot
    ivb, pvb = a.viewer.vb, a.prof_plot.getPlotItem().vb
    for row in (a.viewer.y_range()[0] + 5, prof_peak, a.viewer.y_range()[1] - 5):
        yi = a.viewer.plot.mapToGlobal(a.viewer.plot.mapFromScene(ivb.mapViewToScene(QPointF(10, row)))).y()
        yp = a.prof_plot.mapToGlobal(a.prof_plot.mapFromScene(pvb.mapViewToScene(QPointF(10, row)))).y()
        assert abs(yi - yp) <= 1, (row, yi, yp)
    lo_r, hi_r = sorted(a.band_overlay.regions["spectrum"].getRegion())  # the rectangle drawn: same rows
    assert abs((lo_r + hi_r) / 2 - band_centre) <= 0.5


# ---- dragged bands survive a view switch; no NaN warnings from far skies or a narrow Re-detect ----------
def _drag(a, key: str, by: int) -> None:
    r = a.band_overlay.regions[key]
    lo, hi = r.getRegion()
    r.setRegion((lo + by, hi + by))
    r.sigRegionChangeFinished.emit(r)


def test_dragged_skies_survive_a_view_switch(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path), deneb_frame(tmp_path, name="Deneb_00002.fits")])
    _image_mode(a, qtbot)
    a.row_fields["gap"].setFocus()  # the user clicked into a field earlier
    _drag(a, "sky_a", -20)  # only Sky A: no longer symmetric with Sky B
    dragged = a.band_overlay.bands()
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=30000)
    a.row_fields["gap"].editingFinished.emit()  # what the click on "ADU profile" does to the focused field
    a.view_mode.set_index(0)
    a._show_sub()
    a.view_mode.set_index(1)
    a._show_sub()
    qtbot.waitUntil(lambda: a._shown_view is not None and a._shown_view["rows"] == a.rows_by["target"], timeout=30000)
    assert a.band_overlay.bands() == dragged
    assert all(tuple(r.rows["sky_a"]) == tuple(a.rows_by["target"].sky_a) for r in a.records)


def test_typing_in_a_row_field_still_applies(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    _load(a, qtbot, [deneb_frame(tmp_path)])
    a.row_fields["gap"].setValue(a.row_fields["gap"].value() + 10)
    a.row_fields["gap"].editingFinished.emit()
    assert a.rows_by["target"].locked_from == "edited"


def _wide_deneb(folder: Path) -> Path:
    p0 = synth.PRESETS["nova"]
    star = replace(p0.target, name="Deneb", tilt_deg=0.3, exptime=20.0, peak=180000.0, row0=ROW0,
                   emission=False, fwhm_y=15.5)  # the real Deneb spectrum is this wide
    p = replace(p0, target=star)
    f = folder / "DenebWide_00001.fits"
    synth.write_frame(f, synth.make_light(p, "target"), synth.light_header(p, "target", datetime(2025, 9, 30, 22)))
    return f


def test_far_sky_bands_use_the_right_rows(tmp_path: Path) -> None:
    import warnings

    f = deneb_frame(tmp_path)
    rows = sb.detect_rows(f)
    near = sb.analyse_sub(f, rows)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "Mean of empty slice" was a sky band outside the ±80-row strip
        # straddling the old strip's edge (its slice was empty) and wholly beyond it (wrapped to other rows)
        for sky_a, sky_b in (((-95, -75), (85, 100)), ((-160, -130), (130, 160))):
            far = replace(rows, sky_a=sky_a, sky_b=sky_b, locked_from="edited")
            rec = sb.analyse_sub(f, far)
            assert np.isfinite(rec.pedestal) and abs(rec.pedestal - near.pedestal) < 3, (sky_a, rec.pedestal)


def test_redetect_in_a_narrow_window_on_a_wide_spectrum(tmp_path: Path) -> None:
    import warnings

    f = _wide_deneb(tmp_path)
    rows = sb.detect_rows(f)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for span in (135, 270):  # ×16 and ×8 views
            got = sb.detect_rows_in(f, rows.tilt_deg, rows.x_centre, rows.row - span / 2, rows.row + span / 2)
            assert abs(got.row - rows.row) <= 1
            assert got.sky_a[1] < got.spec[0] and got.sky_b[0] > got.spec[1]  # skies outside the spectrum
            assert got.sky_a[1] - got.sky_a[0] >= 5 and got.sky_b[1] - got.sky_b[0] >= 5
