# BASS Project calibration: verified reference (from Matt's real export)

Source: BASS Project v0.1.9.9, frame `Gam Cas_00004.fits`, exported 2026-11-16 (Matt's own Star'Ex LR + ASI585MM data). Fixtures: `tests/fixtures/bass_coefficients_GamCas_00004.txt` and `tests/fixtures/bass_calibration_export_GamCas_00004_excerpt.txt`.

## 1. Coefficients box (VERIFIED)
```
348.381371973896, 0.120309271523351, 6.05776509593276E-08
```
- Comma + space separated, **offset first**, full double precision (15 significant digits), exponent form `E-08` for tiny terms. Wavelength unit **nm**. `f(x) = a + b·x + c·x²`.
- **Pixel origin:** `f(0) = a` (the export lists Pixel 0 → 348.381372). The pixel number is the same coordinate BASS shows in its calibration table; treat it as the 0-based column index of the 1D profile and keep it as one constant `PIXEL_ORIGIN = 0`.
- App behaviour: **Copy** writes exactly this layout (repr of float64, `, ` separator). **Paste to apply** accepts 2 or 3 comma/space/semicolon separated numbers, any exponent notation, and reports the order from the count of terms.

## 2. Calibration points (VERIFIED)
| # | Pixel | Wavelength (nm) | Error (nm) | Line |
|---|---|---|---|---|
| 1 | 711.79 | 434.047 | 0 | Hγ |
| 2 | 1144.32 | 486.133 | 0 | Hβ |
| 3 | 2555.98 | 656.2852 | 0 | Hα |

- BASS's line wavelengths (nm): Hγ 434.047, Hβ 486.133, Hα **656.2852**. Use these three exactly (the app's Hα is 6562.852 Å, not 6562.80). Hδ 410.174 and Hε 397.007 nm are the standard air values (BASS's own values for them are unverified).
- With exactly 3 points a quadratic passes through all of them, so BASS shows Error 0. That is an exact fit, not a validation. The app's own rule (order ≤ number of lines − 2) therefore offers Linear only with 3 lines and Quadratic from 4 lines; for the saved-solution "shift only" mode this does not matter. Show a hint "3 lines: quadratic would fit exactly (no check)" instead of silently blocking, and let Matt override it explicitly.
- Error column = fit − entered (nm).

## 3. Linear and polynomial terms in the export (VERIFIED)
Linear terms (nm): `348.2492888`, `0.120513715`. Polynomial terms (nm): `348.381372`, `0.120309272`, `6.06E-08` (rounded; BASS prints c with only 3 significant digits in the export but the box has full precision).
Small differences: re-fitting from the 2-decimal pixels shown gives c ≈ 6.0574E-08 and linear 348.24930, 0.1205137; BASS carries more digits in its internal pixel positions. Tests must therefore compare **fits to ~1e-4 relative** and **evaluation of the stored coefficients exactly**.

## 4. Export file layout (`.xls`, really tab-delimited text) (VERIFIED; **not implemented**: Matt removed the export from the app on 2026-11-16, kept here for reference)
Tab-separated; every line padded with tabs to 5 columns.
```
BASS Project Calibration Export
Version<TAB>0.1.9.9
Date<TAB>dd/mm/yyyy hh:mm:ss
<frame file name>
(blank)
Calibration Point Data Analysis
(blank)
Point#  Pixel  Wavelength (nm)  Error (nm)
1 …  (one row per calibration point)
(blank)
Linear terms (nm)
1<TAB>a_lin        2<TAB>b_lin
(blank)
Polynomial terms (nm)
1<TAB>a  2<TAB>b  (3<TAB>c if quadratic)
(blank)
Pixel  Linear fit  Poly fit  Poly-Linear  Dispersion (nm/px)
0, 5, 10, … every 5 px up to the last column (3835 for a 3840-px frame)
```
- Columns: `Linear fit` = a_lin + b_lin·x; `Poly fit` = f(x); `Poly-Linear` = difference; **`Dispersion` = f(x) − f(x−1)** (one-pixel backward difference; blank on the pixel-0 row). Checked: f(5)−f(4) = 0.120309817 ✓.
- Numbers about 9 decimals (`0.120309817`), `6.06E-08` style for tiny values. The app's export should reproduce this layout; the version/title line may say "Star ExTractor" instead of "BASS Project" if Matt prefers, but keep the column order so the file can still be compared side by side.

## 5. Consequences for the app (VERIFIED from the numbers)
- **Dispersion is about 1.2 Å/px (0.1203 nm/px), not 1.85 Å/px** as shown in the mockups (illustrative only). Across the 3840-px frame the solution covers **348.4–811 nm**; the publishing band 390–730 nm is roughly pixels **346 to 3167**.
- Predicted pixels from this solution for the other Balmer lines: Hε 397.007 nm ≈ **404**, Hδ 410.174 nm ≈ **513.5**, Hγ ≈ 711.8, Hβ ≈ 1144.3, Hα ≈ 2556.0.
- **Seed the instrument profile with this solution** (nm, 0-based pixel, order 2): `a=348.381371973896, b=0.120309271523351, c=6.05776509593276E-08`, date 2026-11-16, instrument Star'Ex LR + ASI585MM, from `Gam Cas_00004.fits`. "Refine the saved solution (shift only)" starts from it. Auto-find for "Fit freely" uses the linear guess 0.1203 nm/px ± 15 %.
- Because Hε is near pixel 404 and the blue end of a hot star is faint, treat Hε as optional in matching.

## 6. Required tests (M4)
1. Evaluate the stored coefficients at the three pixels: errors < 3e-5 nm (Hα +2e-5, others smaller).
2. Dispersion column: `f(5) - f(4)` equals 0.120309817 to 1e-9.
3. Quadratic fit through the three rounded points reproduces a,b,c within 1e-4 relative; linear fit reproduces 348.2492888 / 0.120513715 within 1e-4 relative.
4. Copy → Paste round trip returns identical floats.
5. ~~Export writer output matches the excerpt fixture~~ (dropped with the .xls export). Was: line for line for the header, points, terms and first 7 pixel rows (numeric tolerance 1e-8), and the last row at pixel 3835.
