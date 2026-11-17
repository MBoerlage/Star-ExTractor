"""M4 acceptance on the real V8055 Sgr / HD 163955 night (example_spectra, read-only)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from starex_reduce.core import geometry as g
from starex_reduce.core import wavelength as w
from starex_reduce.core.extract import auto_place, extract
from starex_reduce.core.fitsio import read_image
from starex_reduce.core.instrument import SEED_DISPERSION
from starex_reduce.core.jobs import Ctx

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
pytestmark = [pytest.mark.realdata, pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra not present")]


def _profile(folder: str) -> np.ndarray:
    files = sorted((EXAMPLE / folder).glob("*.fits"))
    stack = np.median(np.stack([read_image(f) for f in files]), axis=0)
    straight = g.correct(stack, g.GeometryParams(), Ctx()).image
    return extract(straight, auto_place(straight).bands).profile.astype(np.float64)


@pytest.fixture(scope="module")
def profiles() -> dict[str, np.ndarray]:
    return {"ref": _profile("hd163955"), "nova": _profile("SgrNov32026")}


@pytest.mark.parametrize("mode", ["refine", "free"])
def test_balmer_lines_found_and_match_bass(profiles: dict[str, np.ndarray], mode: str) -> None:
    saved = w.Solution(list(SEED_DISPERSION["coeffs_nm"]))  # Gam Cas, another night
    cal = w.calibrate(profiles["ref"], saved, mode)
    found = {p.line for p in cal.points}
    assert {"Hα", "Hβ", "Hγ", "Hδ"} <= found  # all visible Balmer lines, found automatically
    assert cal.fit is not None and cal.fit.rms_a < (1.0 if mode == "refine" else 0.3)

    hdr = fits.getheader(EXAMPLE / "SgrNov3_2026_stacked_1D.fit")
    bass = fits.getdata(EXAMPLE / "SgrNov3_2026_stacked_1D.fit").astype(np.float64)
    # BASS grid: 4000 Å start; our mean dispersion over 4000–8000 Å must be within 1 % of its CDELT1
    crval, cdelt, _ = w.linear_grid(cal.solution, 4000.0, 8000.0)
    assert crval == hdr["CRVAL1"] == 4000 and cdelt == pytest.approx(hdr["CDELT1"], rel=0.01)

    ours = np.nan_to_num(w.resample(profiles["nova"], cal.solution, hdr["CRVAL1"], hdr["CDELT1"], len(bass)))
    lam = hdr["CRVAL1"] + hdr["CDELT1"] * np.arange(len(bass))
    x = np.arange(len(bass))
    for name in ("Hα", "Hβ", "Hγ"):  # nova emission lines: within ±1 px of BASS
        i0, i1 = np.searchsorted(lam, w.BALMER[name] - 60), np.searchsorted(lam, w.BALMER[name] + 60)
        lags = np.arange(-5, 5.01, 0.05)
        cc = [np.corrcoef(np.interp(x[i0:i1] + s, x, ours), bass[i0:i1])[0, 1] for s in lags]
        assert abs(lags[int(np.argmax(cc))]) <= 1.0, name
