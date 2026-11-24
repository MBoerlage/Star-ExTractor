"""The bundled reference-star list (starex_refcat, schema 2): columns, values, grades, V classes, the old six-column
file, the Response step's reading of every spectral type, timings and no network."""
from __future__ import annotations

import csv
import json
import socket
import time
from collections import Counter
from pathlib import Path

import pytest

from starextractor.core import reference_stars as rs
from starextractor.core.response import parse_sptype, pick_template

FIRST_SIX = ["name", "ra_deg", "dec_deg", "v_mag", "b_v", "sp_type"]
NUMERIC = ["ra_deg", "dec_deg", "v_mag", "b_v", "ebv", "n_type_measurements", "n_type_consistent", "vsini",
           "distance_pc"]


def _rows() -> list[dict[str, str]]:
    with open(rs._CATALOG_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_bundled_csv_columns_values_grades() -> None:
    with open(rs._CATALOG_PATH, encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header[:6] == FIRST_SIX
    assert header == json.loads(rs.META_PATH.read_text(encoding="utf-8"))["columns"]
    rows = _rows()
    assert len(rows) == 1595
    for r in rows:
        for c in NUMERIC:
            if c in FIRST_SIX or r[c] != "":
                float(r[c])  # parses
        assert not rs.is_emission(r["sp_type"]), r["name"]
        assert r["grade"] in rs.GRADES and r["v_class"] in ("normal", "faint")
        assert float(r["v_mag"]) <= 6.5
        assert r["v_class"] == ("faint" if float(r["v_mag"]) > 6.0 else "normal"), r["name"]
        assert r["verified"] in ("", "verified")
    stars = rs._load_catalog()
    assert len(stars) == 1595 and all(s.grade in rs.GRADES for s in stars)
    meta = rs.meta()
    assert dict(Counter(s.grade for s in stars)) == meta["counts"]["grades"]
    assert meta["generator_version"] == "0.1.0" and meta["preset"] == "balanced" and meta["v_limit"] == 6.5


def test_old_six_column_csv_still_loads(tmp_path: Path) -> None:
    old = tmp_path / "old.csv"
    old.write_text("name,ra_deg,dec_deg,v_mag,b_v,sp_type\nHD 1,10.0,20.0,5.0,0.01,A1V\nHD 2,11.0,20.0,5.5,-0.02,B2Ve\n"
                   "HD 3,40.0,20.0,4.0,0.0,B9III\n", encoding="utf-8")
    stars = rs._load_catalog(old)
    assert [s.name for s in stars] == ["HD 1", "HD 3"]  # the emission star is dropped
    assert stars[0].grade == "" and stars[0].ebv is None and stars[0].reasons == () and not stars[0].verified
    found = rs.find_reference_stars(10.0, 20.0, 5, path=old)  # no grades: never hidden by the grade filter
    assert [m.star.name for m in found] == ["HD 1", "HD 3"] and not any(m.best for m in found)


def test_every_spectral_type_reaches_a_template() -> None:
    """parse_sptype reads every type in the list; III and IV stars get a III or IV template (never a V one
    silently); the 15 without a class get V with a visible note."""
    failed, no_class = [], 0
    for s in rs._load_catalog():
        p = parse_sptype(s.sp_type)
        if p is None:
            failed.append(s.sp_type)
            continue
        t, note = pick_template(s.sp_type)
        if p[2] is None:
            no_class += 1
            assert t.lum == "V" and "no luminosity class, assumed V" in note
        else:
            assert t.lum == p[2], (s.sp_type, t.label)
    assert failed == []
    assert no_class == 15
    assert parse_sptype("A1IV-V")[2] == "IV" and parse_sptype("B3III/IV")[2] == "III"
    assert parse_sptype("A3.5IV:") == ("A", 3.5, "IV")


def test_load_and_lookup_are_fast_and_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_net(*_a, **_k):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    rs._load_catalog.cache_clear()
    rs._arrays.cache_clear()
    rs._load_catalog()  # warm numpy and the file cache once, then time a cold parse
    rs._load_catalog.cache_clear()
    rs._arrays.cache_clear()
    t0 = time.perf_counter()
    rs._load_catalog()
    rs._arrays()
    load_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    rs.find_reference_stars(282.5, 33.4, 10, 6.5, ("good", "ok", "poor"))
    lookup_ms = (time.perf_counter() - t0) * 1000
    assert load_ms < 100, load_ms
    assert lookup_ms < 20, lookup_ms


def test_build_bundles_both_files_and_selftest_loads_them() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "starex_extractor.spec").read_text(encoding="utf-8")
    assert '(str(PKG / "data"), "starextractor/data")' in spec  # the whole data folder goes into the build
    assert rs._CATALOG_PATH.parent == root / "starextractor" / "data" and rs.META_PATH.exists()
    from starextractor.selftest import bundled_data

    assert "1595 reference stars (starex_refcat 0.1.0)" in bundled_data()
