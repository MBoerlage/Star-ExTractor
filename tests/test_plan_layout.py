"""Plan tab layout (2026-11-21, docs/PLAN_TAB.md): row 1 two equal columns (left: title, Target, Reference
candidates; right: Observing conditions, then the polar plot and the altitude chart side by side), row 2 Planned
pairs, row 3 Observing plan with a fixed-height view.

Sizes are measured by tools/plan_layout_check.py in a subprocess that has the system fonts: in this process the
offscreen platform's fallback font is about twice as wide as the real one."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "plan_layout_check.py"
LAYOUT_CODE = ["starex_reduce/ui/screens/plan.py", "starex_reduce/ui/screens/plan_conditions.py",
               "starex_reduce/ui/screens/plan_pairs.py", "starex_reduce/ui/widgets/flow.py",
               "starex_reduce/ui/widgets/table_cols.py", "starex_reduce/ui/widgets/sky_polar.py",
               "starex_reduce/ui/widgets/alt_chart.py"]
_cache: dict[tuple, dict] = {}


def layout(size: str, pairs: int = 3) -> dict:
    key = (size, pairs)
    if key not in _cache:
        env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_FONTDIR",)}
        env["PYTHONIOENCODING"] = "utf-8"
        out = subprocess.run([sys.executable, str(TOOL), size, "--pairs", str(pairs)], capture_output=True,
                             text=True, encoding="utf-8", env=env, timeout=240, check=True)
        _cache[key] = json.loads(out.stdout.strip().splitlines()[-1])
    return _cache[key]


SIZES = ["1280x800", "min", "1920x1080", "1920x2400"]  # the last one is tall enough not to scroll


@pytest.mark.parametrize("size", SIZES)
def test_rows_and_columns(size: str) -> None:
    d = layout(size)
    assert d["in_left"] == ["target", "reference"]
    assert d["in_right"] == ["conditions", "polar", "chart"]
    r = d["rects"]
    assert r["target"][1] < r["reference"][1]  # left column, top to bottom
    assert r["conditions"][1] < r["polar"][1] == r["chart"][1]  # right column: the plots side by side under it
    assert r["polar"][0] < r["chart"][0]
    polar_share = r["polar"][2] / (r["polar"][2] + r["chart"][2])
    assert 0.40 < polar_share < 0.50  # about 45 / 55
    (lx, _, lw, _), (rx, _, rw, _) = d["columns"]
    assert abs(lw - rw) <= 2 and lw >= 420 and rx - (lx + lw) == 10  # equal, at least 420 px, 10 px gap
    row1_bottom = max(r["reference"][1] + r["reference"][3], r["chart"][1] + r["chart"][3])
    assert r["pairs"][1] > row1_bottom and r["plan"][1] > r["pairs"][1] + r["pairs"][3]  # rows 2 and 3
    assert r["pairs"][2] == r["plan"][2] == d["page_w"]  # full width


@pytest.mark.parametrize("size", SIZES)
def test_nothing_clipped_and_no_horizontal_scroll(size: str) -> None:
    d = layout(size)
    assert d["clipped"] == []  # every button and one-line label fits its size hint; table cells fit
    s = d["simbad"]
    assert s["visible"] and s["w"] >= s["hint"] and s["x"] + s["w"] <= s["card_w"]  # Check with SIMBAD in full
    assert d["page_w"] <= d["viewport_w"] and d["page_min_w"] <= d["viewport_w"] and not d["hbar_visible"]


@pytest.mark.parametrize("size", SIZES)
def test_time_zone_line_once_and_square_polar_plot(size: str) -> None:
    d = layout(size)
    assert len(d["tz_lines"]) == 1
    side, plot_h = d["polar"]
    assert side == plot_h and side >= 180  # a circle, growing with the column
    assert d["chart_h"] >= side  # the chart at least as tall as the plot beside it


@pytest.mark.parametrize("size", SIZES)
def test_header_above_both_columns(size: str) -> None:
    d = layout(size)
    r = d["in_window"]
    (tx, ty, _, _), (ix, iy, _, ih) = d["title_intro"]
    assert ty < iy and iy + ih <= r["target"][1] and iy + ih <= r["conditions"][1]  # title, intro, then the columns
    assert r["header"][0] == r["target"][0] and r["header"][2] == r["pairs"][2]  # spans the full width
    assert tx == ix == r["target"][0]


@pytest.mark.parametrize("size", SIZES)
def test_columns_start_and_end_level(size: str) -> None:
    r = layout(size)["in_window"]
    assert r["target"][1] == r["conditions"][1]  # same top
    bottom = {k: r[k][1] + r[k][3] for k in ("reference", "polar", "chart")}
    assert bottom["reference"] == bottom["polar"] == bottom["chart"]  # same bottom; Sky and Altitude level
    assert r["polar"][1] == r["chart"][1]


@pytest.mark.parametrize("size", SIZES)
def test_symmetric_margins_and_scrollbar_outside_the_cards(size: str) -> None:
    d = layout(size)
    left, right = d["margins"]
    assert abs(left - right) <= 1 and left >= 16  # window edge to the outermost card, both sides
    bar = d["scrollbar"]
    assert bar["overlaps"] == []
    assert bar["visible"] == (bar["inner_max"] > 0) and bar["max"] == bar["inner_max"]
    if bar["visible"]:
        x, _, w, _ = bar["rect"]
        assert x >= d["window"][0] - right and x + w <= d["window"][0]  # in the right margin band
    if size == "1920x2400":
        assert not bar["visible"]  # nothing to scroll: no bar, the margins stay the same


@pytest.mark.parametrize("size", SIZES)
def test_observing_conditions_controls_not_clipped(size: str) -> None:
    assert layout(size)["conditions_clipped"] == []  # inside the card, text fits, no overlaps


def test_pairs_card_status_and_buttons() -> None:
    d = layout("1280x800")
    assert d["status"] == ["● In Acquisition", "Ready", "! pick reference"]
    assert d["buttons"] == ["Add pair", "Update pair", "Remove", "Move up", "Move down", "Sort by transit time",
                            "Load for acquisition"]
    cols = d["pair_cols"]
    assert cols[0] == min(cols) and cols[5] == max(cols)  # # narrow, Status widest
    assert d["pairs_right"] - d["load_right"] < 24  # Load for acquisition right-aligned
    y, shown = d["status_line"]
    assert shown and y > d["buttons_y"]  # the result line ("✓ Added …") under the buttons


def test_plan_view_has_a_fixed_height_and_scrolls() -> None:
    few, many = layout("1280x800"), layout("1280x800", pairs=12)
    assert few["plan_box"]["min"] == few["plan_box"]["max"] == few["plan_box"]["h"]
    assert many["plan_box"]["h"] == few["plan_box"]["h"]  # the page does not grow with the pairs
    assert many["plan_box"]["scroll_max"] > few["plan_box"]["scroll_max"] > 0
    assert many["rects"]["plan"][3] == few["rects"]["plan"][3]


# ---- palettes ----------------------------------------------------------------------------------------------------
def test_no_hard_coded_colours_in_the_layout_code() -> None:
    pattern = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|QColor\(\s*\d|rgba?\(\s*\d|Qt\.GlobalColor\.")
    for rel in LAYOUT_CODE:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not pattern.findall(text), rel


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    w = MainWindow(session=Session.create(tmp_path / "pair"))
    w.settings["site"] = {"lat": 30.6714, "lon": -104.0225}  # McDonald Observatory
    qtbot.addWidget(w)
    w.resize(1280, 800)
    w.show()
    w.show_tab("plan")
    return w


@pytest.mark.parametrize("palette", ["night", "red"])
def test_no_green_or_blue_in_the_night_palettes(win: MainWindow, qtbot: QtBot, palette: str) -> None:
    from starex_reduce.core import plan as pl
    from starex_reduce.ui import retheme

    page = win.ensure_plan()
    for k, (name, ra, dec) in enumerate((("bet Cas", 2.2945, 59.1498), ("V1405 Cas", 351.1592, 61.1525),
                                         ("P Cyg", 304.4466, 38.0329))):
        pl.set_target(win.session, name, ra, dec, "simbad", None)
        page.load_session()
        if k < 2:
            page.use_selected(0)
        page.pairs.add_pair()
    page.pairs.load_for_acquisition(0)
    win.show_tab("plan")
    qtbot.waitUntil(lambda: page.conditions.idle() and page.pairs.idle(), timeout=5000)
    retheme.switch(palette)
    try:
        page.setFocus()  # no text caret: Qt draws it inverted (docs/THEMES.md)
        qtbot.wait(50)
        img = page.scroll.widget().grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
        px = a[:, : img.width(), [2, 1, 0]].astype(int)
        lit = px.sum(axis=2) > 24
        r, g, b = (px[..., i][lit] for i in range(3))
        if palette == "red":
            assert np.all(g <= 2) and np.all(b <= 2)
        else:
            assert np.all(g <= r + 2) and np.all(b <= r + 2)
    finally:
        retheme.switch("default")
