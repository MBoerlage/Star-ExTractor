"""Data architecture: one data root with Stacks, Masters, Processed, Published; core/naming.py; the pair logbook;
Promote to Published; scan_pairs. Synthetic data only."""
from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytestqt.qtbot import QtBot

from starextractor.core import datalocs as dl
from starextractor.core import logbook
from starextractor.core import naming as nm
from starextractor.core import pair as pr
from starextractor.core.cache import file_sha256
from starextractor.core.files_stage import spec_from, stack_job
from starextractor.core.fitsio import read_header, write_float32
from starextractor.core.frames import BIAS, DARK, LIGHT, load_frame, scan
from starextractor.core.jobs import Ctx
from starextractor.core.masters import MasterLibrary, Tolerances, build_master, group_for_masters
from starextractor.core.settings import Settings
from starextractor.core.subs import detect_rows
from starextractor.synth import write_example


# 1 ---- naming ---------------------------------------------------------------------------------------------------
def test_sanitize_and_names() -> None:
    assert nm.sanitize_name("AG Dra") == "AGDra"
    assert nm.sanitize_name("HD 145454") == "HD145454"
    assert nm.sanitize_name("V-799 Sgr") == "V_799Sgr"
    assert nm.sanitize_name("") == nm.sanitize_name("  ") == nm.sanitize_name(None) == "Unnamed"
    assert nm.sanitize_name("a__b") == "a_b" and nm.sanitize_name("--x--") == "x"
    assert nm.sanitize_name("P Cyg+nova.2/x") == "PCyg_nova_2_x"
    assert nm.sanitize_name('a<b>:c"d\\e|f?g*h') == "a_b_c_d_e_f_g_h"  # Windows-illegal characters
    assert nm.sanitize_name("gam Cas") == "gamCas"  # no lowercasing, no abbreviation
    assert nm.stack_filename("AG Dra", "target") == "AGDra_target_S.fit"
    assert nm.stack_filename("HD 145454", "reference") == "HD145454_ref_S.fit"
    assert nm.pair_folder("2025-09-30", "AG Dra", "HD 145454") == "2025-09-30_AGDra__HD145454"
    assert nm.published_folder(2025, "2025-09-30", "AG Dra", 2) == Path("2025") / "2025-09-30_AGDra_v2"
    assert nm.compact_date("2025-09-30") == "20250930"
    assert nm.with_suffix_number(Path("X_target_S.fit"), 2).name == "X_target_S_2.fit"


# 2 ---- observing night ------------------------------------------------------------------------------------------
class USCentral(tzinfo):
    """UTC−6, UTC−5 in summer (2nd Sunday of March 02:00 to 1st Sunday of November 02:00), without tzdata."""

    def _dst(self, dt: datetime) -> bool:
        y = dt.year
        start = datetime(y, 3, 8) + timedelta(days=(6 - datetime(y, 3, 8).weekday()) % 7, hours=2)
        end = datetime(y, 11, 1) + timedelta(days=(6 - datetime(y, 11, 1).weekday()) % 7, hours=2)
        return start <= dt.replace(tzinfo=None) < end

    def utcoffset(self, dt):
        return timedelta(hours=-5 if self._dst(dt) else -6)

    def dst(self, dt):
        return timedelta(hours=1 if self._dst(dt) else 0)

    def tzname(self, dt):
        return "CDT" if self._dst(dt) else "CST"

    def fromutc(self, dt):
        std = dt + timedelta(hours=-6)
        return std + timedelta(hours=1) if self._dst(std.replace(tzinfo=self)) else std


def test_observing_night_noon_midnight_dst_and_zones() -> None:
    ct = USCentral()
    utc = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731
    # Central summer time (UTC−5): local 21:00 on the 30th and 02:00 on the 1st are the night of the 30th
    assert nm.observing_night(utc(2025, 10, 1, 2, 0), ct) == "2025-09-30"
    assert nm.observing_night(utc(2025, 10, 1, 7, 0), ct) == "2025-09-30"
    assert nm.observing_night(utc(2025, 10, 1, 16, 59), ct) == "2025-09-30"  # 11:59 local
    assert nm.observing_night(utc(2025, 10, 1, 17, 0), ct) == "2025-10-01"  # 12:00 local
    # the DST change nights (March 8 and November 1, 2026): local noon still splits
    assert nm.observing_night(utc(2026, 3, 8, 7, 30), ct) == "2026-03-07"  # 01:30 CST, before the change
    assert nm.observing_night(utc(2026, 3, 8, 8, 30), ct) == "2026-03-07"  # 03:30 CDT, after it
    assert nm.observing_night(utc(2026, 3, 8, 17, 30), ct) == "2026-03-08"  # 12:30 CDT
    assert nm.observing_night(utc(2026, 11, 1, 17, 59), ct) == "2026-10-31"  # 11:59 CST (UTC−6)
    assert nm.observing_night(utc(2026, 11, 1, 18, 0), ct) == "2026-11-01"
    # another zone: the same instant is the next evening in Sydney (UTC+10)
    syd = timezone_fixed(10)
    assert nm.observing_night(utc(2025, 10, 1, 2, 0), syd) == "2025-10-01"  # 12:00 local
    assert nm.observing_night(utc(2025, 10, 1, 1, 59), syd) == "2025-09-30"
    # DATE-OBS strings and naive datetimes are UTC
    assert nm.observing_night("2025-10-01T02:39:24.3156260", ct) == "2025-09-30"
    assert nm.observing_night(datetime(2025, 10, 1, 2, 0), ct) == "2025-09-30"


def timezone_fixed(hours: int) -> tzinfo:
    from datetime import timezone

    return timezone(timedelta(hours=hours))


# 3 ---- settings: data locations -----------------------------------------------------------------------------------
def test_data_locations_defaults_overrides_relative_and_migration(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json")
    s["data_root"] = str(tmp_path / "root")
    p = s.paths()
    assert p.stacks == tmp_path / "root" / "Stacks" and p.published == tmp_path / "root" / "Published"
    s["data_paths"] = {"published": str(tmp_path / "other" / "Pub"), "stacks": "MyStacks"}
    p = s.paths()
    assert p.published == tmp_path / "other" / "Pub" and p.stacks == tmp_path / "root" / "MyStacks"
    assert p.masters == tmp_path / "root" / "Masters"
    assert dl.to_stored(p.root / "Stacks" / "2025-09-30" / "x.fit", p.root) == "Stacks/2025-09-30/x.fit"
    assert dl.to_stored(tmp_path / "raw" / "a.fits", p.root) == str(tmp_path / "raw" / "a.fits")  # outside: absolute
    assert dl.resolve("Stacks/2025-09-30/x.fit", p.root) == p.root / "Stacks" / "2025-09-30" / "x.fit"
    # the old master library setting becomes the Masters path once, then the key is gone
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"masters_dir": str(tmp_path / "oldlib"), "data_root": str(tmp_path / "r2")}), "utf-8")
    m = Settings(old)
    assert m.masters_dir() == tmp_path / "oldlib" and "masters_dir" not in json.loads(old.read_text("utf-8"))
    m.data["data_paths"]["masters"] = ""
    m.save()
    assert Settings(old).masters_dir() == tmp_path / "r2" / "Masters"  # read once only


def test_missing_location_is_reported_and_nothing_is_created(tmp_path: Path, qtbot: QtBot,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from starextractor.ui.data_locations import check_locations

    s = Settings(tmp_path / "settings.json")
    s["data_root"] = str(tmp_path / "root")
    dl.create_missing(s.paths())
    s["data_paths"] = {"published": str(tmp_path / "unplugged" / "Published")}
    bad = dl.problems(s.paths())
    assert [(n, st) for n, _p, st in bad] == [("Published", dl.MISSING)]
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.informativeText()) or 0)
    assert check_locations(s) is False
    assert "Published" in shown[0] and str(tmp_path / "unplugged") in shown[0]
    MasterLibrary(tmp_path / "unplugged" / "Masters")  # reading a library never creates its folder
    assert not (tmp_path / "unplugged").exists()


# shared synthetic night: masters in Masters/<camera>/<year>, stacks in Stacks/<night>/ -----------------------------
@pytest.fixture(scope="module")
def night(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    return write_example(tmp_path_factory.mktemp("night"))


def _root(tmp: Path) -> dl.DataPaths:
    paths = dl.data_paths({"data_root": str(tmp / "Data")})
    dl.create_missing(paths)
    return paths


def _masters(lib: MasterLibrary, night: dict) -> None:
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, lib, Ctx())


def _stack(paths: dl.DataPaths, lib: MasterLibrary, night: dict, role: str, name: str,
           frames: list[Path] | None = None) -> Path:
    from starextractor.ui.screens.acquisition import stack_name

    files = frames or night[role]
    folder = paths.stacks / nm.observing_night(read_header(files[0])["DATE-OBS"])
    folder.mkdir(parents=True, exist_ok=True)
    spec, problem = spec_from(role.title(), [load_frame(p, LIGHT) for p in files], {}, {}, name, lib, Tolerances())
    assert spec is not None, problem
    spec.rows = detect_rows(files[0]).to_json()
    spec.out_name = stack_name(folder, name, role, str(files[0].resolve().parent))
    stack_job(Ctx(), [spec], lib, folder)
    return folder / spec.out_name


# 4 ---- stacks, masters ---------------------------------------------------------------------------------------------
def test_stack_goes_to_stacks_night_with_header_and_collision_rule(tmp_path: Path, night: dict) -> None:
    paths = _root(tmp_path)
    lib = MasterLibrary(paths.masters)
    _masters(lib, night)
    idx = json.loads((paths.masters / "index.json").read_text("utf-8"))
    assert idx["masters"] and all(m["sha256"] and "/" in m["path"] for m in idx["masters"])  # <camera>/<year>/
    assert all((paths.masters / m["path"]).parent.parent.parent == paths.masters for m in idx["masters"])
    t = _stack(paths, lib, night, "target", "AG Dra")
    n = nm.observing_night(read_header(night["target"][0])["DATE-OBS"])
    assert t == paths.stacks / n / "AGDra_target_S.fit"
    h = read_header(t)
    assert h["SXRAWDIR"] == str(night["target"][0].resolve().parent)
    dark = lib.masters[[m.kind for m in lib.masters].index(DARK)]
    assert h["SXDARK"] == dark.path and h["SXDARKH"] == file_sha256(paths.masters / dark.path)
    assert h["SXBIASH"] == file_sha256(paths.masters / h["SXBIAS"])
    again = _stack(paths, lib, night, "target", "AG Dra")  # the same set again: replaces its file
    assert again == t
    elsewhere = [_copy(p, tmp_path / "elsewhere") for p in night["target"]]  # the same subs from another folder
    other = _stack(paths, lib, night, "target", "AG Dra", elsewhere)
    assert other.name == "AGDra_target_S_2.fit"  # a different set (other raw folder) with the same name


def _copy(p: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(p, folder / p.name))


def test_master_matching_and_index_rebuild(tmp_path: Path, night: dict) -> None:
    from starextractor.core.masters import LightSettings, pick_calibration

    paths = _root(tmp_path)
    lib = MasterLibrary(paths.masters)
    _masters(lib, night)
    d0 = next(m for m in lib.masters if m.kind == DARK)
    twin_path = paths.masters / Path(d0.path).with_name("dark_twin.fits")
    shutil.copy2(paths.masters / d0.path, twin_path)
    twin = lib.import_file(twin_path, DARK)
    light = LightSettings.from_frames([load_frame(p, LIGHT) for p in night["target"]])
    assert light is not None and light.date
    d0.date, twin.date = "2020-01-01", light.date  # equal in every rule but time: the nearer one wins
    assert pick_calibration(light, lib).dark.id == twin.id
    d0.date, twin.date = light.date, "2020-01-01"
    assert pick_calibration(light, lib).dark.id == d0.id
    (paths.masters / "index.json").unlink()
    rebuilt = MasterLibrary(paths.masters)
    assert len(rebuilt.masters) == len(lib.masters) and (paths.masters / "index.json").exists()
    assert {m.kind for m in rebuilt.masters} == {DARK, BIAS}


# 5 ---- Load pair ---------------------------------------------------------------------------------------------------
@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path, night: dict):
    from PySide6.QtWidgets import QApplication

    from starextractor.core.session import Session
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session())
    w.settings["data_root"] = str(tmp_path / "Data")
    dl.create_missing(w.settings.paths())
    w._library = None
    _masters(w.library, night)
    qtbot.addWidget(w)
    w.show()
    w.open_step(0)
    return w


def _pair(w, night: dict) -> dict[str, Path]:
    paths = w.settings.paths()
    return {"target": _stack(paths, w.library, night, "target", "AG Dra"),
            "reference": _stack(paths, w.library, night, "reference", "HD 145454")}


def test_load_pair_creates_pair_folder_work_and_logbook(win, qtbot: QtBot, night: dict) -> None:
    from starextractor.core.geometry_stage import geometry_job
    from starextractor.core.geometry_stage import specs_for as geometry_specs

    stacks = _pair(win, night)
    f = win.files_screen
    f.use_latest()
    assert f.cards["target"].info.path == stacks["target"] and "Picked " in f.message.text()
    assert f.load_pair(), f.message.text()
    paths = win.settings.paths()
    n = nm.observing_night(read_header(night["target"][0])["DATE-OBS"])
    pair = paths.processed / f"{n}_AGDra__HD145454"
    assert win.session.folder == pair
    for name in ("Target_S.fit", "Reference_S.fit"):
        assert (pair / "work" / name).exists()
    doc = logbook.load(pair)
    assert doc["schema_version"] == 2 and doc["pair_id"] == pair.name and doc["night"] == n
    assert doc["target"]["stack_file"] == f"Stacks/{n}/AGDra_target_S.fit"
    assert doc["target"]["sha256"] == file_sha256(stacks["target"]) and doc["target"]["rows"]["spectrum"]
    assert doc["camera"]["gain"] is not None and {m["kind"] for m in doc["masters_used"]} == {"dark", "bias"}
    assert all(m["file"].startswith("Masters/") and len(m["sha256"]) == 64 for m in doc["masters_used"])
    # reloading the same pair reuses the folder and its logbook
    created = doc["created_utc"]
    assert f.load_pair()
    assert win.session.folder == pair and logbook.load(pair)["created_utc"] == created
    assert not (paths.processed / f"{pair.name}_2").exists()
    # Geometry runs on work/Target_S.fit; its output lands in the pair folder (work/)
    specs, problems = geometry_specs(win.session, ["Target", "Reference"], win.profile, win.library)
    assert not problems and specs[0].src == pair / "work" / "Target_S.fit"
    run = geometry_job(Ctx(), specs)
    assert run.outputs["Target"] == "work/Target_G.fit" and (pair / "work" / "Target_G.fit").exists()
    win._stage_done("geometry", run)
    assert logbook.load(pair)["processing"]["geometry"]["summary"]
    assert win.session.status["geometry"].status == "valid"


def test_different_pair_with_the_same_name_gets_2(tmp_path: Path, night: dict) -> None:
    paths = _root(tmp_path)
    lib = MasterLibrary(paths.masters)
    _masters(lib, night)
    t = _stack(paths, lib, night, "target", "AG Dra")
    r = _stack(paths, lib, night, "reference", "HD 145454")
    infos = {"target": pr.read_stack(t), "reference": pr.read_stack(r)}
    names = {"target": "AG Dra", "reference": "HD 145454"}
    folder, reused = pr.pair_folder_for(paths.processed, infos, names, paths.root)
    folder.mkdir()
    logbook.save(folder, {**logbook.empty(folder.name, "x"), "target": {"stack_file": "Stacks/other.fit"}})
    second, reused = pr.pair_folder_for(paths.processed, infos, names, paths.root)
    assert second.name == folder.name + "_2" and not reused


# 6 ---- logbook ----------------------------------------------------------------------------------------------------
def test_logbook_atomic_unknown_fields_status_and_moved_root(tmp_path: Path, night: dict) -> None:
    from starextractor.core.session import Session

    paths = _root(tmp_path)
    lib = MasterLibrary(paths.masters)
    _masters(lib, night)
    infos = {"target": pr.read_stack(_stack(paths, lib, night, "target", "AG Dra")),
             "reference": pr.read_stack(_stack(paths, lib, night, "reference", "HD 145454"))}
    names = {"target": "AG Dra", "reference": "HD 145454"}
    folder, _ = pr.pair_folder_for(paths.processed, infos, names, paths.root)
    folder.mkdir()
    s = Session.create(folder)
    pr.load_pair(s, infos, names, data_root=paths.root, masters_root=paths.masters)
    doc = logbook.load(folder)
    doc["from_the_future"] = {"keep": [1, 2]}
    doc["target"]["extra_key"] = "x"
    logbook.save(folder, doc)
    logbook.update(folder, {"processing": {"geometry": {"tilt": 0.3}}, "notes": "clouds at 23:10"})
    doc = logbook.load(folder)
    assert doc["from_the_future"] == {"keep": [1, 2]} and doc["target"]["extra_key"] == "x"
    assert doc["processing"]["geometry"]["tilt"] == 0.3 and doc["notes"] == "clouds at 23:10"
    assert not list(folder.glob("*.tmp"))  # temp file replaced
    assert logbook.derive_status(folder, doc, paths.root) == {"stacked": True, "reduced": False, "published": False}
    (folder / "Target_X.fit").write_bytes(b"")
    assert logbook.derive_status(folder, doc, paths.root)["reduced"]
    # the whole data root copied elsewhere: relative paths still resolve
    moved = tmp_path / "moved" / "Data"
    shutil.copytree(paths.root, moved)
    shutil.rmtree(paths.root)
    found = logbook.scan_pairs(moved)
    assert len(found) == 1 and found[0].status["stacked"] and found[0].target == "AG Dra"
    assert found[0].notes_preview == "clouds at 23:10"


def test_notes_autosave_round_trip(win, qtbot: QtBot, night: dict) -> None:
    _pair(win, night)
    f = win.files_screen
    f.use_latest()
    f.notes.setPlainText("typed before loading")
    assert f.load_pair()
    folder = win.session.folder
    assert "typed before loading" in logbook.load(folder)["notes"]
    f.notes.setPlainText("seeing 2.5 arcsec, thin cirrus")
    qtbot.waitUntil(lambda: logbook.load(folder)["notes"] == "seeing 2.5 arcsec, thin cirrus", timeout=4000)
    win.load_session(folder)
    assert win.files_screen.notes.toPlainText() == "seeing 2.5 arcsec, thin cirrus"


def test_set_notes_are_imported_once(tmp_path: Path, night: dict) -> None:
    from starextractor.core import acq_sets
    from starextractor.core.session import Session

    paths = _root(tmp_path)
    lib = MasterLibrary(paths.masters)
    _masters(lib, night)
    t = _stack(paths, lib, night, "target", "AG Dra")
    r = _stack(paths, lib, night, "reference", "HD 145454")
    doc = {"version": 1, "folder": str(t.parent), "night": "",
           "target": {"name": "AG Dra", "stack_file": dl.to_stored(t, paths.root), "notes": "focus redone"},
           "reference": {"name": "HD 145454", "stack_file": dl.to_stored(r, paths.root)}}
    (t.parent / acq_sets.FILE_NAME).write_text(json.dumps(doc), "utf-8")
    infos = {"target": pr.read_stack(t), "reference": pr.read_stack(r)}
    folder, _ = pr.pair_folder_for(paths.processed, infos, {"target": "AG Dra", "reference": "HD 145454"},
                                   paths.root)
    folder.mkdir()
    s = Session.create(folder)
    for _ in range(2):
        pr.load_pair(s, infos, {"target": "AG Dra", "reference": "HD 145454"}, data_root=paths.root,
                     masters_root=paths.masters)
    notes = logbook.load(folder)["notes"]
    assert notes.count("focus redone") == 1
    assert "focus redone" in json.loads((t.parent / acq_sets.FILE_NAME).read_text("utf-8"))["target"]["notes"]


# 7 ---- Promote -----------------------------------------------------------------------------------------------------
def test_promote_versions_never_overwrite_and_no_sx(tmp_path: Path) -> None:
    from starextractor.core import export as ex
    from starextractor.core import stack_keys as sk
    from starextractor.core.publish import SUBMISSION, promote

    paths = _root(tmp_path)
    pair = paths.processed / "2025-09-30_AGDra__HD145454"
    pair.mkdir()
    logbook.save(pair, {**logbook.empty(pair.name, "2025-09-30"), "target": {"name": "AG Dra"}})
    hdr = {**sk.sx_cards("AG Dra", 3, 60.0, {"spec": (5, 9)}), "SXRAWDIR": "C:/raw", "NAXIS": 1,
           "DATE-OBS": "2025-10-01T02:39:24", "EXPTIME": 60.0, "NCOMBINE": 3}
    plan = ex.build_header(hdr, ex.ObserverInfo(obscode="XXX"), "AG Dra", None, None)
    avspec = ex.write_export(pair / "export" / "_agdra_20251001_110_XXX.fit", np.ones(20, np.float32), plan)
    e1 = promote(pair, avspec, paths.published, paths.root)
    v1 = paths.published / "2025" / "2025-09-30_AGDra_v1"
    assert e1["version"] == 1 and sorted(p.name for p in v1.iterdir()) == sorted([avspec.name, SUBMISSION])
    assert not (v1 / "logbook.json").exists() and avspec.exists()  # a copy; the pair folder keeps it
    sub = json.loads((v1 / SUBMISSION).read_text("utf-8"))
    assert sub["sha256"] == file_sha256(v1 / avspec.name) and sub["app_version"] and sub["date_utc"]
    with fits.open(v1 / avspec.name) as f:
        assert not any(sk.is_sx(k) for k in f[0].header)
    before = (v1 / avspec.name).read_bytes()
    e2 = promote(pair, avspec, paths.published, paths.root)
    assert e2["version"] == 2 and (v1 / avspec.name).read_bytes() == before
    (paths.published / "2025" / "2025-09-30_AGDra_v3").mkdir()  # a folder already there is skipped
    assert promote(pair, avspec, paths.published, paths.root)["version"] == 4
    doc = logbook.load(pair)
    assert [p["version"] for p in doc["published"]] == [1, 2, 4] and doc["status"]["published"]
    assert doc["published"][0]["file"] == f"Published/2025/2025-09-30_AGDra_v1/{avspec.name}"


# 8 ---- scan_pairs ----------------------------------------------------------------------------------------------------
def test_scan_pairs_reads_logbooks_only_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import starextractor.core.fitsio as fio

    paths = _root(tmp_path)
    for i in range(20):
        night = f"2025-09-{i + 1:02d}"
        folder = paths.processed / nm.pair_folder(night, f"Star {i}", "HD 1")
        folder.mkdir()
        stack = paths.stacks / night / f"Star{i}_target_S.fit"
        stack.parent.mkdir(parents=True, exist_ok=True)
        write_float32(stack, np.zeros((4, 4), np.float32))
        logbook.save(folder, {**logbook.empty(folder.name, night), "notes": f"pair {i}",
                              "target": {"name": f"Star {i}", "stack_file": dl.to_stored(stack, paths.root)},
                              "reference": {"name": "HD 1", "stack_file": dl.to_stored(stack, paths.root)}})

    def no_pixels(*_a, **_k):
        raise AssertionError("scan_pairs must not read pixel data")

    monkeypatch.setattr(fio, "open_image", no_pixels)
    monkeypatch.setattr(fio, "read_image", no_pixels)
    t0 = time.perf_counter()
    found = logbook.scan_pairs(paths.root)
    dt = time.perf_counter() - t0
    assert len(found) == 20 and dt < 0.2, dt
    assert found[0].night == "2025-09-20" and found[0].status["stacked"] and found[-1].notes_preview == "pair 0"
