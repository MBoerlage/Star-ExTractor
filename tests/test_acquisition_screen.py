"""GUI tests of the Acquisition tab (doc test 8) on copies of the nova subs."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core import subs as sb
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
NOVA = sorted((ROOT / "SgrNov32026").glob("*.fits"))
REF = sorted((ROOT / "hd163955").glob("*.fits"))
pytestmark = pytest.mark.skipif(not NOVA or not REF, reason="example_spectra/novasrg2026 not present")


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    w.show_tab("acquisition")
    return w


def _drain(a, qtbot: QtBot, timeout: int = 20000) -> None:
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=timeout)


def test_collecting_for_decides_where_the_next_file_goes(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:2])
    _drain(a, qtbot)
    a.collect.set_index(1)  # the user flips the switch
    a.enqueue(REF[:2])
    _drain(a, qtbot)
    assert [r.role for r in a.records] == ["target", "target", "reference", "reference"]
    assert {r.object for r in a.records} == {"Sgrnov32026", "hd163955"}  # OBJECT shown, never used for roles
    assert a.role_hint.isVisible() is False or "new object" in a.role_hint.text()
    assert set(a.rows_by) == {"target", "reference"}  # each set locked its own rows
    assert abs(a.rows_by["target"].row - a.rows_by["reference"].row) > 10
    assert all(not r.flags for r in a.records)


def test_object_change_only_hints(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:1])
    _drain(a, qtbot)
    a.enqueue(REF[:1])  # a new object arrives while still collecting for Target
    _drain(a, qtbot)
    assert a.records[-1].role == "target"  # never switched by itself
    assert a.role_hint.isVisible() and "Switch to Reference?" in a.role_hint.text()


def test_slow_analysis_does_not_block_the_ui(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.rows_by["target"] = sb.detect_rows(NOVA[0])
    real = a.analyse

    def slow(path, rows, lin):
        time.sleep(1.5)
        return real(path, rows, lin)
    a.analyse = slow
    t = time.perf_counter()
    a.enqueue(NOVA[:1])
    assert time.perf_counter() - t < 0.2 and a.busy  # returned at once, the work runs in a worker
    t = time.perf_counter()
    a.collect.buttons[1].click()  # the UI still reacts while the sub is being analysed
    qtbot.wait(50)
    assert a.collect.index() == 1 and a.busy and time.perf_counter() - t < 0.5
    _drain(a, qtbot)
    assert a.records[0].role == "target"  # it joined the set chosen when it arrived


def test_watch_folder_end_to_end(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    folder = tmp_path / "capture"
    folder.mkdir()
    for p in NOVA[:2]:
        shutil.copy(p, folder / p.name)
        old = time.time() - 30
        import os
        os.utime(folder / p.name, (old, old))
    a.folder.setText(str(folder))
    a.interval.setValue(1)
    a.existing.setChecked(True)
    a.toggle_watch()
    qtbot.waitUntil(lambda: len(a.records) == 2 and not a.busy, timeout=15000)
    a.stop_watch()
    assert [r.name for r in a.records] == [p.name for p in NOVA[:2]]
    import json

    again = json.loads((a.night_folder() / "acquisition.json").read_text(encoding="utf-8"))  # Stacks/<night>/
    assert len(again["subs"]) == 2 and again["rows"]["target"]


def test_untick_is_a_user_override(win: MainWindow, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt

    a = win.ensure_acquisition()
    a.enqueue(NOVA[:4])
    _drain(a, qtbot)
    a.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    rec = a.records[1]
    assert rec.user_override and not rec.included
    a.enqueue(NOVA[4:5])
    _drain(a, qtbot)
    assert not rec.included  # later flag recalculations keep the user's choice
    a.move(a.records[0].path, "reference")
    assert a.records[0].role == "reference"


def test_layout_snr_under_adu_and_list_on_the_right(win: MainWindow) -> None:
    a = win.ensure_acquisition()
    sub_card, snr_card = a.sub_stack.parentWidget(), a.snr_plot.parentWidget()
    table_card = a.table.parentWidget()
    assert snr_card.mapTo(a, snr_card.rect().topLeft()).y() > sub_card.mapTo(a, sub_card.rect().topLeft()).y()
    assert table_card.mapTo(a, table_card.rect().topLeft()).x() > sub_card.mapTo(a, sub_card.rect().topRight()).x()
    assert a.snr_target.parentWidget() is snr_card and a.snr_target.value() == 100
    names = {lab.text for _s, lab in a.plot.getPlotItem().legend.items}
    assert names == {"Spectrum rows: column max", "Sky rows: median"}


def test_plots_never_zoom_out_below_zero(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:2])
    _drain(a, qtbot)
    for pw in (a.plot, a.snr_plot):
        vb = pw.getPlotItem().vb
        for _ in range(40):
            vb.scaleBy(x=1.5, y=1.5)
        (x0, _x1), (y0, _y1) = vb.viewRange()
        assert x0 >= 0 and y0 >= 0
    assert a.plot.getPlotItem().vb.viewRange()[1][1] <= 1.1 * sb.FULL_SCALE + 1


def test_sub_image_shows_the_rows_of_the_picked_sub(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:3])
    _drain(a, qtbot)
    a.view_mode.set_index(1)
    assert a.sub_stack.currentIndex() == 1
    a.table.selectRow(1)  # pick sub 2: the image follows the table, not the latest sub
    qtbot.waitUntil(lambda: a.img_note.text().startswith(NOVA[1].name), timeout=10000)
    rec = a.records[1]
    r0 = round(rec.rows["row"])
    b = a.band_overlay.bands()
    assert (b.spectrum.top, b.spectrum.bottom) == (r0 + rec.rows["spec"][0], r0 + rec.rows["spec"][1])
    assert (b.sky_a.top, b.sky_b.bottom) == (r0 + rec.rows["sky_a"][0], r0 + rec.rows["sky_b"][1])
    assert "(picked)" in a.latest_note.text()
    d = a.viewer.current
    assert d is not None and d.shape[0] == 2160  # the whole frame, every row reachable
    y0, y1 = a.viewer.y_range()  # opens on the bands ("Fit to bands")
    assert y0 <= b.sky_a.top and y1 >= b.sky_b.bottom + 1 and y1 - y0 < 400
    a.zoom_chips.set_index(0)  # Full height
    y0, y1 = a.viewer.y_range()
    assert (round(y0), round(y1)) == (0, 2160)
    assert [round(v) for v in a.prof_plot.getPlotItem().vb.viewRange()[1]] == [0, 2160]
    a.zoom_chips.set_index(1)
    sky = a.p_sky.yData is not None and a.p_sky.xData[0]
    assert 0 < sky < float(max(a.p_prof.xData))  # the sky level sits below the spectrum peak
    a.enqueue(NOVA[3:4])  # a new sub arrives: the view follows it again
    _drain(a, qtbot)
    qtbot.waitUntil(lambda: a.img_note.text().startswith(NOVA[3].name), timeout=10000)
    assert "(latest)" in a.latest_note.text()


def test_rows_change_reanalyses_the_set_and_updates_the_sub_image(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:3])
    _drain(a, qtbot)
    a.exclude(str(NOVA[0]))  # a user choice that must survive the re-analysis
    a.view_mode.set_index(1)
    qtbot.waitUntil(lambda: a._shown_view is not None, timeout=10000)
    before = [r.continuum_snr for r in a.records]
    a.row_fields["half"].setValue(a.row_fields["half"].value() + 3)  # spin-box arrow: no editingFinished
    a.row_fields["gap"].setValue(a.row_fields["gap"].value() + 5)
    assert a.rows_by["target"].locked_from != "edited"  # applied after the 0.5 s pause, not per click
    qtbot.waitUntil(lambda: a.rows_by["target"].locked_from == "edited", timeout=5000)
    qtbot.waitUntil(lambda: all(r.rows["spec"][1] == a.rows_by["target"].spec[1] for r in a.records)
                    and not a.queue and not a.busy, timeout=20000)
    assert a.rows_by["target"].locked_from == "edited"
    assert [r.continuum_snr for r in a.records] != before  # new numbers with the new rows
    assert [r.path for r in a.records] == [str(p) for p in NOVA[:3]]  # replaced in place, nothing added
    assert not a.records[0].included and a.records[0].user_override
    rows = a.rows_by["target"]
    r0 = round(rows.row)
    qtbot.waitUntil(lambda: a.band_overlay.bands().spectrum.bottom == r0 + rows.spec[1], timeout=10000)
    assert a.band_overlay.bands().sky_a.bottom == r0 + rows.sky_a[1]


def test_dragging_a_band_reanalyses(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:2])
    _drain(a, qtbot)
    a.view_mode.set_index(1)
    qtbot.waitUntil(lambda: a._shown_view is not None, timeout=10000)
    b = a.band_overlay.bands()
    from starex_reduce.core.extract import Band, Bands

    a.band_overlay.moved.emit(Bands(b.spectrum, Band(b.sky_a.top - 10, b.sky_a.bottom - 10), b.sky_b))
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)
    assert a.rows_by["target"].sky_a[1] == a.records[0].rows["sky_a"][1] == b.sky_a.bottom - 10 - round(
        a.rows_by["target"].row)


def test_profile_beside_the_image_follows_its_zoom(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:1])
    _drain(a, qtbot)
    a.view_mode.set_index(1)
    qtbot.waitUntil(lambda: a._shown_view is not None, timeout=10000)
    ivb, pvb = a.viewer.vb, a.prof_plot.getPlotItem().vb
    ivb.setYRange(1050, 1080, padding=0)
    qtbot.wait(50)
    assert [round(v) for v in pvb.viewRange()[1]] == [1050, 1080]
    ivb.scaleBy(y=0.5)
    qtbot.wait(50)
    assert [round(v, 1) for v in pvb.viewRange()[1]] == [round(v, 1) for v in ivb.viewRange()[1]]
    for rng in ((1000, 1140), (300, 600)):  # around the spectrum, then sky only: peak at 80 % of the width
        a.viewer.set_rows(*rng)
        qtbot.wait(50)
        seg = a._shown_view["profile"][rng[0]:rng[1] + 1]
        x0, x1 = pvb.viewRange()[0]
        assert abs((float(seg.max()) - x0) / (x1 - x0) - 0.8) < 0.06


def test_redetect_searches_only_the_rows_in_view(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:1])
    _drain(a, qtbot)
    a.view_mode.set_index(1)
    qtbot.waitUntil(lambda: a._shown_view is not None, timeout=10000)
    assert abs(a.rows_by["target"].row - 1067) < 5
    a.zoom_chips.set_index(0)
    assert a.redetect_window() is None  # full height: the whole frame, as before
    a.viewer.set_rows(20, 260)  # the bright band near the top of the frame
    a.redetect()
    qtbot.waitUntil(lambda: "rows 20" in a.rows_by["target"].locked_from and not a.busy and not a.queue,
                    timeout=20000)
    assert 20 <= a.rows_by["target"].row <= 260
    assert a.records[0].rows["row"] == a.rows_by["target"].row  # the set was re-analysed with them


def test_reopened_session_plots_the_adu_profile(win: MainWindow, qtbot: QtBot) -> None:
    a = win.ensure_acquisition()
    a.enqueue(NOVA[:2])
    _drain(a, qtbot)
    w2 = MainWindow(session=Session.load(win.session.folder))
    qtbot.addWidget(w2)
    w2.show()
    w2.show_tab("acquisition")
    b = w2.ensure_acquisition()
    qtbot.waitUntil(lambda: b.c_max.xData is not None and len(b.c_max.xData) > 100, timeout=10000)
    assert b.records[-1].continuum_snr == a.records[-1].continuum_snr
