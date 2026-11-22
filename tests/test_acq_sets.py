"""acquisition_sets.json (doc test 6): round trip, tolerance, missing/changed files, roles."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from starextractor.core import acq_sets
from starextractor.core.subs import SubRecord, refresh_flags


def _rec(path: Path, role: str, snr: float = 60.0, included: bool = True, flags: list[str] | None = None,
         obj: str = "Sgrnov32026") -> SubRecord:
    st = path.stat()
    return SubRecord(path=str(path), name=path.name, time="2026-08-21T03:42:58", exptime=300 if role == "target"
                     else 30, gain=200, object=obj, peak_fill=0.5, continuum_snr=snr, n_continuum=2000,
                     role=role, included=included, flags=flags or [], size=st.st_size, mtime=st.st_mtime)


@pytest.fixture
def night(tmp_path: Path) -> dict[str, list[Path]]:
    out = {}
    for sub, n in (("SgrNov32026", 4), ("hd163955", 3)):
        d = tmp_path / "night" / sub
        d.mkdir(parents=True)
        out[sub] = []
        for i in range(n):
            p = d / f"{sub}_{i + 1:05d}.fits"
            p.write_bytes(b"x" * (2880 + i))
            out[sub].append(p)
    return out


def test_round_trip_and_relative_paths(tmp_path: Path, night: dict) -> None:
    recs = [_rec(p, "target") for p in night["SgrNov32026"]] + [_rec(p, "reference") for p in night["hd163955"]]
    recs[2].included, recs[2].flags = False, ["SNR low"]
    doc = acq_sets.make_sets(recs, {"target": "V8055 Sgr", "reference": "HD 163955"},
                             {"target": "plan", "reference": "typed"})
    from starextractor.core.session import night_of

    # 03:42 UTC is the evening of the 20th in Texas: the night is local noon to noon, as the session folders
    assert doc["version"] == 1 and doc["night"] == night_of("2026-08-21T03:42:58")
    assert Path(doc["folder"]) == tmp_path / "night"
    assert doc["target"]["good"] == ["SgrNov32026/SgrNov32026_00001.fits", "SgrNov32026/SgrNov32026_00002.fits",
                                     "SgrNov32026/SgrNov32026_00004.fits"]
    assert doc["target"]["excluded"] == [{"file": "SgrNov32026/SgrNov32026_00003.fits", "reason": "SNR low"}]
    assert doc["target"]["exposure_s"] == 300 and doc["reference"]["exposure_s"] == 30
    assert doc["qc"]["hd163955/hd163955_00001.fits"]["size"] == 2880
    path = acq_sets.save_sets(tmp_path / "s" / acq_sets.FILE_NAME, doc)
    back = acq_sets.load_sets(path)
    for role in ("target", "reference"):
        # optional keys defaulted
        assert back[role] == {**doc[role], "stack_file": "", "rows": None, "ra": "", "dec": "", "sp_type": "",
                              "notes": "", "lobe_mode": "auto"}
    assert back["qc"] == doc["qc"] and back["folder"] == doc["folder"]
    assert acq_sets.check_files(back) == {"missing": [], "changed": []}
    assert acq_sets.summary(back) == "Target V8055 Sgr, 3 good subs. Reference HD 163955, 3 good subs."


def test_unknown_and_missing_keys_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"version": 1, "folder": str(tmp_path), "comment": "hand edited", "extra": [1, 2],
                             "target": {"name": "T", "good": ["a.fits"], "colour": "blue"},
                             "reference": {"excluded": ["b.fits"]}}), encoding="utf-8")
    d = acq_sets.load_sets(p)
    assert d["target"] == {"name": "T", "name_source": "typed", "good": ["a.fits"], "excluded": [],
                           "exposure_s": None, "gain": None, "stack_file": "", "rows": None, "ra": "", "dec": "",
                           "sp_type": "", "notes": "", "lobe_mode": "auto"}
    assert d["reference"]["excluded"] == [{"file": "b.fits", "reason": ""}] and d["night"] == "" and d["qc"] == {}


def test_missing_and_changed_files_flagged(tmp_path: Path, night: dict) -> None:
    recs = [_rec(p, "target") for p in night["SgrNov32026"]]
    doc = acq_sets.load_sets(acq_sets.save_sets(tmp_path / "s.json", acq_sets.make_sets(recs, {}, {})))
    night["SgrNov32026"][0].unlink()
    with open(night["SgrNov32026"][1], "ab") as fh:
        fh.write(b"more")
    res = acq_sets.check_files(doc)
    assert res["missing"] == ["SgrNov32026_00001.fits"]  # one folder: paths are relative to it
    assert res["changed"] == ["SgrNov32026_00002.fits"]


def test_user_override_survives_recalculation_and_roles_ignore_object(tmp_path: Path, night: dict) -> None:
    recs = [_rec(p, "target", snr=60 + i) for i, p in enumerate(night["SgrNov32026"])]
    recs[1].included, recs[1].user_override = False, True
    recs.append(_rec(night["hd163955"][0], "target", obj="hd163955"))  # OBJECT says another star
    refresh_flags(recs)
    doc = acq_sets.make_sets(recs, {}, {})
    assert "SgrNov32026/SgrNov32026_00002.fits" in [e["file"] for e in doc["target"]["excluded"]]
    assert "hd163955/hd163955_00001.fits" in doc["target"]["good"]  # the role came from the user, not OBJECT
    assert doc["reference"]["good"] == []


def test_acquisition_tab_autosaves_the_sets(qtbot: QtBot, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
    nova = sorted((root / "SgrNov32026").glob("*.fits"))[:2]
    if not nova:
        pytest.skip("example_spectra not present")
    from PySide6.QtWidgets import QApplication

    from starextractor.core.session import Session
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    a = w.ensure_acquisition()
    copies = []
    for p in nova:
        copies.append(Path(shutil.copy(p, tmp_path / p.name)))
        os.utime(copies[-1], (p.stat().st_mtime, p.stat().st_mtime))
    a.names["target"].setText("V8055 Sgr")
    a.enqueue(copies)
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)
    sets = acq_sets.load_sets(a.night_folder() / acq_sets.FILE_NAME)
    assert sets["target"]["good"] == [c.name for c in copies] and sets["target"]["name"] == "V8055 Sgr"
    a.exclude(str(copies[1]))
    sets = acq_sets.load_sets(a.night_folder() / acq_sets.FILE_NAME)
    assert sets["target"]["excluded"] == [{"file": copies[1].name, "reason": "excluded by hand"}]
