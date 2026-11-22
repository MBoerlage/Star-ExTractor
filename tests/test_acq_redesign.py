"""Acquisition tab redesign: per-set source boxes, one watcher at a time, name/coordinates, Stack selected (same
output as Reduce > Files), Pre Processing settings (shared library folder and master table), logbook, sets file.
Synthetic subs only."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from starextractor.core import acq_sets, simbad
from starextractor.core.files_stage import spec_from, stack_job, tolerances
from starextractor.core.frames import BIAS, DARK, LIGHT, load_frame, scan
from starextractor.core.jobs import Ctx
from starextractor.core.masters import build_master, group_for_masters
from starextractor.core.session import Session
from starextractor.synth import write_example
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def night(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    return write_example(tmp_path_factory.mktemp("night"))


def _window(qtbot: QtBot, folder: Path | None) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(folder) if folder is not None else Session())
    qtbot.addWidget(w)
    w.show()
    w.show_tab("acquisition")
    return w


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    return _window(qtbot, tmp_path / "sess")


def _drain(a, qtbot: QtBot, timeout: int = 20000) -> None:
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=timeout)


def _copy_old(src: list[Path], folder: Path) -> list[Path]:
    """Copies that look finished (mtime in the past), as a capture program leaves them."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for p in src:
        shutil.copy(p, folder / p.name)
        old = time.time() - 30
        os.utime(folder / p.name, (old, old))
        out.append(folder / p.name)
    return out


def _masters(win: MainWindow, night: dict) -> None:
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, win.library, Ctx())
    win.library_changed.emit()


# 1 -------------------------------------------------------------------------------------------------------------
def test_per_set_values_are_separate_and_restored(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    a = win.ensure_acquisition()
    a.folders["target"].setText(str(tmp_path / "t"))
    a._folder_typed("target")
    a.names["target"].setText("V8055 Sgr")
    a._name_typed("target")
    a.coord_srcs["target"].set_index(2)  # Enter by hand
    a.ras["target"].setText("18 10 31.0")
    a._coords_edited("target")
    a.collect.set_index(1)
    assert a.view_role == "reference" and a.folder is a.folders["reference"]
    assert a.folder_stack.currentWidget().isVisible() and a.folders["reference"].isVisible()
    assert not a.folders["target"].isVisible() and not a.names["target"].isVisible()
    assert a.folders["reference"].text() == "" and a.names["reference"].text() == ""
    a.names["reference"].setText("HD 163955")
    a._name_typed("reference")
    a.collect.set_index(0)
    assert a.names["target"].text() == "V8055 Sgr" and a.ras["target"].text() == "18 10 31.0"
    a.collect.set_index(1)  # the tab is restored too
    w2 = _window(qtbot, None)
    w2._attach_session(Session.load(win.session.folder))
    b = w2.ensure_acquisition()
    assert b.view_role == "reference" and b.collect.index() == 1
    assert b.folders["target"].text() == str(tmp_path / "t") and b.names["reference"].text() == "HD 163955"
    assert b.coord_srcs["target"].index() == 2 and not b.ras["target"].isReadOnly()
    assert b.ras["target"].text() == "18 10 31.0" and b.names["target"].text() == "V8055 Sgr"


# 2 -------------------------------------------------------------------------------------------------------------
def test_one_watcher_at_a_time_and_subs_join_its_set(win: MainWindow, qtbot: QtBot, tmp_path: Path,
                                                     night: dict) -> None:
    a = win.ensure_acquisition()
    a.interval.setValue(1)
    a.existing.setChecked(True)
    tdir, rdir = tmp_path / "cap_t", tmp_path / "cap_r"
    _copy_old(night["target"][:2], tdir)
    _copy_old(night["reference"][:1], rdir)
    a.folders["target"].setText(str(tdir))
    a.toggle_watch()
    assert a.watching() and a.watch_role == "target" and a.watch_btn.text() == "Pause"
    qtbot.waitUntil(lambda: len(a.records) == 2 and not a.busy, timeout=15000)
    a.collect.set_index(1)  # only the display changes: Target is still watched
    assert a.watching() and a.watch_role == "target" and a.watch_btn.text() == "Start watching"
    a.folders["reference"].setText(str(rdir))
    a.toggle_watch()  # starting Reference pauses Target
    assert a.watch_role == "reference"
    qtbot.waitUntil(lambda: len(a.records) == 3 and not a.busy, timeout=15000)
    a.collect.set_index(0)  # shown: Target; a sub the Reference watcher found still joins Reference
    a.enqueue([night["reference"][1]], "reference")
    _drain(a, qtbot)
    a.stop_watch()
    assert [r.role for r in a.records] == ["target", "target", "reference", "reference"]
    # the subs' OBJECT differs per set, but it is information only: the watcher's set decides
    assert all(r.role == "reference" for r in a.records if r.path.startswith(str(rdir)))


# 3 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("existing", [True, False])
def test_process_existing_files(win: MainWindow, qtbot: QtBot, tmp_path: Path, night: dict, existing: bool) -> None:
    a = win.ensure_acquisition()
    folder = tmp_path / "cap"
    _copy_old(night["target"][:2], folder)
    a.folders["target"].setText(str(folder))
    a.interval.setValue(1)
    a.existing.setChecked(existing)
    a.toggle_watch()
    if existing:
        qtbot.waitUntil(lambda: len(a.records) == 2 and not a.busy, timeout=15000)
    else:
        qtbot.wait(2500)
        assert a.records == []  # the files already there are left alone
        _copy_old(night["target"][2:3], folder)  # a new sub arrives
        qtbot.waitUntil(lambda: len(a.records) == 1 and not a.busy, timeout=15000)
        assert a.records[0].name == night["target"][2].name
    a.stop_watch()
    assert a.watch_status.text.text() == "Paused." or "subs" in a.watch_status.text.text()


# 4 -------------------------------------------------------------------------------------------------------------
def test_clear_all_needs_confirmation_and_keeps_files(win: MainWindow, qtbot: QtBot, tmp_path: Path, night: dict,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    a = win.ensure_acquisition()
    folder = tmp_path / "cap"
    files = _copy_old(night["target"][:2], folder)
    a.folders["target"].setText(str(folder))
    a.interval.setValue(1)
    a.existing.setChecked(True)
    a.toggle_watch()
    qtbot.waitUntil(lambda: len(a.records) == 2 and not a.busy, timeout=15000)
    a.enqueue(night["reference"][:1], "reference")
    _drain(a, qtbot)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    assert not a.clear_all() and len(a.records) == 3 and a.watching()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    assert a.clear_all()
    assert a.records == [] and a.rows_by == {} and not a.watching()
    assert all(p.exists() for p in files) and all(p.exists() for p in night["reference"][:1])


# 5 -------------------------------------------------------------------------------------------------------------
def test_simbad_header_and_hand_coordinates(win: MainWindow, qtbot: QtBot, tmp_path: Path, night: dict,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    a = win.ensure_acquisition()
    res = simbad.SimbadResult("V8055 Sgr", True, "V* V8055 Sgr", 272.629, -28.1, "No*", "", 9.1)
    monkeypatch.setattr(simbad, "lookup", lambda name, timeout=0, fetch=None: res)
    a.names["target"].setText("V8055 Sgr")
    a.resolve_simbad("target")
    qtbot.waitUntil(lambda: a.ras["target"].text() != "", timeout=5000)
    assert a.coord_srcs["target"].index() == 0 and a.ras["target"].isReadOnly()
    assert (a.ras["target"].text().startswith("18 10") and a.decs["target"].text().startswith("−28")) \
        or a.decs["target"].text().startswith("-28")
    # Header: the name from OBJECT of the first FITS file in this set's folder, nothing else
    folder = tmp_path / "ref"
    _copy_old(night["reference"][:1], folder)
    obj = fits.getheader(night["reference"][0])["OBJECT"]
    a.collect.set_index(1)
    a.folders["reference"].setText(str(folder))
    assert a.name_from_header("reference")
    assert a.names["reference"].text() == obj and a.name_source["reference"] == "header"
    assert a.view_role == "reference"  # never switches the set
    a.coord_srcs["reference"].set_index(2)
    assert not a.ras["reference"].isReadOnly() and not a.decs["reference"].isReadOnly()
    a.coord_srcs["reference"].set_index(0)
    assert a.ras["reference"].isReadOnly()


# 6, 10 ---------------------------------------------------------------------------------------------------------
def test_stack_selected_equals_the_files_step(win: MainWindow, qtbot: QtBot, tmp_path: Path, night: dict) -> None:
    a = win.ensure_acquisition()
    _masters(win, night)
    a.names["target"].setText("Synth Nova")
    a.enqueue(night["target"])
    _drain(a, qtbot)
    rec = a.records[1]
    rec.included, rec.user_override = False, True  # untick sub 2
    a.persist()
    a.refresh()
    with qtbot.waitSignal(win.runner.finished, timeout=30000, check_params_cb=lambda s, _r: s == "acq.stack"):
        assert a.stack_selected()
    out = win.settings.paths().stacks / "2026-08-20" / "SynthNova_target_S.fit"  # Stacks/<night of sub 1>/
    assert out.exists()
    text = a.stack_result.text()
    assert "✓ Stacked 2 subs → SynthNova_target_S.fit · calibrated with Dark" in text
    assert "sigma-clip mean 3.0 σ" in text
    assert any("Acquisition: ✓ Stacked 2 subs" in ln for ln in win.session.log_lines)
    # the same inputs straight through the core stacking functions (any other caller): identical file, header
    # included, the SX* block too
    other = tmp_path / "core_out"
    other.mkdir()
    frames = [load_frame(r.path, LIGHT) for r in a.records if r.included]
    spec, problem = spec_from("Target", frames, a.prep.cal(), a.prep.stack_params(), "Synth Nova", win.library,
                              tolerances(win.settings["tolerances"]))
    assert spec is not None, problem
    spec.rows = a.rows_by["target"].to_json()
    spec.out_name = out.name
    stack_job(Ctx(), [spec], win.library, other)
    with fits.open(out) as f1, fits.open(other / out.name) as f2:
        assert np.array_equal(f1[0].data, f2[0].data)
        h1, h2 = dict(f1[0].header), dict(f2[0].header)
        assert {k: v for k, v in h1.items() if k != "DATE"} == {k: v for k, v in h2.items() if k != "DATE"}
        assert [str(x) for x in f1[0].header["HISTORY"]] == [str(x) for x in f2[0].header["HISTORY"]]
        assert h1["SXVER"] == 1 and h1["SXNSUB"] == 2 and h1["SXNAME"] == "Synth Nova"
        assert h1["SXROW1"] <= h1["SXROW2"] and "SXSKA1" in h1 and "SXSKB2" in h1
    # 10: the sets file in the project folder, now with the stack and its rows
    doc = acq_sets.load_sets(out.parent / acq_sets.FILE_NAME)  # next to the stack, path relative to the root
    assert doc["target"]["stack_file"] == "Stacks/2026-08-20/SynthNova_target_S.fit"
    assert doc["target"]["rows"]["spec"]
    assert len(doc["target"]["good"]) == 2
    # the rows travel in the stack header; Load pair hands them to Extract (tests/test_files_pair.py)
    from starextractor.core.stack_keys import rows1_of, rows_from_header

    assert rows_from_header(h1) == rows1_of(a.rows_by["target"].to_json())


def test_extract_first_guess_finds_the_stacked_spectrum_not_the_brightest_row() -> None:
    from starextractor.core.extract_stage import resolve_bands

    img = np.full((400, 600), 100.0, np.float32)
    img[60:90] += 3000.0  # a diffuse bright band
    img[300:304] += 800.0  # the star, where the stack's rows were
    off = {"spec": [-6, 6], "sky_a": [-30, -15], "sky_b": [15, 30], "near": [302.0, 0.0]}
    bands, _how, _ = resolve_bands(img, None, off, "x")
    assert bands.spectrum.top <= 302 <= bands.spectrum.bottom
    flipped = img[::-1].copy()
    bands, _how, _ = resolve_bands(flipped, None, {**off, "near": [302.0, 1.0]}, "x")
    assert bands.spectrum.top <= 400 - 1 - 302 <= bands.spectrum.bottom


# 7 -------------------------------------------------------------------------------------------------------------
def test_stack_needs_a_dark_and_a_project_folder(win: MainWindow, qtbot: QtBot, night: dict) -> None:
    a = win.ensure_acquisition()
    a.enqueue(night["target"][:2])
    _drain(a, qtbot)
    assert not a.stack_selected()  # empty library: no dark
    assert not (win.session.folder / "Target_S.fit").exists()
    assert "dark" in a.stack_result.text().lower() and "href='pre'" in a.stack_result.text()
    a._stack_link("pre")
    assert a.pages.currentIndex() == 1 and a.collect.on_settings()
    a.collect.set_index(0)
    for r in a.records:
        r.included = False
    assert not a.stack_selected() and "Tick at least one" in a.stack_result.text()


def test_stack_without_stacks_folder(qtbot: QtBot, night: dict, tmp_path: Path) -> None:
    w = _window(qtbot, None)
    w.settings["data_root"] = str(tmp_path / "unplugged")  # a chosen root whose drive is gone: never created
    a = w.ensure_acquisition()
    a.enqueue(night["target"][:1])
    _drain(a, qtbot)
    assert not a.stack_selected()
    assert "Stacks folder" in a.stack_result.text() and "href='settings'" in a.stack_result.text()
    assert not (tmp_path / "unplugged").exists()


# 8 -------------------------------------------------------------------------------------------------------------
def test_library_folder_is_one_setting(win: MainWindow, qtbot: QtBot, tmp_path: Path, night: dict) -> None:
    a = win.ensure_acquisition()
    a.collect.open_settings()
    p = a.prep
    new = tmp_path / "lib2"
    p.lib_folder.setText(str(new))
    p.lib_folder.editingFinished.emit()
    assert win.settings["data_paths"]["masters"] == str(new) and win.library.folder == new
    from starextractor.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win.settings, win)
    assert dlg.locations.fields["masters"].text() == str(new)  # Settings › Data locations › Masters
    third = tmp_path / "lib3"
    win.set_masters_dir(str(third))  # what Settings → OK does when the folder changed
    assert p.lib_folder.text() == str(third)
    # a master made here shows in the library table (the one table: Reduce > Files no longer has one)
    assert p.library_card.table.rowCount() == 0
    p.create_card.bins[DARK].set_folder(str(night["darks"][0].parent))
    with qtbot.waitSignal(win.runner.finished, timeout=30000, check_params_cb=lambda s, _r: s == "masters"):
        p.create_card.create_masters([DARK])
    assert p.library_card.table.rowCount() == 1
    assert not p.create_card.status.isHidden()


# 9 -------------------------------------------------------------------------------------------------------------
def test_set_notes_autosave_with_their_set(win: MainWindow, qtbot: QtBot, night: dict) -> None:
    """The notes card belongs to the set shown and is kept in the sets file (no project logbook any more)."""
    a = win.ensure_acquisition()
    a.enqueue(night["target"][:1])
    _drain(a, qtbot)
    path = a.night_folder() / acq_sets.FILE_NAME
    a.logbook.setPlainText("Seeing 2\", thin cirrus at 23:10")
    qtbot.waitUntil(lambda: acq_sets.load_sets(path)["target"]["notes"].startswith("Seeing 2"), timeout=2500)
    a.collect.set_index(1)
    assert a.logbook.toPlainText() == ""  # the reference set has its own notes
    a.logbook.setPlainText("ref ok")
    a.collect.set_index(0)
    assert a.logbook.toPlainText().startswith("Seeing 2")
    doc = acq_sets.load_sets(path)
    assert doc["reference"]["notes"] == "ref ok" and not path.with_suffix(".json.tmp").exists()
    w2 = _window(qtbot, None)  # the same night's state comes back (Stacks/<night>/acquisition.json)
    assert w2.ensure_acquisition().logbook.toPlainText().startswith("Seeing 2")
