"""Acquisition → Clear all: a clean slate (synthetic subs, runs without the real data)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from starextractor.core import acq_sets
from starextractor.core.session import Session
from starextractor.synth import write_example
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow


@pytest.fixture
def acq(qtbot: QtBot, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    files = write_example(tmp_path / "night")
    return w, w.ensure_acquisition(), files


def _drain(a, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)


def test_clear_all_forgets_everything(acq, qtbot: QtBot, tmp_path: Path) -> None:
    _w, a, files = acq
    a.names["target"].setText("Synth Nova")
    a.enqueue(files["target"])
    _drain(a, qtbot)
    a.collect.set_index(1)
    a.enqueue(files["reference"][:1])
    _drain(a, qtbot)
    sets_file = a.night_folder() / acq_sets.FILE_NAME  # Stacks/<night>/
    assert len(a.records) == 4 and set(a.rows_by) == {"target", "reference"} and sets_file.exists()

    assert a.clear_all(confirm=False)
    assert a.records == [] and a.rows_by == {} and a.queue == [] and a.views == {}
    assert a.names["target"].text() == "" and a.collect.index() == 0
    assert a.table.rowCount() == 0 and not sets_file.exists()
    st = a.store  # Stacks/<night>/acquisition.json
    assert st["subs"] == [] and st["rows"] == {} and st["names"] == {"target": "", "reference": ""}
    assert all(f.exists() for f in files["target"])  # the images are never touched

    a.enqueue(files["target"][:1])  # starts again from scratch: rows are found on the new first sub
    _drain(a, qtbot)
    assert len(a.records) == 1 and set(a.rows_by) == {"target"}


def test_clear_while_a_sub_is_analysed_drops_its_result(acq, qtbot: QtBot) -> None:
    _w, a, files = acq
    a.enqueue(files["target"])
    assert a.busy
    a.clear_all(confirm=False)
    _drain(a, qtbot)
    qtbot.wait(200)
    assert a.records == [] and a.rows_by == {}


def test_clear_asks_first(acq, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    _w, a, _files = acq
    a.names["target"].setText("Synth Nova")
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    assert not a.clear_all() and a.names["target"].text() == "Synth Nova"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    assert a.clear_all() and a.names["target"].text() == ""
