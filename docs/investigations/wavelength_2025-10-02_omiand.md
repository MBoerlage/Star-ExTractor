# Wavelength step on 2025-10-02 omi And / HD 217782 (pair `_2`): diagnosis, line-centre study, decision

2026-11-23. Tools: `tools/diagnose_wavelength.py <pair>` (dips, the best Balmer pattern, what v0.1 did),
`tools/centre_study.py` (the centre benchmark below), `tools/wavelength_shots.py <pair>` (screenshots on a copy).
Fixture for the tests: `tests/fixtures/wavelength_profiles.npz` (1D profiles only, 41 kB).

## Root cause

The camera sits about **150 px** further along the dispersion on 2025-10-02 than on 2025-12-30 (Hβ at x = 1111 on
2025-10-02, 1080 on 2025-12-18, 954–968 on 2025-12-30). The saved solution came from 2025-12-30 (beta Cas / HD 2772,
saved 2026-11-22) and v0.1 used it as the place to look, with a shift search of only ±80 px. It took the best comb
within ±80 px (−73 px) and fitted whatever was there: "Hα" on the O2 γ band (6278 Å, x ≈ 2285), "Hβ" on a 6 % bump
(x ≈ 883), "Hδ" on Hη (x ≈ 255); Hγ and Hε found nothing. Nothing checked the result, so a calibration on the wrong
dips was accepted. The user then dragged markers; one (Hδ) landed on the Hγ dip, which nothing prevented, and the
refine fit of that state (RMS 155.764 Å) was accepted as well. The target's +199.6 px manual shift compensated for the
wrong reference solution.

The saved solution's **shape** was not the problem: on the true dips the shift-only fit has RMS 1.08 Å (free fit
0.71 Å). Its offset was.

## Dips (reference, prominence ≥ 0.05, selection; full table: `tools/diagnose_wavelength.py`)

| x | depth | width px | λ (best pattern) | feature |
|---:|---:|---:|---:|---|
| 254 | 0.64 | 12.1 | 3834 | Hη |
| 300 | 0.63 | 14.8 | 3889 | Hζ |
| 337 | 0.31 | 6.0 | 3933 | Ca II K |
| 368 | 0.67 | 14.7 | 3970 | Hε (+ Ca II H) |
| 479 | 0.63 | 15.3 | 4103 | Hδ |
| 678 | 0.62 | 15.4 | 4341 | Hγ |
| 1111 | 0.55 | 15.8 | 4861 | Hβ |
| 1962 | 0.11 | 17.4 | 5887 | Na D / He I 5876 |
| 2285 | 0.07 | 13.5 | 6278 | O2 γ (telluric) |
| 2520 | 0.38 | 18.7 | 6563 | Hα |
| 2776 | 0.32 | 11.3 | 6874 | O2 B (band head) |
| 3037 | 0.32 | 35.2 | 7192 | H2O |
| 3380 | 0.77 | 18.0 | 7611 | O2 A |

Best pattern (blind search): Hα–Hη, 1.205 Å/px, Hβ at x = 1111, RMS 0.76 Å. Second best (different): 1.086 Å/px,
Hβ at x = 955, score 2.32 against 3.81: not ambiguous. Target: the same dips at −2…+2 px (median 0.0 px).

## What v0.1 did (prior = saved solution of 2025-12-30)

| line | predicted x | after the ±80 px shift (−73) | fitted | true dip | off |
|---|---:|---:|---:|---:|---:|
| Hα | 2368.3 | 2295.3 | 2285.3 | 2520 | −234.7 |
| Hβ | 959.1 | 886.1 | 882.9 | 1111 | −228.1 |
| Hγ | 526.5 | 453.5 | (rejected) | 678 | — |
| Hδ | 328.1 | 255.1 | 254.8 | 479 | −224.2 |
| Hε | 218.6 | 145.6 | (rejected) | 368 | — |

Fits: refine (shift only, saved shape): 3 lines, order 2, shift −76.21 px, **RMS 5.135 Å**; a second, hidden free
fit on the same wrong dips (linear, 3 lines): **RMS 0.760 Å**. With the dragged markers (Hα 2519, Hβ 1113.9, Hδ 679.5
on the Hγ dip, Hγ 240): refine **RMS 155.764 Å** (log 08:48:33); with the markers stored now (Hα 2521.69, Hβ 1111.26,
Hδ 679.73): 137.8 Å.

## Hypotheses

| | verdict | evidence |
|---|---|---|
| H1 saved solution of another night as prior; matcher grabs wrong dips | **yes** | prior from 2025-12-30_betacas__HD2772; lines 151 px from its predictions; the ±80 px search cannot reach them; Hα→O2 γ, Hβ→bump, Hδ→Hη; Hγ none |
| H2 no quality gate | **yes** | `calibrate` returned any fit; the run needed only ≥ 2 lines; RMS 155.764 Å logged as "matched", step done, ribbon "3 lines · RMS 155.76 Å" |
| H3 "Refine" kept although free is better | **yes** | the default stayed refine; the card only noted "Fit freely gives RMS 0.76 Å" (itself on wrong dips) |
| H4 target fine relative to the reference | **yes** | with the corrected reference and manual shift 0: target fit −0.61 px (−0.7 Å), markers on the target's Hβ–Hε dips within 0–1.7 px (Hα +2.6 px: Be emission). The "wrong shape" impression came from the wrong reference solution, not the saved shape (1.08 Å on the true dips) |
| H5 numbers from different fits, unlabelled | **yes** | 155.764 Å = refine with the dragged markers (log); 5.14 Å = refine on the wrong dips (card); 0.76 Å = hidden free fit on the same wrong dips (card); 0.212 Å = the saved solution's own RMS on 2025-12-30 (card "Saved solution: … RMS 0.212 Å"); the table footer RMS had no label; the target log said "Shift −0.6 px" while +199.6 px was applied on top |
| H6 non-Balmer dips taken as Balmer | **yes, in kind** | v0.1 assigned Hα to O2 γ and Hδ to Hη; Na D (1962) and O2 B (2776) were not picked on this run, but nothing excluded them |
| H7 centre method biased/noisy for broad lines | **yes, for dragged markers** | see below |

H7, exactly what v0.1 did (`core/wavelength.py fit_line`): scipy `curve_fit` of a Gaussian plus a straight background
(amplitude, centre, σ, base, slope) on the envelope-normalised profile (continuum: median 9 → max 50 → median 151 →
mean 75), no weights, no masking, no smoothing; start at the window's minimum; σ bounded 0.5…half window; accepted when
|amplitude| > 3 × noise, the centre is not at the window edge and σ < 0.8 × half window. Window ±12 px for automatic
lines, **±6 px around a dragged marker**. At R ≈ 360 a Balmer line here has FWHM ≈ 15 px (σ 4.6–5.2 px): the marker
window (13 px) is narrower than the line and the σ limit (4.8 px) is below the line's σ, so the fit is rejected in most
cases and the app keeps the marker exactly where it was dropped; when accepted it is pulled by up to 1.8 px. Automatic
lines (±12 px) are fine: a 3 px start error moves them by 0.1 px. A hot pixel in the core made v0.1 fail every time.

## Line-centre study

`tools/centre_study.py --reps 8 --special-reps 200 --boot 40` (seed 20261123; full output reproduced by the tool).
Every method gets the same start (true centre + 2 px rms, FWHM guess 12 px). Windows are total widths in FWHM.

**Synthetic grid** (486 cases × 8: FWHM 8/12/16 px, depth 0.1/0.3/0.6, Gaussian / Lorentzian / Voigt, continuum tilt
0/±40 % across 4 FWHM with curvature, S/N 15/50/200 per pixel, white or correlated pixels). Failure = not measured or
off by more than ½ FWHM. σ ratio = rms(error / reported σ), 1 = honest.

| method | bias px | scatter px | RMS Å | fail | σ ratio |
|---|---:|---:|---:|---:|---:|
| M0 v0.1 auto (±12 px) | +0.009 | 0.823 | 0.99 | 10.9 % | — |
| M0 v0.1 dragged marker (±6 px) | −0.067 | 1.611 | 1.94 | 0.8 %¹ | — |
| M1 argmin | −0.001 | 1.333 | 1.61 | 16.4 % | 2.67 |
| M2 parabola 7 | +0.016 | 1.338 | 1.61 | 16.5 % | 2.68 |
| M3 centroid | −0.004 | 1.077 | 1.30 | 0.3 % | 2.15 |
| M4 Gauss 1.5× / 2.5× / 4× / adaptive | +0.030 / −0.014 / +0.007 / +0.015 | 1.208 / 0.959 / 0.868 / 0.940 | 1.46 / 1.16 / 1.05 / 1.13 | 5.3 / 2.6 / 1.5 / 1.3 % | 2.2–2.6 |
| M5 Lorentz 1.5× / 2.5× / 4× / adaptive | +0.022 / −0.020 / +0.008 / +0.003 | 1.272 / 1.025 / 0.866 / 0.945 | 1.53 / 1.24 / 1.04 / 1.14 | 3.5 / 1.6 / 1.5 / 1.2 % | 2.0–2.5 |
| M6 pseudo-Voigt 1.5× / 2.5× / 4× / adaptive | +0.039 / −0.015 / +0.009 / +0.014 | 1.169 / 0.968 / 0.842 / 0.935 | 1.41 / 1.17 / 1.01 / 1.13 | 5.0 / 2.2 / 1.5 / 1.1 % | 1.7–2.5 |
| M7 pseudo-Voigt on the global continuum | +0.007 | 0.909 | 1.09 | 1.2 % | 2.34 |
| M8 profile-fit chain (pVoigt → Gauss → parabola) | +0.013 | 0.952 | 1.15 | 1.0 % | 2.43 |
| **M0 v0.2 (default)** | +0.001 | 0.803 | 0.97 | 11.0 % | 2.00² |

¹ the app kept the marker when the fit failed: "measured" but at the drop point. ² over the whole grid, dominated by
correlated noise and S/N 15; at S/N 50, FWHM 10–14 px, tilted continuum the ratio is 1.13 (robust 0.98).

By S/N (RMS px, failures): S/N 15: M0 1.47 (28 %), M0 v0.2 1.41 (28 %), M6 1.44 (3 %); S/N 50: 0.65 (5 %), 0.58 (5 %),
0.57 (0 %); S/N 200: 0.21 (1 %), 0.17 (1 %), 0.11 (0 %). Continuum tilt ±40 %: every method within 0.015 px except the
±6 px marker path (+0.13 px).

**Special cases** (FWHM 12, depth 0.4, S/N 50; bias / scatter px, failures, flagged by the profile fit):

| case | M0 v0.1 | M0 v0.2 | M8 profile fit (flags) |
|---|---|---|---|
| blend 0.8 FWHM away | −0.07 / 0.35, 4 % | −0.03 / 0.26, 4 % | +1.37 / 0.22, flagged 71 % |
| Hε + Ca II H (1.3 px) | −0.63 / 0.12 | −0.64 / 0.09 | −0.61 / 0.07 (always flagged by name in the app) |
| emission core (Be) | −2.82 / 0.65, 34 % fail | −3.23 / 0.63, 34 % fail | −0.63 / 0.61, flagged 100 % (wings only) |
| hot pixel in the core | **100 % fail** | −0.04 / 0.17 | −0.01 / 0.13 |
| start 0.6 FWHM off | +0.01 / 0.47 (marker path: 91 % kept at the drop point) | −0.04 / 0.17 | −0.01 / 0.13 |

**Real data** (8 pairs; centres from the blind-search positions). Leave-one-out = each interior line predicted from
the others with a **cubic** (a quadratic would measure the polynomial: see "Fit order" below); median over pairs:

| method | LOO median Å | app's quadratic fit, median Å | start 3 px off: centre moves (median px) |
|---|---:|---:|---:|
| M0 v0.1 auto | **0.50** | 0.40 | 0.03–0.20 |
| M0 v0.1 dragged marker | 1.09 | 0.52 | 0.14–1.59 |
| M3 centroid | 0.74 | 0.26 | 0.47–0.94 |
| M4 Gauss 1.5× / 2.5× / adaptive | 0.96 / 0.60 / 0.58 | 0.37 | 0.00–0.19 |
| M5 Lorentz adaptive | 0.45 | 0.36 | 0.00 |
| M6 pVoigt 1.5× / 2.5× / adaptive | 0.87 / 0.56 / 0.61 | 0.36–0.37 | 0.00–0.20 |
| M7 pVoigt, global continuum | 0.51 | 0.36 | 0.00 |
| M8 profile-fit chain | 0.61 | 0.36 | 0.00 |
| **M0 v0.2 (default)** | 0.52 | 0.39 | **0.00** |

M0 v0.2's 0.52 includes Hα on Z And and beta Cas, which v0.1 cannot fit (σ above its window limit); on the same lines
it is equal or better (Z And 0.09 vs 0.25 Å, beta Cas 0.27 vs 0.35, RT Aur 1.47 vs 1.74), worse on AE Aur (0.23 vs
0.11 Å). Reference/target per-line differences scatter 0.2–1.1 px for every method (omi And, AE Aur, beta Cas), i.e.
the target's own line shapes, not the method. Bootstrap noise 0.05–0.10 px, reported σ 0.06–0.13 px (M0 v0.2).

**Decision rule** (adopt a new default only if (1) lower bias and scatter than M0 on the synthetic grid, judged on the
lines both measured, and failing no more often, and (2) the median real-data LOO at least 15 % lower and never worse
than 1.5 × M0 on any pair):

| candidate | (1) synthetic | (2) real |
|---|---|---|
| M8 profile-fit chain | pass (scatter 0.696 vs 0.813 px, failures 1.0 vs 10.9 %) | **fail**: 0.58 vs 0.50 Å (17 % worse), worst pair 3.9 × |
| M6 pVoigt adaptive | pass | **fail**: 0.58 vs 0.50 Å, worst pair 3.9 × |
| M4 Gauss adaptive | pass | **fail**: 0.56 vs 0.50 Å, worst pair 3.4 × |
| M6 pVoigt 1.5× | fail | fail: 0.87 vs 0.50 Å |

**Decision: keep M0** as the centre method. The profile fits are better on clean synthetic lines but worse on real
Balmer lines: their wider windows take in Stark wings and the metal lines of cooler stars (beta Cas as a target is
F-type), while M0's narrow core fit is not disturbed. What would be needed to change that: a line-shape model per
spectral type (template cross-correlation of each line against the reference star's library spectrum), or a higher
fit order so the real-data comparison is not limited by the polynomial. M0 was **corrected**, not replaced: hot
pixels interpolated over, the window re-centred until it stops moving, a ±0.8 FWHM window only when the ±12 px fit
fails on a line wider than 18 px, and dragged markers fitted the same way as automatic lines. Benefit: for automatic
lines the centres are as before (0.50 → 0.52 Å median LOO, within noise; Hα now measured on broad-lined stars); for
dragged markers the drop point no longer matters (0.14–1.59 px → 0.00 px for a 3 px misplacement; LOO 1.09 → 0.52 Å);
hot pixels no longer fail the line. The floor: the polynomial (0.6–0.8 Å on the October nights), Hε + Ca II H (−0.6 px =
−0.7 Å), emission cores (left out), the star's radial velocity (0.4 px per 30 km/s at Hβ).

**Lines where every method fails or is biased**: Hα of the Be target omi And (emission: −0.6 to −3 px; flagged
"emission core", left out of a fit); Hε (Ca II H, −0.6 px for all; flagged, weight 0.3); Hη/Hζ in the crowded blue end
(optional, off by default); the targets V799 Sgr, Z And and SU And (emission or carbon star: no Balmer absorption
pattern; the search reports them as ambiguous and never calibrates on them).

## Fit order

With Hα–Hη the residuals of a quadratic follow the same pattern on every October pair (Hβ −1.0, Hγ +1.0, Hδ +1.0 Å;
RMS 0.76–0.80 Å) and a smaller opposite one in December (RMS 0.16–0.40 Å); a cubic brings all of them to 0.05–0.19 Å
(V799 Sgr 0.85 Å: a noisy reference). The spectrograph's dispersion is not quadratic at the 1 Å level, and the October
setup differs from December's. This limits the free fit to about 0.7 Å RMS on those nights whatever the centres.

## What changed

* **Blind Balmer search** (`core/balmer_search.py`): pattern of dip pairs × line pairs, dispersion 1.0–1.4 Å/px, any
  offset; weighted score, ≥ 3 of Hα–Hε, the second-best pattern reported, ambiguity stops the step. Non-Balmer features
  (Na D, He I, Ca II K, telluric bands from `core/linelists.py`) are never Balmer lines; the quadratic term is bounded
  and lines the others cannot predict are pruned. On all 8 reference stars in the data root it finds Hα–Hε at
  1.2025–1.2065 Å/px; with any one of Hα…Hε cut out of the omi And or beta Cas profile it still finds the other four.
* **Prior validation**, **default mode**, **quality gate**, **labelled numbers**, **order rule**, **target reset**:
  `docs/WAVELENGTH.md` § "The reference star's solution".
* **Centres**: M0 v0.2 (above) for automatic lines and dragged markers alike; uncertainties, flags, overlays, "mine".

## Part B: how the order could be broken

Only by **dragging a marker**: markers had no bounds and the dragged position was stored as is (`manual`), so a marker
could sit on another line's dip (this pair: Hδ at 679.7 on the Hγ dip). The Calibration points table was never editable
or sortable, and Add or remove lines only toggles lines. Now: markers stop 15 px before their neighbours, stray values
are refused and logged, saved out-of-order markers are ignored with a message, the table is always in wavelength order
(sorting and moving off), centres that cross are left out, and a non-monotonic solution fails.

## Not followed / open

* **Fit order.** The Star'Ex dispersion is not a quadratic to better than ±1 Å on the October nights (±0.3 Å in
  December): with 7 lines a cubic brings the residuals from 0.8 to 0.1 Å. Decision (2026-11-23): **Cubic** was added
  as a user choice in Fit order (5+ lines); the default stays at most quadratic.
* **Part C adopted only in part**, as the decision rule requires: the profile fits did not beat M0 on real data, so the
  default stays M0 (corrected); the profile fit is a diagnostic, and the uncertainty, flag, overlay, "mine",
  weighting and rejection parts are implemented.
* Editing pixel values in the table: the table was never editable; nothing to validate.
* The reported centre uncertainty is calibrated for independent and mildly correlated pixels (factor 1.0–1.24 measured
  on the real profiles); for strongly correlated synthetic noise it is still about 2× too small.
* Every processed pair's Wavelength step becomes out of date (its parameters gained `centre`, `saved_source`,
  `mode_user`): re-run Wavelength (and the later steps) per pair.
