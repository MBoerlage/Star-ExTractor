"""Geometry → 1D Extract: Extract shows and reads the current corrected stack of the selected image, straight,
and band edits never land on the other image while it loads."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core.extract import Band, Bands
from starex_reduce.core.extract_stage import corrected_path, specs_for
from starex_reduce.core.fitsio import read_image, write_float32
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

W, H = 1600, 1100
TILT, SLANT = 0.3, -0.9
ROWS = {"Target": 550.0, "Reference": 420.0}
LINE_X = {"Target": 700.0, "Reference": 1000.0}


def frame(image: str, seed: int) -> np.ndarray:
    """Raw stack: a horizontal trace tilted by TILT, and a bright emission line across the slit leaning by SLANT
    (in the straightened frame), as Geometry models them (x = x_i + tan(slant)·(y − row))."""
    rng = np.random.default_rng(seed)
    row0 = ROWS[image]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    a = math.radians(TILT)
    cx = W / 2
    uy = row0 + math.cos(a) * (yy - row0) - math.sin(a) * (xx - cx)
    ux = cx + math.sin(a) * (yy - row0) + math.cos(a) * (xx - cx)
    lit = (uy > 80) & (uy < H - 80)
    img = 400.0 + 120 * lit
    img += 6000 * np.exp(-0.5 * ((uy - row0) / 2.5) ** 2) * ((ux > 150) & (ux < W - 150))
    d = ux - (LINE_X[image] + math.tan(math.radians(SLANT)) * (uy - row0))
    img += lit * 3000 * np.exp(-0.5 * (d / 1.6) ** 2)
    return (img + rng.normal(0, 8, img.shape)).astype(np.float32)


def digest(a: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(a, np.float32).tobytes()).hexdigest()


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    for img, seed in (("Target", 1), ("Reference", 2)):
        write_float32(s.folder / f"work/{img}_S.fit", frame(img, seed), {"OBJECT": img})
    s.mark_done("files", {"Target": "work/Target_S.fit", "Reference": "work/Reference_S.fit"}, "stacked")
    set_geometry(s, tilt=TILT, slant=SLANT)
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def set_geometry(s: Session, tilt: float, slant: float, flip_x: bool = False) -> None:
    s.params["geometry"] = {**s.params["geometry"], "use_saved": False, "hot": True, "flip_x": flip_x,
                            "flip_y": False, "slant_on": True, "slant": slant, "slant_source": "manual",
                            "per_image": {i: {"tilt": tilt, "source": "manual"} for i in ("Target", "Reference")}}
    s.changed()


def run(win: MainWindow, qtbot: QtBot, sid: str) -> None:
    with qtbot.waitSignal(win.runner.finished, timeout=30000, check_params_cb=lambda slot, _r: slot == sid):
        assert win.run_stage(sid)


def open_extract(win: MainWindow, qtbot: QtBot, image: str = "Target"):
    win.open_step(2)
    es = win.ensure_screen("extract")
    if es.image != image:
        es.header._step_image(1 if image == "Reference" else -1)
    qtbot.waitUntil(lambda: es.shown == image and image in es.display, timeout=15000)
    return es


def view_array(es, image: str) -> np.ndarray:
    return es.display[image]["orig"].raw


def trace_rows(img: np.ndarray, row: float) -> np.ndarray:
    r = round(row)
    out = []
    for x0 in range(200, W - 250, 100):
        p = img[r - 12:r + 13, x0:x0 + 40].mean(axis=1)
        p = np.clip(p - np.median(img[r - 60:r - 30, x0:x0 + 40]), 0, None)
        out.append(r - 12 + (p * np.arange(len(p))).sum() / p.sum())
    return np.array(out)


def line_cols(img: np.ndarray, x: float, row: float) -> np.ndarray:
    c, out = round(x), []
    for y0 in range(round(row) - 300, round(row) + 300, 30):
        if abs(y0 + 15 - row) < 20:  # skip the trace itself
            continue
        q = img[y0:y0 + 30, c - 10:c + 11].mean(axis=0)
        q = np.clip(q - np.median(img[y0:y0 + 30, c - 40:c - 20]), 0, None)
        out.append(c - 10 + (q * np.arange(len(q))).sum() / q.sum())
    return np.array(out)


def test_extract_shows_and_reads_the_geometry_output(win: MainWindow, qtbot: QtBot) -> None:
    run(win, qtbot, "geometry")
    s = win.session
    for image in ("Target", "Reference"):
        es = open_extract(win, qtbot, image)
        g = read_image(s.folder / f"work/{image}_G.fit")
        raw = read_image(s.folder / f"work/{image}_S.fit")
        assert corrected_path(s, image) == s.folder / f"work/{image}_G.fit"
        assert digest(view_array(es, image)) == digest(g) != digest(raw)  # view = Geometry output, not the stack
        spec = next(sp for sp in specs_for(s, [image])[0])
        assert spec.src == corrected_path(s, image)  # the run reads the same file
        assert digest(read_image(spec.src)) == digest(g)


def test_what_extract_reads_is_straight(win: MainWindow, qtbot: QtBot) -> None:
    raw = read_image(win.session.folder / "work/Target_S.fit")
    assert np.ptp(trace_rows(raw, ROWS["Target"])) > 4  # the input really is tilted ...
    run(win, qtbot, "geometry")
    es = open_extract(win, qtbot)
    run(win, qtbot, "extract")
    src = specs_for(win.session, ["Target"])[0][0].src
    img = read_image(src)
    rows = trace_rows(img, ROWS["Target"])
    assert np.ptp(rows) < 0.3, rows  # trace horizontal
    raw_cols = line_cols(np.asarray(raw), LINE_X["Target"] + 0.0, ROWS["Target"])
    assert np.ptp(raw_cols) > 3  # ... and slanted
    # where the line lands after straightening: find it near its raw column
    col = float(np.argmax(img[round(ROWS["Target"]) - 200, 500:900])) + 500
    cols = line_cols(img, col, ROWS["Target"])
    assert np.ptp(cols) < 0.3, cols  # line vertical
    assert digest(view_array(es, "Target")) == digest(img)


@pytest.mark.parametrize("change", ["slant", "tilt", "flip"])
def test_changing_one_geometry_value_changes_the_extract_input(win: MainWindow, qtbot: QtBot, change: str) -> None:
    s = win.session
    run(win, qtbot, "geometry")
    es = open_extract(win, qtbot)
    before, key_before = digest(read_image(corrected_path(s, "Target"))), s.compute_keys()["geometry"]["Target"]
    view_before = digest(view_array(es, "Target"))
    kw = {"tilt": TILT, "slant": SLANT, "flip_x": False}
    kw.update({"slant": SLANT + 0.4} if change == "slant" else {"tilt": TILT - 0.2} if change == "tilt"
              else {"flip_x": True})
    set_geometry(s, **kw)
    assert s.compute_keys()["geometry"]["Target"] != key_before
    assert not s.image_done("geometry", "Target") and not s.image_done("extract", "Target")
    run(win, qtbot, "geometry")
    after = digest(read_image(corrected_path(s, "Target")))
    assert after != before  # recomputed, not served from a cache
    es = open_extract(win, qtbot)
    qtbot.waitUntil(lambda: digest(view_array(es, "Target")) == after, timeout=15000)  # the view follows at once
    assert digest(view_array(es, "Target")) != view_before
    assert "Needs re-run" in es.profile_note.text()  # the 1D profile is a preview until Extract runs
    run(win, qtbot, "extract")
    qtbot.waitUntil(lambda: "Needs re-run" not in es.profile_note.text(), timeout=5000)


def test_reference_shows_its_own_corrected_image(win: MainWindow, qtbot: QtBot) -> None:
    run(win, qtbot, "geometry")
    es = open_extract(win, qtbot, "Target")
    es.header._step_image(1)
    # while the reference loads, the target (and its bands) are not left on screen to be edited
    assert es.shown is None and es.viewer.images["orig"] is None
    es.overlay.moved.emit(Bands(Band(10, 20), Band(1, 5), Band(30, 40)))
    assert "Reference" not in (win.session.params["extract"].get("per_image") or {})
    qtbot.waitUntil(lambda: es.shown == "Reference", timeout=15000)
    ref = digest(read_image(win.session.folder / "work/Reference_G.fit"))
    assert digest(view_array(es, "Reference")) == ref != digest(read_image(win.session.folder / "work/Target_G.fit"))
    assert abs(es.bands["Reference"].spectrum.centre - ROWS["Reference"]) < 3


def test_band_follow_is_applied_once(win: MainWindow, qtbot: QtBot) -> None:
    s = win.session
    run(win, qtbot, "geometry")
    es = open_extract(win, qtbot)
    b = es.bands["Target"]
    es._store("Target", b, "moved")  # stored bands with their anchor
    stored = s.params["extract"]["per_image"]["Target"]
    set_geometry(s, tilt=TILT, slant=SLANT)
    run(win, qtbot, "geometry")  # identical settings, run again
    run(win, qtbot, "geometry")
    es = open_extract(win, qtbot)
    qtbot.wait(300)
    assert s.params["extract"]["per_image"]["Target"] == stored
    assert es.bands["Target"] == b
    assert not any("followed the spectrum" in line for line in s.log_lines)


# ---- band follow on a frame with two spectra (AG Dra: a bright diffuse band above the star) -----------------
def two_spectra(star_row: float, glow_row: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = np.arange(H, dtype=np.float64)[:, None]
    img = 400 + 9000 * np.exp(-0.5 * ((y - glow_row) / 25) ** 2) + 3000 * np.exp(-0.5 * ((y - star_row) / 2.5) ** 2)
    return (img * np.ones((1, W)) + rng.normal(0, 8, (H, W))).astype(np.float32)


STAR_BANDS = Bands(Band(785, 815), Band(740, 769), Band(831, 860))


def test_follow_measures_the_spectrum_in_the_band_not_the_brightest_row() -> None:
    from starex_reduce.core.extract_stage import anchor_row, resolve_bands

    set_on = two_spectra(800, 300)
    anchor = anchor_row(set_on, STAR_BANDS.spectrum)
    assert abs(anchor - 800) <= 1
    stored = {"bands": STAR_BANDS.to_json(), "anchor": anchor}
    # Geometry moved the glow by 9 rows, not the star: the bands stay on the star
    bands, how, _ = resolve_bands(two_spectra(800, 291, seed=1), stored, None)
    assert bands == STAR_BANDS and how == "stored"
    # the star itself moved 6 rows: the bands follow it, once
    bands, how, _ = resolve_bands(two_spectra(806, 291, seed=2), stored, None)
    assert bands.spectrum == Band(791, 821) and how == "followed the spectrum (+6 rows)"


def test_an_old_anchor_on_another_spectrum_does_not_move_the_bands() -> None:
    from starex_reduce.core.extract_stage import resolve_bands

    stored = {"bands": STAR_BANDS.to_json(), "anchor": 300.0}  # older sessions: the frame's brightest row
    bands, how, _ = resolve_bands(two_spectra(800, 291), stored, None)
    assert bands == STAR_BANDS and how == "stored"
