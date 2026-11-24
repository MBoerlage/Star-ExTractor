"""Acquisition → Reduce: the last_stacks record (core/last_stacks.py), Select's "Use Acquisition stacks" (fills both
boxes from the record, never scans a folder, never loads) and the "Both stacks are ready" strip in Acquisition."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from astropy.io import fits
from pytestqt.qtbot import QtBot
from test_files_pair import night, stacks  # noqa: F401 - fixtures

from starextractor.core import last_stacks as ls
from starextractor.core import pair as pr
from starextractor.core.fitsio import read_header, read_image, write_float32
from starextractor.core.session import Session
from starextractor.core.settings import Settings
from starextractor.ui import dialogs, theme
from starextractor.ui.main_window import MainWindow

NIGHT = "2026-08-20"


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    w.settings["data_root"] = str(tmp_path / "Data")
    from starextractor.core import datalocs as dl

    dl.create_missing(w.settings.paths())
    w.settings.save()
    qtbot.addWidget(w)
    w.show()
    w.open_step(0)
    return w


def _put(win: MainWindow, stacks: Path, role: str, name: str, night: str = NIGHT) -> Path:  # noqa: F811
    folder = win.settings.paths().stacks / night
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / name
    shutil.copy2(stacks / f"{role.title()}_S.fit", out)
    return out


def _rec(win: MainWindow, role: str, path: Path, written: str = "2026-08-21T03:00:00Z", name: str = "") -> None:
    ls.record(win.settings, role, path, name=name or {"target": "AG Dra", "reference": "HD 145454"}[role],
              subs=5, sub_exposure_s=60.0, written_utc=written, observing_night=path.parent.name)


def _acq_stack(win: MainWindow, role: str, path: Path) -> None:
    """Acquisition's own end of a stack (on_stacked) for a file already written."""
    a = win.ensure_acquisition()
    a.names[role].setText({"target": "AG Dra", "reference": "HD 145454"}[role])
    a._stack_job = {"role": role, "folder": str(path.parent), "plan": {}, "params": {}, "subs": ["a.fit", "b.fit"],
                    "rows": None}
    image = role.title()
    run = SimpleNamespace(outputs={image: path.name}, data={image: {"n": 2}}, log=[])
    import starextractor.ui.screens.acquisition as acq

    old = (acq.calibration_text, acq.method_text)
    acq.calibration_text, acq.method_text = (lambda _p: "no masters"), (lambda _p: "median")
    try:
        a.on_stacked(run)
    finally:
        acq.calibration_text, acq.method_text = old


def _no_sx(src: Path, out: Path) -> Path:
    h = {k: v for k, v in read_header(src).items() if k in ("DATE-OBS", "DATE-END", "EXPTIME")}
    return write_float32(out, read_image(src), h)


# 1 ---- the record ------------------------------------------------------------------------------------------------
def test_acquisition_records_each_stack_and_keeps_it(win: MainWindow, stacks: Path) -> None:  # noqa: F811
    t = _put(win, stacks, "target", "AGDra_target_S.fit")
    r = _put(win, stacks, "reference", "HD145454_ref_S.fit")
    _acq_stack(win, "target", t)
    rec = ls.raw(win.settings)
    assert set(rec) == {"target"} and rec["target"]["path"] == f"Stacks/{NIGHT}/AGDra_target_S.fit"  # relative
    assert rec["target"]["subs"] == 2 and rec["target"]["observing_night"] == NIGHT and rec["target"]["written_utc"]
    _acq_stack(win, "reference", r)
    first = ls.raw(win.settings)["target"]
    t2 = _put(win, stacks, "target", "AGDra_target_S_2.fit")
    _acq_stack(win, "target", t2)  # a re-stack replaces only its own role
    rec = ls.raw(win.settings)
    assert rec["target"]["path"].endswith("AGDra_target_S_2.fit") and rec["reference"]["path"].endswith("_ref_S.fit")
    assert first["path"] != rec["target"]["path"]
    win.ensure_acquisition().clear_all(confirm=False)
    assert ls.raw(win.settings) == rec  # survives Clear all
    assert ls.raw(Settings()) == rec  # and a restart (read back from disk)


# 2 ---- Use Acquisition stacks ---------------------------------------------------------------------------------
def test_fills_both_boxes_runs_checks_and_does_not_load(win: MainWindow, stacks: Path) -> None:  # noqa: F811
    t = _put(win, stacks, "target", "AGDra_target_S.fit")
    r = _put(win, stacks, "reference", "HD145454_ref_S.fit")
    _rec(win, "target", t)
    _rec(win, "reference", r)
    f = win.files_screen
    filled = f.use_acquisition()
    assert filled == {"target": t, "reference": r}
    assert f.cards["target"].info.path == t and f.cards["reference"].info.path == r
    assert f.cards["target"].name.text() == "AG Dra" and f.cards["target"].info.name_src == "file header"
    assert f._items and not f.blocked()  # the checks ran
    assert pr.active_pair(win.session) is None  # not loaded: Load pair stays the commit
    assert f.message.text() == f"Filled from Acquisition: {t.name} and {r.name}. Check, then Load pair."
    assert f.latest_btn.text() == "Use Acquisition stacks" and f.latest_btn.isEnabled()
    assert f.latest_btn.toolTip() == "Fill both boxes with the target and reference you just stacked in Acquisition."
    assert f"Select: filled from Acquisition (target {t.name}, reference {r.name})" in win.session.log_lines[-1]


def test_name_from_the_header_set_name_only_as_fallback(win: MainWindow, stacks: Path, tmp_path: Path) -> None:  # noqa: F811
    t = _put(win, stacks, "target", "X_target_S.fit")
    with fits.open(t, mode="update") as hdul:  # an SX stack whose header has no name
        for k in ("SXNAME", "OBJECT"):
            hdul[0].header.remove(k, ignore_missing=True)
    _rec(win, "target", t, name="Set Name")
    f = win.files_screen
    f.use_acquisition()
    assert f.cards["target"].name.text() == "Set Name" and f.cards["target"].info.name_src == "Acquisition set"
    t2 = _put(win, stacks, "target", "Y_target_S.fit")
    _rec(win, "target", t2, name="Set Name")
    f.use_acquisition()
    assert f.cards["target"].name.text() == "AG Dra"  # the header wins


# 3 ---- missing pieces ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("only", ["target", "reference"])
def test_only_one_role_stacked(win: MainWindow, stacks: Path, only: str) -> None:  # noqa: F811
    p = _put(win, stacks, only, f"S_{only}.fit")
    _rec(win, only, p)
    other = "reference" if only == "target" else "target"
    f = win.files_screen
    f.use_acquisition()
    assert f.cards[only].info.path == p and f.cards[other].info is None
    assert f.message.text() == f"{pr.TITLE[other]} not stacked yet. Stack it in Acquisition or browse to a file."
    assert f.latest_btn.isEnabled()


def test_nothing_recorded_and_no_fallback(win: MainWindow) -> None:
    f = win.files_screen
    assert f.use_acquisition() == {}
    assert f.message.text().startswith("Nothing stacked in Acquisition yet.") and f.offer.isHidden()
    assert all(c.info is None for c in f.cards.values()) and f.latest_btn.isEnabled()


def test_missing_and_invalid_recorded_files(win: MainWindow, stacks: Path, tmp_path: Path) -> None:  # noqa: F811
    t = _put(win, stacks, "target", "Gone_target_S.fit")
    _rec(win, "target", t)
    t.unlink()
    bad = _no_sx(stacks / "Reference_S.fit", win.settings.paths().stacks / NIGHT / "Plain_ref_S.fit")
    _rec(win, "reference", bad)
    f = win.files_screen
    assert f.use_acquisition() == {}
    msg = f.message.text()
    assert "Target stack Gone_target_S.fit: the file no longer exists. Not filled." in msg
    assert "Reference stack Plain_ref_S.fit: not a stack from Acquisition (no SX* header). Not filled." in msg
    assert all(c.info is None for c in f.cards.values()) and f.latest_btn.isEnabled()


# 4 ---- fallback ---------------------------------------------------------------------------------------------------
def test_fallback_is_labelled_and_needs_confirmation(win: MainWindow, stacks: Path, tmp_path: Path) -> None:  # noqa: F811
    old = _put(win, stacks, "target", "Old_target_S.fit", "2026-08-19")
    new_t = _put(win, stacks, "target", "New_target_S.fit")
    new_r = _put(win, stacks, "reference", "New_ref_S.fit")
    folder = win.settings.paths().stacks
    (folder / "2026-08-19" / ls.STATE_FILE).write_text(json.dumps({"stacked": {"target": {"file": str(old)}}}))
    (folder / NIGHT / ls.STATE_FILE).write_text(json.dumps(
        {"stacked": {"target": {"file": str(new_t)}, "reference": {"file": str(new_r)}}}))
    # a typed path in another folder full of stacks: never scanned
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.copy2(stacks / "Target_S.fit", elsewhere / "Newest_target_S.fit")
    f = win.files_screen
    f.cards["reference"].set_file(shutil.copy2(stacks / "Reference_S.fit", elsewhere / "Newest_ref_S.fit"))
    f.use_acquisition()
    assert f.message.text() == "Nothing stacked in Acquisition yet."
    assert not f.offer.isHidden()
    assert f.offer_text.text() == (f"From the Stacks folder, night {NIGHT}: target New_target_S.fit and reference "
                                   "New_ref_S.fit.")
    assert f.cards["target"].info is None  # nothing filled before "Use these"
    f.cancel_offer()
    assert f.offer.isHidden() and f.cards["target"].info is None
    f.use_acquisition()
    f.offer_use.click()
    assert f.cards["target"].info.path == new_t and f.cards["reference"].info.path == new_r
    assert f.message.text() == f"Filled from the Stacks folder, night {NIGHT}. Check, then Load pair."


def test_fallback_never_scans_by_file_name(win: MainWindow, stacks: Path) -> None:  # noqa: F811
    _put(win, stacks, "target", "Lonely_target_S.fit")  # a stack Acquisition never recorded
    f = win.files_screen
    f.use_acquisition()
    assert f.offer.isHidden() and f.cards["target"].info is None


# 5 ---- roles from the record, never from names ------------------------------------------------------------------
def test_roles_never_from_file_names(win: MainWindow, stacks: Path) -> None:  # noqa: F811
    t = _put(win, stacks, "target", "Swap_ref_S.fit")  # the target stack, named like a reference
    r = _put(win, stacks, "reference", "Swap_target_S.fit")
    _rec(win, "target", t)
    _rec(win, "reference", r)
    f = win.files_screen
    f.use_acquisition()
    assert f.cards["target"].info.path == t and f.cards["reference"].info.path == r


# 6 ---- the hand-off strip -------------------------------------------------------------------------------------------
def test_handoff_strip(win: MainWindow, stacks: Path, qtbot: QtBot) -> None:  # noqa: F811
    a = win.ensure_acquisition()
    win.show_tab("acquisition")
    t = _put(win, stacks, "target", "AGDra_target_S.fit")
    r = _put(win, stacks, "reference", "HD145454_ref_S.fit")
    _acq_stack(win, "target", t)
    assert a.handoff.isHidden()  # one stack only
    _acq_stack(win, "reference", r)
    assert not a.handoff.isHidden()
    assert a.handoff_text.text() == "Both stacks are ready." and a.handoff_open.text() == "Open in Reduce"
    win.activateWindow()
    a.handoff_open.click()
    f = win.files_screen
    assert win.tab_name() == "reduce" and win.current == 0
    assert f.cards["target"].info.path == t and f.cards["reference"].info.path == r
    assert pr.active_pair(win.session) is None  # filled and checked, not loaded
    qtbot.waitUntil(lambda: f.load_btn.hasFocus(), timeout=3000)
    assert f.load_pair(), f.message.text()
    a.update_handoff()
    assert a.handoff.isHidden()  # the pair is loaded in Reduce
    # Not now hides it until the next stack
    _rec(win, "target", t, written="2026-08-21T04:00:00Z")
    win.files_screen.cards["target"].set_file(None)
    win.session.params["files"] = {}
    a.update_handoff()
    assert not a.handoff.isHidden()
    a.handoff_later.click()
    assert a.handoff.isHidden()
    a.update_handoff()
    assert a.handoff.isHidden()
    _rec(win, "reference", r, written="2026-08-21T05:00:00Z")
    a.update_handoff()
    assert not a.handoff.isHidden()


# 7 ---- Browse starts in the latest Stacks night ---------------------------------------------------------------
def test_browse_starts_in_latest_night(win: MainWindow, stacks: Path, monkeypatch) -> None:  # noqa: F811
    _put(win, stacks, "target", "A_target_S.fit", "2026-08-19")
    p = _put(win, stacks, "target", "B_target_S.fit", NIGHT)
    seen: list[str] = []
    monkeypatch.setattr(dialogs, "open_file", lambda _w, _t, start, _f: seen.append(start) or "")
    card = win.files_screen.cards["target"]
    card.browse()
    assert Path(seen[-1]) == win.settings.paths().stacks / NIGHT
    card.set_file(stacks / "Target_S.fit")
    card.browse()
    assert Path(seen[-1]) == stacks  # the folder of the current file
    assert p.exists()
