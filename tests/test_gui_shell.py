"""GUI smoke test of the shell: ribbon states follow runs and invalidation."""
from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session, Status
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow
from starex_reduce.ui.ribbon import StepState


@pytest.fixture
def win(qtbot: QtBot) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session(None))
    qtbot.addWidget(w)
    w.show()
    return w


def _states(w: MainWindow) -> list[StepState]:
    return [s.state for s in w.ribbon.steps]


def test_initial_ribbon(win: MainWindow) -> None:
    assert _states(win) == [StepState.CURRENT] + [StepState.NOT_RUN] * 4
    assert not win.screens[0].banner.isVisibleTo(win.screens[0])
    assert win.screens[1].banner.isVisibleTo(win.screens[1])  # Geometry needs Files first


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
    assert win.ribbon.steps[3].status == "Needs re-run"


def test_files_without_lights_does_not_run(win: MainWindow) -> None:
    assert win.run_stage("files") is False
    assert "no frames ticked" in win.session.log_lines[-1]


def test_later_step_blocked_without_inputs(win: MainWindow) -> None:
    assert win.run_stage("wavelength") is False
    win.open_step(3)
    assert win.screens[3].banner.isVisible()
    assert "Files" in win.screens[3].banner.text()


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
    win.run_all(force=True)  # Shift+Run all
    qtbot.waitUntil(lambda: len(order) == 5, timeout=5000)
    assert order == ["files", "geometry", "extract", "wavelength", "response"]


def test_screenshot_renders(win: MainWindow) -> None:
    img = win.grab().toImage()
    assert img.width() > 1000 and img.height() > 600


def test_top_row_tabs_and_ribbon_only_on_reduce(win: MainWindow) -> None:
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Plan", "Acquisition", "Reduce", "Export"]
    assert win.tab_name() == "reduce" and win.ribbon_box.isVisible()
    assert not hasattr(win, "target_edit") and not hasattr(win.screens[0].header, "btn_params")
    for name in ("plan", "acquisition", "export"):
        win.show_tab(name)
        assert not win.ribbon_box.isVisible()
    win.open_step(2)
    assert win.tab_name() == "reduce" and win.ribbon_box.isVisible()


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
    assert w.ribbon.steps[4].status == "response ok"
    assert elapsed < 0.5


def test_keyboard_shortcuts(win: MainWindow, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt

    win.activateWindow()
    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert win.current == 2 and win.tab_name() == "reduce"
    qtbot.keyClick(win, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert win.current == 3
    qtbot.keyClick(win, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ControlModifier)
    assert win.tab_name() == "export" and not win.ribbon_box.isVisible()
    win.open_step(0)
    qtbot.keyClick(win, Qt.Key.Key_F5)  # run current step: Files without lights refuses, says why
    assert "no frames ticked" in win.session.log_lines[-1]


def test_settings_dialog_validates_and_saves(win: MainWindow, tmp_path) -> None:
    from starex_reduce.core.vsx import DEFAULT_URL
    from starex_reduce.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win.settings, win)
    assert dlg.vsx.text() == DEFAULT_URL
    dlg.vsx.setText("https://example.invalid/vsx")  # no {name}
    assert isinstance(dlg.values(), str)
    dlg.vsx.setText(DEFAULT_URL)
    dlg.gain.setText("x")
    assert dlg.values() == "Tolerances must be numbers."
    dlg.gain.setText("25")
    dlg.masters.setText(str(tmp_path / "lib"))
    dlg.accept()
    assert win.settings["tolerances"]["gain_rel"] == 0.25
    assert win.settings["vsx_api_url"] == ""  # default stays implicit
    assert win.settings.masters_dir() == tmp_path / "lib" and (tmp_path / "lib").is_dir()


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

    from starex_reduce.app import install_excepthook
    from starex_reduce.core.settings import app_dir

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


def test_top_tabs_never_clip_or_scroll_at_minimum_width(win: MainWindow, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QToolButton

    win.resize(win.minimumWidth(), win.minimumHeight())
    qtbot.wait(20)
    assert win.minimumWidth() >= win.topbar.minimumSizeHint().width()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        qtbot.wait(10)
        assert win.tab_name() == ("plan", "acquisition", "reduce", "export")[i]
        bar = win.tabs.rect()
        for b in win.tabs.buttons:
            assert b.isVisible() and bar.contains(b.geometry()), (b.text(), b.geometry(), bar)
            assert b.width() >= b.sizeHint().width()  # label never elided
        assert win.tabs.buttons[i].isChecked()
        assert not [t for t in win.tabs.findChildren(QToolButton) if t.isVisible()]  # no scroll buttons
        # the whole tab row sits inside the window
        right = win.tabs.mapTo(win, win.tabs.rect().topRight()).x()
        assert right < win.session_chip.mapTo(win, win.session_chip.rect().topLeft()).x()
