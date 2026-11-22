# Wavelength step: the target's own solution (target calibration v2, 2026-11-19)

The reference star's Balmer fit is unchanged (`core/wavelength.py`, refine / free / fixed). The **target** now gets
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
