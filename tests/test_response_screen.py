"""GUI smoke test of step 5 · Response on synthetic wavelength-calibrated spectra."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core import response as r
from starex_reduce.core.fitsio import read_header, write_float32
from starex_reduce.core.session import Session, Status
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

CRVAL, CDELT, N = 3900.0, 1.2056, 3401


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    lam = CRVAL + CDELT * np.arange(N)
    ll, lf = r.load_template("a0v")
    resp = np.exp(-0.5 * ((lam - 6200) / 1500) ** 2)
    ref = (np.interp(lam, ll, lf) * resp * 8000).astype(np.float32)
    tgt = (resp * 3000 * (1 + 3 * np.exp(-0.5 * ((lam - 6562.85) / 8) ** 2))).astype(np.float32)
    hdr = {"CRVAL1": CRVAL, "CDELT1": CDELT, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
           "WAV_FWHM": 6.0, "DATE-OBS": "2026-08-21T03:42:58.415", "DATE-END": "2026-08-21T04:12:59.852"}
    ref_times = {"DATE-OBS": "2026-08-21T04:15:13.4", "DATE-END": "2026-08-21T04:20:15.0"}
    write_float32(s.folder / "Reference_W.fit", ref, {**hdr, "OBJECT": "hd163955", **ref_times})
    write_float32(s.folder / "Target_W.fit", tgt, {**hdr, "OBJECT": "Sgrnov32026"})
    for stage in ("files", "geometry", "extract", "wavelength"):
        s.mark_done(stage, {}, "done")
    s.inputs["reference"]["simbad"] = {"found": True, "sp_type": "A0", "query": "HD 163955"}
    s.inputs["target"]["coords"] = {"source": "manual", "ra": "18 05 00", "dec": "-28 30 00"}
    s.inputs["reference"]["coords"] = {"source": "manual", "ra": "17 59 47.6", "dec": "-23 48 58"}
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def _open(win: MainWindow, qtbot: QtBot):
    win.open_step(4)
    rs = win.ensure_screen("response")
    qtbot.waitUntil(lambda: rs.result is not None, timeout=10000)
    return rs


def test_preview_run_and_outputs(win: MainWindow, qtbot: QtBot) -> None:
    rs = _open(win, qtbot)
    assert rs.library.currentText() == "Auto · A0V"
    assert "Pickles A0V" in rs.lib_note.text() and len(rs.mask_items) >= 5
    assert "site" in rs.airmass_note.text()  # no site set yet: explains why there is no airmass correction
    rs.show1["raw"].setChecked(False)  # view only
    assert not rs.c_raw.isVisible()
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "response"):
        assert win.run_stage("response")
    assert win.session.status["response"].status == Status.VALID
    h = read_header(win.session.folder / "Target_R.fit")
    assert (h["CTYPE1"], h["CUNIT1"], h["RSP_MODE"]) == ("Wavelength", "Angstrom", "library")
    assert h["CRVAL1"] >= 3900 and h["CRVAL1"] + h["CDELT1"] * (h["NAXIS1"] - 1) <= 7700
    assert (win.session.folder / "response.fit").exists()
    assert win.rail.by_id["response"].status == "Pickles A0V · normalised"
    assert h["RSP_ENV"] is True


def test_envelope_checkbox(win: MainWindow, qtbot: QtBot) -> None:
    rs = _open(win, qtbot)
    assert rs.envelope.isChecked() and rs.params()["envelope"] is True  # default ON
    rs.envelope.setChecked(False)
    assert rs.params()["envelope"] is False
    assert win.session.status["response"].status != Status.VALID
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "response"):
        win.run_stage("response")
    assert read_header(win.session.folder / "Target_R.fit")["RSP_ENV"] is False
    last = [ln for ln in win.session.log_lines if "Response: Reference" in ln][-1]
    assert "upper envelope" not in last


def test_no_response_and_save(win: MainWindow, qtbot: QtBot) -> None:
    rs = _open(win, qtbot)
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "response"):
        win.run_stage("response")
    rs.save()
    assert any(rs.library.itemText(i).startswith("Saved · ") for i in range(rs.library.count()))
    rs.none_btn.setChecked(True)
    assert win.session.status["response"].status == Status.STALE
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "response"):
        win.run_stage("response")
    assert read_header(win.session.folder / "Target_R.fit")["RSP_MODE"] == "none"


def test_airmass_correction_with_site(win: MainWindow, qtbot: QtBot) -> None:
    win.settings["site"] = {"lat": 31.96, "lon": -111.60, "elev": 2096}
    rs = _open(win, qtbot)
    assert rs.result["ext"] and rs.result["x_target"] > 1.0
    assert rs.airmass_note.text().startswith("Reference ")
