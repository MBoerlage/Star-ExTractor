"""Step 5 core: library handling, airmass, response building; real-data acceptance vs BASS."""
from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from starextractor.core import airmass as am
from starextractor.core import response as r

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"


# ---- library ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("stem,label", [("a0v", "A0V"), ("a47iv", "A4-7IV"), ("b12iii", "B1-2III"),
                                        ("m2.5v", "M2.5V"), ("m10iii", "M10III"), ("f02iv", "F0-2IV"),
                                        ("k01ii", "K0-1II"), ("o5v", "O5V")])
def test_template_names(stem: str, label: str) -> None:
    t = r.parse_template_name(stem)
    assert t is not None and t.label == label


def test_library_present_and_parsable() -> None:
    temps = r.list_templates()
    assert len(temps) == 108  # Matt's HILIB copy without the metal-weak/rich variants
    lam, flux = r.load_template("a0v")
    assert lam[0] == 3000 and lam[-1] == 10000 and len(lam) == 1401
    assert (flux > 0).all() and abs(np.interp(5556, lam, flux) - 1.0) < 0.02  # normalised at 5556 Å


@pytest.mark.parametrize("sp,stem,noted", [("A0", "a0v", True), ("A0Va", "a0v", False), ("B9V", "b9v", False),
                                           ("A3V", "a3v", False), ("K1III", "k1iii", False),
                                           ("B8.5Ib-II", "b8i", True), ("F7V", "f6v", True), ("", "a0v", True),
                                           ("A5IV", "a47iv", False)])
def test_pick_template(sp: str, stem: str, noted: bool) -> None:
    t, note = r.pick_template(sp)
    assert t.stem == stem and bool(note) == noted


def test_masks_and_bands() -> None:
    bands = {n: (a, b) for n, a, b in r.telluric_bands()}
    assert bands["O2 A band"][0] <= 7594 and bands["O2 B band"][0] <= 6867
    assert bands["H2O near Ha"][0] < 6532.4 and bands["H2O near Ha"][1] > 6586.6
    regions = r.mask_regions(True, True, 3900, 7700)
    names = [n for n, _, _ in regions]
    assert "Hα" in names and "O2 A band" in names and not any("8230" in n for n in names)
    assert r.mask_regions(False, False, 3900, 7700) == []


def test_extinction_table() -> None:
    lam, k = r.extinction()
    assert lam[0] == 3200 and len(lam) == 82 and lam[-1] == 10400
    assert np.interp(4000, lam, k) > np.interp(7000, lam, k)  # blue is extinguished more
    f = r.extinction_factor(np.array([4000.0, 7000.0]), 1.5, 1.2)
    assert f[0] > f[1] > 1.0


# ---- airmass ----------------------------------------------------------------------------------------
def test_airmass_formula() -> None:
    assert am.airmass(90) == pytest.approx(1.0, abs=1e-3)
    assert am.airmass(30) == pytest.approx(1.995, abs=0.01)  # Kasten & Young
    assert am.airmass(-1) == math.inf


def test_sidereal_time_and_meridian() -> None:
    assert am.lst_deg(2451545.0, 0.0) == pytest.approx(280.46061837, abs=1e-6)  # GMST at J2000.0
    t = datetime(2026, 8, 21, 4, 0, tzinfo=UTC)
    lst = am.lst_deg(am.julian_date(t), -111.60)
    # an object on the meridian at the site latitude is at the zenith
    assert am.airmass_at(lst, 31.96, 31.96, -111.60, t) == pytest.approx(1.0, abs=1e-3)


# ---- building -----------------------------------------------------------------------------------------
def _synthetic(true_resp: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lam = 3900 + 1.2 * np.arange(3300)
    ll, lf = r.load_template("a0v")
    lib = np.interp(lam, ll, lf)
    resp = np.exp(-0.5 * ((lam - 6200) / 1500) ** 2)
    return lam, lib * resp * 5000, resp


def test_response_recovers_a_known_curve() -> None:
    lam, ref, truth = _synthetic()
    t = next(x for x in r.list_templates() if x.stem == "a0v")
    res = r.build_response(lam, ref, t, 6.0)
    q = res.smooth / (truth / truth.max())
    inner = (lam > 4000) & (lam < 7600)
    assert np.all(np.abs(q[inner] / np.median(q[inner]) - 1) < 0.05)
    lo, flux, _div = r.normalise_crop(lam, ref / res.smooth, (5500, 5600), (3900, 7700))
    assert lo[0] >= 3900 and lo[-1] <= 7700 and abs(np.mean(flux[(lo >= 5500) & (lo <= 5600)]) - 1) < 1e-6
    with pytest.raises(ValueError):
        r.normalise_crop(lam, ref, (9000, 9100), (3900, 7700))


def _telluric_synthetic() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from telluric_model import transmission

    lam = 3900 + 1.2 * np.arange(3500)  # to 8100 Å
    ll, lf = r.load_template("a0v")
    truth = np.exp(-0.5 * ((lam - 6200) / 1500) ** 2)
    ref = np.interp(lam, ll, lf) * truth * transmission(lam) * 5000
    ref *= 1 + 0.007 * np.random.default_rng(1).standard_normal(len(lam))
    return lam, ref, truth / truth.max()


@pytest.mark.parametrize("level", ["medium", "very high"])
def test_upper_envelope_ignores_absorption_and_bridges_gaps(level: str) -> None:
    lam, ref, truth = _telluric_synthetic()
    t = next(x for x in r.list_templates() if x.stem == "a0v")
    res = r.build_response(lam, ref, t, 6.0, smoothing=level, envelope=True)
    inner = (lam > 4000) & (lam < 8000)
    assert np.max(np.abs(res.smooth[inner] / truth[inner] - 1)) < 0.02  # gaps included
    # envelope: the band wings (only the band cores are masked) stay below the curve
    core = r.masked(lam, r.mask_regions(True, True, float(lam[0]), float(lam[-1])))
    sel = (lam > 6700) & (lam < 7900) & ~core
    assert np.mean(res.raw[sel] <= res.smooth[sel]) >= 0.85
    # no kinks: bounded second difference everywhere (gap edges included)
    assert np.max(np.abs(np.diff(res.smooth, 2))) < 2e-5
    # the median fit, for comparison, sits low inside the telluric wings
    old = r.build_response(lam, ref, t, 6.0, smoothing=level, envelope=False)
    assert np.mean(res.raw[sel] <= old.smooth[sel]) < np.mean(res.raw[sel] <= res.smooth[sel])


def test_envelope_off_is_the_original_fit() -> None:
    lam, ref, _ = _telluric_synthetic()
    t = next(x for x in r.list_templates() if x.stem == "a0v")
    res = r.build_response(lam, ref, t, 6.0, envelope=False)
    regions = r.mask_regions(True, True, float(lam[0]), float(lam[-1]))
    lib = np.interp(lam, *r.load_template("a0v"), left=np.nan, right=np.nan)
    obs_s = r.smooth_to_library(ref, float(lam[1] - lam[0]), 6.0)
    keep = ~r.masked(lam, regions) & np.isfinite(ref) & np.isfinite(lib)
    sm = r.smooth_curve(lam, obs_s / lib, keep, "medium")
    assert np.array_equal(res.smooth, sm / np.nanmax(sm)) and res.regions == regions and res.ignored is None


def test_envelope_widens_telluric_masks_only() -> None:
    narrow = {n: (a, b) for n, a, b in r.mask_regions(True, True, 3900, 8000)}
    wide = {n: (a, b) for n, a, b in r.wide_regions(True, True, 3900, 8000)}
    assert wide["O2 A band"][0] < narrow["O2 A band"][0] - 10 and wide["Hα"] == narrow["Hα"]


_LIB: dict[str, object] = {}


def _calibrated_profile(folder: str) -> np.ndarray:
    """The app's own chain on the real frames: masters → Match-pedestal stack → geometry → extraction."""
    import tempfile

    from starextractor.core import geometry as g
    from starextractor.core.extract import auto_place, extract
    from starextractor.core.frames import BIAS, DARK, scan
    from starextractor.core.jobs import Ctx
    from starextractor.core.masters import (
        LightSettings,
        MasterLibrary,
        build_master,
        calibration_frame,
        group_for_masters,
        pick_calibration,
    )
    from starextractor.core.stack import StackParams, stack

    if "lib" not in _LIB:
        lib = MasterLibrary(Path(tempfile.mkdtemp()) / "masters")
        for kind, sub in ((BIAS, "bias"), (DARK, "darks")):
            build_master(group_for_masters(scan(EXAMPLE / sub))[0], kind, lib, Ctx())
        _LIB["lib"] = lib
    lib = _LIB["lib"]
    lights = scan(EXAMPLE / folder)
    plan = pick_calibration(LightSettings.from_frames(lights), lib)  # type: ignore[arg-type]
    data, _ = stack(lights, calibration_frame(plan, lib), StackParams(), Ctx())  # type: ignore[arg-type]
    straight = g.correct(data, g.GeometryParams(), Ctx()).image
    return extract(straight, auto_place(straight).bands).profile.astype(np.float64)


@pytest.mark.realdata
@pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra not present")
def test_real_response_matches_bass_and_flattens_the_reference() -> None:
    from starextractor.core import wavelength as w
    from starextractor.core.instrument import SEED_DISPERSION

    ref_px, nova_px = _calibrated_profile("hd163955"), _calibrated_profile("SgrNov32026")
    cal = w.calibrate(ref_px, w.Solution(list(SEED_DISPERSION["coeffs_nm"])), "free")
    crval, cdelt, n = w.linear_grid(cal.solution, 3900.0, 8000.0)
    lam = crval + cdelt * np.arange(n)
    ref = w.resample(ref_px, cal.solution, crval, cdelt, n).astype(np.float64)
    nova = w.resample(nova_px, cal.solution, crval, cdelt, n).astype(np.float64)
    template, note = r.pick_template("A0")  # SIMBAD: HD 163955 = 4 Sgr, "A0"
    assert template.stem == "a0v" and "assumed V" in note
    for envelope in (False, True):
        _check_real_response(lam, ref, nova, r.build_response(lam, ref, template, cal.fwhm_a, envelope=envelope))


def _check_real_response(lam: np.ndarray, ref: np.ndarray, nova: np.ndarray, res: r.ResponseResult) -> None:
    from starextractor.core import wavelength as w

    bass =np.loadtxt(EXAMPLE / "InstrumentResponse_from_HD163955.dat")
    # peak near 6200 Å: the top is flat (0.999 at 6200, 0.998 at 6400), so check BASS's peak position
    # is on our plateau rather than comparing argmax positions
    bass_peak = bass[np.argmax(bass[:, 1]), 0]
    assert np.interp(bass_peak, lam, res.smooth) > 0.98 * np.nanmax(res.smooth)
    sel = (bass[:, 0] >= 4500) & (bass[:, 0] <= 7500)
    ratio = np.interp(bass[sel, 0], lam, res.smooth) / bass[sel, 1]
    ratio /= np.median(ratio)
    assert np.all(np.abs(ratio - 1) < 0.11)  # same shape within ~10 %

    corr = ref / res.smooth
    keep = (lam > 4000) & (lam < 7000) & ~r.masked(lam, res.regions) & np.isfinite(corr)
    flat = corr[keep] / res.library[keep]
    flat /= np.median(flat)
    dev = np.abs(flat - 1)
    # flat to within a few %: median ~0.7 %, 90 % of points within ~2 %. The last few % are real absorption
    # just outside the masks (H2O/O2 B tail at 6940–6975 Å, far Hα wing near 6500 Å), kept by design.
    assert np.median(dev) < 0.015 and np.percentile(dev, 90) < 0.03

    nova_c = nova / res.smooth  # Balmer emission at the right wavelengths
    for name in ("Hα", "Hβ"):
        c = w.BALMER[name]
        win = (lam > c - 40) & (lam < c + 40)
        assert abs(lam[win][np.argmax(nova_c[win])] - c) < 6
