"""core/subs.py and core/camera.py on the nova example (copies only; example_spectra is never modified)."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from starextractor.core import camera, subs
from starextractor.core.fitsio import read_image, write_float32

ROOT = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
NOVA = sorted((ROOT / "SgrNov32026").glob("*.fits"))
REF = sorted((ROOT / "hd163955").glob("*.fits"))
needs_nova = pytest.mark.skipif(not NOVA or not REF, reason="example_spectra/novasrg2026 not present")


def _edit(src: Path, dst: Path, fn) -> Path:
    """Copy a sub, change its pixels with fn(uint16 array) and write it back as int16 + BZERO."""
    with fits.open(src) as hdul:
        data = hdul[0].data.astype(np.int64)
        header = hdul[0].header.copy()
    data = np.clip(fn(data), 0, 65535).astype(np.uint16)
    fits.PrimaryHDU(data=data, header=header).writeto(dst, overwrite=True)
    return dst


@pytest.fixture(scope="module")
def nova_rows() -> subs.Rows:
    return subs.detect_rows(NOVA[0])


# ---- doc test 2: peak, saturation, SNR flag, hot pixels -------------------------------------------------
@needs_nova
def test_peak_and_fill_on_the_nova(nova_rows: subs.Rows) -> None:
    for p in NOVA:
        r = subs.analyse_sub(p, nova_rows)
        assert 10000 < r.peak_adu < subs.limit_adu() and 0 < r.peak_fill < 1
        assert r.peak_fill == pytest.approx(r.peak_adu / 52416.0)
        assert r.continuum_snr > 20 and r.n_continuum > 1000 and r.flags == []
        assert r.g_source == "header" and r.object == "Sgrnov32026" and r.role is None  # OBJECT is info only


@needs_nova
def test_saturated_column_sets_fill_and_flag(tmp_path: Path, nova_rows: subs.Rows) -> None:
    ridge = round(nova_rows.ridge(3840)[2000])

    def burn(d: np.ndarray) -> np.ndarray:
        d[ridge - 2:ridge + 3, 2000:2004] = 65520
        return d
    r = subs.analyse_sub(_edit(NOVA[0], tmp_path / "sat.fits", burn), nova_rows)
    assert r.peak_fill >= 1.0 and r.sat_cols >= 1 and subs.SATURATED in r.flags
    assert 1996 <= r.peak_col * 1 <= 2004


@needs_nova
def test_hot_pixel_in_the_rows_does_not_set_the_peak(tmp_path: Path, nova_rows: subs.Rows) -> None:
    ridge = round(nova_rows.ridge(3840)[700])

    def hot(d: np.ndarray) -> np.ndarray:
        d[ridge, 700] = 65520  # one pixel only
        return d
    clean = subs.analyse_sub(NOVA[0], nova_rows)
    r = subs.analyse_sub(_edit(NOVA[0], tmp_path / "hot.fits", hot), nova_rows)
    assert r.peak_adu == clean.peak_adu and r.peak_col == clean.peak_col and r.peak_fill < 1


@needs_nova
def test_dim_sub_is_flagged_by_the_snr_rule(tmp_path: Path, nova_rows: subs.Rows) -> None:
    recs = [subs.analyse_sub(p, nova_rows) for p in NOVA[:5]]
    dim = _edit(NOVA[0], tmp_path / "dim.fits", lambda d: ((d - 450) * 0.3 + 450).astype(np.int64))
    recs.append(subs.analyse_sub(dim, nova_rows))
    subs.refresh_flags(recs, sigma=2.0)
    assert subs.LOW_SNR in recs[-1].flags and not recs[-1].included
    assert all(not r.flags and r.included for r in recs[:5])


@needs_nova
def test_user_override_survives_recalculation(nova_rows: subs.Rows) -> None:
    recs = [subs.analyse_sub(p, nova_rows) for p in NOVA[:4]]
    recs[1].included, recs[1].user_override = False, True  # unticked by hand although clean
    subs.refresh_flags(recs)
    assert not recs[1].included and all(r.included for r in recs if r is not recs[1])


# ---- doc test 3: conversion gain and read noise ---------------------------------------------------------
def test_gain_from_header_then_table_never_across_hcg() -> None:
    assert camera.gain_and_noise({"EGAINSAV": 0.06081, "RDNOISE": 3.65, "GAIN": 200}) == (0.06081, 3.65, "header")
    g, r, src = camera.gain_and_noise({"GAIN": 200, "SWCREATE": "N.I.N.A."})  # NINA-style: no EGAINSAV
    assert src == "table" and (g, r) == (0.047, 1.0)
    assert camera.from_table(199) == (0.063, 4.0)  # below HCG: nearest low-gain point, not toward 200
    g201, r201 = camera.from_table(201)
    assert r201 < 1.0 and 0.022 < g201 < 0.047  # interpolated within the HCG side only
    assert camera.from_table(125) == pytest.approx(((0.188 + 0.113) / 2, (4.7 + 4.1) / 2))


# ---- doc test 4: rows -------------------------------------------------------------------------------------
@needs_nova
def test_auto_rows_lock_and_move_hint(tmp_path: Path, nova_rows: subs.Rows) -> None:
    assert abs(nova_rows.row - 1070) <= 6 and nova_rows.spec[0] < 0 < nova_rows.spec[1]
    assert nova_rows.sky_a[1] < nova_rows.spec[0] and nova_rows.sky_b[0] > nova_rows.spec[1]
    assert abs(subs.detect_rows(REF[0]).row - 1042) <= 6
    shifted = _edit(NOVA[0], tmp_path / "moved.fits", lambda d: np.roll(d, 5, axis=0))
    r = subs.analyse_sub(shifted, nova_rows)
    assert r.shift_px == pytest.approx(5, abs=1.0) and subs.moved_hint(r) is not None
    assert r.rows == nova_rows.to_json()  # rows are never moved silently
    assert subs.moved_hint(subs.analyse_sub(NOVA[1], nova_rows)) is None


# ---- doc test 5: stack SNR ------------------------------------------------------------------------------
@needs_nova
def test_quadrature_stack_matches_a_direct_stack(tmp_path: Path, nova_rows: subs.Rows) -> None:
    recs = [subs.analyse_sub(p, nova_rows) for p in NOVA]
    total = sum(read_image(p).astype(np.float64) for p in NOVA)
    with fits.open(NOVA[0]) as hdul:
        h = dict(hdul[0].header)
    h = {k: v for k, v in h.items() if k in ("EGAINSAV", "RDNOISE", "GAIN", "EXPTIME", "OBJECT")}
    h["RDNOISE"] = 3.65 * np.sqrt(len(NOVA))  # six reads
    direct = subs.analyse_sub(write_float32(tmp_path / "stack.fits", total.astype(np.float32), h), nova_rows)
    quad = subs.stack_snr([r.continuum_snr for r in recs])
    assert direct.continuum_snr == pytest.approx(quad, rel=0.05)


def test_projection_and_safe_exposure() -> None:
    assert subs.subs_needed(100, 50) == 4 and subs.subs_needed(100, 40) == 7
    assert subs.stack_curve([50, 50, 50, 50])[-1] == pytest.approx(100)
    assert subs.safe_exposure(300, 25000 + 400, 400, 52416, 0.95) == pytest.approx(300 * (0.95 * 52416 - 400) / 25000)
    assert subs.scaled_snr(50, 300, 1200) == pytest.approx(100)
    assert not subs.transparency_hint([50, 50, 50, 50])
    assert subs.transparency_hint([50, 5, 5, 5, 5, 5])


def test_shared_folder_copy_is_never_written(tmp_path: Path) -> None:
    if not NOVA:
        pytest.skip("example_spectra not present")
    before = NOVA[0].stat().st_mtime
    copy = shutil.copy(NOVA[0], tmp_path / NOVA[0].name)
    subs.analyse_sub(copy, subs.detect_rows(copy))
    assert NOVA[0].stat().st_mtime == before
