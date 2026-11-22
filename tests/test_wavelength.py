"""Step 4 core: the BASS tests of docs/BASS_CALIBRATION.md §6 (1–4; the .xls export, test 5, was removed
at Matt's request) and calibration behaviour."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from starextractor.core import wavelength as w
from starextractor.core.instrument import SEED_DISPERSION

FIX = Path(__file__).parent / "fixtures"
BOX = (FIX / "bass_coefficients_GamCas_00004.txt").read_text(encoding="utf-8").strip()
POINTS = [w.Point("Hγ", 4340.47, 711.79), w.Point("Hβ", 4861.33, 1144.32), w.Point("Hα", 6562.852, 2555.98)]
LIN = (348.2492888, 0.120513715)


# ---- the five required BASS tests -----------------------------------------------------------------
def test_1_stored_coefficients_hit_the_lines() -> None:
    sol = w.Solution(w.parse_coeffs(BOX))
    errs = [float(sol.nm(p.pixel)) - p.lam_a / 10 for p in POINTS]
    assert all(abs(e) < 3e-5 for e in errs)


def test_2_dispersion_column() -> None:
    sol = w.Solution(w.parse_coeffs(BOX))
    assert float(sol.nm(5)) - float(sol.nm(4)) == pytest.approx(0.120309817, abs=1e-9)


def test_3_refits_reproduce_bass_terms() -> None:
    px = np.array([p.pixel for p in POINTS])
    lam = np.array([p.lam_a for p in POINTS])
    quad = w.fit(px, lam, 2).solution.coeffs_nm
    for got, want in zip(quad, w.parse_coeffs(BOX), strict=True):
        assert got == pytest.approx(want, rel=1e-4)
    lin = w.fit(px, lam, 1).solution.coeffs_nm
    assert lin[0] == pytest.approx(LIN[0], rel=1e-4) and lin[1] == pytest.approx(LIN[1], rel=1e-4)


def test_4_copy_paste_round_trip() -> None:
    coeffs = w.parse_coeffs(BOX)
    assert w.format_coeffs(coeffs) == BOX  # exactly BASS's layout
    assert w.parse_coeffs(w.format_coeffs(coeffs)) == coeffs  # identical floats
    assert w.parse_coeffs("348.38;0.1203 6.0e-8") == [348.38, 0.1203, 6.0e-8]
    assert len(w.parse_coeffs("348.2492888, 0.120513715")) == 2


# ---- solution behaviour -----------------------------------------------------------------------
def test_order_guard() -> None:
    assert w.max_order(2) == 1 and w.max_order(3) == 1 and w.max_order(4) == 2 and w.max_order(5) == 2
    assert w.max_order(3, allow_exact=True) == 2 and w.max_order(1) == 0
    with pytest.raises(ValueError):
        w.fit(np.array([1.0, 2.0]), np.array([4000.0, 4001.0]), 2)


def test_paste_rejects_nonsense() -> None:
    for bad in ("1, 2, 3, 4", "3900, 1.85", "abc, 0.1"):
        with pytest.raises(ValueError):
            w.parse_coeffs(bad)


def test_shift_and_inverse() -> None:
    sol = w.Solution(list(SEED_DISPERSION["coeffs_nm"]))
    assert sol.pixel(6562.852) == pytest.approx(2555.98, abs=0.01)
    moved = sol.shifted(17.5)
    assert moved.pixel(6562.852) == pytest.approx(sol.pixel(6562.852) + 17.5, abs=1e-6)
    assert float(moved.nm(1000 + 17.5)) == pytest.approx(float(sol.nm(1000)), abs=1e-12)


def _synthetic_profile(shift: float = 12.3) -> np.ndarray:
    sol = w.Solution(list(SEED_DISPERSION["coeffs_nm"])).shifted(shift)
    x = np.arange(3840.0)
    prof = 20000 * np.exp(-0.5 * ((x - 1700) / 900) ** 2) + 500
    for lam in w.BALMER.values():
        prof *= 1 - 0.5 * np.exp(-0.5 * ((x - sol.pixel(lam)) / 3.0) ** 2)
    return prof + np.random.default_rng(1).normal(0, 20, x.size)


def test_refine_finds_the_shift() -> None:
    cal = w.calibrate(_synthetic_profile(12.3), w.Solution(list(SEED_DISPERSION["coeffs_nm"])), "refine")
    assert cal.shift_px == pytest.approx(12.3, abs=0.1)
    assert len(cal.points) >= 4 and cal.fit is not None and cal.fit.rms_a < 0.2
    assert cal.fwhm_a == pytest.approx(2.3548 * 3.0 * 1.2035, rel=0.1)


def test_free_fit_and_order_cap() -> None:
    cal = w.calibrate(_synthetic_profile(-30), w.Solution(list(SEED_DISPERSION["coeffs_nm"])), "free",
                      lines=["Hα", "Hβ", "Hγ"], order=2)
    assert cal.solution.order == 1 and any("needs more lines" in m for m in cal.messages)
    cal2 = w.calibrate(_synthetic_profile(-30), w.Solution(list(SEED_DISPERSION["coeffs_nm"])), "free",
                       lines=["Hα", "Hβ", "Hγ"], order=2, allow_exact=True)
    assert cal2.solution.order == 2 and cal2.fit.rms_a < 1e-6  # exact fit through 3 lines


def test_resample_conserves_flux_and_grid() -> None:
    sol = w.Solution(list(SEED_DISPERSION["coeffs_nm"]))
    prof = np.full(3840, 100.0)
    crval, cdelt, n = w.linear_grid(sol, 4000.0, 8000.0)
    out = w.resample(prof, sol, crval, cdelt, n)
    assert crval == 4000.0 and n == int((8000 - 4000) / cdelt) + 1
    inside = out[np.isfinite(out)]
    # counts per output bin = 100 × (output bin width / input pixel width)
    lam = crval + cdelt * np.arange(n)
    expect = 100 * cdelt / np.array([sol.dispersion_a(sol.pixel(v)) for v in lam])
    np.testing.assert_allclose(inside, expect[np.isfinite(out)], rtol=1e-3)
    total_in = prof[int(sol.pixel(4000)) + 1:int(sol.pixel(8000))].sum()
    assert np.nansum(out) == pytest.approx(total_in, rel=2e-3)
