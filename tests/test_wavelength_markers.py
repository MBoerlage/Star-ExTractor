"""Wavelength screen on the real 2025-10-02 omi And pair (fixture: 1D profiles only) with a saved solution from another
night: the prior is rejected and said so, markers keep the wavelength order (Part B), a dragged marker is refined from
wherever it was dropped, a failed fit keeps the marker with a warning, "my position" skips the fit, and the centre
method is part of the stage key."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from starextractor.core import wavelength as w
from starextractor.core import wavelength_stage as ws_core
from starextractor.core.fitsio import write_float32
from starextractor.core.instrument import InstrumentProfile
from starextractor.core.session import Session
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow

FIX = np.load(Path(__file__).parent / "fixtures" / "wavelength_profiles.npz")
DIPS = {"Hε": 368.0, "Hδ": 479.0, "Hγ": 678.0, "Hβ": 1111.0, "Hα": 2520.0}


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    prof = InstrumentProfile.load()  # the test's own settings folder (conftest)
    prof.dispersion = {"coeffs_nm": [float(c) for c in FIX["betacas_saved"]], "order": 2, "date": "2026-11-22",
                       "source": "2025-12-30_betacas__HD2772, free, 5 lines, RMS 0.212 Å"}
    prof.save()
    s = Session.create(tmp_path / "2025-10-02_omiand__hd217782_2")
    hdr = {"CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU"}
    write_float32(s.folder / "Reference_X.fit", FIX["omiand_ref"], {**hdr, "OBJECT": "HD 217782"})
    write_float32(s.folder / "Target_X.fit", FIX["omiand_tgt"], {**hdr, "OBJECT": "omi And"})
    for stage in ("files", "geometry", "extract"):
        s.mark_done(stage, {}, "done")
    s.status["extract"].outputs = {"Target": "Target_X.fit", "Reference": "Reference_X.fit"}
    s.params["wavelength"] = {"per_image": {"Target": {"adjust": {"mode": "shift", "shift": 199.6,
                                                                  "source": "manual"}}}}
    w_ = MainWindow(session=s)
    qtbot.addWidget(w_)
    w_.resize(1280, 960)
    w_.show()
    return w_


def _open(win: MainWindow, qtbot: QtBot):
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    qtbot.waitUntil(lambda: ws.result is not None, timeout=20000)
    return ws


def _point(ws, name: str) -> dict:
    return next(q for q in ws.result["points"] if q["line"] == name)


def _drop(ws, qtbot: QtBot, name: str, px: float) -> None:
    m = ws.marker_by_line[name]
    ws.result_before = ws.result
    m.setValue(px)
    m.sigPositionChangeFinished.emit(m)
    qtbot.waitUntil(lambda: ws.result is not ws.result_before, timeout=20000)


def test_prior_rejected_free_fit_and_labels(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    r = ws.result
    assert r["status"] == "ok" and r["mode"] == "free" and ws.mode_buttons["free"].isChecked()
    assert "does not fit this night (markers 151 px from the dips)" in ws.saved_note.text()
    assert "2025-12-30_betacas__HD2772" in ws.saved_note.text()
    assert "Fit freely: the saved solution does not fit this night" in ws.shift_note.text()
    assert ws.footer.text().startswith(f"Free fit: RMS {r['rms']:.3f} Å, 5 lines")
    for name, x in DIPS.items():
        assert _point(ws, name)["pixel"] == pytest.approx(x, abs=3), name
    # the table: wavelength order, never sortable, uncertainty / FWHM / fit / flag columns
    assert ws.table_lines == ["Hε", "Hδ", "Hγ", "Hβ", "Hα"] and not ws.table.isSortingEnabled()
    heads = [ws.table.horizontalHeaderItem(c).text() for c in range(ws.table.columnCount())]
    assert heads[3] == "± PX" and heads[6] == "± Å" and heads[7] == "FWHM PX" and heads[8] == "FIT"
    assert float(ws.table.item(3, 3).text()) > 0  # Hβ has an uncertainty
    assert ws.table.item(0, 9).text() == "⚠" and "Ca II H" in ws.table.item(0, 9).toolTip()  # Hε: the blend
    # the old manual target shift is reset with a note, the target sits on its own dips
    t = r["target"]
    assert abs(t["shift"]) < 2.0 and ws.adjust().shift == 0.0
    assert any("Manual target shift +199.60 px reset" in ln for ln in win.session.log_lines)


def test_markers_keep_the_wavelength_order(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    hd, hg = ws.marker_by_line["Hδ"], ws.marker_by_line["Hγ"]
    hd.setValue(_point(ws, "Hγ")["pixel"] + 40)  # dragged past Hγ: stops 15 px before it
    assert hd.value() == pytest.approx(_point(ws, "Hγ")["pixel"] - w.MIN_GAP_PX, abs=0.01)
    hg.setValue(0.0)  # and the other way
    assert hg.value() == pytest.approx(_point(ws, "Hδ")["pixel"] + w.MIN_GAP_PX, abs=0.01)
    assert "wavelength order" in hd.toolTip()
    # a stray out-of-order value (not through the clamp) is refused and logged
    before = dict(win.session.params["wavelength"].get("manual") or {})
    ws._marker_moved("Hδ", _point(ws, "Hγ")["pixel"] + 5)
    assert (win.session.params["wavelength"].get("manual") or {}) == before
    assert "refused (wavelength order)" in win.session.log_lines[-1]


def test_dragged_marker_is_refined_and_my_position_skips_the_fit(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    auto = _point(ws, "Hβ")["pixel"]
    _drop(ws, qtbot, "Hβ", auto + 5.0)
    q = _point(ws, "Hβ")
    assert q["manual"] and q["pixel"] == pytest.approx(auto, abs=0.15)  # the fit follows, from wherever it lands
    assert q["fit_x"] and q["fit_y"] and len(ws.overlays) >= 10  # fitted profiles and centre ticks drawn
    ws.set_nofit("Hβ", True)
    qtbot.waitUntil(lambda: "Hβ" in (ws.params().get("nofit") or []) and
                    _point(ws, "Hβ")["pixel"] == pytest.approx(auto + 5.0, abs=0.01), timeout=20000)
    assert "my position" in _point(ws, "Hβ")["flags"] and ws.table.item(3, 8).text() == "mine"
    assert "Line Hβ: my position used (no fit)" in win.session.log_lines[-1]


def test_failed_fit_keeps_the_marker_with_a_warning(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    _drop(ws, qtbot, "Hγ", 860.0)  # flat continuum, 180 px from any Balmer line
    q = _point(ws, "Hγ")
    assert q["pixel"] == pytest.approx(860.0, abs=0.01) and "fit failed: marker kept" in q["flags"]


def test_centre_method_is_part_of_the_stage_key(win: MainWindow) -> None:
    ws_core.sync_params(win.session, win.profile)
    k1 = win.session.compute_keys()["wavelength"]["Reference"]
    try:
        ws_core.set_centre_method("voigt")
        ws_core.sync_params(win.session, win.profile)
        assert win.session.params["wavelength"]["centre"] == "voigt"
        assert win.session.compute_keys()["wavelength"]["Reference"] != k1
    finally:
        ws_core.set_centre_method(None)
        ws_core.sync_params(win.session, win.profile)
    assert win.session.compute_keys()["wavelength"]["Reference"] == k1


def test_outlier_rejection_marks_the_line() -> None:
    sol = w.Solution([352.95, 0.12005, 4.5e-7])
    pts = [w.Point(k, w.BALMER[k], float(sol.pixel(w.BALMER[k])), 5.0, err_px=0.08) for k in w.BALMER]
    pts[2].pixel += 6.0  # Hγ on a wrong dip: 7 Å off
    w._reject_outlier(pts, 2, 1.205)
    assert pts[2].rejected.startswith("rejected: ") and pts[2].rejected.endswith(" σ")
    assert all(not p.rejected for i, p in enumerate(pts) if i != 2)


def test_stored_out_of_order_marker_is_repaired(win: MainWindow, qtbot: QtBot) -> None:
    # the pair's own state: Hδ dragged onto the Hγ dip before the order rule existed
    win.session.params["wavelength"]["manual"] = {"Hα": 2521.69, "Hβ": 1111.26, "Hδ": 679.73}
    ws = _open(win, qtbot)
    qtbot.waitUntil(lambda: "Hδ" not in (win.session.params["wavelength"].get("manual") or {}), timeout=20000)
    assert set(win.session.params["wavelength"]["manual"]) == {"Hα", "Hβ"}
    assert any("Line Hδ: dragged marker removed (out of wavelength order)" in ln for ln in win.session.log_lines)
    assert _point(ws, "Hδ")["pixel"] == pytest.approx(479.0, abs=2) and ws.result["status"] == "ok"


def test_uncertainties_weight_the_fit() -> None:
    sol = w.Solution([352.95, 0.12005, 4.5e-7])
    names = list(w.BALMER)

    def residual_of_hg(err_hg: float) -> float:
        pts = [w.Point(k, w.BALMER[k], float(sol.pixel(w.BALMER[k])), 5.0, err_px=0.05) for k in names]
        pts[2].pixel += 1.0  # Hγ one pixel off
        pts[2].err_px = err_hg
        fr = w._weighted_fit(pts, 2, 1.205)
        return abs(float(fr.errors_a[2]))  # how far the fit stays from the off-line point

    assert residual_of_hg(2.0) > residual_of_hg(0.05) + 0.2  # an uncertain centre pulls the solution less


def test_cubic_is_a_user_choice(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    assert len(ws.result["coeffs"]) == 3 and ws.order.buttons[2].isEnabled()  # 5 lines: cubic offered, not used
    before = ws.result
    ws.order.buttons[2].click()
    qtbot.waitUntil(lambda: ws.result is not before, timeout=20000)
    r = ws.result
    assert len(r["coeffs"]) == 4 and r["status"] == "ok" and r["rms"] < 0.5
    assert "Cubic on 5 lines" in ws.order_note.text()
