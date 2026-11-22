# Worked example: V8055 Sgr (Nova Sgr 2026 No. 3), reduced in BASS, submitted to AAVSO

Location: `example_spectra/novasrg2026/` (ground truth for M2–M6 acceptance tests; never modify).

## Download
The real frames (about 790 MB) are **not in the repository**. Download them from:

**<ADD THE DOWNLOAD LINK HERE (for example a GitHub Release asset or a cloud folder)>**

and unpack them so that this folder exists: `example_spectra/novasrg2026/` (with `SgrNov32026/`, `hd163955/`,
`darks/`, `bias/`, …). The tests marked `realdata` use it and are skipped when it is missing. For a quick walk
through every step without downloading anything, use the small synthetic night instead:
`StarExtractor.exe --make-example <folder>` (or `python -m starextractor.app --make-example <folder>`).

## Files
| Path | What it is |
|---|---|
| `SgrNov32026/Sgrnov32026_0000{1..6}.fits` | 6 raw lights, nova, 300 s each, gain 200, offset 3, 3840x2160 int16 + BZERO 32768 (SharpCap). Plus `*.CameraSettings.txt` |
| `hd163955/hd163955_0000{1..10}.fits` | 10 raw lights, reference star HD 163955, 30 s each |
| `SgrNov3_2026_stacked.fit`, `hd163955_stacked.fit` | BASS stacked 2D, float32 big-endian, 3840x2160, no WCS (CDELT1=0). Calibration targets for our Files stage |
| `SgrNov3_2026_stacked_1D.fit` | BASS 1D extraction, 3331 px, linear WCS. Target for our Extract + Wavelength stages |
| `InstrumentResponse_from_HD163955.dat` | BASS response curve, 848 rows, 3789-8024 A, 5 A step, 2 columns (lambda, relative response), peak 1.0 at 6199 A. Target for our Response stage |
| `submitted/_v8055sgr_20260821_155_CSagan.fit` | The file actually submitted to AAVSO. Target for our Export stage. (Observer name redacted in these docs; the real file name differs, so tests locate it with `submitted/*.fit`.) |

| `bias/` | 11 frames, NINA 3.2, exposure 0.001 s, gain 251, offset 20, sensor -10 C, dated 2025-06-04 |
| `darks/` | 11 frames, NINA 3.2, 300 s, gain 251, offset 20, sensor -7.4..-7.8 C, dated 2025-06-04 |

No flats. Stack headers say Light=6/Dark=1/Bias=1 (nova) and Light=10/Dark=2/Bias=1 (reference), i.e. BASS used these library frames (or similar), possibly combined into one master each.

### Calibration mismatch (measured, important for M1)
| | gain | offset | median ADU | temp |
|---|---|---|---|---|
| nova lights (300 s) | 200 | 3 | 384 | -5.5 C |
| reference lights (30 s) | 200 | 3 | 352 | -5.8 C |
| master bias (11) | 251 | 20 | 2064 | -10 C |
| master dark (11 x 300 s) | 251 | 20 | 2080 | -7.5 C |

- **Raw subtraction is wrong:** light minus dark has median -1696 ADU. The library frames sit on a pedestal ~1700 ADU higher than the lights because of different gain/offset.
- **Dark minus bias is pedestal-free:** median +32 ADU per 300 s at -7.5 C, robust sigma 47, 99.9th percentile 528, 102,703 pixels above +200 (hot pixel pattern, ~1.2 % of the sensor). This is the usable thermal/hot-pixel template.
- **Rule for the Files stage ("Match pedestal", default ON when settings differ beyond tolerance):** build `dark_signal = master_dark - master_bias`, subtract that from the lights, never subtract the raw master bias or raw dark. The light's own pedestal stays untouched. Show a warning chip "Calibration frames taken at gain 251/offset 20, lights at 200/3: using dark-bias only".
- **Gain scaling (option, default OFF until verified):** hot-pixel ADU scales with gain, ZWO gain unit is 0.1 dB so 251 vs 200 is about a 1.8x amplitude difference (verify the ZWO gain formula before shipping). Provide a "scale dark signal by gain ratio" toggle and compare both results against the BASS stacked image on hot-pixel residuals.
- **Gain scaling VERIFIED (M1, 2026-11-16) → default ON when Match pedestal is active.** Ratio = 10^((g_light − g_dark)/200) = 0.556 for 200 vs 251. Isolated pixels (|p − median 3×3| > 200, rows 200–1900, cols 200–3600) in the 6 × 300 s nova stack: raw 20 254 hot / 4 641 cold; dark−bias unscaled 14 234 / **18 187** (over-subtracted ~1.8×); dark−bias × 0.556 **8 678** / 6 026; BASS stack 12 271 / 1 934. The 30 s reference barely changes (dark scaled ×0.1). Remaining isolated pixels are for the step-2 hot-pixel filter.
- Temperature differs by ~2 C (dark -7.5, lights -5.5): negligible vs gain effect; record it but do not scale.
- The bias exposure is 0.001 s, not 0: the calibration matcher must accept bias with the camera minimum exposure.
- Header dialect differs: NINA writes IMAGETYP='BIAS'/'DARK', EXPOSURE+EXPTIME, no FRAMETYP; SharpCap writes IMAGETYP/FRAMETYP='Light', no EXPOSURE. Filenames from NINA carry temp and exposure (`2025-06-04_11-19-21__-7.50_300.00s_0000.fits`). Frame typing must use the header first, filename second.

## Facts measured from the data
- Nova: 6 x 300 s raw frames, matching the BASS stack (EXPTIME2 '6 x 300 s'). Reference: 10 x 30 s raw, matching its stack.
- Spectrum row in stacked 2D: nova ~1070, reference ~1042. Centroid drifts ~4.6 rows over x=300..3500 (nova), so tilt/curvature is small but present. BASS header says `HISTORY TILT 0.7566`: most likely the rotation (degrees) BASS had ALREADY applied to these stacked images, which explains why only ~0.08 deg remains. Our app detected -0.662 deg on a raw stack of another target, the same order of magnitude, so raw frames of this instrument tilt by roughly 0.7 deg. Do not apply 0.7566 again to the BASS stacks.
- **Corrected (M2, 2026-11-16): the BASS stacks are already rotated.** Raw single frames and our own stacks are tilted **−0.67° (nova) and −0.73° (HD 163955)** (rows decrease with X: nova ridge 1077 at x=1359 → 1052 at x=3548). BASS `TILT 0.7566` is in **degrees**: the rotation BASS applied while stacking. The ~0.08° measured above is only BASS's residual (0.7566 − 0.67 ≈ 0.09). After our own correction the nova ridge is flat to 0.63 px (BASS stack: 3.3 px) and HD 163955 to 0.47 px over the part with ≥ 35 % of peak signal; its faint blue end (x < 700) curls by up to ~3 px, which a rotation cannot remove.
- BASS 1D wavelength solution is LINEAR: CRVAL1=4000 A, CDELT1=1.20117199 A/px, CRPIX1=1, 3331 px -> 4000-8001 A. (The Gam Cas quadratic is a different night/solution; this one was resampled to a linear grid with a 4000 A start. Our Wavelength stage should produce an equivalent grid.)
- The 1D data in the submitted file is IDENTICAL to the BASS 1D file (checked bit for bit). It is NOT normalised (BSS_NORM='none', peak ~9146 at Halpha), and no telluric removal (BSS_TELL='none').
- **Corrected (M3, 2026-11-16): the BASS 1D / submitted file IS response-corrected.** Our own extraction (sum of rows, sky subtracted, detector counts) divided by the BASS 1D follows `InstrumentResponse_from_HD163955.dat` to within ~2 % at every wavelength (ratio 0.194 vs response 0.202 at 4100 Å, 0.994 vs 0.990 at 6000 Å, 0.597 vs 0.598 at 7900 Å). Correlation of our counts with BASS 1D × response: r = 0.99905. So AAVSO received a response-corrected, not normalised spectrum. Export should default to response-corrected (as BASS did), keep "no response applied" as an option, and state either in the header/HISTORY.
- Hbeta, Hgamma and Halpha emission peaks fall at predicted pixels (Halpha ~px 2133 => 6562 A). Good test for auto-fit and for the wavelength-grid check.
- Response curve rises smoothly from 0.037 at 3789 A to 1.0 at 6199 A, then falls to 0.53 at 8024 A. Use as plausibility check for our response (shape, not exact values).

## Submitted AAVSO header (what BASS wrote; the real accepted example)
```
SIMPLE=T BITPIX=-32 NAXIS=1 NAXIS1=3331 BSCALE=1.0 BZERO=0
SWCREATE='BASS Project 1.9.9 Beta 08F  64bit'
CCD-TEMP=-5.6833 INSTRUME='ZWO ASI585MM Pro'
DATE-END='2026-08-21T04:12:59.852148' DATE-OBS='2026-08-21T03:42:58.415266'
EXPOSURE=1800 EXPTIME=1800.0 EXPTIME2='6 x 300 s'
BAS_MEX='2026-08-21T03:57:58.415266' JD-MID=2461273.66525463 JD-OBS=2461273.654843
COMMENT Stack frame counts. Light=6 Dark=1 Bias=1 Flat=0 FlatDark=0
CUNIT1='Angstrom' CDELT1=1.20117199420929 CRVAL1=4000 CRPIX1=1 CTYPE1='Wavelength'
CBLACK=659.5695 CWHITE=9146.211
OBJNAME='V8055 Sgr'  (comment: Nova Sagittarii 2026 No. 3)
OBSERVER='Carl Sagan'
BSS_INST='200mm f/4 + StarEx LR 300l/mm + ASI585MM'
BSS_SITE='<observer site>'
BSS_ESRP=500 BSS_ITRP=500 BSS_VHEL=0 BSS_TELL='none' BSS_COSM='none' BSS_NORM='none'
HISTORY Prepared for ARAS/BeSS submission; spectral samples unchanged.
HISTORY Acquisition: 6 x 300 s; SharpCap 4.1.14310.0; gain 200.
HISTORY Reduction/wavelength calibration: BASS Project 1.9.9 Beta 08F.
```
Observations for FORMATS.md / Export:
- **CTYPE1='Wavelength' with CUNIT1='Angstrom' was accepted** (resolves that VERIFY item).
- OBSERVER here is the person's name, not an obscode. The manual says obscode; the submitted file is a real-world case, so make OBSERVER configurable and default to what the AAVSO manual says; verify against AVSpec on first upload.
- BSS_ESRP/BSS_ITRP = 500 were accepted as resolving power (the instrument value for this setup). Make these settings, default 500.
- Telescope in BSS_INST is a 200 mm f/4 (not the 790 mm f/3.9 inferred earlier from dark frames). Treat the app setting as authoritative; do not hard-code optics.
- Dates use ISO with microseconds and no trailing Z.
- Header has no airmass keyword and no target RA/Dec; AVSpec accepted it, but add what the manual lists as mandatory/recommended in our own output and flag differences in the preflight check.

## Filename convention (hypothesis, verify against AAVSO guide)
`_v8055sgr_20260821_155_CSagan.fit` = `_<object lowercase no spaces>_<UT date yyyymmdd>_<thousandths of day>_<observer name>.fit`.
The `155` equals the start of exposure as a fraction of the UT day (03:42:58 -> 0.1548 -> 155). Implement as a default pattern with the pieces editable, and confirm against the AAVSO technical guide before relying on it.

## Camera setting differences from earlier assumptions
Sample lights use gain 200, offset 3 (earlier Gam Cas set used 250). Camera: ASI585MM Pro, 2.9 um pixels, EGAIN 0.9729, RDNOISE 3.65, BIASADU 281 in the SharpCap header, sensor -6.8 C vs set -10 (cooler 96 %). Read these from the raw header; do not hard-code.

## Acceptance tests this enables
1. Extract 1D from `SgrNov3_2026_stacked.fit`; correlate with `SgrNov3_2026_stacked_1D.fit` (expect r > 0.99 after resampling to the 4000 A / 1.20117 A grid).
2. Wavelength auto-fit on that 1D: Hbeta, Hgamma, Halpha within +-1 px of BASS positions; CDELT1 within 1 %.
3. Response from HD 163955 stacked vs `InstrumentResponse_from_HD163955.dat`: same shape, peak near 6200 A, ratio within ~10 % over 4500-7500 A after matching normalisation.
4. Export: our header contains every key of the submitted header that AVSpec requires; filename matches the pattern; round-trip read with astropy gives 3331 samples, CRVAL1=4000, CDELT1=1.20117.
5. Raw-lights path: stack the 6 raw nova frames and 10 reference frames, once with no calibration and once with dark-bias "Match pedestal" and check the spectrum row, tilt direction and extracted line positions match the BASS stacked images to within a few pixels.
6. Calibration: after Match pedestal, the calibrated stack background median stays near the light pedestal (~350-400 ADU, not negative) and the hot-pixel pattern visible in the raw stack shrinks; compare residual hot pixels to `SgrNov3_2026_stacked.fit` (BASS result).
