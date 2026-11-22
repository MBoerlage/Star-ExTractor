"""Double-lobed (defocused, ring-shaped) star profiles: one spectrum band over both lobes, skies clear of both;
single-peak profiles give exactly the rows they gave before. Synthetic data only."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import synth
from scipy import ndimage

from starex_reduce.core import extract as ex
from starex_reduce.core.extract import Band, Bands, auto_place, extract
from starex_reduce.synth import Donut, donut_total, make_donut, write_donut


def old_auto_place(img: np.ndarray, sky_width: int = ex.SKY_WIDTH) -> Bands:
    """auto_place before double-lobed profiles (verbatim), the reference for 'unchanged'."""
    h = img.shape[0]
    row0 = ex.spectrum_row(img)
    lit = ex.lit_range(img, row0)
    prof = ex.row_profile(img, lit)
    sm = ndimage.gaussian_filter1d(prof - np.median(prof), 1.5)
    lo, hi = max(0, int(row0) - 40), min(h, int(row0) + 41)
    row = lo + int(np.argmax(sm[lo:hi]))
    fwhm = ex.fwhm_rows(prof, row)
    half = max(2, round(fwhm))
    spec = Band(row - half, row + half)
    gap = max(round(2.5 * fwhm), 12)
    slit = ex.slit_rows(prof, row, fwhm)
    lo_edge = slit[0] if slit[0] < spec.top - 1 else 0
    hi_edge = slit[1] if slit[1] > spec.bottom + 1 else h - 1
    a_bot = min(max(lo_edge + 1, spec.top - gap), spec.top - 1)
    b_top = max(min(hi_edge - 1, spec.bottom + gap), spec.bottom + 1)
    sky_a = Band(max(lo_edge, a_bot - sky_width + 1), a_bot)
    sky_b = Band(b_top, min(hi_edge, b_top + sky_width - 1))
    return Bands(spec, sky_a, sky_b).ordered(h)


def small(**kw) -> Donut:
    """The donut on a 700-row frame (lobes near rows 356 and 378, like 1356 / 1378 on the full frame)."""
    base = {"main_row": 378.0, "second_row": 356.0, "height": 700, "width": 1600}
    return Donut(**{**base, **kw})


# 1 -------------------------------------------------------------------------------------------------------------
def _single_fixtures() -> list[np.ndarray]:
    out = []
    p = synth.scaled(synth.PRESETS["nova"], 0.25)
    for which in ("target", "reference"):
        for seed in (0, 1):
            out.append(synth.make_light(p, which, seed).astype(np.float32))
    out.append(synth.slanted_frame(-0.67, 1.0, seed=2, width=1600, height=1100).astype(np.float32))
    rng = np.random.default_rng(4)
    for fwhm in (3.0, 6.0, 12.0):  # plain Gaussians
        img = rng.normal(100, 3, (500, 900)).astype(np.float32)
        img += (800 * np.exp(-0.5 * ((np.arange(500)[:, None] - 250) / (fwhm / 2.3548)) ** 2)).astype(np.float32)
        out.append(img)
    return out


def test_single_peak_rows_are_unchanged() -> None:
    for img in _single_fixtures():
        pl = auto_place(img)
        assert pl.mode == "single"
        assert pl.bands == old_auto_place(img)
        assert auto_place(img, mode="single").bands == old_auto_place(img)


# 2 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("dip", [0.2, 0.5, 0.8])
@pytest.mark.parametrize("ratio", [1.0, 0.35])
@pytest.mark.parametrize("tilt", [0.0, 0.15])
def test_donut_span_covers_both_lobes_and_skies_stay_clear(dip: float, ratio: float, tilt: float) -> None:
    d = small(dip=dip, ratio=ratio, tilt_deg=tilt)
    results = []
    for seed in range(3):
        img = make_donut(d, seed=seed, hot=200, emission_col=800)
        pl = auto_place(img)
        b = pl.bands
        assert pl.mode == "double-lobed" and len(pl.lobes) == 2, (seed, pl)
        half = d.fwhm / 2
        assert b.spectrum.top <= d.second_row - half and b.spectrum.bottom >= d.main_row + half
        assert b.sky_a.bottom < d.second_row - d.fwhm and b.sky_b.top > d.main_row + d.fwhm  # clear of both lobes
        results.append((b.spectrum.top, b.spectrum.bottom))
    tops, bots = zip(*results, strict=True)
    assert max(tops) - min(tops) <= 1 and max(bots) - min(bots) <= 1  # stable between noise realisations


# 3 -------------------------------------------------------------------------------------------------------------
def test_v799_like_case() -> None:
    d = Donut()  # lobes at 1378 (main) and 1356 (35 %), a 15 s sub
    strip = make_donut(d, seed=5)[1200:1560]
    pl = auto_place(strip)
    b = Bands(*(Band(x.top + 1200, x.bottom + 1200) for x in (pl.bands.spectrum, pl.bands.sky_a, pl.bands.sky_b)))
    assert pl.mode == "double-lobed"
    assert 1340 <= b.spectrum.top <= 1352 and 1385 <= b.spectrum.bottom <= 1396  # about 1350–1393
    assert b.sky_a.bottom < b.spectrum.top - 10 and b.sky_b.top > b.spectrum.bottom + 10  # the usual gap
    assert old_auto_place(strip).spectrum.top + 1200 > 1365  # before: the band sat on the main lobe only


# 4 -------------------------------------------------------------------------------------------------------------
def test_separate_sources_are_not_merged() -> None:
    # a second star 60 rows away (beyond MAX_LOBE_SEP_FWHM × FWHM) with the dip back at the sky
    d = small(second_row=318.0, ratio=0.6, dip=0.0)
    pl = auto_place(make_donut(d, seed=1))
    assert pl.mode == "single" and pl.found == 2 and "second source in the slit, not merged" in pl.warnings
    assert pl.bands == old_auto_place(make_donut(d, seed=1)) or any("shortened" in w for w in pl.warnings)
    assert pl.bands.spectrum.top > 330  # the brightest star only
    assert not (pl.bands.sky_a.top <= 318 <= pl.bands.sky_a.bottom)  # no sky on the other star
    # a weak lobe below LOBE_MIN_FRAC is not merged either
    weak = auto_place(make_donut(small(ratio=0.08, dip=0.5), seed=2))
    assert weak.mode == "single"


# 5 -------------------------------------------------------------------------------------------------------------
def test_noise_never_makes_a_second_lobe() -> None:
    rows = np.arange(400)[:, None]
    star = (250 * np.exp(-0.5 * ((rows - 200) / (7 / 2.3548)) ** 2)).astype(np.float32)
    for seed in range(200):
        img = np.random.default_rng(seed).normal(400, 6, (400, 400)).astype(np.float32) + star
        pl = auto_place(img)
        assert pl.mode == "single" and pl.found == 1, seed


# 6 -------------------------------------------------------------------------------------------------------------
def test_mode_override() -> None:
    img = make_donut(small(), seed=3)
    assert auto_place(img, mode="single").bands == old_auto_place(img)  # Single peak only: the old rows
    three = make_donut(small(second_row=318.0, ratio=0.6, dip=0.0), seed=3)
    allb = auto_place(three, mode="all")
    assert allb.mode == "double-lobed" and allb.bands.spectrum.top <= 318 - 3 and allb.bands.spectrum.bottom >= 381


# 7 -------------------------------------------------------------------------------------------------------------
def test_extraction_of_the_donut_sums_all_its_flux() -> None:
    d = small(dip=0.4)
    img = make_donut(d, seed=7)
    pl = auto_place(img)
    e = extract(img, pl.bands)
    truth = donut_total(d)
    mid = slice(200, 1400)
    ratio = np.median(e.profile[mid] / truth[mid])
    assert 0.98 < ratio < 1.01  # a plain sum: no Gaussian-profile bias, the wings at 10 % are inside
    assert abs(float(np.median(e.sky)) - d.sky) < 1.0  # sky from the two sky bands
    one_lobe = extract(img, old_auto_place(img))
    assert np.median(one_lobe.profile[mid] / truth[mid]) < 0.8  # what was lost before


# 8 -------------------------------------------------------------------------------------------------------------
def test_full_frame_sub_detection_timing_and_handover(tmp_path: Path) -> None:
    from starex_reduce.core import subs as sb
    from starex_reduce.core.stack_keys import rows1_of, sx_cards

    path = write_donut(tmp_path / "donut.fits")
    rows = sb.detect_rows(path, tilt_deg=0.0)
    r0 = round(rows.row)
    assert rows.shape == "double-lobed" and r0 + rows.spec[0] <= 1352 and r0 + rows.spec[1] >= 1385
    single = sb.detect_rows(path, tilt_deg=0.0, mode="single")
    assert single.shape == "single" and round(single.row) + single.spec[0] > 1365
    t0 = time.perf_counter()
    rec = sb.analyse_sub(path, rows)
    assert (time.perf_counter() - t0) < 0.3 and rec.continuum_snr > 0  # budget 100 ms in tools/perf.py
    h = sx_cards("Donut", 1, 15.0, rows1_of(rows.to_json()))
    assert h["SXROW1"] == r0 + rows.spec[0] + 1 and h["SXROW2"] == r0 + rows.spec[1] + 1  # the wider rows
    assert sb.Rows.from_json(rows.to_json()).shape == "double-lobed"


def test_acquisition_mode_chip_and_sets_file(qtbot, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from starex_reduce.core import acq_sets
    from starex_reduce.core.session import Session
    from starex_reduce.ui import theme
    from starex_reduce.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    path = write_donut(tmp_path / "subs" / "donut_00001.fits")
    w = MainWindow(session=Session())
    qtbot.addWidget(w)
    w.show()
    w.show_tab("acquisition")
    a = w.ensure_acquisition()
    a.lobe_mode.setCurrentIndex(a.lobe_mode.findData("single"))
    assert a.sets["target"]["lobes"] == "single"
    a.lobe_mode.setCurrentIndex(a.lobe_mode.findData("auto"))
    a.enqueue([path])
    qtbot.waitUntil(lambda: not a.queue and not a.busy, timeout=20000)
    r = a.rows_by["target"]
    assert r.shape == "double-lobed" and not a.lobe_chip.isHidden()
    assert "Double-lobed profile (defocus?)" in a.lobe_chip.text() and "sharper focus" in a.lobe_chip.text()
    doc = acq_sets.load_sets(a.night_folder() / acq_sets.FILE_NAME)
    assert doc["target"]["lobe_mode"] == "auto"
    a.lobe_mode.setCurrentIndex(a.lobe_mode.findData("single"))
    with qtbot.waitSignal(w.runner.finished, timeout=20000, check_params_cb=lambda s, _r: s == "acq.redetect"):
        a.redetect()
    qtbot.waitUntil(lambda: not a.busy, timeout=20000)
    assert a.rows_by["target"].shape == "single" and a.lobe_chip.isHidden()
    assert acq_sets.load_sets(a.night_folder() / acq_sets.FILE_NAME)["target"]["lobe_mode"] == "single"
