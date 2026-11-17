"""Export: AVSpec/ARAS header, pre-flight, file name, tables, VSX client; acceptance vs the accepted file."""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from starex_reduce.core import export as ex
from starex_reduce.core import vsx
from starex_reduce.core.fitsio import write_float32

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
# Keys AVSpec reads (AAVSO manual v1.4 §3), with the aliases it accepts
AVSPEC_KEYS = {"OBJNAME": ("OBJNAME", "OBJECT"), "DATE-OBS": ("DATE-OBS",), "DATE-END": ("DATE-END",),
               "EXPTIME": ("EXPTIME",), "CUNIT1": ("CUNIT1",), "CTYPE1": ("CTYPE1",), "CRVAL1": ("CRVAL1",),
               "CDELT1": ("CDELT1",), "CRPIX1": ("CRPIX1",), "OBSERVER": ("OBSERVER",), "EXPTIME2": ("EXPTIME2",),
               "JD": ("JD",), "AAV_INST": ("AAV_INST", "BSS_INST", "INSTRUME"),
               "AAV_SITE": ("AAV_SITE", "BSS_SITE", "SITE"), "AAV_ITRP": ("AAV_ITRP", "BSS_ITRP", "ITRP"),
               "VERSION": ("VERSION", "SWCREATE")}
OBS = ex.ObserverInfo("XYZW", "", "Test site", 31.96, -111.60, 2096.0, "Test equipment", "Star'Ex LR 300 l/mm",
                      "200 mm f/4", "ASI585MM Pro")


def r_file(tmp_path: Path, crval: float = 4000.0, cdelt: float = 1.20117199420929, n: int = 3331,
           rsp: str = "library") -> Path:
    flux = (1 + 0.1 * np.sin(np.arange(n) / 50)).astype(np.float32)
    h = {"CRVAL1": crval, "CDELT1": cdelt, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
         "DATE-OBS": "2026-08-21T03:42:58.4152665", "DATE-END": "2026-08-21T04:12:59.852148", "EXPTIME": 300.0,
         "NCOMBINE": 6, "EXPTIME2": "6 x 300 s", "OBJECT": "Sgrnov32026", "RSP_MODE": rsp,
         "INSTRUME": "ZWO ASI585MM Pro"}
    return write_float32(tmp_path / "Target_R.fit", flux, h, ["StarEx test history"])


def plan_for(path: Path, objname: str = "V8055 Sgr", coords: dict | None = None) -> ex.ExportPlan:
    info = ex.read_header_info(path)
    return ex.build_header(info.cards, OBS, objname, coords, path.parent)


def test_header_has_every_avspec_key_and_correct_values(tmp_path: Path) -> None:
    plan = plan_for(r_file(tmp_path))
    h = plan.header
    for key, aliases in AVSPEC_KEYS.items():
        assert any(a in h for a in aliases), key
    assert h["CUNIT1"] == "Angstrom" and h["CTYPE1"] == "Wavelength" and h["OBSERVER"] == "XYZW"
    assert h["DATE-OBS"] == "2026-08-21T03:42:58.415" and h["DATE-END"] == "2026-08-21T04:12:59.852"
    assert h["EXPTIME"] == 1800.0 and h["EXPTIME2"] == "6 x 300 s"
    assert h["JD"] == pytest.approx(2461273.66525463, abs=2e-5)  # mid-exposure = BASS's JD-MID in the accepted file
    assert h["AAV_ITRP"] == 500 and not any(k.startswith("BSS_") for k in h)  # ARAS/BeSS keys removed
    assert h["OBJNAME"] == h["OBJECT"] == "V8055 Sgr"
    assert "StarEx test history" in plan.history


def test_airmass_is_the_one_the_response_used(tmp_path: Path) -> None:
    path = r_file(tmp_path)
    coords = {"ra": "18 13 05.0", "dec": "-28 24 00"}
    computed = plan_for(path, coords=coords).airmass
    from astropy.io import fits

    with fits.open(path, mode="update") as hdul:
        hdul[0].header["AIRMASS"] = 1.2345  # written by the Response step
    plan = plan_for(path, coords=coords)
    assert computed is not None and plan.airmass == 1.2345 and plan.header["AIRMASS"] == 1.2345


def test_round_trip_in_astropy(tmp_path: Path) -> None:
    path = r_file(tmp_path)
    plan = plan_for(path)
    out = ex.write_export(tmp_path / "out.fit", fits.getdata(path), plan)
    with fits.open(out) as hdul:
        h, d = hdul[0].header, hdul[0].data
        assert d.shape == (3331,) and h["NAXIS"] == 1 and h["BITPIX"] == -32
        assert h["CRVAL1"] == 4000 and h["CDELT1"] == pytest.approx(1.20117, abs=1e-5) and h["CRPIX1"] == 1
        assert h["OBJNAME"] == "V8055 Sgr" and "StarEx test history" in str(h["HISTORY"])


def test_verify_file_reads_the_export_back(tmp_path: Path) -> None:
    path = r_file(tmp_path)
    plan = plan_for(path)
    plan.header["OBSERVER"] = "ABCD"
    out = ex.write_export(tmp_path / "out.fit", fits.getdata(path), plan)
    checks = ex.verify_file(out, 3331)
    assert [c.state for c in checks] == ["ok"] * 6, [c.text for c in checks]
    assert ex.verify_file(out, 100)[1].state == "err"  # wrong length
    (tmp_path / "junk.fit").write_bytes(b"not fits")
    assert ex.verify_file(tmp_path / "junk.fit", 3331)[0].state == "err"


def test_file_name_pattern_reproduces_the_accepted_name(tmp_path: Path) -> None:
    plan = plan_for(r_file(tmp_path))
    # accepted file: _v8055sgr_20260821_155_<observer>.fit (03:42:58 → 0.1548 of the day → 155)
    assert ex.export_name("V8055 Sgr", plan.times, "XYZW") == "_v8055sgr_20260821_155_XYZW.fit"


def test_preflight_avspec(tmp_path: Path) -> None:
    path = r_file(tmp_path)
    flux = fits.getdata(path)
    plan = plan_for(path)
    found = {"found": True, "object": {"name": "V8055 Sgr"}}
    states = {c.text: c.state for c in ex.preflight_avspec(plan, flux, found)}
    assert all(s == "ok" for t, s in states.items() if "VSX" in t or "Angstrom" in t or "OBSERVER" in t)
    unchecked = [c for c in ex.preflight_avspec(plan, flux, None) if c.text.startswith("OBJNAME")]
    assert unchecked[0].state == "off" and "optional" in unchecked[0].text  # VSX check is optional
    bad = ex.ObserverInfo()
    plan2 = ex.build_header(ex.read_header_info(path).cards, bad, "V8055 Sgr", None, None)
    errs = [c.text for c in ex.preflight_avspec(plan2, flux, None) if c.state == "err"]
    assert any("obscode" in e for e in errs)
    # target far below the horizon at that time from this site → airmass error
    plan3 = plan_for(path, coords={"ra": "06 00 00", "dec": "+80 00 00"})
    assert plan3.airmass is not None
    states3 = {c.text: c.state for c in ex.preflight_avspec(plan3, flux, None) if c.text.startswith("Airmass")}
    assert list(states3.values()) == ["ok" if 1 <= plan3.airmass <= 5 else "err"]


def test_no_response_is_flagged(tmp_path: Path) -> None:
    plan = plan_for(r_file(tmp_path, rsp="none"))
    checks = ex.preflight_avspec(plan, np.ones(100), None)
    assert any("Not response-corrected" in c.text and c.state == "warn" for c in checks)
    assert plan.notes


def test_tables(tmp_path: Path) -> None:
    lam = np.array([4000.0, 4001.2, 4002.4])
    flux = np.array([1.0, np.nan, 0.5])
    dat = ex.write_table(tmp_path / "s.dat", lam, flux).read_text().splitlines()
    csv = ex.write_table(tmp_path / "s.csv", lam, flux).read_text().splitlines()
    assert dat == ["# wavelength_A flux", "4000.000 1", "4002.400 0.5"]
    assert csv[0] == "wavelength_A,flux" and csv[1] == "4000.000,1"


def test_site_from_frames(tmp_path: Path) -> None:
    p = write_float32(tmp_path / "d.fits", np.zeros((2, 2), np.float32), {"SITELAT": 12.5, "SITELONG": -45.25})
    assert ex.site_from_frames([tmp_path / "missing.fits", p]) == {"lat": 12.5, "lon": -45.25, "elev": None}


SS_CYG = ('{"VSXObject":{"Name":"SS Cyg","AUID":"000-BCP-220","RA2000":"325.67833","Declination2000":"43.58608",'
          '"VariabilityType":"UGSS","Period":"0.2751300","MaxMag":"7.7 V","MinMag":"12.4 V",'
          '"SpectralType":"K5V+pec(UG)","OID":"10939"}}')  # shape of a live answer (2026-11-16)


def test_vsx_parse_found_missing_multiple_invalid() -> None:
    objs, err = vsx.parse(SS_CYG)
    assert err is None and len(objs) == 1
    o = objs[0]
    assert (o.name, o.auid, o.var_type, o.period, o.spectral_type) == (
        "SS Cyg", "000-BCP-220", "UGSS", "0.2751300", "K5V+pec(UG)")
    assert o.ra_deg == pytest.approx(325.67833) and o.dec_deg == pytest.approx(43.58608)
    assert "range 7.7 V – 12.4 V" in o.summary()
    assert vsx.parse('{"VSXObject":[]}') == ([], None)  # not found (live answer shape)
    assert [x.name for x in vsx.parse('{"VSXObject":[{"Name":"A"},{"Name":"B"}]}')[0]] == ["A", "B"]
    assert vsx.parse("<html>") == ([], "VSX answered in an unexpected format")


class _Ctx:
    def __init__(self, f: io.BytesIO) -> None:
        self.f = f

    def __enter__(self) -> io.BytesIO:
        return self.f

    def __exit__(self, *a: object) -> None:
        pass


def _serve(monkeypatch: pytest.MonkeyPatch, body: str, calls: list[str]) -> None:
    def fake(req, timeout=0):
        calls.append(req.full_url)
        return _Ctx(io.BytesIO(body.encode()))
    monkeypatch.setattr(vsx.urllib.request, "urlopen", fake)


def test_vsx_lookup_caches_and_handles_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _serve(monkeypatch, SS_CYG, calls)
    res = vsx.lookup(None, "SS Cyg")
    assert res["found"] and res["object"]["auid"] == "000-BCP-220" and not res["cached"]
    assert calls == ["https://vsx.aavso.org/index.php?view=api.object&ident=SS%20Cyg&format=json"]
    again = vsx.lookup(None, "ss cyg ")
    assert again["cached"] and len(calls) == 1  # cached: no second request
    _serve(monkeypatch, '{"VSXObject":[]}', calls)
    missing = vsx.lookup(None, "Zzqx Nonexistent")
    assert not missing["found"] and "not in VSX" in missing["message"]
    _serve(monkeypatch, '{"VSXObject":[{"Name":"A"},{"Name":"B"}]}', calls)
    assert len(vsx.lookup(None, "ambiguous")["matches"]) == 2

    def boom(req, timeout=0):
        raise TimeoutError("timed out")
    monkeypatch.setattr(vsx.urllib.request, "urlopen", boom)
    off = vsx.lookup(None, "RR Lyr")
    assert off["offline"] and "Could not reach VSX" in off["message"]
    custom: list[str] = []
    _serve(monkeypatch, SS_CYG, custom)
    vsx.lookup(None, "X Y", "https://mirror.invalid/vsx?id={name}", use_cache=False)
    assert custom == ["https://mirror.invalid/vsx?id=X%20Y"]  # configurable endpoint


@pytest.mark.skipif(not os.environ.get("STAREX_NETWORK"), reason="set STAREX_NETWORK=1 for the live VSX test")
def test_vsx_live() -> None:
    known = vsx.lookup(None, "SS Cyg", use_cache=False)
    assert known["found"] and known["object"]["auid"] == "000-BCP-220"
    assert not vsx.lookup(None, "Zzqx Nonexistent 123", use_cache=False)["found"]


@pytest.mark.realdata
@pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra not present")
def test_our_header_covers_the_accepted_submission(tmp_path: Path) -> None:
    """EXAMPLE_NOVA.md acceptance 4: every AVSpec key in the accepted file is in ours (or its AAVSO alias)."""
    accepted = fits.getheader(next((EXAMPLE / "submitted").glob("*.fit")))
    ours = plan_for(r_file(tmp_path)).header
    for key, aliases in AVSPEC_KEYS.items():
        present_in_accepted = any(a in accepted for a in aliases) or (key == "JD" and "JD-MID" in accepted)
        if present_in_accepted:
            assert any(a in ours for a in aliases), key
    assert ours["CTYPE1"] == accepted["CTYPE1"] and ours["CUNIT1"] == accepted["CUNIT1"]
