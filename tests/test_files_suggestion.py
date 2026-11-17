"""Reduce > Files suggestion from Acquisition (doc test 7) on the nova example."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from starex_reduce.core import acq_sets
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow
from starex_reduce.ui.screens import files_suggest

ROOT = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
NOVA = sorted((ROOT / "SgrNov32026").glob("*.fits"))
REF = sorted((ROOT / "hd163955").glob("*.fits"))
pytestmark = pytest.mark.skipif(not NOVA or not REF, reason="example_spectra/novasrg2026 not present")


def _window(qtbot: QtBot, folder: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.load(folder) if Session.is_session(folder) else Session.create(folder))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: "files" in w._built, timeout=15000)
    return w


def _acquisition_sets(win: MainWindow, qtbot: QtBot) -> dict:
    """Let the Acquisition tab process the nova night (target subs, then reference subs)."""
    a = win.ensure_acquisition()
    a.names["target"].setText("V8055 Sgr")
    a.names["reference"].setText("HD 163955")
    a.enqueue(NOVA)
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=30000)
    a.collect.set_index(1)
    a.enqueue(REF)
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=30000)
    a.persist()
    return acq_sets.load_sets(win.session.folder / acq_sets.FILE_NAME)


def _ticked(win: MainWindow, side: str) -> set[str]:
    return {Path(i["path"]).name for i in win.session.inputs[side]["lights"] if i.get("used", True)}


def test_use_suggestion_ticks_files_and_fills_names(qtbot: QtBot, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path / "sess")
    doc = _acquisition_sets(win, qtbot)
    path = win.session.folder / acq_sets.FILE_NAME
    before = path.read_bytes()
    files = win.files_screen
    files.load_session()  # Files picks the sets file up
    sug = files.suggestion
    assert sug.use_btn.isEnabled() and sug.provenance.startswith(f"sets file from {doc['night']}")
    assert sug.text.text().startswith("From Acquisition: Target V8055 Sgr, ")
    assert win.session.inputs["target"]["lights"] == []  # nothing applied automatically
    sug.use()
    good_t = {Path(p).name for p in doc["target"]["good"]}
    assert _ticked(win, "target") == good_t and _ticked(win, "reference") == {p.name for p in REF}
    excluded = {Path(e["file"]).name for e in doc["target"]["excluded"]}
    listed = {Path(i["path"]).name for i in win.session.inputs["target"]["lights"]}
    assert excluded <= listed and not (excluded & _ticked(win, "target"))  # listed but unticked
    names = (win.session.inputs["target"]["name"], win.session.inputs["reference"]["name"])
    assert names == ("V8055 Sgr", "HD 163955")
    assert files.cards["target"].name.text() == "V8055 Sgr"
    assert path.read_bytes() == before  # Files never writes the sets file


def test_files_without_a_sets_file_is_unchanged(qtbot: QtBot, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path / "plain")
    sug = win.files_screen.suggestion
    assert sug.doc is None and not sug.use_btn.isEnabled() and "No acquisition sets" in sug.text.text()
    sug.use()
    assert win.session.inputs["target"]["lights"] == [] and win.session.inputs["reference"]["lights"] == []


def test_missing_files_shown_in_red(qtbot: QtBot, tmp_path: Path) -> None:
    import json

    sets = tmp_path / "old_sets.json"
    sets.write_text(json.dumps({"version": 1, "folder": str(ROOT), "target": {"name": "X", "good": [
        "SgrNov32026/Sgrnov32026_00001.fits", "SgrNov32026/gone_00099.fits"]}}), encoding="utf-8")
    before = sets.read_bytes()
    win = _window(qtbot, tmp_path / "sess2")
    sug = win.files_screen.suggestion
    assert sug.load_file(str(sets))
    assert sug.problems.isVisible() and sug.problems.text() == "1 missing"
    assert "gone_00099.fits" in sug.problems.toolTip()
    sug.use()
    assert sets.read_bytes() == before


def test_scan_folder_gives_the_same_suggestion(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    win = _window(qtbot, tmp_path / "sess3")
    expected = _acquisition_sets(win, qtbot)
    (win.session.folder / acq_sets.FILE_NAME).unlink()  # pretend this is old data without a sets file
    sug = win.files_screen.suggestion
    sug.load_session()
    assert sug.doc is None
    order: list[str] = []

    def answer(dlg) -> int:
        for g in dlg.groups:  # the user assigns by folder; bias and darks were never offered
            order.append({"SgrNov32026": "target", "hd163955": "reference"}.get(Path(g["folder"]).name, "ignore"))
        dlg.set_assignment(order)
        dlg.names["target"].setText("V8055 Sgr")
        dlg.names["reference"].setText("HD 163955")
        return 1
    monkeypatch.setattr(files_suggest.AssignDialog, "exec", answer)
    sug.scan(str(ROOT))
    qtbot.waitUntil(lambda: sug.scan_btn.isEnabled(), timeout=60000)
    assert sorted(order) == ["reference", "target"]  # only the two light folders, calibration skipped
    got = sug.doc
    assert sug.provenance == "folder scan" and not (win.session.folder / acq_sets.FILE_NAME).exists()
    for role in ("target", "reference"):
        def names(side: dict, key: str) -> list[str]:
            return sorted(Path(x if isinstance(x, str) else x["file"]).name for x in side[key])
        assert names(got[role], "good") == names(expected[role], "good")
        assert names(got[role], "excluded") == names(expected[role], "excluded")
        assert got[role]["name"] == expected[role]["name"]
    assert acq_sets.summary(got) == acq_sets.summary(expected)
    again = Session.load(win.session.folder)
    assert again.params["acq_scan"]["doc"]["target"]["good"]  # kept in the session for reopening
