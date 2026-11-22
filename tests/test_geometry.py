"""Step 2 core: tilt detection, rotation, hot pixels (synthetic) and real-data acceptance."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import synth

from starextractor.core import geometry as g
from starextractor.core.fitsio import read_image
from starextractor.core.instrument import SEED_DISPERSION, InstrumentProfile
from starextractor.core.jobs import Ctx

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"


@pytest.fixture(scope="module", params=["nova", "sheliak"])
def frame(request: pytest.FixtureRequest) -> tuple[np.ndarray, float]:
    p = synth.PRESETS[request.param]
    return synth.make_light(p, "target").astype(np.float32), p.target.tilt_deg


def test_detect_tilt_synthetic(frame: tuple[np.ndarray, float]) -> None:
    img, truth = frame
    r = g.detect_tilt(img)
    assert abs(r.angle_deg - truth) < 0.01
    assert r.residual_px < 0.5 and len(r.points) >= 10


def test_rotate_makes_rows_flat(frame: tuple[np.ndarray, float]) -> None:
    img, _ = frame
    assert g.flatness(img) > 2
    res = g.correct(img, g.GeometryParams(), Ctx())
    assert g.flatness(res.image) <= 1.0
    assert abs(g.detect_tilt(res.image).angle_deg) < 0.01
    assert res.image.dtype == np.float32 and res.image.shape == img.shape


def test_rotation_threads_match_single_call() -> None:
    from scipy import ndimage

    img = np.random.default_rng(0).random((300, 500), dtype=np.float32)
    a = math.radians(0.7)
    m = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    c = np.array([150.0, 250.0])
    ref = ndimage.affine_transform(img, m, offset=c - m @ c, order=1, mode="nearest", output=np.float32)
    np.testing.assert_array_equal(g.rotate(img, 0.7, (150.0, 250.0), threads=4), ref)


def test_drawn_angle_uses_real_pixels() -> None:
    assert g.drawn_angle(100, 500, 3100, 500 + 3000 * math.tan(math.radians(0.31))) == pytest.approx(0.31)
    assert g.drawn_angle(3100, 10, 100, 0) == pytest.approx(math.degrees(math.atan2(10, 3000)))
    with pytest.raises(ValueError):
        g.drawn_angle(5, 5, 5, 5)


def test_hot_pixels_removed_spectrum_kept() -> None:
    p = synth.scaled(synth.PRESETS["nova"], 0.25)
    img = synth.make_light(p, "target").astype(np.float32)
    clean = img.copy()
    g.replace_masked(clean, g.outlier_mask(img))
    row = round(g.spectrum_row(img))
    lit = slice(*synth.scaled(synth.PRESETS["nova"], 0.25).target.lit)
    # the spectrum ridge is untouched (it is smooth along X)
    ridge_before, ridge_after = img[row, lit], clean[row, lit]
    assert np.mean(np.isclose(ridge_before, ridge_after)) > 0.97
    assert (img >= 65000).sum() > 0 and (clean >= 65000).sum() == 0


def test_dark_mask_and_flip() -> None:
    dark = np.full((50, 60), 30.0, np.float32)
    dark[10, 20] = 900
    dark[30, 40] = 65520
    m = g.dark_hot_mask(dark)
    assert m[10, 20] and m[30, 40] and m.sum() == 2
    img = np.arange(12, dtype=np.float32).reshape(3, 4)
    assert g.flip(img, True, False)[0, 0] == 3 and g.flip(img, False, True)[0, 0] == 8


def test_smile_is_noop_without_radius() -> None:
    img = np.ones((5, 5), np.float32)
    assert g.apply_smile(img, None, None) is img


def test_profile_seeded_with_bass_solution(tmp_path: Path) -> None:
    prof = InstrumentProfile.load(tmp_path / "p.json")
    assert prof.dispersion["coeffs_nm"] == SEED_DISPERSION["coeffs_nm"] and not prof.has_geometry
    prof.tilt_deg, prof.tilt_date = -0.67, "2026-11-16"
    prof.save()
    again = InstrumentProfile.load(tmp_path / "p.json")
    assert again.tilt_deg == -0.67 and again.crop_a == (3900.0, 7700.0)


@pytest.mark.realdata
@pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra not present")
@pytest.mark.parametrize("folder,truth", [("SgrNov32026", -0.67), ("hd163955", -0.73)])
def test_real_raw_tilt_and_flat_after(folder: str, truth: float) -> None:
    """Raw frames are tilted ~-0.7°; BASS's stacks are pre-rotated by TILT 0.7566 (degrees)."""
    files = sorted((EXAMPLE / folder).glob("*.fits"))
    first = read_image(files[0])
    assert abs(g.detect_tilt(first).angle_deg - truth) < 0.05  # a single raw frame already shows it
    stack = np.median(np.stack([read_image(f) for f in files]), axis=0)  # step 2 works on stacks
    res = g.correct(stack, g.GeometryParams(), Ctx())
    assert abs(res.tilt_deg - truth) < 0.03
    assert g.flatness(res.image) <= 1.0
