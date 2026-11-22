"""Plan › Planned pairs (core/planned_pairs.py, ui/screens/plan_pairs.py) and loading a pair into Acquisition."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QDate, QTime
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starextractor.core import plan as pl
from starextractor.core import planned_pairs as pp
from starextractor.core import sky
from starextractor.core.session import Session
from starextractor.core.settings import Settings
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow
from starextractor.ui.screens.plan_pairs import EMPTY, IN_ACQ, PICK_REF

VEGA = ("Vega", 279.2347, 38.7837)
DENEB = ("Deneb", 310.3580, 45.2803)
ALTAIR = ("Altair", 297.6958, 8.8683)
LAT, LON = 30.6714, -104.0225  # McDonald Observatory, a public site
SITE_LAT, SITE_LON = 12.3456, 65.4321  # an odd site, to search the plan output for


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    w = MainWindow(session=Session.create(tmp_path / "pair"))
    qtbot.addWidget(w)
    w.resize(1400, 1600)
    w.show()
    w.show_tab("plan")
    return w


def _idle(win: MainWindow, qtbot: QtBot) -> None:
    page = win.plan_page
    qtbot.wait(10)
    qtbot.waitUntil(lambda: page.conditions.idle() and page.pairs.idle(), timeout=5000)
    qtbot.wait(10)
    qtbot.waitUntil(lambda: page.conditions.idle() and page.pairs.idle(), timeout=5000)


def _editor(win: MainWindow, star, ref_row: int | None = 0) -> None:
    """Put a target (and the ref_row-th candidate as reference) into the editor."""
    page = win.plan_page
    pl.set_target(win.session, star[0], star[1], star[2], "manual", None)
    page.load_session()
    if ref_row is not None:
        page.use_selected(ref_row)


def _add(win: MainWindow, star, ref_row: int | None = 0) -> dict:
    _editor(win, star, ref_row)
    return win.plan_page.pairs.add_pair()


def _site(win: MainWindow, qtbot: QtBot, lat=LAT, lon=LON) -> None:
    card = win.plan_page.conditions
    win.settings["site"] = {"lat": lat, "lon": lon}
    card.refresh_site()
    card.date.setDate(QDate(2025, 11, 20))
    card.t_from.setTime(QTime(20, 0))
    card.t_to.setTime(QTime(6, 0))
    _idle(win, qtbot)


# 1. editing the list ------------------------------------------------------------------------------------------------
def test_empty_state_and_add_update_remove_reorder(win: MainWindow, qtbot: QtBot) -> None:
    cards = win.plan_page.pairs
    assert cards.pairs == [] and cards.empty.text() == EMPTY and cards.empty.isVisible()
    a = _add(win, VEGA)
    assert a is not None and a["reference"] is not None and cards.table.rowCount() == 1
    assert not cards.empty.isVisible() and cards.selected_id == a["id"]
    b = _add(win, DENEB, ref_row=None)  # a reference is optional
    assert b["reference"] is None and PICK_REF in cards.chips_of(1)
    assert "without a reference" in cards.status.text.text()
    c = _add(win, ALTAIR, 1)
    assert [p["target"]["name"] for p in cards.pairs] == ["Vega", "Deneb", "Altair"]

    cards.select(0)  # the editor follows the selected row
    st = pl.plan_state(win.session)
    assert st["target"]["name"] == "Vega" and st["reference"]["name"] == a["reference"]["name"]
    assert win.plan_page.name.text() == "Vega" and not cards.update_btn.isEnabled()  # nothing changed yet
    win.plan_page.use_selected(2)  # another reference in the editor
    assert cards.update_btn.isEnabled()
    assert cards.update_pair() and cards.pairs[0]["reference"]["name"] != a["reference"]["name"]
    assert not cards.update_btn.isEnabled()

    cards.select(2)
    cards.move_selected(-1)
    assert [p["target"]["name"] for p in cards.pairs] == ["Vega", "Altair", "Deneb"] and cards.selected_id == c["id"]
    cards.move_selected(-1)
    assert cards.pairs[0]["id"] == c["id"] and not cards.up_btn.isEnabled()
    cards.move_selected(1)
    assert cards.pairs[1]["id"] == c["id"]
    cards.remove_pair()
    assert [p["target"]["name"] for p in cards.pairs] == ["Vega", "Deneb"]


def test_duplicate_target_warns_but_adds(win: MainWindow) -> None:
    cards = win.plan_page.pairs
    _add(win, VEGA)
    _add(win, ("vega", VEGA[1] + 0.001, VEGA[2]), 1)  # same star, other spelling
    assert len(cards.pairs) == 2 and cards.status.state == "warn"
    assert "already planned (row 1)" in cards.status.text.text()


def test_add_needs_a_target_position(win: MainWindow) -> None:
    pl.set_target(win.session, "New nova", None, None, "manual", None)
    win.plan_page.load_session()
    assert win.plan_page.pairs.add_pair() is None and win.plan_page.pairs.pairs == []
    assert win.plan_page.pairs.status.state == "err"


def test_sort_by_transit(win: MainWindow, qtbot: QtBot) -> None:
    for star in (DENEB, VEGA, ALTAIR):
        _add(win, star)
    cards = win.plan_page.pairs
    assert not cards.sort_by_transit()  # no location yet
    _site(win, qtbot)
    assert cards.sort_by_transit()
    w = win.plan_page.conditions.window()
    transit = {s[0]: sky.compute(LAT, LON, w, 25, s[1], s[2]).transit[0] for s in (DENEB, VEGA, ALTAIR)}
    assert [p["target"]["name"] for p in cards.pairs] == sorted(transit, key=transit.get)


def test_persists_across_restart_with_a_schema_version(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = _add(win, VEGA)
    win.plan_page.pairs.note.setText("try 600 s subs")
    win.plan_page.pairs._note_edited()
    stored = Settings()["plan"]["pairs"]
    assert stored["version"] == pp.SCHEMA == 1 and stored["items"][0]["id"] == a["id"]
    assert set(stored["items"][0]) == {"id", "target", "reference", "note", "created"}  # nothing computed stored
    w2 = MainWindow(session=Session.create(tmp_path / "pair2"))
    qtbot.addWidget(w2)
    w2.show_tab("plan")
    again = w2.plan_page.pairs.pairs
    assert [p["id"] for p in again] == [a["id"]] and again[0]["note"] == "try 600 s subs"
    assert pp.load({"pairs": {"version": 7, "items": stored["items"]}})[0]["id"] == a["id"]  # tolerant


# 2. visibility per row ----------------------------------------------------------------------------------------------
def test_rows_show_visible_window_and_moon_separation(win: MainWindow, qtbot: QtBot) -> None:
    for star in (VEGA, ("south", 90.0, -75.0)):
        _add(win, star, None)
    _site(win, qtbot)
    cards = win.plan_page.pairs
    w = win.plan_page.conditions.window()
    vega = sky.compute(LAT, LON, w, 25, VEGA[1], VEGA[2])
    assert cards.table.item(0, 3).text() == sky.visible_short(vega) and "–" in cards.table.item(0, 3).text()
    assert cards.table.item(0, 4).text() == f"{vega.moon_sep:.0f}°"
    assert cards.table.item(1, 3).text() == "never rises"


def test_visuals_follow_the_selected_row(win: MainWindow, qtbot: QtBot) -> None:
    _add(win, VEGA)
    _add(win, ALTAIR)
    _site(win, qtbot)
    card = win.plan_page.conditions
    w = card.window()
    for row, star in ((0, VEGA), (1, ALTAIR)):
        win.plan_page.pairs.select(row)
        _idle(win, qtbot)
        want = sky.compute(LAT, LON, w, 25, star[1], star[2])
        assert np.allclose(card.plan.target_alt, want.target_alt)


# 3. the observing plan ----------------------------------------------------------------------------------------------
def test_plan_text_and_json_cover_all_pairs_and_never_the_site(win: MainWindow, qtbot: QtBot) -> None:
    for star in (VEGA, DENEB):
        _add(win, star)
    win.plan_page.pairs.note.setText("after midnight")
    win.plan_page.pairs._note_edited()
    _site(win, qtbot, SITE_LAT, SITE_LON)
    page = win.plan_page
    text = page.plan_output()
    assert "Pairs:       2" in text and "1. Vega + " in text and "2. Deneb + " in text
    assert "Date:        2025-11-20" in text and "Note:" in text and "after midnight" in text
    assert "Visible:" in text and "Moon:" in text
    page.fmt.set_index(1)
    doc = json.loads(page.plan_output())
    assert doc["kind"] == pp.PLAN_FILE_KIND and [p["target"]["name"] for p in doc["pairs"]] == ["Vega", "Deneb"]
    assert doc["pairs"][0]["reference"]["ra"] and doc["pairs"][1]["note"] == "after midnight"
    for out in (text, page.plan_output()):
        assert "12.34" not in out and "65.43" not in out  # no coordinates of the observing site


def test_open_plan_round_trip_replace_and_merge(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    page = win.plan_page
    for star in (VEGA, DENEB):
        _add(win, star)
    page.fmt.set_index(1)
    saved = page.save_plan(str(tmp_path / "plan.json"))
    before = [dict(p) for p in page.pairs.pairs]
    page.pairs.select(0)
    page.pairs.remove_pair()
    _add(win, ALTAIR)
    assert page.open_plan(str(saved), mode="merge") == 1  # Deneb is already there: only Vega comes back
    assert [p["target"]["name"] for p in page.pairs.pairs] == ["Deneb", "Altair", "Vega"]
    assert page.open_plan(str(saved), mode="replace") == 2
    assert page.pairs.pairs == before  # ids, notes, created, positions: a round trip
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert page.open_plan(str(bad)) is None and page.plan_result.state == "err"


def test_open_plan_asks_replace_or_merge(win: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = win.plan_page
    _add(win, VEGA)
    page.fmt.set_index(1)
    saved = page.save_plan(str(tmp_path / "p.json"))
    asked: list[tuple] = []
    monkeypatch.setattr(page, "_ask_replace_or_merge", lambda *a: asked.append(a) or None)
    assert page.open_plan(str(saved)) is None and asked and len(page.pairs.pairs) == 1


# 4. loading a pair into Acquisition ---------------------------------------------------------------------------------
@pytest.fixture
def subs(tmp_path: Path):
    from starextractor.synth import write_example

    return write_example(tmp_path / "night")


def _drain(a, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)


def test_load_sets_names_and_coordinates_never_folders(win: MainWindow, qtbot: QtBot) -> None:
    pair = _add(win, ("V1405 Cas", 351.1592, 61.1525))
    pair["target"]["source"] = "simbad"
    acq = win.ensure_acquisition()
    acq.folders["target"].setText("D:/capture/tonight")
    asked: list[str] = []
    assert acq.load_planned_pair(pair, ask=lambda t: asked.append(t) or True)
    assert asked == []  # empty sets: no question
    assert acq.names["target"].text() == "V1405 Cas" and acq.names["reference"].text() == pair["reference"]["name"]
    assert acq.name_source == {"target": "plan", "reference": "plan"}
    assert acq.sets["target"]["coord_source"] == "simbad" and acq.coord_srcs["target"].index() == 0
    assert acq.ras["target"].text() == "23 24 38.2" and acq.decs["target"].text().startswith("+61 09")
    assert not acq.plan_tags["target"].isHidden() and not acq.plan_tags["reference"].isHidden()
    assert acq.folders["target"].text() == "D:/capture/tonight"  # never a folder
    assert acq.sets_doc()["target"]["name_source"] == "plan"


def test_manual_target_uses_enter_by_hand(win: MainWindow) -> None:
    pair = _add(win, VEGA)
    acq = win.ensure_acquisition()
    acq.load_planned_pair(pair)
    assert acq.sets["target"]["coord_source"] == "manual" and acq.coord_srcs["target"].index() == 2
    assert acq.ras["target"].text() == "18 36 56.3"


def test_reference_less_pair_sets_only_the_target(win: MainWindow) -> None:
    pair = _add(win, DENEB, None)
    acq = win.ensure_acquisition()
    acq.names["reference"].setText("bet Cas")
    acq.load_planned_pair(pair)
    assert acq.names["target"].text() == "Deneb" and acq.names["reference"].text() == "bet Cas"
    assert acq.name_source["reference"] != "plan"


def test_load_asks_when_subs_belong_to_another_target(win: MainWindow, qtbot: QtBot, subs, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    pair = _add(win, VEGA)
    acq = win.ensure_acquisition()
    acq.names["target"].setText("beta cas")
    acq.folders["target"].setText(str(subs["target"][0].parent))
    acq.enqueue(subs["target"])
    _drain(acq, qtbot)
    n = len(acq.records)
    acq.toggle_watch()
    assert acq.watching() and n > 0
    cleared: list[bool] = []
    real_clear = acq.clear_all
    monkeypatch.setattr(acq, "clear_all", lambda confirm=True: cleared.append(confirm) or real_clear(confirm))

    asked: list[str] = []
    assert not acq.load_planned_pair(pair, ask=lambda t: asked.append(t) or False)  # No: nothing changes
    assert asked == [f"Acquisition has {n} subs for beta cas. Load Vega and clear them? Saved stacks stay in your "
                     "data folder."]
    assert acq.names["target"].text() == "beta cas" and len(acq.records) == n and acq.watching() and not cleared

    assert acq.load_planned_pair(pair, ask=lambda t: True)  # Yes
    assert cleared == [False] and not acq.watching() and acq.records == []
    assert acq.folders["target"].text() == "" and acq.folders["reference"].text() == ""
    assert acq.names["target"].text() == "Vega"

    acq.folders["target"].setText(str(subs["target"][0].parent))  # the same pair again: no question, keep folders
    acq.enqueue(subs["target"][:1])
    _drain(acq, qtbot)
    asked.clear()
    assert acq.load_planned_pair(pair, ask=lambda t: asked.append(t) or True)
    assert asked == [] and acq.folders["target"].text() and len(acq.records) == 1
    acq.stop_watch()


# 5. the strip and the chip ------------------------------------------------------------------------------------------
def test_strip_edited_and_change_in_plan(win: MainWindow, qtbot: QtBot) -> None:
    _add(win, VEGA)
    second = _add(win, ALTAIR)
    cards = win.plan_page.pairs
    assert cards.load_for_acquisition(1)
    acq = win.acquisition_page
    assert win.tab_name() == "acquisition" and acq.strip.isVisible()
    ref = second["reference"]["name"]
    assert acq.strip_text.text() == f"Planned pair: Altair + {ref} (from Plan)" and not acq.strip_edited.isVisible()
    assert cards.chips_of(1) == [IN_ACQ] and IN_ACQ not in cards.chips_of(0)

    acq.names["target"].setText("Altair b")
    acq._name_typed("target")
    assert acq.strip_edited.isVisible() and cards.chips_of(1) == []  # the chip follows the names
    acq.names["target"].setText("Altair")
    acq._name_typed("target")
    assert not acq.strip_edited.isVisible() and cards.chips_of(1) == [IN_ACQ]

    cards.select(0)
    acq.change_in_plan()
    assert win.tab_name() == "plan" and cards.selected_id == second["id"]
    assert pl.plan_state(win.session)["target"]["name"] == "Altair"

    acq.clear_all(confirm=False)
    assert not acq.strip.isVisibleTo(acq) and cards.chips_of(1) == []


# 6. palettes ----------------------------------------------------------------------------------------------------------
def _rgb(w) -> np.ndarray:
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, : img.width(), [2, 1, 0]].astype(int)


@pytest.mark.parametrize("palette", ["night", "red"])
def test_new_card_and_strip_in_the_night_palettes(win: MainWindow, qtbot: QtBot, palette: str) -> None:
    from starextractor.ui import retheme

    _add(win, VEGA)
    _add(win, DENEB, None)
    win.plan_page.pairs.load_for_acquisition(0)
    retheme.switch(palette)
    try:
        qtbot.wait(50)
        cards, strip = win.plan_page.pairs, win.acquisition_page.strip
        for w in (cards, strip):
            rgb = _rgb(w)
            lit = rgb.sum(axis=2) > 30
            r, g, b = (rgb[..., i][lit] for i in range(3))
            if palette == "red":
                assert np.all(g <= 2) and np.all(b <= 2), type(w).__name__
            else:
                assert np.all(g <= r + 2) and np.all(b <= r + 2), type(w).__name__
        # the two chips differ by text and by shape (a filled pill, an outlined tag)
        from PySide6.QtWidgets import QLabel

        chip_in = cards.table.cellWidget(0, 5).findChild(QLabel, "inAcq")
        chip_ref = cards.table.cellWidget(1, 5).findChild(QLabel, "pickRef")
        assert chip_in.text().startswith("●") and chip_ref.text().startswith("!")  # Plan layout 2026-11-21
        assert chip_in.property("role") == "chip-ok" and chip_ref.property("role") == "chip-warn"
    finally:
        retheme.switch("default")


def test_core_round_trip_and_merge() -> None:
    a = pp.normalise({"target": {"name": "Vega", "ra_deg": 279.2347, "dec_deg": 38.7837, "source": "simbad"},
                      "reference": {"name": "HR 7001", "ra_deg": 279.0, "dec_deg": 38.0, "vmag": 0.03},
                      "note": "n"})
    b = pp.normalise({"target": {"name": "Deneb", "ra": "20 41 25.9", "dec": "+45 16 49"}})  # by hand
    assert b["target"]["ra_deg"] == pytest.approx(310.358, abs=1e-3) and b["reference"] is None
    doc = pp.plan_doc([a, b], {"date": "2025-11-20", "window": {"from": "20:00", "to": "06:00", "min_alt": 25}})
    assert pp.pairs_from_doc(json.dumps(doc)) == [a, b]
    merged, added = pp.merge([a], [a, b])
    assert added == 1 and [p["target"]["name"] for p in merged] == ["Vega", "Deneb"]
    with pytest.raises(ValueError):
        pp.pairs_from_doc("[]")
    assert pp.pairs_from_doc({"target": {"name": "X", "ra_deg": 1, "dec_deg": 2}, "reference": None})[0]["target"]
