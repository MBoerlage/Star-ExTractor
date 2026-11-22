"""The Library tab: summary, attention rules, filters, grouping, details from step records and old logbooks, notes
round trip without overwriting steps, Open in Reduce = Files' Load pair, Promote, Create pair, empty and missing
data root, worker thumbnails, persisted selection, scan speed. Synthetic data root (synth.write_library)."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from starextractor.core import datalocs as dl
from starextractor.core import library as lib
from starextractor.core import logbook
from starextractor.synth import write_library


@pytest.fixture
def root(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    r = tmp_path / "Data"
    return r, write_library(r)


@pytest.fixture
def win(qtbot: QtBot, root):
    from PySide6.QtWidgets import QApplication

    from starextractor.core.session import Session
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session())
    w.settings["data_root"] = str(root[0])
    w._library = None
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    return w


def _open_library(w, qtbot: QtBot):
    w.show_tab("library")
    lp = w.library_page
    qtbot.waitUntil(lambda: lp.library is not None and not w.runner.is_busy("library.scan"), timeout=10000)
    return lp


def _select(lp, key: str) -> None:
    i = lp.model.index_of(key)
    assert i >= 0, key
    lp.table.selectRow(i)
    lp._row_selected(i)


# 1 -------------------------------------------------------------------------------------------------------------
def test_summary_attention_filters_and_search(root) -> None:
    r, _made = root
    L = lib.scan(dl.data_paths({"data_root": str(r)}))
    assert (L.nights, L.pairs, L.published) == (6, 11, 3)
    att = {x.target: x.attention for x in L.rows if x.attention}
    assert set(att) == {"T CrB", "RS Oph", "V Sge", "V1405 Cas", "Z And", "R Aqr"}  # 6
    assert L.attention == 6
    assert "1 unacknowledged QC warning" in att["T CrB"] and any("stale" in a for a in att["RS Oph"])
    assert any("failed" in a for a in att["V Sge"]) and any("missing" in a for a in att["V1405 Cas"])
    assert att["Z And"] == ("stack with no reference",)
    assert "CH Cyg" not in att  # its warning is acknowledged
    assert {x.target for x in L.filtered("published")} == {"Sheliak", "V8055 Sgr", "V1405 Cas"}
    assert {x.target for x in L.filtered("stacked")} >= {"P Cyg", "AG Dra"} and "Z And" not in \
        {x.target for x in L.filtered("stacked")}
    assert "P Cyg" not in {x.target for x in L.filtered("reduced")}
    assert {x.target for x in L.filtered("all", "airmass")} == {"T CrB"}  # notes text
    assert {x.target for x in L.filtered("all", "hd 1454")} == {"AG Dra"}  # reference
    assert {x.target for x in L.filtered("attention", "oph")} == {"RS Oph"}
    pub2 = next(x for x in L.rows if x.target == "V8055 Sgr")
    assert pub2.status_word == "Published v2" and pub2.last_note == "v2: better response."


# 2 -------------------------------------------------------------------------------------------------------------
def test_grouping_unpaired_and_reference_candidates(root) -> None:
    r, made = root
    paths = dl.data_paths({"data_root": str(r)})
    L = lib.scan(paths)
    nights = [x.night for x in L.rows]
    assert nights == sorted(nights, reverse=True)  # newest first
    stacks = {x.target: x for x in L.rows if x.kind == "stack"}
    assert set(stacks) == {"Z And", "R Aqr"} and all(not x.reference for x in stacks.values())
    assert stacks["R Aqr"].suggested_reference == dl.to_stored(made["stack_only_reference"], r)  # sets file
    cands = lib.reference_candidates(paths, "2025-09-29", exclude=made["no_reference"])
    roles = [lib.role_of(p) for _t, p in cands]
    assert made["spare_reference"] in [p for _t, p in cands] and roles == sorted(roles, key=lambda x: x != "reference")
    assert "reference stack" in cands[0][0]
    assert all(p != made["no_reference"] for _t, p in cands)
    assert lib.role_of(Path("X_ref_S_2.fit")) == "reference" and lib.role_of(Path("X_target_S.fit")) == "target"
    assert lib.role_of(Path("plain.fit")) is None


def test_table_model_groups_and_marks_no_reference(win, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt

    from starextractor.ui import theme

    lp = _open_library(win, qtbot)
    kinds = [k for k, _v in lp.model.items]
    assert kinds[0] == "night" and kinds.count("night") == 6
    i = lp.model.index_of(dl.to_stored(Path(win.settings["data_root"]) / "Stacks/2025-09-29/ZAnd_target_S.fit",
                                       Path(win.settings["data_root"])))
    idx = lp.model.index(i, 1)
    assert idx.data() == "no reference"
    assert idx.data(Qt.ItemDataRole.ForegroundRole).color().name().lower() == theme.AMBER.lower()


# 3 -------------------------------------------------------------------------------------------------------------
def test_progress_report_from_steps_and_old_logbook(win, qtbot: QtBot) -> None:
    lp = _open_library(win, qtbot)
    _select(lp, "2025-09-27_RSOph__HD161868")
    texts = [lp.report.itemAt(i).widget().text() for i in range(lp.report.count())]
    assert any(t.startswith("out of date: ") for t in texts) and "not acknowledged" in " ".join(texts)
    _select(lp, "2025-09-28_VSge__HD192640")
    texts = [lp.report.itemAt(i).widget().text() for i in range(lp.report.count())]
    assert any(t.startswith("failed: too few lines") for t in texts)
    _select(lp, "2025-09-25_gamCas__HD4222")  # schema 1, no steps: derived from the files
    texts = [lp.report.itemAt(i).widget().text() for i in range(lp.report.count())]
    assert "done (no record)" in texts and "no record" in texts
    assert lp.d_chip.text().startswith("Reduced · not published")
    _select(lp, "2025-09-29_V8055Sgr__HD163955")
    assert lp.d_chip.text().startswith("Published v2 ·") and lp.promote_btn.text() == "Promote as v3"


# 4 -------------------------------------------------------------------------------------------------------------
def test_notes_round_trip_keep_steps_and_sync(win, qtbot: QtBot) -> None:
    lp = _open_library(win, qtbot)
    key = "2025-09-26_AGDra__HD145454"
    _select(lp, key)
    folder = lp.selected.folder
    logbook.set_step(folder, "geometry", logbook.DONE, "written by the reduce flow meanwhile", {"tilt_deg": 0.5})
    lp.notes.setPlainText("Library note\nlast line here")
    qtbot.waitUntil(lambda: (logbook.load(folder) or {}).get("notes") == "Library note\nlast line here",
                    timeout=4000)
    doc = logbook.load(folder)
    assert doc["steps"]["geometry"]["summary"] == "written by the reduce flow meanwhile"  # not overwritten
    assert lp.selected.last_note == "last line here"
    # the same pair loaded in Reduce: the Files box follows the Library, and the other way round
    lp.open_in_reduce()
    files = win.files_screen
    assert files.notes.toPlainText() == "Library note\nlast line here"
    files.notes.setPlainText("edited in Files")
    files._notes_dirty = True
    files.save_notes()
    win.show_tab("library")
    qtbot.waitUntil(lambda: not win.runner.is_busy("library.scan"), timeout=5000)
    _select(lp, key)
    assert lp.notes.toPlainText() == "edited in Files"


# 5 -------------------------------------------------------------------------------------------------------------
def test_open_in_reduce_equals_files_load_pair(win, qtbot: QtBot, root, tmp_path: Path) -> None:
    from starextractor.core.pair import read_stack

    r, made = root
    lp = _open_library(win, qtbot)
    _select(lp, "2025-09-25_PCyg__HD193369")
    assert lp.open_in_reduce()
    s = win.session
    assert s.folder == made["stacked"] and win.tab_name() == "reduce" and win.current == 1  # Files done → Geometry
    lib_state = (json.loads(json.dumps(s.params["files"])), s.inputs["target"]["name"],
                 s.inputs["reference"]["name"])
    # the same pair through Files' Load pair on a copy of the data root
    copy = tmp_path / "copy"
    shutil.copytree(r, copy)
    shutil.rmtree(copy / "Processed" / made["stacked"].name)
    win.settings["data_root"] = str(copy)
    doc = logbook.load(made["stacked"])
    f = win.files_screen
    for role in ("target", "reference"):
        f.cards[role].set_file(copy / doc[role]["stack_file"])
    win.show_tab("reduce")
    win.open_step(0)
    assert f.load_pair()
    s2 = win.session
    files_state = (json.loads(json.dumps(s2.params["files"])), s2.inputs["target"]["name"],
                   s2.inputs["reference"]["name"])
    for a, b in ((lib_state, files_state),):
        pa, pb = a[0]["pairs"][0], b[0]["pairs"][0]
        for role in ("target", "reference"):
            for k in ("file", "name", "rows", "rows_src", "name_src"):
                assert pa[role][k] == pb[role][k], (role, k)
        assert a[1:] == b[1:]
    assert read_stack(s2.folder / "work" / "Target_S.fit").name == "P Cyg"


# 6 -------------------------------------------------------------------------------------------------------------
def test_promote_from_library(win, qtbot: QtBot) -> None:
    lp = _open_library(win, qtbot)
    _select(lp, "2025-09-26_AGDra__HD145454")
    assert not lp.promote_btn.isEnabled() and "No AVSpec file" in lp.promote_btn.toolTip()
    # save an AVSpec file into the pair folder as Export › Publish does
    from starextractor.synth import _spectrum_1d

    folder = lp.selected.folder
    avs = _spectrum_1d(folder / "export" / "_agdra_20250926_110_ABCD.fit", "AG Dra")
    logbook.update(folder, {"processing": {"export": {"file": dl.to_stored(avs, Path(win.settings["data_root"]))}}})
    lp.request_scan()
    qtbot.waitUntil(lambda: not win.runner.is_busy("library.scan"), timeout=5000)
    _select(lp, "2025-09-26_AGDra__HD145454")
    assert lp.promote_btn.isEnabled() and lp.promote_btn.text() == "Promote to Published"
    e1 = lp.promote(confirm=False)
    pub = Path(win.settings["data_root"]) / "Published" / "2025" / "2025-09-26_AGDra_v1"
    assert e1["version"] == 1 and sorted(p.name for p in pub.iterdir()) == [avs.name, "submission.json"]
    qtbot.waitUntil(lambda: not win.runner.is_busy("library.scan"), timeout=5000)
    _select(lp, "2025-09-26_AGDra__HD145454")
    assert lp.selected.status_word == "Published v1" and lp.promote_btn.text() == "Promote as v2"
    assert lp.promote(confirm=False)["version"] == 2
    assert [p["version"] for p in logbook.load(folder)["published"]] == [1, 2]


# 7 -------------------------------------------------------------------------------------------------------------
def test_create_pair_from_two_stacks(win, qtbot: QtBot, root) -> None:
    r, made = root
    lp = _open_library(win, qtbot)
    _select(lp, dl.to_stored(made["stack_only_target"], r))
    assert lp.detail.currentIndex() == 2
    assert lp.ref_pick.currentData() == str(made["stack_only_reference"])  # from the sets file
    assert lp.create_pair()
    folder = r / "Processed" / "2025-09-30_RAqr__HD222661"
    assert win.session.folder == folder and (folder / "work" / "Target_S.fit").exists()
    assert logbook.load(folder)["reference"]["name"] == "HD 222661" and win.tab_name() == "reduce"


# 8 -------------------------------------------------------------------------------------------------------------
def test_empty_and_missing_data_root(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QApplication

    from starextractor.core.session import Session
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session())
    qtbot.addWidget(w)
    w.settings["data_root"] = str(tmp_path / "empty")
    dl.create_missing(w.settings.paths())
    w.show()
    lp = _open_library(w, qtbot)
    assert lp.pages.currentIndex() == 1 and "No pairs yet" in lp.empty_msg.text()
    w.settings["data_root"] = str(tmp_path / "unplugged")
    opened: list[bool] = []
    monkeypatch.setattr(w, "open_settings", lambda: opened.append(True) or False)
    lp.request_scan()
    assert lp.pages.currentIndex() == 2 and "missing or not writable" in lp.root_msg.text()
    lp.settings_btn.click()
    assert opened == [True] and not (tmp_path / "unplugged").exists()


# 9 -------------------------------------------------------------------------------------------------------------
def test_thumbnail_in_a_worker_cached_and_state_persists(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[str] = []
    real = lib.thumb_job

    def spy(ctx, path):
        threads.append(threading.current_thread().name)
        return real(ctx, path)

    monkeypatch.setattr(lib, "thumb_job", spy)
    lp = _open_library(win, qtbot)
    _select(lp, "2025-09-26_AGDra__HD145454")
    qtbot.waitUntil(lambda: bool(lp.thumbs), timeout=5000)
    assert threads and threads[0] != threading.main_thread().name
    assert lp.curve.xData is not None and len(lp.curve.xData) <= 600
    _select(lp, "2025-09-25_PCyg__HD193369")
    assert not lp.no_spectrum.isHidden()  # no 1D file: a muted line
    _select(lp, "2025-09-26_AGDra__HD145454")
    assert len(threads) == 1  # cached by path and mtime
    lp.chips.set_index(2)  # Reduced
    lp.search.setText("dra")
    lp.request_scan()
    qtbot.waitUntil(lambda: not win.runner.is_busy("library.scan"), timeout=5000)
    assert lp.selected.key == "2025-09-26_AGDra__HD145454"  # kept across a rescan
    st = win.settings["library"]
    assert st == {"selected": "2025-09-26_AGDra__HD145454", "filter": "reduced", "search": "dra"}
    from starextractor.core.session import Session
    from starextractor.ui.main_window import MainWindow

    w2 = MainWindow(session=Session())  # a restart: the same settings file
    qtbot.addWidget(w2)
    w2.show()
    lp2 = _open_library(w2, qtbot)
    assert lp2.chip() == "reduced" and lp2.search.text() == "dra" and lp2.selected.key == st["selected"]


# 10 ------------------------------------------------------------------------------------------------------------
def test_scan_speed_and_tab_open(tmp_path: Path, qtbot: QtBot, win) -> None:
    import numpy as np

    from starextractor.core import naming
    from starextractor.core.fitsio import write_float32

    root = tmp_path / "Big"
    paths = dl.data_paths({"data_root": str(root)})
    dl.create_missing(paths)
    for i in range(100):
        night = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        folder = paths.processed / naming.pair_folder(night, f"Star {i}", "HD 1")
        folder.mkdir()
        st = paths.stacks / night / f"Star{i}_target_S.fit"
        st.parent.mkdir(parents=True, exist_ok=True)
        write_float32(st, np.zeros((4, 4), np.float32))
        logbook.save(folder, {**logbook.empty(folder.name, night), "notes": f"pair {i}",
                              "target": {"name": f"Star {i}", "stack_file": dl.to_stored(st, root)},
                              "reference": {"name": "HD 1", "stack_file": dl.to_stored(st, root)}})
    lib.scan(paths)  # warm the file system cache
    t0 = time.perf_counter()
    big = lib.scan(paths)
    assert big.pairs == 100 and (time.perf_counter() - t0) < 0.5
    t0 = time.perf_counter()
    win.show_tab("library")  # the scan runs in a worker: the tab opens at once
    assert (time.perf_counter() - t0) < 0.25
