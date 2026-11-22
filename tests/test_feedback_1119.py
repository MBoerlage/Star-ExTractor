"""2026-11-19 feedback: no 'Card is too long' warning; auto-placement over the rows in view (Acquisition Sub image,
1D Extract); Geometry's blue level line; the target's tilt as the reference's first guess."""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core.extract import auto_place, auto_place_in
from starex_reduce.core.fitsio import _fits_card, read_header, write_float32
from starex_reduce.core.stack_keys import COMMENTS


# 1 -------------------------------------------------------------------------------------------------------------
def test_no_card_too_long_warning(tmp_path: Path) -> None:
    hdr = {"SXRAWDIR": r"C:\Users\someone\Captures\2025-09-30", "SXNAME": "AG Dra",  # a path of medium length
           "SXDARK": "ZWOASI585MMPro/2025/dark_60s_g251_o20_ebd14607c3.fits", "SXDARKH": "a" * 64, "SXVER": 1}
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a VerifyWarning would fail the test
        out = write_float32(tmp_path / "x.fit", np.zeros((4, 4), np.float32), hdr, comments=COMMENTS, keep_sx=True)
    h = read_header(out)
    assert h["SXRAWDIR"] == hdr["SXRAWDIR"] and h["SXDARK"] == hdr["SXDARK"] and h["SXDARKH"] == "a" * 64
    assert _fits_card(1, COMMENTS["SXVER"]) and not _fits_card("x" * 40, COMMENTS["SXRAWDIR"])


# 2 -------------------------------------------------------------------------------------------------------------
def _frame() -> np.ndarray:
    """A broad bright band near the top (the photometric region) and a fainter, narrow star spectrum lower down."""
    rng = np.random.default_rng(3)
    img = rng.normal(100, 2, (1000, 600)).astype(np.float32)
    img[80:200] += 4000.0  # broad, brighter
    rows = np.arange(1000)[:, None]
    img += (1500.0 * np.exp(-0.5 * ((rows - 640) / 2.5) ** 2)).astype(np.float32)
    return img


def test_auto_place_in_view_finds_the_star_not_the_broad_band() -> None:
    img = _frame()
    assert 80 <= auto_place(img).row < 200  # the whole frame: the broad band wins (the problem)
    pl = auto_place_in(img, 560, 720)  # the rows in view
    assert abs(pl.row - 640) <= 1
    b = pl.bands
    assert b.spectrum.top <= 640 <= b.spectrum.bottom and b.sky_a.bottom < b.spectrum.top < b.sky_b.top
    assert b.sky_a.top >= 300  # the skies stay near the star, not in the broad band


def test_extract_auto_place_uses_the_rows_in_view(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from starex_reduce.ui.screens import extract as ex_screen

    seen: list = []

    class Host:
        class runner:
            @staticmethod
            def submit(slot, fn, img, window):
                seen.append(window)

    class Viewer:
        def __init__(self, r):
            self.r = r

        def y_range(self):
            return self.r

    img = _frame()
    for r, want in (((560.0, 720.0), (560.0, 720.0)), ((0.0, 1000.0), None)):
        fake = type("S", (), {"_raw": lambda self: img, "_editable": lambda self: True, "image": "Target",
                              "auto_btn": type("B", (), {"setEnabled": lambda self, on: None})(),
                              "viewer": Viewer(r), "host": Host})()
        ex_screen.ExtractScreen.auto_place(fake)  # type: ignore[arg-type]
        assert seen[-1] == want


def test_acquisition_redetect_window_follows_the_view() -> None:
    from types import SimpleNamespace

    from starex_reduce.ui.screens.acquisition import AcquisitionScreen

    view = {"r": (0.0, 1000.0)}
    fake = SimpleNamespace(view_mode=SimpleNamespace(index=lambda: 1), _shown_view={},
                           viewer=SimpleNamespace(current=np.zeros((1000, 300)), y_range=lambda: view["r"]))
    assert AcquisitionScreen.redetect_window(fake) is None  # type: ignore[arg-type]  # the whole frame
    view["r"] = (560.0, 720.0)  # zoomed in, or the frame-strip window moved onto the star
    assert AcquisitionScreen.redetect_window(fake) == (560.0, 720.0)  # type: ignore[arg-type]
    fake.view_mode = SimpleNamespace(index=lambda: 0)  # ADU profile shown: the whole frame
    assert AcquisitionScreen.redetect_window(fake) is None  # type: ignore[arg-type]


# 3, 4 ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def geo(qtbot: QtBot, tmp_path: Path):
    import synth
    from PySide6.QtWidgets import QApplication

    from starex_reduce.core.session import Session
    from starex_reduce.ui import theme
    from starex_reduce.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    p = synth.scaled(synth.PRESETS["nova"], 0.25)
    for img, which in (("Target", "target"), ("Reference", "reference")):
        write_float32(s.folder / f"work/{img}_S.fit", synth.make_light(p, which).astype(np.float32), {"OBJECT": img})
    s.mark_done("files", {"Target": "work/Target_S.fit", "Reference": "work/Reference_S.fit"}, "stacked")
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    w.open_step(1)
    gs = w.geometry_screen
    qtbot.waitUntil(lambda: gs.viewer.current is not None, timeout=10000)
    return w, gs


def test_level_line_is_blue_draggable_and_only_a_view(geo) -> None:
    from starex_reduce.ui import theme

    w, gs = geo
    assert gs.level.isVisible() and gs.level.movable and gs.level.angle == 0
    assert gs.level.pen.color().name().lower() == theme.BLUE.lower()
    y0, y1 = gs.viewer.y_range()
    assert y0 <= gs.level.value() <= y1  # placed on the spectrum row, in view
    before = dict(w.session.params["geometry"])
    gs.level.setValue(123.0)  # what a drag does
    assert gs.level.value() == 123.0 and w.session.params["geometry"] == before  # nothing recomputed or stored
    gs.level_cb.setChecked(False)
    assert not gs.level.isVisible()


def test_target_tilt_is_the_reference_first_guess(geo, qtbot: QtBot) -> None:
    from starex_reduce.core.fitsio import read_header as rh

    w, gs = geo
    with qtbot.waitSignal(w.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "geometry"):
        gs.detect()  # on the Target stack
    t = w.session.params["geometry"]["per_image"]["Target"]["tilt"]
    ref = w.session.params["geometry"]["per_image"]["Reference"]
    assert ref == {"tilt": t, "source": "target"}
    gs.tabs.set_index(1)  # the Reference stack shows the target's angle and says so
    assert abs(gs.angle.value() - t) < 1e-3 and "From the target" in gs.tilt_note.text()  # shown to 3 decimals
    with qtbot.waitSignal(w.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == "geometry"):
        w.run_stage("geometry", ["Reference"])
    h = rh(w.session.folder / "work/Reference_G.fit")
    assert h["GEO_TSRC"] == "target" and abs(h["GEO_TILT"] - t) < 1e-4
    # an angle of its own wins and is not overwritten by the next target detection
    gs._apply_angle("Reference", -0.5, "manual", "typed")
    gs.tabs.set_index(0)
    gs._apply_angle("Target", -0.7, "manual", "typed")
    assert w.session.params["geometry"]["per_image"]["Reference"] == {"tilt": -0.5, "source": "manual"}
