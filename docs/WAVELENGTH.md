# Wavelength step

## The reference star's solution (v0.2, 2026-11-23)

Why it changed: `docs/investigations/wavelength_2025-10-02_omiand.md`. Code: `core/balmer_search.py` (search, prior
check), `core/wavelength.py` (`calibrate`), `core/centroid.py` (line centres), `core/wavelength_stage.py` (labels, log).

1. **Blind Balmer search.** Absorption dips of the envelope-normalised profile (prominence ≥ 0.03, width ≥ 6 px: Balmer
   lines are 11–27 px wide at R ≈ 360). Every pair of the 30 strongest dips is tried as every pair of Balmer lines
   (Hα…Hη, pixel order = wavelength order, dispersion 1.0–1.4 Å/px), each hypothesis λ = λ0 + d·x is scored by the
   lines that land on a dip (weighted by prominence and closeness, 22 px), refined by least squares (quadratic with
   |c| ≤ 1e-6 nm/px², else linear) and pruned: while the RMS is above 2 Å the line the others predict worst is dropped.
   A dip that sits closer to Na D, He I 4026/4471/5876, Ca II K or a telluric band (`core/linelists.py`) is never a
   Balmer line. At least 3 of Hα–Hε are needed; the second-best different pattern is reported, and when it scores
   within 85 % of the best (same line count) the step stops and asks for the Hβ and Hα markers.
2. **The saved solution is only a prior.** `validate_prior`: at least 2/3 of Hα–Hε on the detector need a line-wide dip
   (prominence ≥ 0.1) within 25 px of where it puts them. If not: *"Saved solution (pair, saved date) does not fit this
   night (markers 151 px from the dips)"*, and the line positions come from the search. The offset moves between nights
   (the camera is moved), the dispersion does not: a pattern whose dispersion is more than 3 % off the saved one is
   not trusted. The saved solution's pair and date are kept with the session (`saved_source`).
3. **Default mode.** Until the user picks one: *Fit freely* when the prior does not fit, or when the free fit's RMS is
   under half the shift-only RMS (above 0.5 Å); the Solution card says why in one line. The user's choice is kept.
4. **Quality gate.** RMS above min(3 Å, max(1 Å, 0.25 × FWHM)), fewer than 2 lines, or a solution that is not
   monotonic on the detector: the step is **failed** (no `_W` files, logbook "failed", the target gets nothing).
5. **Every number says which fit it is.** "Free fit: RMS 0.71 Å, 5 lines, order 2 (in use)" and "Shift only: RMS
   1.08 Å, 5 lines, shift +151.4 px from the saved solution" in the Solution card, the log and the table footer. The
   saved solution's own RMS (on its night) is no longer shown next to tonight's.
6. **Wavelength order (pixel order = wavelength order).** A marker is clamped 15 px before its neighbours (it stops
   there while dragging); a value that would break the order is refused and logged; the table rows are always in
   wavelength order and cannot be sorted or moved; a saved state with out-of-order markers is repaired (the marker
   ignored, with a message); a centre that slides past its neighbour is left out with a reason. Before v0.2 a marker
   could be dragged onto another line's dip (the pair's Hδ marker sat on Hγ: RMS 155 Å, accepted).
7. **Unmatched lines** are grey with a "?" and a tooltip: "no dip within 25 px of predicted x=…".
8. **Optional anchors Hζ 3889.05 and Hη 3835.38** (A-type stars): off by default, drawn grey and dotted until enabled
   in Lines › Add or remove lines (they then join the fit like the others).
9. **Line centres** (`core/centroid.py`, chosen by the benchmark): **M0 v0.2** — v0.1's Gaussian + linear background
   on the normalised profile, ±12 px, with hot pixels interpolated over, the window re-centred until it stops moving
   (a marker 6 px off gives the same centre within 0.1 px), and only when that fails on a line wider than 18 px a
   ±0.8 FWHM window. Dragged markers use the same fit (v0.1 used ±6 px and mostly kept the drop point). The centre
   never moves more than one FWHM from the guess; a failed fit keeps the marker with "fit failed: marker kept".
   Uncertainty: the fit covariance × the profile's pixel-correlation factor, plus a 0.15 px floor in the weights.
   The pseudo-Voigt × continuum fit only diagnoses: an emission core leaves the line out ("emission core: left out"),
   an asymmetric bisector (a blend) gives it weight 0.3; Hε is always flagged "blend: Ca II H 3968". Per line,
   "FIT: mine" uses the dragged marker exactly (logged). The fit is weighted; at most one interior line beyond 3σ
   (others' scatter at least 1 Å) is rejected and shown as "rejected: 4.1 σ". Developer setting:
   `settings.json` → `"wavelength": {"centre_method": "auto" | "voigt" | "gauss" | "lorentz" | "minimum" | "v01"}`;
   the method is part of the step's parameters, so changing it re-runs the step and marks later steps out of date.
10. **Limits.** A quadratic follows the Star'Ex dispersion to about ±1 Å only (October 2025 nights; ±0.3 Å in
   December): with Hα–Hε the free fit's RMS is 0.6–0.8 Å on those nights whatever the centres. A cubic reaches
   0.1 Å: since 2026-11-23 **Cubic** can be picked in Fit order (5+ lines, never by default; with 5 lines only one
   is left to check it). Blends (Hε + Ca II H: −0.6 px), emission cores
   and the star's radial velocity (≈ 0.4 px per 30 km/s at Hβ) set a floor no centre method removes.
11. **The target.** A manual target shift is relative to the reference solution, so it records the reference's Hβ
   column; when the reference solution changes (or the shift predates this check) it is reset to 0 with a note in the
   log and the Target card. The target log line now names a manual shift applied on top of the fit.

## The target's own solution (target calibration v2, 2026-11-19)

The reference star's Balmer fit is described above. The **target** now gets
its own solution, always written as **reference solution + a small correction** measured on the target's own data.
It never becomes a free calibration. Code: `core/linelists.py` (catalogue), `core/skylines.py` (sky lines),
`core/targetcal.py` (model), `core/wavelength_stage.py` (`target_solution`), UI
`ui/screens/wavelength_target.py` (the Target calibration card).

## Coordinates and sign convention

Pixel x is the **0-based column of the straightened frame** (`work/*_G.fit`, tilt and slant removed), the same
axis as the 1D profiles `*_X.fit` (`PIXEL_ORIGIN = 0`, as in BASS). x0 is the middle of the used range (the pixels
of `grid_start`…`grid_end`, 3900–8000 Å).

    D(x) = s + k (x − x0) + q (x − x0)²
    λ_target(x) = λ_ref(x − D(x))

A line of rest wavelength λ appears in the target at `x = x_ref(λ) + D`, so every anchor measures
`D = x_measured − x_ref(λ)`. **Positive s: the target's features sit at larger x than the reference solution
predicts.** "Shift in Å" in the card is `s × dispersion` (same sign as s); Δ(x) = λ_target(x) − λ_ref(x) is the
true wavelength difference and has the opposite sign (tests/test_targetcal.py
`test_sign_convention_puts_a_fixed_line_at_its_rest_wavelength`).

The target polynomial: exact `ref.shifted(s)` for a shift; for a scale or curvature a quadratic fitted to
`λ_ref(x − D(x))` (the substitution of a quadratic into a quadratic; the residual is far below 0.01 Å).

## Anchors

| Type | Measured | D | Weight |
|---|---|---|---|
| Sky lines (airglow, lamps) | Gaussian centroid in the median of the Sky A / Sky B rows of `*_G.fit` | `x − x_ref(λ)` (zero velocity) | catalogue weight; the Na lamp blends 0.3 |
| Telluric bands (O2 B, O2 A, H2O 7186, O2 γ) | the target's band cross-correlated with the same band in the reference profile (the reference is the template; only when the reference is an O, B or A star) | the lag at the band | catalogue weight |
| ISM (Na D, DIBs, Ca II K) | absorption centre on the target profile | `x − x_ref(λ)` | 0.2–0.5 |
| Stellar (Balmer, He I, He II) | centre on the target profile, absorption or emission | `x − x_ref(λ(1 + v/c))`, one v for all | catalogue weight; emission lines × 0.3 |

The stellar term is linear in v (`λ v / (c · dispersion)`), so the whole fit is weighted linear least squares with
Gaussian priors, a Huber loss (`HUBER_C`), outlier rejection (`REJECT_SIGMA`, `GROSS_PX` for misidentified lines)
and leave-one-out residuals (the table's "left out" column).

**Identifiability.** Stellar lines alone cannot separate the shift from the velocity. The velocity is fitted only
with at least `MIN_FIXED_ANCHORS` = 2 fixed anchors (sky, telluric, ISM) spanning `MIN_SPAN_PX` = 800 px; else v is
"not determined" (amber note), and with stellar lines only the model is a shift (no scale).

## Models (Auto never leaves the reference far behind)

M0 shift only (default), M1 + scale, M2 + quadratic. Auto starts from M0 and accepts M1 only when BIC improves by
at least `DBIC_MIN` = 10, there are ≥ 4 anchors spanning ≥ `SPREAD_FRAC` = 60 % of the used range, and
|k| ≤ `K_MAX`; M2 likewise over M1 with ≥ 6 anchors and the curvature at the range edges ≤ `Q_MAX_EDGE_PX` = 1.5 px.
Otherwise it stays simpler and says "Scale term not accepted: <reason>". The explicit **Shift and scale** mode uses
a looser prior (`K_SIGMA_FORCED` = 2 %) and no limit; **Manual** is the old target adjustment (manual shift,
Auto-match, free fit from picked lines).

Constants (top of `core/targetcal.py`): `S_SIGMA_PX` 200, `K_SIGMA` 0.2 %, `Q_EDGE_SIGMA_PX` 0.5, `V_SIGMA_KMS` 150,
`SYS_PX` 0.15, `HUBER_C` 1.5, `REJECT_SIGMA` 3, `GROSS_PX` 4, `EMISSION_WEIGHT` 0.3, `DBIC_MIN` 10,
`K_MAX` **0.4 %** (the brief said 0.3 %; with that limit a true 0.3 % scale, which the brief's test expects Auto to
accept, sits exactly on the limit and is rejected half the time), `FLAT_DEV_A` 0.5 Å, `EXTRAP_FRAC` 10 %.
Sky detector (`core/skylines.py`): `NSIGMA_SKY` 5, `FWHM_MIN`…`FWHM_MAX` 2.5…25 px, glow running median 101 px,
`MAX_SHIFT_PX` 80, `MAX_SCALE_ERR` 2 %, `MIN_SKY_LINES` 3, `MATCH_TOL_PX` 3.

## The difference between the two solutions

Δ(x) = λ_target(x) − λ_ref(x) over the used range: the shift at the centre (px, Å), the change of Δ across the
range, its maximum deviation from a constant, and the implied scale change (dispersion ratio − 1). Verdict:
"Pure shift" (deviation < `FLAT_DEV_A`), "Shift plus scale", "Shape differs: curvature of X Å" (M2), or amber
"Not enough anchors to judge the shape" when the used anchors span less than 60 % of the range (the uncovered
range is named). The reference solution's own sky-line agreement is shown next to it.

Diagnostics, never errors: the **star-in-slit offset** (mean telluric D − mean sky D: the star is not centred in
the slit the way the sky fills it) and the reference's sky-line RMS.

## Sky line list and its limits

Hg I 4047, 4358, 5461, 5770, 5791 (mercury street lamps), O I 5577 and [O I] 6300, 6364 (airglow), Na D 5893
(one blend at this resolution), high-pressure sodium blends near 5685 and 6157 (wide, weight 0.3). The lamp lines
are **site dependent** (LED lighting has none) and their strength varies through the night; the HPS blends are
pressure-broadened and their centres depend on the lamp. A clean sky is a normal result. Sky lines fill the slit
evenly; the star does not, so a star off the slit centre is offset from the sky lines (the star-in-slit offset):
the telluric and stellar anchors carry the star's position.

**Wavelengths.** Air values as in NIST ASD for Hg I, O I, [O I], Na I, He I, He II, Ca II K (entered from the NIST
air values; this session could not query NIST online: the project allows only SIMBAD, geocoding and VSX), DIBs from
Hobbs et al. 2008. Telluric "lines" are band heads / centres used only for differential matching. The Balmer values
are the app's own table (`core/wavelength.py`). Unit-tested against the listed numbers (`test_catalogue_values`).

## Storage

The settings are per target in the session: `params.wavelength.per_image.Target.cal = {mode, disabled}` (the
manual shift stays in `…Target.adjust`, applied after the fit). Results go to `wavelength.json` and
`logbook.steps.wavelength.metrics` (docs/DATA.md) and three HISTORY lines in `Target_W.fit`, carried into the
export: model, shift, scale, quad, RMS; anchors by type and v; "target solution = reference + shift X A, flat
within Y A". No SX* keywords.

## The real example (example_spectra/novasrg2026/2026-11-16)

On the straightened frames the detector finds 8 sky lines in the target (Hg 5461, O I 5577, Na 5685, Hg 5770 /
5791, Na 6157, [O I] 6300 / 6364) and 4 in the reference; target − reference per common line: +1.7, +1.8, +1.6,
+2.4 px (mean **+1.9 px**), not the 13.5 px quoted in the brief (that value is not in these files: the `_S` stacks
give the same +2 to +3 px). Against the reference's Balmer solution the target sits **+3.1 px (+3.7 Å)**, Δ flat
within 0.01 Å; the reference solution itself is 1.6 Å off its own sky lines (a star-in-slit effect on the
reference, or its Balmer fit). The used anchors cover 5461–7186 Å only, so the verdict is "Not enough anchors to
judge the shape", not "Pure shift" (tests/test_targetcal.py `test_real_nova_pair_sky_lines_and_pure_shift`).
