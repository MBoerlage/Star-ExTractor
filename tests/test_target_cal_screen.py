"""Wavelength step › Target calibration card (target calibration v2) on a synthetic pair with sky rows."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starextractor.core import logbook
from starextractor.core.fitsio import write_float32
from starextractor.core.session import Session
from starextractor.core.wavelength import Solution
from starextractor.synth import WAVE_REF, wave_case
from starextractor.ui import retheme, theme
from starextractor.ui.main_window import MainWindow

SHIFT = 13.5


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    c = wave_case(shift=SHIFT, seed=21)
    s = Session.create(tmp_path / "pair")
    rows = {"EXT_SAT": 0, "EXT_SAB": 11, "EXT_SBT": 12, "EXT_SBB": 23, "EXT_SPT": 30, "EXT_SPB": 40}
    hdr = {"CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU", "SXROWS": "keep", **rows}
    write_float32(s.folder / "Reference_X.fit", c.ref_prof.astype(np.float32), {**hdr, "OBJECT": "ref"})
    write_float32(s.folder / "Target_X.fit", c.tgt_prof.astype(np.float32), {**hdr, "OBJECT": "tgt"})
    (s.folder / "work").mkdir(exist_ok=True)
    write_float32(s.folder / "work" / "Target_G.fit", c.tgt_sky, {"OBJECT": "tgt"})
    write_float32(s.folder / "work" / "Reference_G.fit", c.ref_sky, {"OBJECT": "ref"})
    logbook.save(s.folder, logbook.empty(s.folder.name, "2026-11-19"))
    s.params["wavelength"] = {"saved": list(WAVE_REF)}
    for stage in ("files", "geometry", "extract"):
        s.mark_done(stage, {}, "done")
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    return w


def screen(win: MainWindow, qtbot: QtBot):
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    qtbot.waitUntil(lambda: ws.result is not None and (ws.result.get("target") or {}).get("cal") is not None,
                    timeout=20000)
    return ws


def settle(win: MainWindow, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not win.runner.is_busy("preview.wavelength.Reference"), timeout=20000)


def run(win: MainWindow, qtbot: QtBot) -> None:
    with qtbot.waitSignal(win.runner.finished, timeout=30000, check_params_cb=lambda slot, _r: slot == "wavelength"):
        assert win.run_stage("wavelength")


def test_auto_fit_shows_and_is_what_target_w_uses(win: MainWindow, qtbot: QtBot) -> None:
    ws = screen(win, qtbot)
    cal = ws.result["target"]["cal"]
    assert cal["ok"] and abs(cal["s"] - SHIFT) < 0.3 and cal["model"] == 0
    card = ws.cal_card
    assert card.isVisible() and card.summary.text().startswith("Shift +13.5 px")
    assert card.table.rowCount() == len(cal["anchors"]) > 5
    assert any("Sky lines agree" in s.text.text() for s in card.chips if s.isVisible())
    assert card.verdict.text.text().startswith("Pure shift")
    run(win, qtbot)
    folder = win.session.folder
    h = fits.getheader(folder / "Target_W.fit")
    hist = [str(x) for x in h.get("HISTORY", [])]
    assert any(x.startswith("target wavelength: reference solution + correction") for x in hist)
    assert any(x.startswith("target solution = reference + shift") for x in hist)
    assert any("anchors:" in x and "sky" in x for x in hist)
    sx_in = {k for k in fits.getheader(folder / "Target_X.fit") if k.startswith("SX")}
    assert {k for k in h if k.startswith("SX")} == sx_in  # no SX* keywords added
    m = json.loads((folder / "logbook.json").read_text("utf-8"))["steps"]["wavelength"]["metrics"]
    assert abs(m["shift_px"] - SHIFT) < 0.3 and m["model"] == "shift only" and m["verdict"] == "pure_shift"
    for k in ("scale", "quad", "rms_A", "n_anchors", "v_kms", "sky_lines_found", "sky_ref_residual_A",
              "star_slit_offset_px", "delta_center_A", "delta_span_A", "delta_max_dev_A", "scale_pct"):
        assert k in m
    assert m["n_anchors"]["sky"] >= 3 and m["sky_lines_found"] >= 4
    # the target's sky line lands at its rest wavelength in Target_W's grid
    sol = Solution(ws.result["target"]["coeffs"])
    assert abs(sol.angstrom(sol.pixel(5577.339)) - 5577.339) < 1e-6
    assert abs(sol.pixel(5577.339) - (Solution(list(WAVE_REF)).pixel(5577.339) + SHIFT)) < 0.3


def test_fit_target_and_fit_again_rerun_and_the_override_is_applied_after(win: MainWindow, qtbot: QtBot) -> None:
    ws = screen(win, qtbot)
    calls = []
    orig = ws.preview
    ws.preview = lambda: (calls.append(1), orig())[1]  # type: ignore[method-assign]
    ws.cal_card.fit_btn.click()
    ws.tabs.set_index(1)
    ws.fit_again()
    assert len(calls) == 2 and any("fit again" in ln for ln in win.session.log_lines[-3:])
    settle(win, qtbot)
    fit_px = Solution(ws.result["target"]["coeffs"]).pixel(6300.304)
    ws.t_shift.setValue(2.0)  # the manual override, after the fit
    settle(win, qtbot)
    t = ws.result["target"]
    assert abs(Solution(t["coeffs"]).pixel(6300.304) - (fit_px + 2.0)) < 1e-3
    assert t["cal"]["override_px"] == pytest.approx(2.0) and "manual override +2.00 px" in ws.cal_card.summary.text()


def test_unticking_an_anchor_refits_and_mode_is_stored(win: MainWindow, qtbot: QtBot) -> None:
    ws = screen(win, qtbot)
    from PySide6.QtCore import Qt

    tab = ws.cal_card.table
    row = next(r for r in range(tab.rowCount()) if "Telluric" in tab.item(r, 1).text())
    key = tab.item(row, 0).data(Qt.ItemDataRole.UserRole)
    tab.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    settle(win, qtbot)
    assert key in ws.cal_params()["disabled"]
    a = next(x for x in ws.result["target"]["cal"]["anchors"] if x["key"] == key)
    assert not a["use"]
    ws.cal_card.mode.setCurrentIndex(1)  # Shift only
    settle(win, qtbot)
    assert ws.cal_params()["mode"] == "shift" and ws.result["target"]["cal"]["mode"] == "shift"
    ws.cal_card.mode.setCurrentIndex(3)  # Manual: the old behaviour (reference unchanged by default)
    settle(win, qtbot)
    assert ws.result["target"]["coeffs"] == ws.result["coeffs"] and not ws.cal_card.table.isVisible()


def test_card_in_night_palette_and_marker_shapes(win: MainWindow, qtbot: QtBot) -> None:
    ws = screen(win, qtbot)
    from starextractor.ui.screens.wavelength_target import SYMBOL

    assert len(set(SYMBOL.values())) == 4  # every line type its own shape
    syms = {it.opts["symbol"] for it in ws.cal_card.res_items}
    assert len(syms) >= 3
    retheme.switch("night")
    try:
        qtbot.wait(50)
        img = ws.cal_card.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
        b, g, r = (a[:, :img.width(), i].astype(int) for i in range(3))
        bad = (g > r) | (b > r)
        ys, xs = np.nonzero(bad)
        assert not bad.any(), (int(bad.sum()), [(int(x), int(y), int(r[y, x]), int(g[y, x]), int(b[y, x]))
                                                for y, x in list(zip(ys, xs, strict=True))[:: max(1, len(xs) // 8)]])
    finally:
        retheme.switch("default")


def test_missing_frames_fall_back_to_the_old_behaviour(win: MainWindow, qtbot: QtBot) -> None:
    for f in ("Target_G.fit", "Reference_G.fit"):
        (win.session.folder / "work" / f).unlink()
    ws = screen(win, qtbot)
    cal = ws.result["target"]["cal"]
    assert cal["sky"] is None and cal["ok"]  # telluric and stellar anchors still fit
    assert abs(cal["s"] - SHIFT) < 0.4
