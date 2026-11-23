"""Line centres (core/centroid.py) on synthetic lines with known centres, fixed seed. The default method is M0 v0.2 (the
study in docs/investigations/wavelength_2025-10-02_omiand.md kept M0 and corrected it); the profile fits are the
diagnostics (emission core, blend). Tolerances are stated in each test."""
from __future__ import annotations

from pathlib import Path

import centre_study as cs  # tools/ (on the path through conftest): the benchmark's own line generator
import numpy as np
import pytest

from starextractor.core import balmer_search as bs
from starextractor.core import centroid as cz
from starextractor.core import wavelength as w

SEED = 20261123


def _run(case: cs.Case, n: int, f) -> tuple[np.ndarray, np.ndarray, list[list[str]]]:
    rng = np.random.default_rng(SEED)
    err, sig, flags = [], [], []
    for _ in range(n):
        y, c, g = cs.make(case, rng)
        r = f(y, g)
        err.append(r.centre - c if r.ok else np.nan)
        sig.append(r.sigma)
        flags.append(r.flags)
    return np.array(err), np.array(sig), flags


def _m0v2(y, g):
    return cz.m0_line(y, g, 12.0)


def _m0v1(y, g):
    return cz.m0_v01(w.normalised(y), g, 12)


@pytest.mark.parametrize("fwhm", [10.0, 12.0, 14.0])
def test_default_method_bias_and_scatter(fwhm: float) -> None:
    # S/N 50, a continuum tilted by 20 %: |bias| < 0.15 px, scatter < 0.4 px, no failures
    e, _, _ = _run(cs.Case(fwhm, 0.4, "voigt", 0.2, 0.0, 50.0, False), 80, _m0v2)
    assert np.all(np.isfinite(e))
    assert abs(np.mean(e)) < 0.15 and np.std(e) < 0.4


def test_default_is_no_worse_than_v01_and_beats_it_on_hard_lines() -> None:
    # same lines: the scatter does not grow; on shallow, noisy lines v0.1 fails where v0.2 measures
    case = cs.Case(12.0, 0.4, "voigt", 0.2, 0.0, 50.0, False)
    e2, _, _ = _run(case, 80, _m0v2)
    e1, _, _ = _run(case, 80, _m0v1)
    assert np.std(e2) <= np.std(e1) * 1.05
    hard = cs.Case(12.0, 0.1, "lorentz", 0.4, 0.15, 15.0, True)
    f2 = np.mean(np.isnan(_run(hard, 60, _m0v2)[0]))
    f1 = np.mean(np.isnan(_run(hard, 60, _m0v1)[0]))
    assert f2 <= f1


@pytest.mark.parametrize("tilt", [-0.4, 0.4])
def test_steep_continuum(tilt: float) -> None:
    # ±40 % across 4 FWHM (the instrument response): |bias| < 0.15 px
    e, _, _ = _run(cs.Case(12.0, 0.4, "voigt", tilt, 0.15, 50.0, False), 80, _m0v2)
    assert abs(np.nanmean(e)) < 0.15 and np.isfinite(e).all()


def test_hot_pixel_does_not_move_the_centre() -> None:
    # the same profile with and without a spike (8× the continuum) within 4 px of the centre: < 0.3 px apart
    rng = np.random.default_rng(SEED)
    case = cs.Case(12.0, 0.4, "voigt", 0.2, 0.0, 50.0, False)
    for _ in range(60):
        y, c, g = cs.make(case, rng)
        hot = y.copy()
        hot[round(c) + int(rng.integers(-4, 5))] += 8000.0
        a, b = _m0v2(y, g), _m0v2(hot, g)
        assert a.ok and b.ok  # v0.1 failed every one of these
        assert abs(a.centre - b.centre) < 0.3


def test_emission_core_is_flagged_and_blend_is_flagged() -> None:
    case = cs.Case(12.0, 0.4, "voigt", 0.2, 0.0, 200.0, False, "emission")
    _, _, fl = _run(case, 40, lambda y, g: cz.fit_line_centre(y, g, 12.0))
    assert np.mean([cz.EMISSION_CORE in f for f in fl]) > 0.9
    _, _, fl = _run(cs.Case(12.0, 0.4, "voigt", 0.2, 0.0, 200.0, False, "blend"), 40,
                    lambda y, g: cz.fit_line_centre(y, g, 12.0))
    assert np.mean([cz.BLEND in f for f in fl]) > 0.9
    _, _, fl = _run(cs.Case(12.0, 0.4, "voigt", 0.2, 0.0, 200.0, False), 40,
                    lambda y, g: cz.fit_line_centre(y, g, 12.0))
    assert np.mean([bool(f) for f in fl]) < 0.1  # a clean line is (almost) never flagged


def test_reported_uncertainty_matches_the_scatter() -> None:
    # FWHM 10–14, S/N 50, tilted: the reported σ within a factor 1.5 of the true scatter
    errs, sigs = [], []
    for fwhm in (10.0, 12.0, 14.0):
        e, s, _ = _run(cs.Case(fwhm, 0.4, "voigt", 0.2, 0.0, 50.0, False), 60, _m0v2)
        errs += list(e)
        sigs += list(s)
    ratio = float(np.std(errs) / np.median(sigs))
    assert 1 / 1.5 < ratio < 1.5


def test_marker_drop_point_does_not_matter_on_real_lines() -> None:
    # v0.1 kept a marker dropped off the line (±6 px window); v0.2 converges to the same centre within 0.15 px
    prof = np.load(Path(__file__).parent / "fixtures" / "wavelength_profiles.npz")["omiand_ref"].astype(np.float64)
    s = bs.search(prof)
    width = {d.x: d.width for d in s.dips}
    for name in ("Hα", "Hβ", "Hγ", "Hδ", "Hε"):
        g = s.best.matched[name]
        c0 = cz.m0_line(prof, g, width[g]).centre
        for d in (-6.0, -3.0, 3.0, 6.0):
            r = cz.m0_line(prof, g + d, width[g])
            assert r.ok and abs(r.centre - c0) < 0.15, (name, d)
            assert abs(r.centre - (g + d)) < r.fwhm  # never more than one FWHM from the marker
