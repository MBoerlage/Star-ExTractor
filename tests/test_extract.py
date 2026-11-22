"""Step 3 core: band placement, extraction, sky subtraction; real-data correlation with BASS."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import synth
from astropy.io import fits

from starex_reduce.core import geometry as g
from starex_reduce.core.extract import Band, Bands, auto_place, centring, extract, sky_subtracted
from starex_reduce.core.fitsio import read_image
from starex_reduce.core.instrument import SEED_DISPERSION
from starex_reduce.core.jobs import Ctx

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"


@pytest.fixture(scope="module")
def straight() -> np.ndarray:
    """Synthetic nova reference star, already straightened (as after step 2)."""
    p = synth.PRESETS["nova"]
    img = synth.make_light(p, "reference").astype(np.float32)
    return g.correct(img, g.GeometryParams(), Ctx()).image


def test_auto_place(straight: np.ndarray) -> None:
    pl = auto_place(straight)
    b = pl.bands
    star = synth.PRESETS["nova"].reference
    pivot_x = (star.lit[0] + star.lit[1]) / 2  # step 2 rotates about the centre of the lit range
    assert abs(pl.row - synth.spectrum_row(star, pivot_x)) <= 1.5
    assert abs(pl.fwhm - 6.0) < 1.0  # synth FWHM in Y
    assert abs(b.spectrum.rows - 2 * round(pl.fwhm) - 1) <= 1  # width ≈ 2×FWHM
    assert b.sky_a.bottom < b.spectrum.top - 2 * pl.fwhm and b.sky_b.top > b.spectrum.bottom + 2 * pl.fwhm
    lo, hi = int(2160 * 0.15), int(2160 * 0.85)  # synth slit illumination
    assert lo <= b.sky_a.top and b.sky_b.bottom <= hi
    assert centring(b, extract(straight, b).peak_row)[0] == "ok"


def test_sky_subtraction_removes_constant_pedestal(straight: np.ndarray) -> None:
    """Acceptance (M3): the offset-mismatched sample leaves a constant pedestal; sky removes it."""
    b = auto_place(straight).bands
    base = extract(straight, b).profile
    shifted = extract(straight + np.float32(1700.0), b).profile
    np.testing.assert_allclose(shifted, base, atol=0.05 * b.spectrum.rows)
    assert not np.allclose(extract(straight + 1700, b, subtract_sky=False).profile, base, atol=100)


def test_sum_of_rows_and_linear_sky() -> None:
    h, w = 100, 50
    yy = np.arange(h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    img = 10 + 0.5 * yy  # linear sky gradient along the slit
    img[48:53] += 100  # 5-row star, 100 ADU per row
    b = Bands(Band(46, 54), Band(20, 29), Band(70, 79))
    lin = extract(img, b, sky_mode="linear")
    np.testing.assert_allclose(lin.profile, 500, rtol=1e-5)  # exact with a linear sky
    mean = extract(img, b, sky_mode="mean")
    assert abs(float(mean.profile[0]) - 500) < 9 * 0.5 * 5  # mean sky is biased by the gradient
    assert mean.profile.dtype == np.float32 and mean.profile.shape == (w,)
    np.testing.assert_allclose(sky_subtracted(img, b, "linear")[50], 100 + 0 * img[50], atol=1e-4)


def test_bands_json_and_clipping() -> None:
    b = Bands(Band(10, 20), Band(-5, 3), Band(30, 9999))
    assert Bands.from_json(b.to_json()) == b
    c = b.clipped(100)
    assert c.sky_a.top == 0 and c.sky_b.bottom == 99
    assert Band(20, 10).clipped(100) == Band(10, 20)


def test_off_centre_warning() -> None:
    state, msg = centring(Bands(Band(100, 120), Band(50, 60), Band(160, 170)), 119.0)
    assert state == "warn" and "off-centre" in msg


@pytest.mark.realdata
@pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra not present")
def test_real_nova_matches_bass_1d() -> None:
    """Acceptance (M3): r > 0.99 against SgrNov3_2026_stacked_1D.fit. That file is response-corrected
    (BASS divided by InstrumentResponse_from_HD163955.dat), so compare with BASS 1D × response."""
    files = sorted((EXAMPLE / "SgrNov32026").glob("*.fits"))
    stack = np.median(np.stack([read_image(f) for f in files]), axis=0)
    straight = g.correct(stack, g.GeometryParams(), Ctx()).image
    prof = extract(straight, auto_place(straight).bands).profile.astype(np.float64)

    hdr = fits.getheader(EXAMPLE / "SgrNov3_2026_stacked_1D.fit")
    bass = fits.getdata(EXAMPLE / "SgrNov3_2026_stacked_1D.fit").astype(np.float64)
    lam = hdr["CRVAL1"] + hdr["CDELT1"] * (np.arange(len(bass)) + 1 - hdr["CRPIX1"])
    resp = np.loadtxt(EXAMPLE / "InstrumentResponse_from_HD163955.dat")
    bass_counts = bass * np.interp(lam, resp[:, 0], resp[:, 1])

    a0, a1, a2 = SEED_DISPERSION["coeffs_nm"]
    px = (-a1 + np.sqrt(a1 * a1 - 4 * a2 * (a0 - lam / 10))) / (2 * a2)  # Gam Cas solution, other night
    best = max(np.corrcoef(np.interp(px + s, np.arange(len(prof)), prof), bass_counts)[0, 1]
               for s in np.arange(-40, 41, 0.5))
    assert best > 0.99
