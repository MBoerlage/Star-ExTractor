"""GUI smoke test of step 4 · Wavelength on synthetic 1D profiles."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core import wavelength as w
from starex_reduce.core.fitsio import read_header, write_float32
from starex_reduce.core.instrument import SEED_DISPERSION
from starex_reduce.core.session import Session, Status
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

SHIFT = 17.7  # px, like the nova night against the saved Gam Cas solution


def _profile(emission: bool) -> np.ndarray:
    sol = w.Solution(list(SEED_DISPERSION["coeffs_nm"])).shifted(SHIFT)
    x = np.arange(3840.0)
    prof = 20000 * np.exp(-0.5 * ((x - 1700) / 900) ** 2) + 500
    for lam in w.BALMER.values():
        g = np.exp(-0.5 * ((x - sol.pixel(lam)) / 3.0) ** 2)
        prof = prof + 15000 * g if emission else prof * (1 - 0.5 * g)
    return (prof + np.random.default_rng(3).normal(0, 20, x.size)).astype(np.float32)


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    hdr = {"CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU"}
    write_float32(s.folder / "Reference_X.fit", _profile(False), {**hdr, "OBJECT": "ref"})
    write_float32(s.folder / "Target_X.fit", _profile(True), {**hdr, "OBJECT": "nova"})
    for stage in ("files", "geometry", "extract"):
        s.mark_done(stage, {}, "done")
    s.status["extract"].outputs = {"Target": "Target_X.fit", "Reference": "Reference_X.fit"}
    w_ = MainWindow(session=s)
    qtbot.addWidget(w_)
    w_.resize(1280, 960)
    w_.show()
    return w_


def _open(win: MainWindow, qtbot: QtBot):
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    qtbot.waitUntil(lambda: ws.result is not None, timeout=10000)
    return ws


def test_preview_run_and_profile_save(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    assert ws.result["shift"] == pytest.approx(SHIFT, abs=0.2)
    assert ws.table.rowCount() == 5 and "RMS" in ws.footer.text()
    assert len(ws.markers) == 5 and not ws.order.buttons[0].isEnabled()  # order locked when refining
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "wavelength"):
        assert win.run_stage("wavelength")
    for img in ("Reference", "Target"):
        h = read_header(win.session.folder / f"{img}_W.fit")
        assert (h["CTYPE1"], h["CUNIT1"], h["CRPIX1"], h["CRVAL1"]) == ("Wavelength", "Angstrom", 1, 3900.0)
        assert h["WAV_MODE"] == "refine" and h["WAV_NLIN"] == 5
    assert win.profile.dispersion["coeffs_nm"] != SEED_DISPERSION["coeffs_nm"]  # tonight's solution saved
    assert win.session.status["wavelength"].status == Status.VALID  # saving it did not make the step stale
    assert win.rail.by_id["wavelength"].status.startswith("5 lines · RMS")
    from starex_reduce.core.wavelength_stage import specs_for

    specs_for(win.session, ["Target", "Reference"], win.profile)  # the next run: the snapshot is kept
    win.session.refresh_status()
    assert win.session.status["wavelength"].status == Status.VALID


def test_copy_paste_round_trip_and_order_guard(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    ws.copy_coeffs()
    from PySide6.QtWidgets import QApplication

    text = QApplication.clipboard().text()
    before = list(ws.result["coeffs"])
    ws.result = None
    ws.paste_coeffs(text)
    qtbot.waitUntil(lambda: ws.result is not None, timeout=10000)
    assert ws.result["mode"] == "fixed" and ws.result["coeffs"] == before
    ws.paste_coeffs("not, numbers")
    assert ws.paste_status.isVisible()

    ws.mode_buttons["free"].setChecked(True)
    for name in ("Hγ", "Hδ", "Hε"):
        ws.line_actions[name].setChecked(False)
    qtbot.waitUntil(lambda: ws.result is not None and len(ws.result["points"]) == 2, timeout=10000)
    assert len(ws.result["coeffs"]) == 2 and not ws.order.buttons[1].isEnabled()  # 2 lines → linear only


def test_buttons_with_real_clicks(win: MainWindow, qtbot: QtBot) -> None:
    """Regression: clicked(bool) used to reach paste_coeffs(text) → 'bool' has no attribute 'strip'."""
    from PySide6.QtWidgets import QApplication

    ws = _open(win, qtbot)
    ws.copy_btn.click()
    before = list(ws.result["coeffs"])
    assert QApplication.clipboard().text() == w.format_coeffs(before)
    ws.result = None
    ws.paste_btn.click()
    qtbot.waitUntil(lambda: ws.result is not None, timeout=10000)
    assert ws.result["mode"] == "fixed" and ws.result["coeffs"] == before
    assert not hasattr(ws, "export_xls") and not hasattr(ws, "copy_fits_terms")


def test_axis_toggle_px_and_angstrom(win: MainWindow, qtbot: QtBot) -> None:
    ws = _open(win, qtbot)
    assert ws.axis_toggle.isEnabled()
    xs_px = np.array(ws.curve.xData)
    assert xs_px[0] == 0 and xs_px[-1] == 3839
    ws.axis_toggle.set_index(1)  # Å
    xs = np.array(ws.curve.xData)
    sol = w.Solution(ws.result["coeffs"])
    assert xs[0] == pytest.approx(float(sol.angstrom(0))) and 8000 < xs[-1] < 8200
    ha = next(m for m in ws.markers if "Hα" in m.label.toPlainText())
    assert ha.value() == pytest.approx(6562.85, abs=1.5)
    assert "Wavelength (Å)" in ws.plot.getPlotItem().getAxis("bottom").labelText
    ha.setValue(6566.0)  # drag in Å → stored as a detector column
    ha.sigPositionChangeFinished.emit(ha)
    assert win.session.params["wavelength"]["manual"]["Hα"] == pytest.approx(sol.pixel(6566.0), abs=0.01)
    ws.axis_toggle.set_index(0)
    assert np.array(ws.curve.xData)[-1] == 3839


def test_coefficient_help_is_a_popup(win: MainWindow, qtbot: QtBot) -> None:
    from starex_reduce.ui.widgets.common import InfoButton

    ws = _open(win, qtbot)
    info = ws.findChildren(InfoButton)
    assert len(info) == 1
    info[0].click()
    assert info[0].popup is not None and info[0].popup.isVisible()
    assert "offset first, always in nm" in info[0].text_
    info[0].popup.close()
