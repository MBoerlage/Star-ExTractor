"""The workflow rail: nine steps in three groups, states from the old ribbon sources, navigation, keyboard, the
Files → Select rename, no hard-coded colours, narrow widths, repaint only."""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from starextractor.core.pipeline import STAGES
from starextractor.core.session import Session, Status
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow
from starextractor.ui.rail import GROUPS, NARROW_W, Rail, StepState

ROOT = Path(__file__).resolve().parents[1]
REDUCE = ("select", "geometry", "extract", "wavelength", "response")


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.resize(1600, 900)
    w.show()
    return w


# 1 -------------------------------------------------------------------------------------------------------------
def test_nine_steps_in_three_groups_with_progress(win: MainWindow) -> None:
    r = win.rail
    assert [(g.id, g.steps) for g in GROUPS] == [
        ("acquisition", ["acquisition"]), ("reduce", list(REDUCE)), ("export", ["collect", "test", "publish"])]
    assert [s.label for s in r.steps] == ["Capture, stack", "Select", "Geometry", "1D Extract", "Wavelength",
                                         "Response", "Collect", "Test", "Publish"]
    g = {x.id: x for x in GROUPS}
    assert r.progress_text(g["reduce"]) == "0 of 5 done" and r.progress_text(g["acquisition"]) == ""
    for sid in ("files", "geometry", "extract"):
        win.session.mark_done(sid, {}, f"{sid} ok")
    win.refresh_ribbon()
    assert r.progress_text(g["reduce"]) == "3 of 5 done"
    for sid in ("wavelength", "response"):
        win.session.mark_done(sid, {}, f"{sid} ok")
    win.refresh_ribbon()
    assert r.progress_text(g["reduce"]) == "5 of 5 done" and r.progress_text(g["export"]).endswith("of 3")
    rects = r._group_rects()  # widths follow the step counts; Acquisition a little wider than one step
    per_step = rects[1][1].width() / 5
    assert per_step < rects[0][1].width() < 2 * per_step and abs(rects[2][1].width() / 3 - per_step) < 1


# 2 -------------------------------------------------------------------------------------------------------------
def test_state_mapping_and_cascade(win: MainWindow) -> None:
    r = win.rail
    assert r.shown_state("select") == StepState.CURRENT and r.shown_state("geometry") == StepState.NOT_RUN
    for sid in ("files", "geometry", "extract", "wavelength", "response"):
        win.session.mark_done(sid, {}, f"{sid} ok")
    win.open_step(4)
    assert [r.shown_state(x) for x in REDUCE] == [StepState.DONE] * 4 + [StepState.CURRENT]
    assert r.by_id["response"].status == "response ok"  # the old second line, now in the tooltip
    win.session.set_params("wavelength", {"mode": "free"})  # an upstream change …
    win.refresh_ribbon()
    assert win.session.status["wavelength"].status == Status.STALE
    assert [r.shown_state(x) for x in REDUCE[2:]] == [StepState.DONE, StepState.NOT_RUN, StepState.CURRENT]
    assert r.by_id["response"].state == StepState.NOT_RUN  # stale looks like not run
    win.session.mark_missing("geometry", "No stacks")
    win.open_step(1)
    win.refresh_ribbon()
    assert r.shown_state("geometry") == StepState.MISSING  # missing data beats current
    assert "No stacks" in r.tooltip_of(2)


def test_acquisition_step_done_when_the_pairs_stacks_exist(win: MainWindow, tmp_path: Path) -> None:
    r = win.rail
    assert r.by_id["acquisition"].state == StepState.NOT_RUN
    t, ref = tmp_path / "T_target_S.fit", tmp_path / "R_ref_S.fit"
    t.write_bytes(b"x")
    ref.write_bytes(b"x")
    win.session.params["files"] = {"pairs": [{"target": {"source": str(t)}, "reference": {"source": str(ref)}}]}
    win.refresh_ribbon()
    assert r.by_id["acquisition"].state == StepState.DONE
    win.show_tab("acquisition")
    assert r.shown_state("acquisition") == StepState.CURRENT


# 3 -------------------------------------------------------------------------------------------------------------
def test_navigation_groups_keyboard_and_tools(win: MainWindow, qtbot: QtBot) -> None:
    r = win.rail
    for i, rid in enumerate(REDUCE):
        win.go_to(rid)
        assert win.tab_name() == "reduce" and win.current == i and r.current == rid
    win.go_to("acquisition")
    assert win.tab_name() == "acquisition" and r.current == "acquisition"
    for i, rid in enumerate(("collect", "test", "publish")):
        win.go_to(rid)
        assert win.tab_name() == "export" and win.export_screen.step == i and r.current == rid
        assert win.export_screen.pages.currentIndex() == i
    win.go_to_group("reduce")  # not in the group: its first step not done
    assert r.current == "select"
    win.session.mark_done("files", {}, "pair")
    win.refresh_ribbon()
    win.go_to("export" if False else "test")
    win.go_to_group("reduce")
    assert r.current == "geometry"
    win.go_to_group("reduce")  # in the group: stays on its current step
    assert r.current == "geometry"
    win.show_tab("plan")
    assert r.current is None and win.plan_btn.isChecked()  # no current step on Plan
    win.show_tab("library")
    assert r.current is None and win.library_btn.isChecked() and not win.plan_btn.isChecked()
    seen: list[str] = []
    r.step_clicked.connect(seen.append)
    r.setFocus()
    r._focus = 1
    qtbot.keyClick(r, Qt.Key.Key_Right)
    qtbot.keyClick(r, Qt.Key.Key_Return)
    assert seen == ["geometry"] and "Geometry" in r.accessibleName()
    assert r.current == "geometry" and win.tab_name() == "reduce"


def test_click_hits_the_right_step_and_header(win: MainWindow, qtbot: QtBot) -> None:
    r = win.rail
    rects = r._step_rects()
    qtbot.mouseClick(r, Qt.MouseButton.LeftButton, pos=rects[3].center().toPoint())
    assert r.current == "extract" and win.current == 2
    g = r._group_rects()[2][1]
    from PySide6.QtCore import QPoint

    qtbot.mouseClick(r, Qt.MouseButton.LeftButton, pos=g.topLeft().toPoint() + QPoint(20, 8))
    assert win.tab_name() == "export" and r.current in ("collect", "test", "publish")


# 4 -------------------------------------------------------------------------------------------------------------
def test_old_tabs_and_ribbons_are_gone(win: MainWindow) -> None:
    import importlib

    for mod in ("starextractor.ui.ribbon", "starextractor.ui.tab_row"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)
    assert not hasattr(win, "ribbon") and not hasattr(win, "tabs") and not hasattr(win, "ribbon_box")
    ex = win.ensure_export()
    assert not hasattr(ex, "steps") and len(ex.step_states) == 3
    assert len(win.findChildren(Rail)) == 1  # one rail, no inner ribbons
    win.go_to("test")  # Export works from the rail alone
    assert ex.pages.currentIndex() == 1 and win.legend.isVisible()


# 5 -------------------------------------------------------------------------------------------------------------
def test_rename_files_to_select(win: MainWindow, tmp_path: Path, qtbot: QtBot) -> None:
    assert STAGES[0].title == "Select" and win.screens[0].header.title.text() == "Select"
    win.run_stage("files")
    assert win.session.log_lines[-1].endswith("Load a pair in Select first.")
    step_name = re.compile(r"(Reduce\s*[>›]\s*Files|\bFiles step\b|in Files first|\bFiles says\b|"
                           r"Files › Pair notes|Files › Load pair|from Files:)")
    hits = []
    for p in [*ROOT.joinpath("starextractor").rglob("*.py"), ROOT / "README.md",
              *(d for d in ROOT.joinpath("docs").glob("*.md") if d.name != "BUILD_PLAN.md")]:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if step_name.search(line) and not re.search(r"old Files step|called Files|now Select", line):
                hits.append(f"{p.name}:{n}: {line.strip()[:90]}")
    assert not hits, hits
    # an old stored location (the step id from before) opens on Select
    from starextractor.core.settings import Settings

    sp = tmp_path / "home" / "settings.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"location": "files"}), "utf-8")
    w = MainWindow(session=Session(), settings=Settings(sp))
    qtbot.addWidget(w)
    w.show_tab("export")
    w.restore_location()
    assert w.tab_name() == "reduce" and w.rail.current == "select"


# 6 -------------------------------------------------------------------------------------------------------------
def test_rail_uses_theme_tokens_only() -> None:
    src = ROOT.joinpath("starextractor", "ui", "rail.py").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{6}\b|QColor\(\s*\d", src)  # colours come from theme only


# 7 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("width", ["min", 1100, 1600])
def test_labels_fit_at_narrow_widths(win: MainWindow, qtbot: QtBot, width: object) -> None:
    w = win.minimumWidth() if width == "min" else int(width)  # type: ignore[call-overload]
    win.resize(w, 900)
    qtbot.wait(30)
    r = win.rail
    if r.width() < NARROW_W:
        assert r.label_of(r.by_id["extract"]) == "Extract" and r.label_of(r.by_id["acquisition"]) == "Capture"
    if r.width() >= NARROW_W + 200:
        assert not [r.steps[i].label for i in range(len(r.steps)) if r.elided(i)]  # nothing elided
    # narrower: a label may elide (… inside its chevron, never past it), the whole name is in the tooltip
    for i, s in enumerate(r.steps):
        box = r._text_box(i, r._step_rects()[i], r._firsts()[i])
        assert box.width() > 20 and s.label in r.tooltip_of(i)
    assert all(r.tooltip_of(i) for i in range(len(r.steps)))
    rects = r._step_rects()  # no overlap beyond the interlocking points
    for a, b in itertools.pairwise(rects):
        assert b.left() >= a.right() - 12


# 8 -------------------------------------------------------------------------------------------------------------
def test_state_change_is_a_repaint_only(win: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    r = win.rail
    calls = {"update": 0, "geometry": 0}
    monkeypatch.setattr(r, "update", lambda *a: calls.__setitem__("update", calls["update"] + 1))
    monkeypatch.setattr(r, "updateGeometry", lambda: calls.__setitem__("geometry", calls["geometry"] + 1))
    hint = r.sizeHint()
    for _ in range(50):
        r.set_step("geometry", state=StepState.DONE, status="x")
        r.set_step("geometry", state=StepState.NOT_RUN, status="y")
    assert calls["geometry"] == 0 and calls["update"] == 100 and r.sizeHint() == hint
