"""GUI smoke test of step 1 · Files on a small synthetic night."""
from __future__ import annotations

from pathlib import Path

import pytest
import synth
from pytestqt.qtbot import QtBot

from starex_reduce.core.frames import BIAS, DARK, scan
from starex_reduce.core.jobs import Ctx
from starex_reduce.core.masters import build_master, group_for_masters
from starex_reduce.core.session import Session, Status
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow
from starex_reduce.ui.ribbon import StepState


@pytest.fixture(scope="module")
def night(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    return synth.write_set(tmp_path_factory.mktemp("night"), "nova", n_target=3, n_ref=3, n_cal=3, scale=0.1)


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    return w


def _load(win: MainWindow, qtbot: QtBot, side: str, folder: Path) -> None:
    card = win.files_screen.cards[side]
    with qtbot.waitSignal(win.runner.finished, timeout=5000,
                          check_params_cb=lambda slot, _r: slot == f"peaks.{side}"):
        win.runner.submit(f"scan.{side}", __import__("starex_reduce.core.files_stage",
                                                     fromlist=["scan_lights"]).scan_lights, str(folder))
    assert card.table.rowCount() == 3


def test_load_lights_then_needs_dark_then_stack(win: MainWindow, qtbot: QtBot, night: dict) -> None:
    _load(win, qtbot, "target", night["target"][0].parent)
    card = win.files_screen.cards["target"]
    assert card.counter.text() == "3 of 3 used"
    assert "300 s · gain 200" in card.summary.text()
    assert card.table.item(0, 4).text() not in ("…", "—")  # peak measured in a worker
    assert win.ribbon.steps[0].state == StepState.MISSING
    assert win.ribbon.steps[0].status == "Target needs a dark"

    lib = win.library
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, lib, Ctx())
    win.files_screen._replan()
    texts = [card.cal_lines.itemAt(i).widget().text.text() for i in range(card.cal_lines.count())]
    assert any("pedestal match" in t for t in texts)
    assert card.pedestal_cb.isVisible() and card.gain_cb.isChecked()
    assert win.files_screen.lib_table.rowCount() == 2

    _load(win, qtbot, "reference", night["reference"][0].parent)
    with qtbot.waitSignal(win.runner.finished, timeout=10000):
        assert win.run_stage("files")
    assert win.session.status["files"].status == Status.VALID
    assert (win.session.folder / "Target_S.fit").exists() and (win.session.folder / "Reference_S.fit").exists()
    assert win.ribbon.steps[0].status == "2 stacks · 3 + 3 frames"

    # unticking a frame makes the step need a re-run
    card.table.item(0, 0).setCheckState(card.table.item(0, 0).checkState().__class__.Unchecked)
    assert card.counter.text() == "2 of 3 used"
    assert win.session.status["files"].status == Status.STALE
    assert win.ribbon.steps[0].status == "Reference done · run Target"


def test_reopen_restores_files(win: MainWindow, qtbot: QtBot, night: dict) -> None:
    _load(win, qtbot, "target", night["target"][0].parent)
    win.files_screen.cards["target"].set_name("V8055 Sgr")
    folder = win.session.folder
    again = Session.load(folder)
    assert again.inputs["target"]["name"] == "V8055 Sgr"
    assert len(again.inputs["target"]["lights"]) == 3
    assert all(i.get("peak") for i in again.inputs["target"]["lights"])
