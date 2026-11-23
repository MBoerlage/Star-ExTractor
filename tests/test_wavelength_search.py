"""Wavelength v0.2: blind Balmer search, the saved solution as a validated prior, the quality gate, wavelength order
(Part B) and labelled numbers. Fixture: the 1D profiles of the 2025-10-02 omi And / HD 217782 pair and of the
2025-12-30 beta Cas reference (docs/investigations/wavelength_2025-10-02_omiand.md)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from starextractor.core import balmer_search as bs
from starextractor.core import wavelength as w
from starextractor.core import wavelength_stage as ws
from starextractor.core.target_wave import Adjust

FIX = np.load(Path(__file__).parent / "fixtures" / "wavelength_profiles.npz")
REF = FIX["omiand_ref"].astype(np.float64)
TGT = FIX["omiand_tgt"].astype(np.float64)
BETACAS = FIX["betacas_ref"].astype(np.float64)
SAVED = w.Solution([float(c) for c in FIX["betacas_saved"]])  # beta Cas night, 2025-12-30
TRUE = {"Hα": 2520, "Hβ": 1111, "Hγ": 680, "Hδ": 480, "Hε": 371}  # the dips, read off the profile


# 1. the blind search ---------------------------------------------------------------------------------------------
def test_blind_search_finds_the_balmer_series() -> None:
    r = bs.search(REF)
    assert r.best is not None and not r.ambiguous
    for name, x in TRUE.items():
        assert r.best.matched[name] == pytest.approx(x, abs=5), name
    assert r.best.dispersion == pytest.approx(1.208, abs=0.01)
    cal = w.calibrate(REF, SAVED, "refine", mode_user=False)
    assert cal.status == "ok" and cal.mode == "free" and cal.fit.rms_a < 1.0
    assert {p.line for p in cal.points if p.used} == set(TRUE)
    assert cal.solution.dispersion_a(1500) == pytest.approx(1.208, abs=0.01)


def test_second_candidate_is_reported_and_far_behind() -> None:
    r = bs.search(REF)
    assert r.second is not None and r.second.score < bs.AMBIGUOUS * r.best.score


# 2. the saved solution is only a validated prior -------------------------------------------------------------------
def test_prior_from_another_night_is_rejected() -> None:
    r = bs.search(REF)
    chk = bs.validate_prior(SAVED, REF, found_by=r.best, source="2025-12-30_betacas__HD2772, saved 2026-11-22")
    assert not chk.ok and chk.median_offset == pytest.approx(151, abs=3)
    assert "does not fit this night (markers 151 px from the dips)" in chk.message and "betacas" in chk.message
    cal = w.calibrate(REF, SAVED, "refine", mode_user=False, prior_source="beta Cas")
    assert not cal.prior["ok"] and cal.mode == "free" and "does not fit" in cal.mode_reason


def test_prior_that_fits_is_accepted() -> None:
    chk = bs.validate_prior(SAVED, BETACAS)
    assert chk.ok and chk.found == chk.total == 5
    cal = w.calibrate(BETACAS, SAVED, "refine", mode_user=True)  # the user's choice is kept
    assert cal.prior["ok"] and cal.status == "ok" and cal.mode == "refine" and cal.fit.rms_a < 1.0
    auto = w.calibrate(BETACAS, SAVED, "refine", mode_user=False)  # free is far better: chosen, and said why
    assert auto.prior["ok"] and auto.mode == "free" and "Fit freely: RMS" in auto.mode_reason


# 3. the quality gate -----------------------------------------------------------------------------------------------
def test_a_wrong_match_fails_and_is_not_passed_on(tmp_path: Path) -> None:
    wrong = {"Hα": 2285.0, "Hβ": 883.0, "Hδ": 254.0}  # the dips v0.1 took: O2 γ, a weak bump, Hη
    cal = w.calibrate(REF, SAVED, "refine", lines=["Hα", "Hβ", "Hδ"], manual=wrong, nofit=list(wrong),
                      mode_user=True)
    assert cal.status == "failed" and "RMS" in cal.reason
    p = {**ws.DEFAULTS, "saved": list(SAVED.coeffs_nm), "lines": ["Hα", "Hβ", "Hδ"], "manual": wrong,
         "nofit": list(wrong), "mode_user": True}
    ref_f, tgt_f = tmp_path / "Reference_X.fit", tmp_path / "Target_X.fit"
    from starextractor.core.fitsio import write_float32

    write_float32(ref_f, REF.astype(np.float32), {"OBJECT": "ref"})
    write_float32(tgt_f, TGT.astype(np.float32), {"OBJECT": "tgt"})
    out = ws.preview_job(_Ctx(), ref_f, tgt_f, p)
    assert out["status"] == "failed" and "target" not in out
    spec = ws.WavelengthSpec(ref_f, {"Reference": ref_f, "Target": tgt_f},
                             {"Reference": tmp_path / "Reference_W.fit", "Target": tmp_path / "Target_W.fit"}, p)
    with pytest.raises(ValueError, match="Wavelength failed"):
        ws.wavelength_job(_Ctx(), spec)
    assert not (tmp_path / "Target_W.fit").exists()


def test_rms_155_is_never_a_success() -> None:
    # v0.1 accepted the dragged-marker state of the pair (Hδ on the Hγ dip) with RMS 155 Å
    cal = w.calibrate(REF, SAVED, "refine", manual={"Hα": 2521.69, "Hβ": 1111.26, "Hδ": 679.73}, mode_user=True)
    assert cal.status == "ok" and cal.fit.rms_a < 3.0  # the out-of-order marker is repaired, not used
    assert any("out of wavelength order" in m for m in cal.messages)


# 4. non-Balmer dips --------------------------------------------------------------------------------------------------
def test_na_d_and_o2_b_are_not_balmer_lines() -> None:
    r = bs.search(REF)
    xs = list(r.best.matched.values())
    assert all(abs(x - 1962) > 20 and abs(x - 2776) > 20 for x in xs)
    # with Hα cut out of the profile, nothing else is taken for it
    cut = REF.copy()
    cut[2490:2550] = np.interp(np.arange(2490, 2550), [2489, 2550], [REF[2489], REF[2550]])
    r2 = bs.search(cut)
    assert r2.best is not None and "Hα" not in r2.best.matched
    for k in ("Hβ", "Hγ", "Hδ"):
        assert r2.best.matched[k] == pytest.approx(TRUE[k], abs=5)


# 5. the target ---------------------------------------------------------------------------------------------------
def _ref_solution() -> w.Solution:
    return w.calibrate(REF, SAVED, "refine", mode_user=False).solution


def _target(adjust: dict | None) -> dict:
    p = {**ws.DEFAULTS, "per_image": {"Target": {"adjust": adjust or {}}}}
    ti = ws.TargetInputs({}, {}, "")
    return ws.target_solution(_ref_solution(), REF, TGT, p, ti)


def test_target_needs_no_manual_shift() -> None:
    sol, data, _ = _target(None)
    assert data["shift"] == pytest.approx(-0.6, abs=1.0)
    found = bs.search(TGT).best.matched
    for k in ("Hβ", "Hγ", "Hδ", "Hε"):  # Hα: the Be star's emission moves its dip
        assert abs(found[k] - sol.pixel(w.BALMER[k])) < 3.0, k


def test_old_manual_shift_is_reset_with_a_note() -> None:
    _sol, data, hist = _target({"mode": "shift", "shift": 199.6, "source": "manual"})
    assert data["manual_reset"]["shift"] == 199.6 and "reset" in data["manual_reset"]["text"]
    assert abs(data["shift"]) < 2.0 and any("reset" in h for h in hist)
    hb = float(_ref_solution().pixel(w.BALMER["Hβ"]))
    _, kept, _ = _target({"mode": "shift", "shift": 1.5, "source": "manual", "ref_hb": hb})
    assert kept["manual_reset"] is None and kept["shift"] == pytest.approx(data["shift"] + 1.5, abs=0.05)
    assert Adjust.from_params({"mode": "shift", "shift": 3, "ref_hb": hb + 10}).stale_for(_ref_solution())


# 6. wavelength order (Part B) ----------------------------------------------------------------------------------------
def test_order_check_and_repair() -> None:
    guesses = {"Hε": 368.0, "Hδ": 479.0, "Hγ": 678.0, "Hβ": 1111.0, "Hα": 2520.0}
    assert w.check_order(guesses) == []
    assert w.check_order({**guesses, "Hδ": 679.7}) == ["Hγ"]
    assert w.check_order({**guesses, "Hγ": 485.0}) == ["Hγ"]  # closer than 15 px
    fixed, msgs = w.repair_manual({"Hδ": 679.7, "Hβ": 1112.0}, guesses)
    assert fixed == {"Hβ": 1112.0} and "Hδ marker (px 679.7) was out of wavelength order" in msgs[0]


def test_non_monotonic_solution_fails() -> None:
    assert w.monotonic(w.Solution([350.0, 0.12, 1e-7]), 3840)
    assert not w.monotonic(w.Solution([350.0, 0.12, -2e-5]), 3840)  # turns over inside the detector


# 7. labelled numbers --------------------------------------------------------------------------------------------------
def test_numbers_say_which_fit_they_belong_to() -> None:
    from starextractor.ui.screens.wavelength import solution_note

    cal = w.calibrate(REF, SAVED, "refine", mode_user=False)
    d = ws.describe(cal)
    d["shift"] = cal.shift_px
    log = ws.calibration_log(cal, d)
    free, shift = cal.fits["free"], cal.fits["shift"]
    assert d["label"] == "Free fit" and d["rms"] == pytest.approx(free["rms"])
    assert any(s.startswith(f"Free fit (used): order {free['order']}, {free['n']} lines, RMS {free['rms']:.3f} Å")
               for s in log)
    assert any(s.startswith("Shift only: ") and f"RMS {shift['rms']:.3f} Å" in s for s in log)
    note = solution_note(d)
    assert f"Free fit: RMS {free['rms']:.2f} Å, {free['n']} lines" in note and "(in use)" in note
    assert f"Shift only: RMS {shift['rms']:.2f} Å" in note and "does not fit" in note


class _Ctx:
    def step(self, *_a, **_k) -> None:
        pass

    def check(self) -> None:
        pass


def test_unmatched_line_says_why() -> None:
    cut = REF.copy()
    cut[650:710] = np.interp(np.arange(650, 710), [649, 710], [REF[649], REF[710]])  # Hγ removed
    cal = w.calibrate(cut, SAVED, "refine", mode_user=False)
    assert "Hγ" not in {p.line for p in cal.points}
    assert cal.unmatched["Hγ"].startswith("no dip within 25 px of predicted x=6")
    assert any(m.startswith("Not found: Hγ (no dip within 25 px") for m in cal.messages)
    assert cal.status == "ok"  # four lines are enough


@pytest.mark.parametrize("profile", ["omiand_ref", "betacas_ref"])
@pytest.mark.parametrize("missing", ["Hα", "Hβ", "Hγ", "Hδ", "Hε"])
def test_one_line_missing_still_finds_the_others(profile: str, missing: str) -> None:
    # ranked by count, five weak bumps (v0.1's O2 γ / Hη match) once beat the true series with Hδ cut out
    ref = FIX[profile].astype(np.float64)
    full = bs.search(ref).best.matched
    x = int(full[missing])
    cut = ref.copy()
    cut[x - 30:x + 30] = np.interp(np.arange(x - 30, x + 30), [x - 31, x + 30], [ref[x - 31], ref[x + 30]])
    cal = w.calibrate(cut, SAVED, "refine", mode_user=False)
    assert cal.status == "ok" and missing not in {p.line for p in cal.points}
    assert len(cal.points) == 4 and all(abs(p.pixel - full[p.line]) < 3 for p in cal.points)
