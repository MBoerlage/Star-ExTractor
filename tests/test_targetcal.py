"""Target calibration v2: line catalogue, sky-line detector, the correction model and its Δ verdict (core only)."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from starex_reduce.core import linelists, targetcal
from starex_reduce.core.skylines import Peak, find_sky_lines, identify
from starex_reduce.core.targetcal import fit_target
from starex_reduce.synth import wave_case

ROOT = Path(__file__).resolve().parents[1]
NOVA = ROOT / "example_spectra" / "novasrg2026" / "2026-11-16"


def run(mode: str = "auto", sptype: str | None = "A0V", disabled=frozenset(), **kw):
    c = wave_case(**kw)
    t = find_sky_lines(c.tgt_sky, c.sky_rows, c.ref)
    r = find_sky_lines(c.ref_sky, c.sky_rows, c.ref)
    f = fit_target(c.ref, c.ref_prof, c.tgt_prof, tgt_sky=t, ref_sky=r, ref_sptype=sptype, mode=mode,
                   disabled=disabled, used_range=(c.ref.pixel(3900), c.ref.pixel(8000)))
    return c, t, f


def d_at(f, x: float) -> float:
    return float(f.d(x))


# ---- 1. catalogue ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("label,lam", [
    ("Hg I 4047", 4046.56), ("Hg I 4358", 4358.33), ("Hg I 5461", 5460.74), ("Hg I 5770", 5769.60),
    ("Hg I 5791", 5790.66), ("O I 5577", 5577.34), ("[O I] 6300", 6300.30), ("[O I] 6364", 6363.78),
    ("Na D1 ISM", 5895.92), ("Na D2 ISM", 5889.95), ("DIB 5780", 5780.5), ("DIB 5797", 5797.1),
    ("DIB 6284", 6283.9), ("DIB 6614", 6613.6), ("Ca II K ISM", 3933.66), ("Hα", 6562.8), ("Hβ", 4861.3),
    ("Hγ", 4340.5), ("Hδ", 4101.7), ("Hε", 3970.1), ("He I 4026", 4026.2), ("He I 4388", 4387.9),
    ("He I 4471", 4471.5), ("He I 4713", 4713.1), ("He I 4922", 4921.9), ("He I 5016", 5015.7),
    ("He I 5876", 5875.6), ("He I 6678", 6678.2), ("He I 7065", 7065.2), ("He II 4200", 4200.0),
    ("He II 4541", 4541.0), ("He II 4686", 4685.7), ("He II 5412", 5411.0), ("O2 B", 6867.0),
    ("H2O 7186", 7186.0), ("O2 A", 7594.0), ("O2 γ", 6277.0), ("Na 5685 (HPS)", 5685.0),
    ("Na 6157 (HPS)", 6157.0)])
def test_catalogue_values(label: str, lam: float) -> None:
    tol = 1.0 if label.startswith(("He II", "O2", "H2O", "Na 5", "Na 6")) else 0.06
    assert abs(linelists.BY_LABEL[label].lam_a - lam) <= tol
    ln = linelists.BY_LABEL[label]
    assert ln.kind in (linelists.SKY_AIRGLOW, linelists.SKY_LAMP, linelists.TELLURIC, linelists.ISM,
                       linelists.STELLAR_BALMER, linelists.STELLAR_HE)


def test_catalogue_balmer_is_the_apps_table_and_blends_are_low_weight() -> None:
    from starex_reduce.core.wavelength import BALMER

    for name, lam in BALMER.items():
        assert linelists.BY_LABEL[name].lam_a == lam
    for ln in linelists.LINES:
        if ln.blend:
            assert ln.weight <= 0.5


# ---- 2. sky lines: detection, identification -------------------------------------------------------------------
def test_detector_finds_the_lines_rejects_the_hot_column_and_is_fast() -> None:
    c = wave_case(shift=13.5, seed=3)
    big = np.tile(c.tgt_sky, (90, 1))  # a full-size frame: 2160 rows
    rows = [(100, 400), (1500, 1800)]
    t0 = time.perf_counter()
    res = find_sky_lines(big, rows, c.ref)
    dt = time.perf_counter() - t0
    assert dt < 0.3, dt
    assert res.identified and {m.line.label for m in res.matches} >= {"Hg I 5461", "O I 5577", "[O I] 6300"}
    assert not any(abs(p.x - 900) < 2 for p in res.peaks)  # the hot column


@pytest.mark.parametrize("offset", [10.0, 40.0, 70.0])
def test_identified_at_large_initial_offsets(offset: float) -> None:
    c = wave_case(shift=offset, seed=4)
    res = find_sky_lines(c.tgt_sky, c.sky_rows, c.ref)
    assert res.identified and abs(res.offset_px - offset) < 1.0


def test_absent_or_ambiguous_patterns_are_not_identified() -> None:
    c = wave_case(shift=0.0, sky_lines=False, seed=5)
    res = find_sky_lines(c.tgt_sky, c.sky_rows, c.ref)
    assert not res.identified and res.text().startswith(("No sky lines", "1 sky", "2 sky", "3 sky", "0"))
    # a regular comb: every offset fits equally well
    from starex_reduce.core.linelists import Line

    comb = [Line(f"L{i}", 5000.0 + 100.0 * i, linelists.SKY_AIRGLOW, "X") for i in range(6)]
    peaks = [Peak(c.ref.pixel(5000.0 + 100.0 * i) + 20, 0.1, 6, 100, 50) for i in range(6)]
    peaks = peaks[:3] + [Peak(c.ref.pixel(5050.0 + 100.0 * i), 0.1, 6, 100, 50) for i in range(3)]
    matches, info = identify(peaks, c.ref, 3840, comb)
    assert not info["identified"] and not matches


# ---- 3. model choice and recovery ---------------------------------------------------------------------------
def test_shift_only_is_recovered_and_m0_is_chosen() -> None:
    hits, pure = 0, 0
    for seed in range(100):
        c, _t, f = run(shift=13.5, seed=seed)
        assert abs(d_at(f, c.truth["x0"]) - 13.5) < 0.3
        hits += f.model == 0
        pure += f.delta["verdict"] == "pure_shift" and f.delta["max_dev_a"] < targetcal.FLAT_DEV_A
        assert abs(f.k) <= targetcal.K_MAX and abs(f.q) * (f.used_range[1] - f.used_range[0]) ** 2 / 4 <= \
            targetcal.Q_MAX_EDGE_PX
    assert hits >= 90 and pure >= 95


def test_scale_accepted_within_the_limit_rejected_above_it() -> None:
    c, _t, f = run(shift=13.5, scale=0.003, seed=1)
    assert f.model == 1 and abs(f.k - 0.003) < 0.001 and f.delta["verdict"] == "shift_scale"
    x = np.array(f.delta["x"], dtype=float)
    true_span = float(np.ptp(np.asarray(c.ref.angstrom(x - (13.5 + 0.003 * (x - c.truth["x0"]))))
                             - np.asarray(c.ref.angstrom(x))))
    assert abs(abs(f.delta["span_a"]) - true_span) < 0.5
    c, _t, f = run(shift=13.5, scale=0.01, seed=1)
    assert f.model == 0 and any("Scale term not accepted" in t for _l, t in f.notes)
    c, _t, f = run(shift=13.5, scale=0.01, seed=1, mode="shift_scale")
    assert f.model == 1 and abs(f.k - 0.01) < 0.001


def test_clean_sky_still_fits_from_telluric_and_stellar_lines() -> None:
    c, t, f = run(shift=8.0, sky_lines=False, seed=2)
    assert not t.peaks or not t.identified
    assert f.ok and abs(d_at(f, c.truth["x0"]) - 8.0) < 0.3
    assert any("No sky lines found" in txt for _lvl, txt in f.notes) or not t.peaks
    assert f.counts()["sky"] == 0 and f.counts()["telluric"] >= 2


@pytest.mark.parametrize("v", [100.0, -100.0])
def test_stellar_velocity_recovered_with_fixed_anchors(v: float) -> None:
    c, _t, f = run(shift=13.5, v_kms=v, seed=6)
    assert f.v_determined and abs(f.v - v) < 15 and abs(d_at(f, c.truth["x0"]) - 13.5) < 0.3


def test_stellar_lines_only_velocity_not_determined_and_no_scale() -> None:
    _c, _t, f = run(shift=5.0, v_kms=80, sky_lines=False, tellurics=False, seed=7)
    assert f.ok and not f.v_determined and f.model == 0
    assert any("Velocity not determined" in txt for _l, txt in f.notes)


def test_outliers_rejected_blend_down_weighted_and_untick_refits() -> None:
    c, _t, f = run(shift=13.5, seed=8)
    na = next(a for a in f.anchors if a.name == "Na 5685 (HPS)")
    assert na.weight <= 0.3
    # a misidentified line: move one sky anchor by 6 px and refit through the same code path
    from starex_reduce.core import targetcal as tc

    anchors = [a for a in f.anchors]
    anchors[0].d_px += 6.0
    anchors[0].x += 6.0
    t = tc._robust_fit(anchors, c.ref, f.x0, (f.used_range[1] - f.used_range[0]) / 2, 0, f.v_determined,
                       tc.K_SIGMA)
    assert anchors[0].rejected and abs(t.params[0] - f.s) < 0.2
    key = next(a.key for a in f.anchors if a.kind == linelists.TELLURIC)
    _c2, _t2, g = run(shift=13.5, seed=8, disabled={key})
    assert not next(a for a in g.anchors if a.key == key).use and g.counts()["telluric"] == f.counts()["telluric"] - 1


def test_telluric_lag_recovered_and_skipped_for_a_late_type_reference() -> None:
    _c, _t, f = run(shift=9.0, sky_lines=False, stellar=False, seed=9)
    tel = [a for a in f.anchors if a.kind == linelists.TELLURIC]
    assert len(tel) >= 2 and all(abs(a.d_px - 9.0) < 0.4 for a in tel)
    _c, _t, f = run(shift=9.0, sky_lines=False, seed=9, sptype="K2III")
    assert not [a for a in f.anchors if a.kind == linelists.TELLURIC]
    assert any("not an A or B star" in t for _l, t in f.notes)


def test_sign_convention_puts_a_fixed_line_at_its_rest_wavelength() -> None:
    from starex_reduce.core.wavelength import linear_grid, resample

    c, _t, f = run(shift=13.5, seed=10)
    crval, cdelt, n = linear_grid(c.ref, 3900.0, 8000.0)
    lam = crval + cdelt * np.arange(n)
    sky1d = np.median(c.tgt_sky, axis=0).astype(float)
    w = resample(sky1d, f.solution, crval, cdelt, n)
    sel = (lam > 5577.34 - 15) & (lam < 5577.34 + 15)
    seg = w[sel] - np.nanmin(w[sel])
    centre = float(np.sum(lam[sel] * seg) / np.sum(seg))
    assert abs(centre - 5577.34) < 0.2
    # positive D: target features at larger x than the reference solution predicts
    assert f.s > 0 and f.solution.pixel(5577.34) > c.ref.pixel(5577.34)


def test_few_or_one_sided_anchors_cannot_judge_the_shape() -> None:
    _c, _t, f = run(shift=6.0, stellar=False, tellurics=False, seed=11)  # sky lines only: 5460–6300 Å
    assert f.delta["verdict"] == "not_enough" and "Not enough anchors to judge the shape" in f.delta["text"]
    assert f.delta["gaps"]


# ---- real data -------------------------------------------------------------------------------------------------
@pytest.mark.realdata
@pytest.mark.skipif(not (NOVA / "Target_G.fit").exists(), reason="novasrg2026 example not present")
def test_real_nova_pair_sky_lines_and_pure_shift() -> None:
    from astropy.io import fits

    from starex_reduce.core.wavelength import Solution

    h0 = fits.getheader(NOVA / "Reference_W.fit")
    ref = Solution([h0["WAV_C0"], h0["WAV_C1"], h0["WAV_C2"]])
    res = {}
    for img in ("Target", "Reference"):
        h = fits.getheader(NOVA / f"{img}_X.fit")
        g = fits.getdata(NOVA / f"{img}_G.fit").astype(np.float32)
        res[img] = find_sky_lines(g, [(h["EXT_SAT"], h["EXT_SAB"]), (h["EXT_SBT"], h["EXT_SBB"])], ref)
        assert res[img].identified and len(res[img].matches) >= 4
    common = {m.line.label: m.peak.x for m in res["Reference"].matches}
    d = [m.peak.x - common[m.line.label] for m in res["Target"].matches if m.line.label in common]
    # measured on the straightened frames: +1.9 px (see docs/WAVELENGTH.md: not the 13.5 px of the brief)
    assert len(d) >= 4 and abs(np.mean(d) - 1.9) < 0.5 and np.std(d) < 0.5
    tp = fits.getdata(NOVA / "Target_X.fit").astype(float)
    rp = fits.getdata(NOVA / "Reference_X.fit").astype(float)
    f = fit_target(ref, rp, tp, tgt_sky=res["Target"], ref_sky=res["Reference"], ref_sptype="A0V",
                   used_range=(ref.pixel(3900), ref.pixel(8000)))
    assert f.ok and f.model == 0 and abs(f.delta["max_dev_a"]) < 0.5
    # against the reference's Balmer solution the target sits +3.1 px (+3.7 Å) and Δ is flat, but the anchors only
    # cover 5461–7186 Å (O2 A is an outlier), so the shape is honestly "not enough anchors to judge"
    assert abs(f.s - 3.1) < 0.5 and f.delta["max_dev_a"] < 0.1
    assert f.delta["verdict"] == "not_enough" and f.delta["gaps"]
