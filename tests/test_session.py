from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from starextractor.core.cache import ArrayLRU, DiskCache, file_fingerprint, stage_key
from starextractor.core.pipeline import STAGE_IDS, input_name, output_name
from starextractor.core.session import Session, Status
from starextractor.core.settings import Settings


def _all_done(s: Session) -> None:
    for sid in STAGE_IDS:
        s.mark_done(sid, {}, f"{sid} ok")


def test_round_trip(tmp_path: Path) -> None:
    s = Session.create(tmp_path / "2026-08-21")
    s.inputs["target"]["name"] = "V8055 Sgr"
    s.params["geometry"] = {"tilt": 0.08}
    _all_done(s)
    s.log("hello")
    s.save()
    r = Session.load(tmp_path / "2026-08-21")
    assert r.inputs["target"]["name"] == "V8055 Sgr"
    assert r.params["geometry"] == {"tilt": 0.08}
    assert all(r.status[sid].status == Status.VALID for sid in STAGE_IDS)
    assert r.status["extract"].summary == "extract ok"
    assert r.log_lines[-1].endswith("hello")


def test_param_change_invalidates_later_never_earlier() -> None:
    s = Session()
    _all_done(s)
    changed = s.set_params("extract", {"top": 1060})
    assert changed == ["extract", "wavelength", "response"]
    assert s.status["files"].status == Status.VALID
    assert s.status["geometry"].status == Status.VALID
    assert s.set_params("extract", {"top": 1060}) == []  # same value: nothing changes


def test_rerun_with_same_key_keeps_later_valid() -> None:
    s = Session()
    _all_done(s)
    s.mark_done("geometry", {}, "again")
    assert all(s.status[sid].status == Status.VALID for sid in STAGE_IDS)


def test_input_change_detected_on_load(tmp_path: Path) -> None:
    light = tmp_path / "a.fits"
    light.write_bytes(b"x" * 10)
    s = Session.create(tmp_path / "sess")
    s.inputs["target"]["lights"] = [{"path": str(light), "used": True}]
    _all_done(s)
    s.save()
    time.sleep(0.01)
    light.write_bytes(b"y" * 20)
    r = Session.load(tmp_path / "sess")
    assert all(r.status[sid].status == Status.STALE for sid in STAGE_IDS)


def test_missing_output_file_makes_stage_stale(tmp_path: Path) -> None:
    s = Session.create(tmp_path / "sess")
    _all_done(s)
    s.status["geometry"].outputs = {"Target": "work/Target_G.fit"}
    s.save()
    r = Session.load(tmp_path / "sess")
    assert r.status["files"].status == Status.VALID
    assert r.status["geometry"].status == Status.STALE


def test_load_is_fast_and_reads_no_pixels(tmp_path: Path) -> None:
    s = Session.create(tmp_path / "sess")
    s.inputs["target"]["lights"] = [{"path": str(tmp_path / f"f{i}.fits")} for i in range(30)]
    _all_done(s)
    s.save()
    t = time.perf_counter()
    Session.load(tmp_path / "sess")
    assert time.perf_counter() - t < 0.5


def test_file_names() -> None:
    assert output_name("files", "Target") == "work/Target_S.fit" and output_name("extract", "Target") == "Target_X.fit"
    assert input_name("geometry", "Reference") == "work/Reference_S.fit"
    assert input_name("files", "Target") is None


def test_stage_key_depends_on_params_and_upstream() -> None:
    k = stage_key("geometry", {"a": 1}, ["up"])
    assert k == stage_key("geometry", {"a": 1}, ["up"])
    assert k != stage_key("geometry", {"a": 2}, ["up"])
    assert k != stage_key("geometry", {"a": 1}, ["up2"])


def test_fingerprint_changes_with_file(tmp_path: Path) -> None:
    f = tmp_path / "x.fits"
    f.write_bytes(b"1")
    a = file_fingerprint(f)
    f.write_bytes(b"22")
    assert a != file_fingerprint(f)
    assert file_fingerprint(tmp_path / "nope").endswith("missing")


def test_lru_evicts_by_bytes() -> None:
    lru = ArrayLRU(cap_bytes=3 * 4000)
    for i in range(4):
        lru.put(str(i), np.zeros(1000, np.float32))
    assert "0" not in lru and "3" in lru
    lru.get("1")
    lru.put("4", np.zeros(1000, np.float32))
    assert "1" in lru and "2" not in lru
    lru.put("big", np.zeros(10_000, np.float32))
    assert "big" not in lru


def test_disk_cache_round_trip(tmp_path: Path) -> None:
    dc = DiskCache(tmp_path)
    arr = np.arange(12, dtype=np.float32).reshape(3, 4)
    dc.save("k", "mask", arr)
    np.testing.assert_array_equal(dc.load("k", "mask"), arr)
    assert dc.load("k", "other") is None


def test_settings_isolated_and_recent(tmp_path: Path, _isolated_home: Path) -> None:
    st = Settings()
    assert st.path.parent == _isolated_home
    st.add_recent(tmp_path / "a")
    st.add_recent(tmp_path / "b")
    st.add_recent(tmp_path / "a")
    st.save()
    assert Settings()["recent_sessions"] == [str(tmp_path / "a"), str(tmp_path / "b")]
