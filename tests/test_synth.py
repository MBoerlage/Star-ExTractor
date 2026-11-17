"""The synthetic generator must mimic the real frames (docs/DATA.md, docs/EXAMPLE_NOVA.md)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import synth
from astropy.io import fits

P = synth.PRESETS["nova"]


@pytest.fixture(scope="module")
def frames() -> dict[str, np.ndarray]:
    return {
        "target": synth.make_light(P, "target"),
        "reference": synth.make_light(P, "reference"),
        "bias": synth.make_bias(P),
        "dark": synth.make_dark(P),
    }


def _centre_row(img: np.ndarray, x: int) -> float:
    col = np.median(img[:, x - 20:x + 20].astype(np.float32), axis=1)
    col -= np.median(col)
    y = int(np.argmax(col))
    w = np.clip(col[y - 6:y + 7], 0, None)
    return float((np.arange(y - 6, y + 7) * w).sum() / w.sum())


def test_shape_dtype(frames: dict[str, np.ndarray]) -> None:
    for img in frames.values():
        assert img.shape == (2160, 3840) and img.dtype == np.uint16


def test_pedestals_match_measured(frames: dict[str, np.ndarray]) -> None:
    assert abs(np.median(frames["target"]) - 384) < 15
    assert abs(np.median(frames["reference"]) - 352) < 15
    assert abs(np.median(frames["bias"]) - 2064) < 5
    signal = frames["dark"].astype(np.int32) - frames["bias"]
    assert abs(np.median(signal) - 32) < 5
    assert 0.008 < np.mean(signal > 200) < 0.016  # ~1.2 % hot pixels
    assert (frames["dark"] >= synth.SATURATION).any()


def test_spectrum_row_and_tilt(frames: dict[str, np.ndarray]) -> None:
    rows = [_centre_row(frames["target"], x) for x in (1200, 2800)]
    assert abs(_centre_row(frames["target"], 1920) - 1070) < 1.5
    assert abs(_centre_row(frames["reference"], 1920) - 1042) < 1.5
    slope_deg = math.degrees(math.atan2(rows[1] - rows[0], 1600))
    assert abs(slope_deg - (-0.67)) < 0.03


def test_balmer_pixels_follow_bass_solution() -> None:
    assert abs(synth.balmer_pixel(6562.852) - 2555.98) < 0.1
    assert abs(synth.balmer_pixel(4861.33) - 1144.32) < 0.1
    assert abs(synth.balmer_pixel(4340.47) - 711.79) < 0.1


def test_a0v_shows_absorption_nova_emission() -> None:
    ha = round(synth.balmer_pixel(6562.852))
    hb = round(synth.balmer_pixel(4861.33))
    ref = synth.continuum(P.reference)
    nova = synth.continuum(P.target)
    assert ref[hb] < 0.6 * ref[hb - 30]
    assert nova[ha] > 2 * nova[ha - 60]


def test_written_files_use_bzero(tmp_path: Path) -> None:
    files = synth.write_set(tmp_path, n_target=1, n_ref=1, n_cal=1, scale=0.1)
    with fits.open(files["target"][0]) as hdul:
        h = hdul[0].header
        assert h["BITPIX"] == 16 and h["BZERO"] == 32768
        assert h["IMAGETYP"] == "Light" and h["GAIN"] == 200
    with fits.open(files["bias"][0]) as hdul:
        assert hdul[0].header["IMAGETYP"] == "BIAS" and hdul[0].header["EXPOSURE"] == 0.001
