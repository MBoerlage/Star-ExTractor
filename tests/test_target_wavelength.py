"""Per-target wavelength adjustment: a target shifted against the reference solution (other slit position,
flexure) is found by Auto-match, set by hand, by dragging a marker, or fitted from its own lines."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytestqt.qtbot import QtBot

from starex_reduce.core import target_wave as tw
from starex_reduce.core import wavelength as w
from starex_reduce.core.fitsio import write_float32
from starex_reduce.core.instrument import SEED_DISPERSION
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

TRUE = w.Solution(list(SEED_DISPERSION["coeffs_nm"])).shifted(17.7)  # the reference's true solution
TSHIFT = 9.3  # the target sits 9.3 px off it
EMISSION = ("He II 4686", "Hβ", "He I 5876", "Hα", "He I 7065")
N = 3840


def telluric(lam: np.ndarray) -> np.ndarray:
    """Transmission: O2 B and O2 A with a sharp head and a tail, ragged H2O from 7168 Å."""
    t = np.ones_like(lam)
    for head, length, depth in ((6867.0, 60.0, 0.35), (7594.0, 100.0, 0.7)):
        d = lam - head
        t *= 1 - depth * np.where(d >= 0, np.exp(-d / length * 3), np.exp(-(d / 1.5) ** 2))
    rng = np.random.default_rng(11)
    for c in rng.uniform(7170, 7320, 14):
        t *= 1 - 0.12 * np.exp(-0.5 * ((lam - c) / 2.0) ** 2)
    return t


def smooth(y: np.ndarray, px: float = 1.6) -> np.ndarray:
    from scipy import ndimage

    return ndimage.gaussian_filter1d(y, px)


def reference_profile() -> np.ndarray:
    x = np.arange(N, dtype=np.float64)
    lam = TRUE.angstrom(x)
    prof = 20000 * np.exp(-0.5 * ((x - 1700) / 900) ** 2) + 500
    for lam0 in w.BALMER.values():
        prof *= 1 - 0.5 * np.exp(-0.5 * ((x - TRUE.pixel(lam0)) / 3.0) ** 2)
    prof = smooth(prof * telluric(lam))
    return (prof + np.random.default_rng(3).normal(0, 15, N)).astype(np.float32)


def target_profile(shift: float = TSHIFT, features: bool = True) -> np.ndarray:
    x = np.arange(N, dtype=np.float64)
    sol = TRUE.shifted(shift)
    prof = 12000 * np.exp(-0.5 * ((x - 1900) / 1000) ** 2) + 400
    if features:
        for name in EMISSION:
            prof += 9000 * np.exp(-0.5 * ((x - sol.pixel(tw.TARGET_LINES[name])) / 2.5) ** 2)
        prof = prof * telluric(sol.angstrom(x))
    prof = smooth(prof)
    return (prof + np.random.default_rng(4).normal(0, 15, N)).astype(np.float32)


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    hdr = {"CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU"}
    write_float32(s.folder / "Reference_X.fit", reference_profile(), {**hdr, "OBJECT": "ref"})
    write_float32(s.folder / "Target_X.fit", target_profile(), {**hdr, "OBJECT": "tgt"})
    for stage in ("files", "geometry", "extract"):
        s.mark_done(stage, {}, "done")
    w_ = MainWindow(session=s)
    qtbot.addWidget(w_)
    w_.resize(1280, 960)
    w_.show()
    return w_


def open_target(win: MainWindow, qtbot: QtBot, mode: str = "manual"):
    """The Wavelength step on the Target tab. These tests check the old target adjustment, which is the target
    calibration's Manual mode now (Auto is tested in tests/test_target_cal_screen.py)."""
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    qtbot.waitUntil(lambda: ws.result is not None and ws.result.get("target") is not None, timeout=15000)
    if mode != ws.cal_params()["mode"]:
        ws.set_cal_mode(mode)
        settle(win, qtbot)
    ws.tabs.set_index(1)
    assert ws.view == "Target" and ws.target_card.isVisible()
    return ws


def run(win: MainWindow, qtbot: QtBot, sid: str = "wavelength") -> None:
    with qtbot.waitSignal(win.runner.finished, timeout=20000, check_params_cb=lambda slot, _r: slot == sid):
        assert win.run_stage(sid)


def settle(win: MainWindow, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not win.runner.is_busy("preview.wavelength.Reference")
                    and not win.runner.is_busy("preview.wavelength.match"), timeout=15000)


def line_error_px(path: Path) -> list[float]:
    """Where the target's emission lines land in a W file, against their rest wavelengths (px)."""
    with fits.open(path) as f:
        flux, h = f[0].data.astype(np.float64), f[0].header
    lam = h["CRVAL1"] + h["CDELT1"] * np.arange(len(flux))
    flat = tw.flattened(flux)
    out = []
    disp = TRUE.dispersion_a(N / 2)
    for name in EMISSION:
        lam0 = tw.TARGET_LINES[name]
        c, ok = tw.measure_line(flat, (lam0 - h["CRVAL1"]) / h["CDELT1"], half=40)
        assert ok, name
        out.append((h["CRVAL1"] + h["CDELT1"] * c - lam0) / disp)
    del lam
    return out


def test_auto_match_finds_the_target_shift_and_target_w_lands_on_the_lines(win: MainWindow, qtbot: QtBot) -> None:
    ws = open_target(win, qtbot)
    run(win, qtbot)
    errs = line_error_px(win.session.folder / "Target_W.fit")
    assert np.allclose(errs, TSHIFT, atol=0.5), errs  # unadjusted: every line 9.3 px off
    ws.auto_match()
    qtbot.waitUntil(lambda: getattr(ws, "match", None) is not None, timeout=15000)
    m = ws.match
    assert m["reliable"] and abs(m["shift"] - TSHIFT) < 0.5, m["text"]
    assert ws.t_accept.isVisible() and "Å" in ws.t_match.text.text()
    ws.accept_match()
    settle(win, qtbot)
    assert ws.adjust().source == "auto-match"
    run(win, qtbot)
    errs = line_error_px(win.session.folder / "Target_W.fit")
    assert np.all(np.abs(errs) < 0.5), errs
    assert any("Target: offset +9." in line and "by auto-match" in line for line in win.session.log_lines)


def test_dragging_a_target_marker_moves_only_the_target(win: MainWindow, qtbot: QtBot) -> None:
    ws = open_target(win, qtbot)
    run(win, qtbot)
    ref_w = (win.session.folder / "Reference_W.fit").read_bytes()
    ref_coeffs = list(ws.result["coeffs"])
    ref_params = {k: v for k, v in win.session.params["wavelength"].items() if k != "per_image"}
    sol = w.Solution(ws.result["target"]["coeffs"])
    old = sol.pixel(w.BALMER["Hβ"])
    ws._target_marker_moved("Hβ", old, old + 5.0)
    settle(win, qtbot)
    adj = ws.adjust()
    assert adj.shift == pytest.approx(5.0) and adj.source == "marker" and ws.t_shift.value() == pytest.approx(5.0)
    assert ws.result["coeffs"] == ref_coeffs
    assert w.Solution(ws.result["target"]["coeffs"]).pixel(w.BALMER["Hβ"]) == pytest.approx(old + 5.0, abs=1e-6)
    assert {k: v for k, v in win.session.params["wavelength"].items() if k != "per_image"} == ref_params
    assert not win.session.image_done("wavelength", "Target") and win.session.image_done("wavelength", "Reference")
    run(win, qtbot)
    assert (win.session.folder / "Reference_W.fit").read_bytes() == ref_w  # byte-identical


def test_fit_again_acts_on_the_profile_on_screen(win: MainWindow, qtbot: QtBot) -> None:
    ws = open_target(win, qtbot)
    assert ws.again_btn.text() == "Fit again · Target"
    ws.t_shift.setValue(3.0)  # a manual shift
    settle(win, qtbot)
    before = list(ws.result["target"]["coeffs"])
    ws.fit_again()  # shift only: measures the shift again
    qtbot.waitUntil(lambda: ws.adjust().source == "auto-match", timeout=15000)
    settle(win, qtbot)
    assert ws.result["target"]["coeffs"] != before and abs(ws.adjust().shift - TSHIFT) < 0.5
    # free fit from the target's own lines, one marker dragged wrongly; Fit again forgets it
    for name in EMISSION:
        ws.t_line_actions[name].setChecked(True)
    ws.t_free.setChecked(True)
    settle(win, qtbot)
    t = ws.result["target"]
    assert t["mode"] == "free" and len(t["points"]) == 5 and t["rms"] < 0.5 and ws.table.rowCount() == 5
    hb = w.Solution(t["coeffs"]).pixel(tw.TARGET_LINES["Hβ"])
    ws._target_marker_moved("Hβ", hb, hb + 30)
    settle(win, qtbot)
    dragged = list(ws.result["target"]["coeffs"])
    assert ws.adjust().manual
    ws.fit_again()
    settle(win, qtbot)
    assert not ws.adjust().manual and ws.result["target"]["coeffs"] != dragged
    # Reference tab: as before (forgets the reference's dragged markers), the target is left alone
    adj = ws.adjust().to_params()
    ws.tabs.set_index(0)
    assert ws.again_btn.text() == "Fit again · Reference" and not ws.target_card.isVisible()
    win.session.params["wavelength"]["manual"] = {"Hβ": 1200.0}
    ws.fit_again()
    settle(win, qtbot)
    assert win.session.params["wavelength"]["manual"] == {} and ws.adjust().to_params() == adj


def test_auto_match_refuses_without_features() -> None:
    m = tw.auto_match(reference_profile(), TRUE, target_profile(features=False), tw.Adjust())
    assert not m.reliable and m.shift_px is None and "no usable feature" in m.reason
    one = tw.auto_match(reference_profile(), TRUE, target_profile(features=False), tw.Adjust(lines=["Hβ"]))
    assert not one.reliable


def test_reset_returns_exactly_to_the_reference(win: MainWindow, qtbot: QtBot) -> None:
    ws = open_target(win, qtbot)
    ws.t_shift.setValue(4.2)
    settle(win, qtbot)
    assert ws.result["target"]["coeffs"] != ws.result["coeffs"]
    ws.reset_target()
    settle(win, qtbot)
    assert ws.adjust().is_reference and ws.t_shift.value() == 0.0
    assert ws.result["target"]["coeffs"] == ws.result["coeffs"] and ws.result["target"]["mode"] == "reference"


def test_a_target_shift_invalidates_response_and_later(win: MainWindow, qtbot: QtBot) -> None:
    ws = open_target(win, qtbot)
    s = win.session
    run(win, qtbot)
    s.mark_done("response", {}, "done")
    assert s.image_done("response", "Target")
    ws.t_shift.setValue(1.0)
    settle(win, qtbot)
    assert not s.image_done("wavelength", "Target") and not s.image_done("response", "Target")
    assert not s.image_done("response", "Reference")  # Response uses both images
    assert s.image_done("wavelength", "Reference")
