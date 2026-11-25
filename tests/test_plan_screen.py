"""GUI test of the Plan tab: target entry and the reference-star candidates."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from starextractor.core import simbad
from starextractor.core.session import Session
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    w.show_tab("plan")
    return w


def _type_coords(plan, ra: str, dec: str) -> None:
    for e, text in ((plan.ra, ra), (plan.dec, dec)):
        e.setText(text)
        e.textEdited.emit(text)
        e.editingFinished.emit()


def _distances(plan) -> list[float]:
    return [float(plan.table.item(r, 1).text()) for r in range(plan.table.rowCount())]


def test_typed_position_lists_ten_nearest(win: MainWindow) -> None:
    plan = win.ensure_plan()
    assert plan.table.rowCount() == 0 and plan.source_chip.text() == "No position"
    plan.name.setText("V8055 Sgr")
    _type_coords(plan, "18 53 02", "-28 20 00")
    assert plan.table.rowCount() == 10 and plan.source_chip.text() == "Manual"
    d = _distances(plan)
    assert d == sorted(d)
    plan.n_box.setValue(5)
    assert plan.table.rowCount() == 5
    _type_coords(plan, "282.470489", "32.55106")  # exact catalogue position of HD 174602 (graded good)
    assert plan.table.item(0, 0).text() == "HD 174602" and plan.matches[0].distance_deg < 1e-3
    assert win.session.inputs["target"]["name"] == ""  # the plan does not fill in Files


def test_bad_coordinates_show_an_error(win: MainWindow) -> None:
    plan = win.ensure_plan()
    _type_coords(plan, "25 00 00", "+10 00 00")
    assert plan.coord_error.isVisible() and "out of range" in plan.coord_error.text.text()
    assert plan.table.rowCount() == 0


def test_simbad_offline_then_manual_entry(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simbad, "lookup_detailed", simbad.fixed_answer(None))  # no server answers, nothing kept
    plan = win.ensure_plan()
    plan.name.setText("Nova Sgr 2026")
    plan.check_simbad()
    qtbot.waitUntil(lambda: plan.simbad_btn.isEnabled(), timeout=5000)
    assert plan.simbad_status.state == "warn" and "SIMBAD unreachable" in plan.simbad_status.text.text()
    _type_coords(plan, "18 53 02", "-28 20 00")
    assert plan.table.rowCount() == 10


def test_simbad_found_fills_position(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    res = simbad.SimbadResult("gam Cas", True, "* gam Cas", 14.17721289, 60.71674002, "Be*", "B0.5IVpe", 2.39)
    monkeypatch.setattr(simbad, "lookup_detailed", simbad.fixed_answer(res))
    plan = win.ensure_plan()
    plan.name.setText("gam Cas")
    plan.check_simbad()
    qtbot.waitUntil(lambda: plan.simbad_btn.isEnabled(), timeout=5000)
    assert plan.ra.text() == "00 56 42.5" and plan.dec.text() == "+60 43 00"
    assert plan.source_chip.text() == "SIMBAD" and "V 2.39" in plan.simbad_status.text.text()
    assert plan.table.rowCount() == 10
    assert "V:           2.39" in plan.plan_box.toPlainText()
    assert not win.session.inputs["target"].get("simbad")  # not linked to Files


def test_reference_choice_goes_into_the_observing_plan_only(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    import json

    from starextractor.core.response_stage import params_of

    plan = win.ensure_plan()
    plan.name.setText("V8055 Sgr")
    plan.name.editingFinished.emit()
    _type_coords(plan, "18 53 02", "-28 20 00")
    text = plan.plan_box.toPlainText()
    assert "Target:      V8055 Sgr" in text and "RA (J2000):  18 53 02.0" in text and "Reference:   -" in text
    plan.table.selectRow(0)
    plan.use_btn.click()
    first = plan.matches[0].star
    assert plan.table.item(0, 0).text() == "✓ " + first.name
    text = plan.plan_box.toPlainText()
    assert f"Reference:   {first.name}" in text and f"V:           {first.v_mag:.2f}" in text

    plan.fmt.set_index(1)  # JSON
    data = json.loads(plan.plan_box.toPlainText())
    assert data["target"] == {"name": "V8055 Sgr", "vmag": None, "ra": "18 53 02.0", "dec": "-28 20 00",
                              "source": "manual"}
    assert data["reference"]["name"] == first.name and data["reference"]["vmag"] == first.v_mag
    plan.copy_plan()
    from PySide6.QtGui import QGuiApplication
    assert "Copied" in plan.plan_result.text.text()
    if QGuiApplication.clipboard().text():  # the offscreen test platform has no real clipboard
        assert json.loads(QGuiApplication.clipboard().text())["reference"]["name"] == first.name
    out = plan.save_plan(str(tmp_path / "plan.json"))
    assert json.loads(out.read_text(encoding="utf-8")) == data

    # Files inputs stay untouched
    assert win.session.inputs["reference"]["name"] == "" and not win.session.inputs["reference"].get("catalog")
    # Files is never filled in by itself; Response may use the Plan's spectral type while Files names no
    # other star (core/identity.sp_type_for), and says so in its note
    ctx = params_of(win.session, win.profile)["_ctx"]
    assert ctx["sptype"] == first.sp_type and ctx["sptype_source"] == "Plan"

    again = Session.load(win.session.folder)  # the plan itself survives a restart
    assert again.params["plan"]["reference"]["name"] == first.name
    w2 = MainWindow(session=again)
    qtbot.addWidget(w2)
    w2.show_tab("plan")
    p2 = w2.ensure_plan()
    assert p2.name.text() == "V8055 Sgr" and p2.table.item(0, 0).text().startswith("✓ ")
    assert first.name in p2.plan_box.toPlainText()

    _type_coords(p2, "05 00 00", "+10 00 00")  # a different target clears the choice
    assert not again.params["plan"]["reference"] and "Target changed" in p2.ref_status.text.text()
    assert "Reference:   -" in p2.plan_box.toPlainText()


PCYG = simbad.SimbadResult("P Cyg", True, "* P Cyg", 304.44667489, 38.03293031, "s*b", "B1-2Ia-0ep", 4.82)


def _simbad_pcyg(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(simbad, "lookup_detailed", simbad.fixed_answer(PCYG))
    plan = win.ensure_plan()
    plan.name.setText("P Cyg")
    plan.check_simbad()
    qtbot.waitUntil(lambda: plan.simbad_btn.isEnabled(), timeout=5000)
    return plan


def _plan_lines(win: MainWindow) -> list[str]:
    return [ln for ln in win.session.log_lines if "Plan:" in ln]


def _msg(line: str) -> str:
    return line.split("  ", 1)[1]


# ---- 1: every reference change is logged, and the newest line is always in view ------------------------
def test_every_reference_change_is_logged_and_visible(win: MainWindow, qtbot: QtBot,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(30):  # a long log, filled before the Plan screen existed
        win.session.log(f"earlier line {i}")
    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    for row in (0, 2, 1):
        plan.table.selectRow(row)
        plan.use_btn.click()
        m = plan.matches[row]
        assert _msg(_plan_lines(win)[-1]) == (f"Plan: reference star {m.star.name} ({m.star.sp_type}, "
                                              f"V {m.star.v_mag:.2f}, {m.distance_deg:.2f}°, grade {m.grade})")
        assert win.session.params["plan"]["reference"]["name"] == m.star.name
        assert plan.log.last_visible_line().endswith(_msg(_plan_lines(win)[-1]))
        selected = plan.table.selectionModel().selectedRows()[0].row()
        assert plan.matches[selected].star.name == m.star.name  # selection, status, plan and log agree
        assert m.star.name in plan.ref_status.text.text()
    n = len(_plan_lines(win))
    plan.use_selected(1)  # the same star again: no change, no log line
    assert len(_plan_lines(win)) == n
    old = win.session.params["plan"]["reference"]["name"]
    _type_coords(plan, "05 00 00", "+10 00 00")
    assert any(f"reference star {old} cleared (target changed)" in ln for ln in _plan_lines(win)[-2:])
    assert plan.log.last_visible_line().endswith(_msg(_plan_lines(win)[-1]))


def test_log_panel_filled_while_hidden_still_follows(qtbot: QtBot) -> None:
    from starextractor.ui.log_panel import LogPanel

    panel = LogPanel()
    qtbot.addWidget(panel)
    panel.set_lines([f"line {i}" for i in range(40)])  # before it is ever shown
    panel.show()
    panel.resize(600, panel.sizeHint().height())
    panel.append("newest")
    assert panel.last_visible_line() == "newest"


# ---- 2: the Grade column (the spectral-type flag only for rows without a grade) -------------------------------
def test_flux_flag_is_the_fallback_without_a_grade() -> None:
    from starextractor.core.plan import FLUX_WARN_TIP, flux_flag

    for sp, flag in (("B2Ve", "warn"), ("B5V", "warn"), ("O9V", "warn"), ("A1Vne", "warn"), ("A0V", "good"),
                     ("A3V", "good"), ("A0Vn", "good"), ("A4V", None), ("A2IV", None), ("F0V", None)):
        assert flux_flag(sp) == flag, sp
    assert FLUX_WARN_TIP == "Emission/early type: poor flux reference"


def test_grade_chips_tooltips_and_markers(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from starextractor.core import reference_stars as rs
    from starextractor.core.plan import GRADE_CHIP

    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    assert plan.table.horizontalHeaderItem(5).text() == "Grade" and not plan.include_poor.isChecked()
    plan.n_box.setValue(30)
    expected = rs.find_reference_stars(PCYG.ra_deg, PCYG.dec_deg, 30, 6.5, ("good", "ok"))
    assert [m.star.name for m in plan.matches] == [m.star.name for m in expected]  # good and ok only, nearest first
    for r, m in enumerate(plan.matches):
        grade, tip, tags = plan.grade_of(r)
        assert grade == m.grade in ("good", "ok")
        chip = plan.table.cellWidget(r, 5).findChild(QWidget, "grade_chip")
        assert chip.text().startswith(GRADE_CHIP[grade])
        if m.reason_text:
            assert m.reason_text.split("; ")[0] in tip
        assert ("faint" in tags) == (m.v_class == "faint") and "best" not in tags  # best only with poor listed
        assert m.star.sp_type in plan.table.item(r, 4).toolTip()
    plan.include_poor.setChecked(True)
    assert win.settings["plan"]["ref_include_poor"] is True
    every = rs.find_reference_stars(PCYG.ra_deg, PCYG.dec_deg, 30, 6.5, rs.GRADES)
    assert [m.star.name for m in plan.matches] == [m.star.name for m in every]
    grades = [plan.grade_of(r)[0] for r in range(plan.table.rowCount())]
    assert "poor" in grades and all(g for g in grades)  # the grade is always visible
    best = [r for r in range(plan.table.rowCount()) if "best" in plan.grade_of(r)[2]]
    first_good_ok = next(r for r, g in enumerate(grades) if g in ("good", "ok"))
    assert best == [first_good_ok]
    poor_chip = plan.table.cellWidget(grades.index("poor"), 5).findChild(QWidget, "grade_chip")
    assert poor_chip.text() == "! poor"


def test_verified_marker(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from starextractor.core import reference_stars as rs

    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    m = plan.matches[0]
    m2 = rs.ReferenceStarMatch(replace(m.star, verified=True), m.distance_deg, m.best)
    plan._set_grade(0, m2)
    _grade, tip, _ = plan.grade_of(0)
    assert "✔" in plan.table.cellWidget(0, 5).findChild(QWidget, "grade_chip").text()
    assert "Verified by hand" in tip


def test_poor_hint_and_faintest_v(win: MainWindow) -> None:
    from starextractor.core import reference_stars as rs

    plan = win.ensure_plan()
    near = rs.nearer_poor(282.52, 33.36)  # Sheliak: nu.01 Lyr (poor) is nearer than HD 174602 (good)
    assert near is not None and near.star.name == "HD 174585"
    _type_coords(plan, "18 50 04.8", "+33 21 46")
    assert plan.poor_hint.isVisible()
    assert plan.poor_hint.text().startswith("A closer star is graded poor: HD 174585 (0.")
    assert near.reason_text in plan.poor_hint.text()
    assert plan.matches[0].star.name == "HD 174602" and plan.matches[0].grade == "good"
    _type_coords(plan, "282.470489", "32.55106")  # on HD 174602 itself: nothing nearer
    assert not plan.poor_hint.isVisible()
    plan.n_box.setValue(50)
    assert any(m.v_class == "faint" for m in plan.matches)
    plan.v_box.setValue(6.0)
    assert win.settings["plan"]["ref_v_max"] == 6.0
    assert plan.matches and all(m.star.v_mag <= 6.0 and m.v_class == "normal" for m in plan.matches)
    plan.v_box.setValue(6.5)


# ---- 4: bare coordinate copies -------------------------------------------------------------------------
def test_copy_bare_coordinates(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from starextractor.core.coords import format_dec, format_ra

    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    assert not plan.copy_ref_btn.isEnabled() and plan.copy_coords("reference") is None
    assert plan.copy_coords("target") == "20 17 47.2  +38 01 59"
    plan.copy_buttons["ra"].click()
    assert "Copied target RA: 20 17 47.2" in plan.plan_result.text.text()
    assert plan.copy_coords("target", "dec") == "+38 01 59"
    plan.table.selectRow(0)
    plan.use_btn.click()
    m = plan.matches[0].star
    assert plan.copy_ref_btn.isEnabled()
    assert plan.copy_coords("reference") == f"{format_ra(m.ra)}  {format_dec(m.dec)}"
    text = plan.plan_box.toPlainText()
    assert text.startswith("Target:      P Cyg") and "Reference:   " + m.name in text  # side-by-side layout kept


# ---- 5: position source ---------------------------------------------------------------------------------
def test_position_source_chip_log_and_json(win: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    assert plan.source_chip.text() == "SIMBAD"
    assert _plan_lines(win)[-1].endswith("20 17 47.2 +38 01 59, source simbad")
    plan.fmt.set_index(1)
    assert json.loads(plan.plan_box.toPlainText())["target"]["source"] == "simbad"
    _type_coords(plan, "20 17 48.0", "+38 01 59")  # the user edits RA
    assert plan.source_chip.text() == "Manual"
    assert any(ln.endswith("20 17 48.0 +38 01 59, source manual") for ln in _plan_lines(win)[-2:])
    assert json.loads(plan.plan_box.toPlainText())["target"]["source"] == "manual"
    assert plan.source_chip.parentWidget() is plan.ra.parentWidget()  # the chip sits with RA/Dec


# ---- 6: scrolling and a single amber button ------------------------------------------------------------
def test_plan_card_scrolls_clear_of_log_and_one_primary(win: MainWindow, qtbot: QtBot,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QPushButton

    def primaries() -> list:
        return [b for b in plan.findChildren(QPushButton) if b.property("role") == "primary"]

    plan = _simbad_pcyg(win, qtbot, monkeypatch)
    plan.n_box.setValue(50)
    qtbot.wait(50)  # layout pass
    bar = plan.scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    qtbot.wait(20)
    vp = plan.scroll.viewport()
    assert plan.plan_box.mapTo(vp, plan.plan_box.rect().bottomLeft()).y() <= vp.height()
    assert primaries() == [] and not plan.use_btn.isEnabled()  # nothing selected: no amber
    plan.table.selectRow(3)
    assert primaries() == [plan.use_btn] and plan.use_btn.isEnabled()
    plan.use_btn.click()
    assert primaries() == [] and plan.table.selectionModel().selectedRows()[0].row() == 3
