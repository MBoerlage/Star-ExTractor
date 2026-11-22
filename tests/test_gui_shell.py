"""GUI smoke test of the shell: the workflow rail's states follow runs and invalidation."""
from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from starextractor.core.session import Session, Status
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow
from starextractor.ui.rail import StepState


@pytest.fixture
def win(qtbot: QtBot) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session(None))
    qtbot.addWidget(w)
    w.show()
    return w


REDUCE = ("select", "geometry", "extract", "wavelength", "response")


def _states(w: MainWindow) -> list[StepState]:
    """The five Reduce steps as the rail shows them (the open step is current)."""
    return [w.rail.shown_state(r) for r in REDUCE]


def test_initial_ribbon(win: MainWindow) -> None:
    assert _states(win) == [StepState.CURRENT] + [StepState.NOT_RUN] * 4
    assert not win.screens[0].banner.isVisibleTo(win.screens[0])
    assert win.screens[1].banner.isVisibleTo(win.screens[1])  # Geometry needs Select first


def test_done_then_invalidate(win: MainWindow) -> None:
    for sid in ("files", "geometry", "extract", "wavelength", "response"):
        win.session.mark_done(sid, {}, f"{sid} ok")
    win.open_step(4)
    assert _states(win) == [StepState.DONE] * 4 + [StepState.CURRENT]
    win.open_step(0)
    win.session.set_params("wavelength", {"mode": "free"})  # a change in step 4 …
    win.refresh_ribbon()
    assert win.session.status["extract"].status == Status.VALID  # … never touches earlier steps
    assert win.session.status["wavelength"].status == Status.STALE
    assert win.session.status["response"].status == Status.STALE
    assert _states(win)[2:] == [StepState.DONE, StepState.NOT_RUN, StepState.NOT_RUN]
    assert win.rail.by_id["wavelength"].status == "Needs re-run"


def test_files_without_a_pair_does_not_run(win: MainWindow) -> None:
    assert win.run_stage("files") is False
    assert win.session.log_lines[-1].endswith("Load a pair in Select first.")


def test_later_step_blocked_without_inputs(win: MainWindow) -> None:
    assert win.run_stage("wavelength") is False
    win.open_step(3)
    assert win.screens[3].banner.isVisible()
    assert "Select" in win.screens[3].banner.text()


def test_run_all_runs_in_order(win: MainWindow, qtbot: QtBot) -> None:
    order: list[str] = []

    def fake_run(sid: str, images: list[str] | None = None) -> bool:
        order.append(sid)
        win.runner.submit(sid, lambda ctx: {"summary": "ok"})
        return True

    win.run_stage = fake_run  # type: ignore[method-assign]
    for sid in ("files", "geometry", "extract"):
        win.session.mark_done(sid, {}, "done")
    win.run_all()
    qtbot.waitUntil(lambda: win.session.status["response"].status == Status.VALID, timeout=5000)
    assert order == ["wavelength", "response"]  # only what is out of date, in order
    win.show_tab("export")
    assert _states(win) == [StepState.DONE] * 5
    order.clear()
    win.run_all()
    assert order == [] and "up to date" in win.session.log_lines[-1]
    win.run_all(force=True)  # Shift+Run all: every step again, except Select (a loaded pair has nothing to run)
    qtbot.waitUntil(lambda: len(order) == 4, timeout=5000)
    assert order == ["geometry", "extract", "wavelength", "response"]


def test_screenshot_renders(win: MainWindow) -> None:
    img = win.grab().toImage()
    assert img.width() > 1000 and img.height() > 600


def test_top_row_tools_and_the_rail_everywhere(win: MainWindow) -> None:
    from PySide6.QtWidgets import QPushButton

    assert not hasattr(win, "tabs") and not hasattr(win, "ribbon") and not hasattr(win, "ribbon_box")
    names = [b.text() for b in win.topbar.findChildren(QPushButton) if b.isVisibleTo(win)]
    assert names == ["Plan", "Library"]  # the old tabs are gone; Run all is hidden for now (ui.show_run_all)
    assert win.tab_name() == "reduce" and win.rail.isVisible() and win.legend.isVisible()
    for name in ("plan", "acquisition", "export", "library"):
        win.show_tab(name)
        assert win.rail.isVisible()  # the rail is on every page
        assert win.legend.isVisible() == (name == "export")  # the legend on Reduce and Export only
        assert win.plan_btn.isChecked() == (name == "plan") and win.library_btn.isChecked() == (name == "library")
    win.open_step(2)
    assert win.tab_name() == "reduce" and win.rail.current == "extract"

def test_reopen_shows_done_ribbon_before_any_screen_is_built(qtbot: QtBot, tmp_path) -> None:
    import time

    s = Session.create(tmp_path / "yesterday")
    for sid in ("files", "geometry", "extract", "wavelength", "response"):
        s.mark_done(sid, {}, f"{sid} ok")
    s.save()
    t = time.perf_counter()
    w = MainWindow(session=Session.load(tmp_path / "yesterday"))
    qtbot.addWidget(w)
    elapsed = time.perf_counter() - t
    assert w._built == {}  # no step screen (and no pixel data) loaded yet
    assert _states(w) == [StepState.CURRENT] + [StepState.DONE] * 4
    assert w.rail.by_id["response"].status == "response ok"
    assert elapsed < 0.5


def test_keyboard_shortcuts(win: MainWindow, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt

    win.activateWindow()
    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert win.current == 2 and win.tab_name() == "reduce"
    qtbot.keyClick(win, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert win.current == 3
    qtbot.keyClick(win, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ControlModifier)
    assert win.tab_name() == "export" and win.rail.current == "collect"
    win.open_step(0)
    qtbot.keyClick(win, Qt.Key.Key_F5)  # run current step: Select without a pair refuses, says why
    assert win.session.log_lines[-1].endswith("Load a pair in Select first.")


def test_settings_dialog_validates_and_saves(win: MainWindow, tmp_path) -> None:
    from starextractor.core.vsx import DEFAULT_URL
    from starextractor.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win.settings, win)
    assert dlg.vsx.text() == DEFAULT_URL
    dlg.vsx.setText("https://example.invalid/vsx")  # no {name}
    assert isinstance(dlg.values(), str)
    dlg.vsx.setText(DEFAULT_URL)
    loc = dlg.locations  # Data locations: the root, and Masters on another folder
    loc.root.setText(str(tmp_path / "Data"))
    loc.fields["masters"].setText(str(tmp_path / "lib"))
    assert loc.icons["stacks"].text() == "✗" and loc.create_btn.isEnabled()
    loc.create_missing()
    assert loc.icons["stacks"].text() == "✓" and not loc.create_btn.isEnabled()
    dlg.accept()
    assert win.settings["vsx_api_url"] == ""  # default stays implicit
    assert win.settings.masters_dir() == tmp_path / "lib" and (tmp_path / "lib").is_dir()
    assert win.settings.paths().stacks == tmp_path / "Data" / "Stacks" and win.settings["data_paths"]["stacks"] == ""


def test_window_geometry_saved_and_errors_reported(qtbot: QtBot, tmp_path) -> None:
    w = MainWindow(session=Session(None))
    qtbot.addWidget(w)
    w.show()
    w.resize(1200, 760)
    qtbot.wait(50)
    size = w.size()  # the window manager may clamp to the screen
    w.close()
    assert w.settings["window"]["geometry"]
    w2 = MainWindow(session=Session(None))
    qtbot.addWidget(w2)
    w2.show()
    qtbot.wait(50)
    assert w2.height() == size.height()  # offscreen test screen is 800x600: width clamps to the 1100 minimum
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "session.json").write_text("{not json", encoding="utf-8")
    w2._quiet_errors = True
    w2.load_session(bad)
    assert w2.session.folder is None and "Could not open the session" in w2.session.log_lines[-1]


def test_unexpected_error_goes_to_log_and_error_file(win: MainWindow) -> None:
    import sys

    from starextractor.app import install_excepthook
    from starextractor.core.settings import app_dir

    old = sys.excepthook
    win._quiet_errors = True
    install_excepthook(win)
    try:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = old
    assert any("Something went wrong: ValueError: boom" in line for line in win.session.log_lines)
    assert "ValueError: boom" in (app_dir() / "error.log").read_text(encoding="utf-8")


def test_top_row_and_rail_fit_at_minimum_width(win: MainWindow, qtbot: QtBot) -> None:
    win.resize(win.minimumWidth(), win.minimumHeight())
    qtbot.wait(20)
    assert win.minimumWidth() >= win.topbar.minimumSizeHint().width()
    chip = win.session_chip.mapTo(win, win.session_chip.rect().topLeft()).x()
    for b in (win.plan_btn, win.library_btn):
        assert b.isVisible() and b.mapTo(win, b.rect().topRight()).x() < chip
        assert b.width() >= b.sizeHint().width()  # label never elided

def test_tolerances_on_pre_processing_page(win: MainWindow) -> None:
    from starextractor.ui.settings_dialog import SettingsDialog

    assert not hasattr(SettingsDialog(win.settings, win), "gain")
    page = win.ensure_acquisition().prep
    seen = []
    win.library_changed.connect(lambda: seen.append(1))
    page.tol_gain.setValue(25)
    assert win.settings["tolerances"]["gain_rel"] == 0.25 and seen
    page.tol_enabled.setChecked(False)
    assert win.settings["tolerances"]["enabled"] is False
