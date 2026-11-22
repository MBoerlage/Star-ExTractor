"""Palettes (default, night, red), the night mode switch, live re-theming and the Part A top row (docs/THEMES.md)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session
from starex_reduce.ui import retheme, theme
from starex_reduce.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_palette  # noqa: E402

PAGES = [("plan", None), ("acquisition", None), ("library", None), ("export", None)] + [("reduce", i)
                                                                                         for i in range(5)]
# UI modules allowed to hold a colour literal: the palette itself, and the logo's own source colours (matched in the
# SVG text to swap them for tokens, never drawn as such)
ALLOWED = {"theme.py": "the palettes", "brand.py": "PRISM_COLOURS / FAN_COLOURS: the SVG's colours, replaced"}


@pytest.fixture(autouse=True)
def _back_to_default():
    yield
    if theme.palette != "default":
        retheme.switch("default")


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.resize(1600, 900)
    w.show()
    return w


def _go(qtbot: QtBot, w: MainWindow, tab: str, i: int | None) -> None:
    if i is None:
        w.show_tab(tab)
    else:
        w.open_step(i)
    qtbot.wait(40)


def _rgb(w: QWidget) -> np.ndarray:
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, :img.width(), [2, 1, 0]].astype(int)  # r, g, b


# 1 -------------------------------------------------------------------------------------------------------------
def test_every_palette_has_every_token() -> None:
    for name in theme.PALETTE_NAMES:
        p = theme.PALETTES[name]
        assert set(p) == set(theme.TOKENS), name
        for spec, attr in theme.SPEC_TOKENS.items():
            assert re.fullmatch(r"#[0-9A-F]{6}", p[attr]), (name, spec)


def test_no_colour_literals_outside_the_palette_module() -> None:
    literal = re.compile(r"#[0-9A-Fa-f]{6}\b|mk(Brush|Pen|Color)\(\s*\d|QColor\(\s*\d|QColor\(\s*\"|"
                         r"Qt\.GlobalColor\.(white|black|red|green|blue|gray|darkGray|lightGray|yellow|cyan)")
    hits = []
    for p in ROOT.joinpath("starex_reduce", "ui").rglob("*.py"):
        if p.name in ALLOWED:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if literal.search(line):
                hits.append(f"{p.name}:{n}: {line.strip()[:90]}")
    assert not hits, hits


def test_baked_colours_round_trip_through_night() -> None:
    """Two tokens equal in night are equal in default too, so default → night → default is exact."""
    for a in theme.TOKENS:
        for b in theme.TOKENS:
            if theme.PALETTES["night"][a] == theme.PALETTES["night"][b]:
                assert theme.PALETTES["default"][a] == theme.PALETTES["default"][b], (a, b)
    there = theme.colour_map("default", "night")
    back = theme.colour_map("night", "default")
    for v in theme.PALETTES["default"].values():
        assert back[there[v.upper()].upper()].upper() == v.upper()


# 2 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["night", "red"])
def test_through_filter_contrast(name: str) -> None:
    bad = [r for r in check_palette.rows(name) if not r[4]]
    assert not bad, bad


# 3 / 4 ---------------------------------------------------------------------------------------------------------
def test_night_and_red_tokens_have_no_green_or_blue() -> None:
    for name in ("night", "red"):
        for t, v in theme.PALETTES[name].items():
            r, g, b = (int(v[i:i + 2], 16) for i in (1, 3, 5))
            assert g <= r and b <= r, (name, t, v)
            if name == "red":
                assert g == b == 0, (t, v)


@pytest.mark.parametrize("name", ["night", "red"])
def test_pages_live_switched_leak_no_green_blue_or_light(win: MainWindow, qtbot: QtBot, name: str) -> None:
    for tab, i in PAGES:  # built in the default palette, then switched live
        _go(qtbot, win, tab, i)
    retheme.switch(name)
    for tab, i in PAGES:
        _go(qtbot, win, tab, i)
        a = _rgb(win)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        bad = ((g > 0) | (b > 0)) if name == "red" else ((g > r) | (b > r))
        assert not bad.any(), (tab, i, int(bad.sum()))
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        # the background: the most common colours of the page are all dark
        vals, counts = np.unique(a.reshape(-1, 3), axis=0, return_counts=True)
        for v in vals[np.argsort(counts)[-3:]]:
            assert 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2] < 40, (tab, i, v)
        assert (lum > 200).mean() < 0.02  # light only in text and small marks


# 5 -------------------------------------------------------------------------------------------------------------
def test_the_switch_in_the_top_row(win: MainWindow, qtbot: QtBot) -> None:
    sw, gear = win.night_switch, win.settings_btn
    lay = win.topbar.layout()
    idx = [lay.itemAt(i).widget() for i in range(lay.count())]
    assert idx.index(sw) + 1 == idx.index(gear)  # immediately left of the gear
    assert sw.mapTo(win, sw.rect().topRight()).x() < gear.mapTo(win, gear.rect().topLeft()).x()
    assert 36 <= sw.track_rect().width() <= 40 and 18 <= sw.track_rect().height() <= 22
    assert sw.toolTip().startswith("Night mode (red-filter safe)")
    assert sw.accessibleName() == "Night mode, off" and not sw.isChecked()
    sw.setFocus()
    qtbot.keyClick(sw, Qt.Key.Key_Space)
    assert theme.palette == "night" and sw.isChecked() and sw.accessibleName() == "Night mode, on"
    assert win.settings["ui"]["palette"] == "night"
    assert json.loads(win.settings.path.read_text("utf-8"))["ui"]["palette"] == "night"
    assert win.session.log_lines[-1].endswith("Night mode on")
    qtbot.keyClick(win, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert theme.palette == "default" and not sw.isChecked()
    assert win.session.log_lines[-1].endswith("Night mode off")


def test_palette_applied_before_the_window_is_shown() -> None:
    src = ROOT.joinpath("starex_reduce", "app.py").read_text("utf-8")
    apply_at = src.index('theme.apply(app, str((settings["ui"]')
    assert apply_at < src.index("win = MainWindow(") < src.index("win.show()")
    assert "FirstRunDialog" not in src[:apply_at]  # even the first-run dialog opens in the palette


def test_toggling_twice_is_the_exact_default_look(win: MainWindow, qtbot: QtBot) -> None:
    for tab, i in PAGES:
        _go(qtbot, win, tab, i)
    before = {}
    for tab, i in PAGES:
        _go(qtbot, win, tab, i)
        before[(tab, i)] = _rgb(win)
    calls = {"rail": 0}
    win.rail.painted.connect(lambda: calls.__setitem__("rail", calls["rail"] + 1))
    win.night_switch.toggle()
    qtbot.wait(40)
    assert calls["rail"] > 0  # the rail repainted in the new palette
    assert win.logo.pixmap(1.0).toImage() != QImage()  # the logo re-rendered (monochrome)
    win.night_switch.toggle()
    from PySide6.QtWidgets import QPlainTextEdit

    for tab, i in PAGES:
        _go(qtbot, win, tab, i)
        d = np.abs(_rgb(win) - before[(tab, i)]).sum(-1)
        for log in win.findChildren(QPlainTextEdit, "log"):  # the log panel gained "Night mode on/off" lines
            if log.isVisible():
                d[log.mapTo(win, log.rect().topLeft()).y() - 4:] = 0
        assert not d.any(), (tab, i, int((d > 0).sum()))


def test_logo_is_monochrome_at_night() -> None:
    from starex_reduce.ui import brand

    theme.set_palette("night", emit=False)
    try:
        text = brand.svg_text(brand.LOGO)
        for c in (*brand.PRISM_COLOURS, *brand.FAN_COLOURS):
            assert c.lower() not in text.lower()
        for c in re.findall(r"#[0-9A-Fa-f]{6}", text):
            r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
            assert g <= r and b <= r
    finally:
        theme.set_palette("default", emit=False)
    assert brand.svg_text(brand.LOGO) == brand.LOGO.read_text("utf-8")


# 6 -------------------------------------------------------------------------------------------------------------
def test_image_lut_follows_the_palette(win: MainWindow, qtbot: QtBot) -> None:
    from starex_reduce.ui.widgets.image_viewer import ImageViewer

    v = ImageViewer()
    qtbot.addWidget(v)
    assert theme.image_lut() is None and all(it.lut is None for it in v.layers.values())
    retheme.switch("night")
    lut = theme.image_lut()
    assert lut.shape == (256, 3) and tuple(lut[0]) == (0, 0, 0) and tuple(lut[-1]) == (255, 154, 46)
    assert all(it.lut is not None and np.array_equal(it.lut, lut) for it in v.layers.values())
    retheme.switch("red")
    assert tuple(theme.image_lut()[-1]) == (255, 0, 0)
    retheme.switch("default")
    assert all(it.lut is None for it in v.layers.values())
    g = QImage(4, 1, QImage.Format.Format_Grayscale8)
    g.fill(128)
    assert theme.tinted(g) is g  # default: the grey image itself


def test_curves_differ_by_line_style_at_night(win: MainWindow) -> None:
    rs = win.ensure_screen("response")
    style = {k: c.opts["pen"].style() for k, c in (("obs", rs.c_obs), ("lib", rs.c_lib), ("raw", rs.c_raw))}
    assert set(style.values()) == {Qt.PenStyle.SolidLine}  # default: as before
    retheme.switch("night")
    pens = {k: c.opts["pen"] for k, c in (("obs", rs.c_obs), ("lib", rs.c_lib), ("raw", rs.c_raw),
                                          ("smooth", rs.c_smooth))}
    looks = {(p.style(), p.widthF()) for p in pens.values()}
    assert len(looks) == 4  # every curve has its own style or width
    assert rs.c_ref.opts["pen"].style() != rs.c_tgt.opts["pen"].style()


def test_dialogs_are_qt_drawn_and_styled() -> None:
    app = QApplication.instance()
    theme.apply(app, "default")
    assert app.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    css = app.styleSheet()
    assert "QMessageBox" in css and "QToolTip" in css and "QMenu" in css and "QScrollBar" in css


# 7 -------------------------------------------------------------------------------------------------------------
def test_part_a_no_optional_tag(win: MainWindow) -> None:
    assert not [lab for lab in win.topbar.findChildren(QLabel) if "optional" in lab.text().lower()]
    p, lib = win.plan_btn, win.library_btn
    assert p.objectName() == lib.objectName() == "mainTab" and p.styleSheet() == lib.styleSheet() == ""
    assert p.height() == lib.height() and p.isCheckable() and lib.isCheckable()
    assert p.toolTip() == "Plan your target and pick a reference star" and p.accessibleName() == "Plan"
