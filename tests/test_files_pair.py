"""Reduce > Files as a pair selector: two stacks in, checks, Load pair; the SX* row keywords written by the stack
writer; header-only row updates from Acquisition; nothing of the SX* block downstream. Synthetic data only."""
from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytestqt.qtbot import QtBot

from starex_reduce.core import acq_sets
from starex_reduce.core import pair as pr
from starex_reduce.core import stack_keys as sk
from starex_reduce.core.files_stage import spec_from, stack_job
from starex_reduce.core.fitsio import read_header, write_float32
from starex_reduce.core.frames import BIAS, DARK, LIGHT, load_frame, scan
from starex_reduce.core.jobs import Ctx
from starex_reduce.core.masters import MasterLibrary, Tolerances, build_master, group_for_masters
from starex_reduce.core.session import Session, Status
from starex_reduce.core.subs import detect_rows
from starex_reduce.synth import write_example
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow
from starex_reduce.ui.rail import StepState

T_RA, T_DEC, R_RA, R_DEC = 240.42, 66.80, 241.10, 67.50  # about 0.8° apart


@pytest.fixture(scope="module")
def night(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    return write_example(tmp_path_factory.mktemp("night"))


@pytest.fixture(scope="module")
def stacks(tmp_path_factory: pytest.TempPathFactory, night: dict) -> Path:
    """Target_S.fit / Reference_S.fit as Acquisition's Stack selected writes them (rows, name, position)."""
    out = tmp_path_factory.mktemp("stacks")
    lib = MasterLibrary(out / "masters")
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, lib, Ctx())
    for image, key, name, ra, dec in (("Target", "target", "AG Dra", T_RA, T_DEC),
                                      ("Reference", "reference", "HD 145454", R_RA, R_DEC)):
        spec, problem = spec_from(image, [load_frame(p, LIGHT) for p in night[key]], {}, {}, name, lib, Tolerances())
        assert spec is not None, problem
        spec.rows = detect_rows(night[key][0]).to_json()
        spec.coords = (ra, dec)
        spec.out_name = f"{image}_S.fit"  # flat in this folder (the app names them <Name>_target_S.fit)
        stack_job(Ctx(), [spec], lib, out)
    return out


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    w.open_step(0)
    return w


def _copy_pair(stacks: Path, folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return {r: Path(shutil.copy2(stacks / f"{r.title()}_S.fit", folder / f"{r.title()}_S.fit")) for r in pr.ROLES}


def _old_stack(path: Path, shape: tuple[int, int] = (320, 3840), spectrum_at: int = 160) -> Path:
    """A stack from another program: pixels only, no name, rows or camera keywords."""
    rng = np.random.default_rng(1)
    data = rng.normal(100, 3, shape).astype(np.float32)
    data[spectrum_at - 3: spectrum_at + 4] += 2000
    write_float32(path, data, {"DATE-OBS": "2026-08-20T02:00:00"})
    return path


def _states(screen) -> list[str]:
    return [s for s, _ in screen._items]


def _wait_previews(win: MainWindow, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not any(win.runner.is_busy(f"preview.files.{r}") for r in pr.ROLES), timeout=5000)


# 1 -------------------------------------------------------------------------------------------------------------
def test_two_valid_stacks_fill_both_cards(win: MainWindow, qtbot: QtBot, stacks: Path) -> None:
    f = win.files_screen
    for role in pr.ROLES:
        f.cards[role].set_file(stacks / f"{role.title()}_S.fit")
    t = f.cards["target"]
    v = t.values
    assert v["Size"].text() == "3840 × 320" and v["Stack"].text() == "3 subs × 300 s"
    assert v["Taken"].text().endswith(" UT") and v["Camera"].text().startswith("gain ")
    assert v["Position"].text().startswith("16 01 4") and t.tags["Position"].text() == "file header"
    assert v["Rows"].text().startswith("Spectrum ") and "Sky A" in v["Rows"].text()
    assert t.tags["Rows"].text() == "file header"
    assert t.name.text() == "AG Dra" and t.name_tag.text() == "file header"
    assert f.cards["reference"].name.text() == "HD 145454"
    assert set(_states(f)) == {pr.OK}, f._items
    assert f.checks_status.text() == "all clear"
    _wait_previews(win, qtbot)
    assert t.preview.result is not None and t.no_rows.isHidden()


# 2 -------------------------------------------------------------------------------------------------------------
def test_old_stack_warns_and_needs_a_name(win: MainWindow, qtbot: QtBot, stacks: Path, tmp_path: Path) -> None:
    f = win.files_screen
    f.cards["target"].set_file(_old_stack(tmp_path / "old_target.fits"))
    f.cards["reference"].set_file(stacks / "Reference_S.fit")
    t = f.cards["target"]
    assert t.name.text() == "" and t.name_tag.text() == "needed"
    assert t.values["Rows"].text() == "not in the file" and not t.no_rows.isHidden()
    assert t.values["Camera"].text() == "not in header"
    texts = " | ".join(x for _s, x in f._items)
    assert "Type a name for the target" in texts
    assert "Rows are not in the files" in texts and "Camera settings are not in the headers" in texts
    assert f.checks_status.text() == "1 to fix"
    assert not f.load_pair() and f.message.text() == "Fix the items marked ✗ first."
    assert win.session.status["files"].status != Status.VALID
    t.name.setText("Old Nova")
    t.name.textEdited.emit("Old Nova")  # typing re-checks live
    assert pr.ERR not in _states(f) and t.name_tag.text() == "typed"
    assert f.message.text() == ""
    assert f.load_pair()


# 3 -------------------------------------------------------------------------------------------------------------
def test_size_same_file_time_gap_and_distance(tmp_path: Path, stacks: Path) -> None:
    names = {"target": "A", "reference": "B"}
    small = pr.read_stack(_old_stack(tmp_path / "small.fits", shape=(300, 1000)))
    target = pr.read_stack(stacks / "Target_S.fit")
    items = pr.checks(target, small, names)
    assert items[0][0] == pr.ERR and "differ in size" in items[0][1]
    same = pr.checks(target, pr.read_stack(stacks / "Target_S.fit"), names)
    assert (pr.ERR, "Target and Reference point at the same file.") in same
    far = pr.read_stack(stacks / "Reference_S.fit")
    far.start = target.start + timedelta(hours=4)  # type: ignore[operator]
    far.ra, far.dec = T_RA + 180.0, -T_DEC
    items = dict((t, s) for s, t in pr.checks(target, far, names))
    gap = next(t for t in items if t.startswith("Reference taken"))
    assert items[gap] == pr.WARN and "4.0 h after" in gap
    sep = next(t for t in items if "from the target on the sky" in t)
    assert items[sep] == pr.WARN
    assert pr.summary(pr.checks(target, far, names))[0] == pr.WARN  # warnings never block


# 4 -------------------------------------------------------------------------------------------------------------
def test_load_pair_feeds_geometry_and_extract(win: MainWindow, qtbot: QtBot, stacks: Path, tmp_path: Path) -> None:
    src = _copy_pair(stacks, tmp_path / "elsewhere")  # stacks outside the project folder are copied in
    f = win.files_screen
    for role in pr.ROLES:
        f.cards[role].set_file(src[role])
    assert f.load_pair()
    folder = win.session.folder  # the pair folder Processed/<night>_AGDra__HD145454; the stacks in work/
    assert folder.parent == win.settings.paths().processed and folder.name.endswith("_AGDra__HD145454")
    for role in pr.ROLES:
        dst = folder / "work" / f"{role.title()}_S.fit"
        assert dst.exists() and dst.read_bytes() == src[role].read_bytes()
    assert win.session.status["files"].status == Status.VALID
    assert win.rail.shown_state("select") in (StepState.DONE, StepState.CURRENT)  # the shown step is current (orange)
    assert win.rail.by_id["select"].status == "AG Dra + HD 145454"
    assert f.message.text() == "✓ Pair loaded. Next: Geometry."
    assert any("Select: pair loaded: AG Dra + HD 145454" in ln for ln in win.session.log_lines)
    assert win.session.inputs["target"]["name"] == "AG Dra"
    assert win.session.inputs["reference"]["coords"]["ra"].startswith("16 04")
    from starex_reduce.core.extract_stage import default_offsets
    from starex_reduce.core.geometry_stage import geometry_job
    from starex_reduce.core.geometry_stage import specs_for as geometry_specs

    specs, problems = geometry_specs(win.session, ["Target", "Reference"], win.profile, win.library)
    assert not problems and [s.src for s in specs] == [folder / "work/Target_S.fit", folder / "work/Reference_S.fit"]
    run = geometry_job(Ctx(), specs)
    assert set(run.outputs) == {"Target", "Reference"}
    off, how = default_offsets(win.session, "Target", None)
    rows = pr.read_stack(folder / "work/Target_S.fit").rows
    assert how == "rows from the stack (file header)"
    spec0 = sk.bands0(rows)["spec"]
    assert off["near"][0] == (spec0.top + spec0.bottom) // 2
    assert off["spec"] == [spec0.top - off["near"][0], spec0.bottom - off["near"][0]]


# 5 -------------------------------------------------------------------------------------------------------------
def test_new_pair_invalidates_later_steps_same_pair_does_not(tmp_path: Path, stacks: Path) -> None:
    s = Session.create(tmp_path / "sess")
    src = _copy_pair(stacks, tmp_path / "a")
    infos = {r: pr.read_stack(src[r]) for r in pr.ROLES}
    names = {"target": "AG Dra", "reference": "HD 145454"}
    pr.load_pair(s, infos, names)
    s.mark_done("geometry", {}, "")
    assert s.status["geometry"].status == Status.VALID
    pr.load_pair(s, {r: pr.read_stack(src[r]) for r in pr.ROLES}, names)  # the same pair again
    assert s.status["geometry"].status == Status.VALID
    other = tmp_path / "b" / "Target_S.fit"
    other.parent.mkdir()
    with fits.open(src["target"]) as h:
        h[0].data = h[0].data * np.float32(1.01)
        h.writeto(other)
    pr.load_pair(s, {"target": pr.read_stack(other), "reference": infos["reference"]}, names)
    assert s.status["files"].status == Status.VALID
    assert s.status["geometry"].status == Status.STALE and not s.image_done("geometry", "Target")


# 6 -------------------------------------------------------------------------------------------------------------
def test_row_keywords_convention_and_round_trip(stacks: Path, night: dict, tmp_path: Path) -> None:
    rows = detect_rows(night["target"][0]).to_json()
    h = read_header(stacks / "Target_S.fit")
    assert h["SXVER"] == sk.SX_VERSION and h["SXNSUB"] == 3 and h["SXSUBEXP"] == 300.0 and h["SXNAME"] == "AG Dra"
    assert h["OBJECT"] == "AG Dra" and h["RA"] == pytest.approx(T_RA) and h["DEC"] == pytest.approx(T_DEC)
    # drawn from the file's 1-based inclusive rows = drawn the way Acquisition's overlay draws its bands
    with fits.open(stacks / "Target_S.fit") as f:
        shape = f[0].data.shape
        assert "1-based" in f[0].header.comments["SXROW1"]
    from_file = np.zeros(shape, bool)
    for band in sk.bands0(sk.rows_from_header(h)).values():
        from_file[band.top: band.bottom + 1] = True
    overlay = np.zeros(shape, bool)
    r0 = round(float(rows["row"]))  # Acquisition: Band(r0 + a, r0 + b) per offset pair, drawn top..bottom + 1
    for a, b in (rows["spec"], rows["sky_a"], rows["sky_b"]):
        overlay[r0 + a: r0 + b + 1] = True
    assert np.array_equal(from_file, overlay)
    assert pr.read_stack(stacks / "Target_S.fit").rows_src == "file header"
    # a band with no selection writes no keywords
    only_spec = sk.sx_cards("X", 2, 10.0, {"spec": (5, 9)})
    assert "SXROW1" in only_spec and not any(k.startswith(("SXSKA", "SXSKB")) for k in only_spec)
    # no sets file anywhere: the rows still come from the header
    lone = tmp_path / "lone" / "T.fits"
    lone.parent.mkdir()
    shutil.copy2(stacks / "Target_S.fit", lone)
    info = pr.read_stack(lone)
    assert info.rows == sk.rows_from_header(h) and info.rows_src == "file header" and info.name == "AG Dra"


def test_header_only_row_update_from_acquisition(qtbot: QtBot, tmp_path: Path, night: dict) -> None:
    from PySide6.QtWidgets import QApplication

    from starex_reduce.core import subs as sb

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    w.show_tab("acquisition")
    a = w.ensure_acquisition()
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, w.library, Ctx())
    w.library_changed.emit()
    a.names["target"].setText("AG Dra")
    a.enqueue(night["target"])
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)
    with qtbot.waitSignal(w.runner.finished, timeout=30000, check_params_cb=lambda s, _r: s == "acq.stack"):
        assert a.stack_selected()
    out = Path(a.store["stacked"]["target"]["file"])
    assert out.name == "AGDra_target_S.fit" and out.parent.parent == w.settings.paths().stacks
    assert a.rows_hint.text() == "Rows saved in the stack file."
    with fits.open(out) as f:
        pixels = f[0].data.tobytes()
        before = sk.rows_from_header(dict(f[0].header))
    cur = a.rows_by["target"]
    new = sb.Rows(cur.row + 4, cur.tilt_deg, cur.x_centre, cur.spec, cur.sky_a, cur.sky_b, "edited", cur.fwhm)
    with qtbot.waitSignal(w.runner.finished, timeout=10000, check_params_cb=lambda s, _r: s == "acq.hdr.target"):
        a._set_rows("target", new)
        assert a.rows_hint.text() == "Rows changed since stacking: updating stack file…"
    assert a.rows_hint.text() == "Rows saved in the stack file."
    with fits.open(out) as f:
        assert f[0].data.tobytes() == pixels  # pixel data byte-identical
        after = sk.rows_from_header(dict(f[0].header))
    assert after["spec"] == (before["spec"][0] + 4, before["spec"][1] + 4)
    doc = acq_sets.load_sets(out.parent / acq_sets.FILE_NAME)
    assert doc["target"]["rows"]["row"] == pytest.approx(cur.row + 4)
    # a missing stack file: a warning, the sets file keeps the rows
    out.unlink()
    newer = sb.Rows(cur.row + 6, cur.tilt_deg, cur.x_centre, cur.spec, cur.sky_a, cur.sky_b, "edited", cur.fwhm)
    with qtbot.waitSignal(w.runner.failed, timeout=10000, check_params_cb=lambda s, _m: s == "acq.hdr.target"):
        a._set_rows("target", newer)
    assert any("could not update the rows" in ln for ln in w.session.log_lines)
    assert acq_sets.load_sets(out.parent / acq_sets.FILE_NAME)["target"]["rows"]["row"] == \
        pytest.approx(cur.row + 6)


# 7 -------------------------------------------------------------------------------------------------------------
def test_sx_block_never_reaches_later_or_exported_headers(stacks: Path, tmp_path: Path) -> None:
    from starex_reduce.core import export as ex

    h = read_header(stacks / "Target_S.fit")
    assert any(sk.is_sx(k) for k in h)
    later = write_float32(tmp_path / "Target_G.fit", np.zeros((4, 4), np.float32), h)  # how every stage copies
    assert not any(sk.is_sx(k) for k in read_header(later))
    obs = ex.ObserverInfo.from_settings({"observer": {"obscode": "XXX"}})
    plan = ex.build_header({**h, "NAXIS": 1}, obs, "AG Dra", None, None)
    assert not any(sk.is_sx(k) for k in plan.header)
    out = ex.write_export(tmp_path / "x.fits", np.ones(10, np.float32), plan)
    assert not any(sk.is_sx(k) for k in read_header(out))


# 8 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("present", [("target", "reference"), ("target",), ()])
def test_use_latest_stacks(win: MainWindow, stacks: Path, present: tuple[str, ...]) -> None:
    night = win.settings.paths().stacks / "2026-08-20"
    night.mkdir(parents=True, exist_ok=True)
    older = win.settings.paths().stacks / "2026-08-19"  # an older night is never picked over the newest
    older.mkdir()
    shutil.copy2(stacks / "Target_S.fit", older / "Old_target_S.fit")
    shutil.copy2(stacks / "Reference_S.fit", older / "Old_ref_S.fit")
    names = {"target": "AGDra_target_S.fit", "reference": "HD145454_ref_S.fit"}
    for role in present:
        shutil.copy2(stacks / f"{role.title()}_S.fit", night / names[role])
    f = win.files_screen
    f.use_latest()
    msg = f.message.text()
    if len(present) == 2:
        assert msg == "Picked 2026-08-20: AGDra_target_S.fit and HD145454_ref_S.fit."
        assert f.cards["target"].name.text() == "AG Dra"
    elif present:
        assert msg == "Picked 2026-08-20: AGDra_target_S.fit; no reference stack in that night."
        assert f.cards["reference"].info is None
    else:
        assert msg == "Picked 2026-08-19: Old_target_S.fit and Old_ref_S.fit."  # the newest night with stacks


# 9 -------------------------------------------------------------------------------------------------------------
def test_pairs_list_survives_reload_and_old_files_open(tmp_path: Path, stacks: Path) -> None:
    s = Session.create(tmp_path / "sess")
    src = _copy_pair(stacks, s.folder)
    pr.load_pair(s, {r: pr.read_stack(src[r]) for r in pr.ROLES}, {"target": "AG Dra", "reference": "HD 145454"})
    s.save()
    back = Session.load(s.folder)
    assert len(pr.pairs_of(back)) == 1 and pr.active_pair(back)["target"]["name"] == "AG Dra"
    assert back.status["files"].status == Status.VALID
    # an old session (Files stacked lights) with the removed keys still opens
    old = tmp_path / "old"
    old.mkdir()
    (old / "session.json").write_text(json.dumps({
        "schema": 1, "inputs": {"target": {"name": "N", "folder": "x", "lights": [{"path": "a.fits", "used": True}],
                                           "stack": {"method": "median"}, "unknown": 1}},
        "calibration": {"target": {"choice": {}, "resolved": []}}, "params": {"files": {"whatever": 2}},
        "status": {}}), encoding="utf-8")
    o = Session.load(old)
    assert o.inputs["target"]["name"] == "N" and pr.active_pair(o) is None and o.compute_keys()["files"]["Target"]
    # old settings with the keys of the removed Files options load too
    from starex_reduce.core.settings import Settings

    sp = tmp_path / "settings.json"
    assert Settings(sp)["masters_dir"] is None and Settings(sp).masters_dir().name == "Masters"  # key migrated
    assert Settings(sp)["masters_dir"] is None and Settings(sp).masters_dir().name == "Masters"  # migrated


# 10 ------------------------------------------------------------------------------------------------------------
def test_preview_off_the_ui_thread_around_far_rows(win: MainWindow, qtbot: QtBot, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    data = np.random.default_rng(2).normal(100, 3, (2160, 1200)).astype(np.float32)
    data[1037:1058] += 3000.0  # rows 1038-1058 (1-based) of a 2160-row frame, away from the centre (1080)
    path = tmp_path / "Reference_S.fit"
    hdr = {**sk.sx_cards("HD 145454", 10, 30.0, {"spec": (1038, 1058), "sky_a": (1000, 1020),
                                                  "sky_b": (1076, 1096)}), "GAIN": 200, "OFFSET": 3}
    write_float32(path, data, hdr, comments=sk.COMMENTS, keep_sx=True)
    threads: list[str] = []
    real = pr.preview_job

    def spy(ctx, p, rows):
        threads.append(threading.current_thread().name)
        return real(ctx, p, rows)

    monkeypatch.setattr(pr, "preview_job", spy)
    card = win.files_screen.cards["reference"]
    t0 = time.perf_counter()
    card.set_file(path)
    assert (time.perf_counter() - t0) < 0.3  # the card is filled without waiting for pixels
    _wait_previews(win, qtbot)
    assert threads and threads[0] != threading.main_thread().name
    res = card.preview.result
    assert res is not None and res["y0"] == 1037 - pr.PREVIEW_HALF_ROWS and res["y1"] == 1058 + pr.PREVIEW_HALF_ROWS
    u8 = res["u8"]
    band = u8[1037 - res["y0"]: 1058 - res["y0"]]
    assert band.mean() > u8.mean() + 50  # the spectrum is in the window, bright; never a blank strip
    assert card.preview.pixmap() is not None and not card.preview.pixmap().isNull()


def test_run_all_needs_a_pair(win: MainWindow) -> None:
    win.run_all()
    assert win.session.log_lines[-1].endswith("Load a pair in Select first.")
    assert not (win.session.folder / "work" / "Target_G.fit").exists()
