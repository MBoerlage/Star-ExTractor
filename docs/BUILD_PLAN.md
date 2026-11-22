# Build plan

Work milestone by milestone. Each ends with: `pytest` green, perf harness numbers, app launched on real data, 5-line status to Matt, a git commit.

## M0 – Skeleton, shell, infrastructure
- `git init`, `pyproject.toml`, venv launcher, `ruff`, `pytest`.
- `ui/theme.py`, main window: top bar, tab bar, ribbon widget with the 4 states, empty screens per step, Log panel.
- `Session` model + `session.json` persistence, stage status/invalidation chain, in-memory + disk cache.
- Job runner with cancel + progress; a fake long job proves the UI never blocks.
- `tools/synth.py`: synthetic 3840×2160 frames (tilted spectrum, sky, Balmer lines, noise, hot pixels, darks, bias).
- `tools/perf.py` skeleton.
**Accept:** app opens in < 1 s, ribbon changes state when a fake stage is run/invalidated, synthetic data generator produces frames that mimic `docs/DATA.md`.

## M1 – Files (step 1)
- Fast FITS reader (memmap), header scan, frame-type normalisation, per-frame table (time, exp, peak ADU, saturation flag).
- Master library (index, auto-pick rules from SPEC §4), Create masters bins, dark scaling rules, mismatch warnings with "Use anyway", pedestal-match option.
- Stack (strip-wise sigma-clip mean / median), progress, `Target_S.fit` / `Reference_S.fit`.
- Target name, SIMBAD (Sesame) with offline fallback, manual RA/Dec, port `simbad.py` tests.
**Accept:** real data from `example_spectra/novasrg2026` (see `docs/EXAMPLE_NOVA.md`): 6 × 300 s nova + 10 × 30 s HD 163955 stacked in < 4 s; master dark/bias build (11 frames each) < 3 s; with Match pedestal the calibrated background stays near 350–400 ADU (never negative) and hot pixels shrink versus the uncalibrated stack; unticking a frame changes the stack; a dark without bias at a different exposure shows the error. Compare stacks to `SgrNov3_2026_stacked.fit` and `hd163955_stacked.fit` (BASS results).

## M2 – Geometry (step 2)
- Image viewer widget (pyqtgraph) with Y-zoom, row ruler, 50 px frame strip with draggable window, fit chips, readout, Original | Corrected toggle.
- Tilt Detect, Draw angle, apply; hot pixels; smile (optional); saved-geometry reuse and residual check.
**Accept:** on the nova and HD 163955 stacks the detected tilt is small (~0.08° slope: spectrum row drifts ~4.6 px over x = 300–3500, nova row ~1070, reference ~1042; BASS's `TILT 0.7566` is most likely a rotation BASS already applied to its stacks, so only ~0.08° is left there; raw frames of this instrument tilt by roughly 0.7°) and the corrected spectrum rows are flat within 1 px; **Measured in M2:** the raw tilt is −0.67° / −0.73°; BASS's 0.7566 is degrees and its stacks are pre-rotated (see EXAMPLE_NOVA.md), so the ~0.08° is BASS's residual. Acceptance is checked as: detected raw tilt matches single raw frames, and after correction the ridge is flat within 1 px where the signal is ≥ 35 % of peak; wheel-zoom and strip drag run at ≥ 60 fps; Draw angle gives the same angle at ×1 and ×16.

## M3 – 1D Extract (step 3)
- Draggable bands (reuse viewer), auto-place, sky model, extraction, live 1D preview, copy bands to reference.
**Accept:** target and reference profiles extracted and correlated (r > 0.99 after resampling) with `SgrNov3_2026_stacked_1D.fit`; moving a band edge updates the profile preview within one frame; sky subtraction removes a constant pedestal (test with the offset-mismatched sample).

## M4 – Wavelength (step 4)
- Balmer line detection/matching, shift-only refine, free fit, table with errors, Linear | Quadratic, BASS-style coefficients box (copy/paste), CRVAL1/CDELT1 (`.xls` export removed by Matt), FWHM and R, resample to a linear grid, instrument-profile save.
**Accept:** on the HD 163955 and V8055 Sgr profiles all visible Balmer lines are found automatically (Hβ, Hγ, Hα within ±1 px of BASS, CDELT1 within 1 % of 1.20117 Å/px), RMS reported; output grid matches BASS (4000 Å start, 1.20117 Å/px); copy → paste of the coefficients reproduces the same solution; order is blocked above (lines − 2). Tests 1–4 in `docs/BASS_CALIBRATION.md` §6 must pass (test 5, the `.xls` export, was dropped with the feature); the profile is seeded from the real Gam Cas solution.

## M5 – Response (step 5)
- Library handling (parse `reference_spectra\*.dat`, spectral-type → file mapping with fallback and dropdown override, smooth the observed reference to the library's ~10 Å resolution), masks, smoothing, airmass correction, normalise, crop, plots with show/hide checkboxes, save response.
**Accept:** our response from HD 163955 has the same shape as `InstrumentResponse_from_HD163955.dat` (peak near 6200 Å, ratio within ~10 % over 4500–7500 Å after matching normalisation); corrected reference of an A0V star is flat to within a few % between 4000 and 7000 Å outside masked regions; the target shows Balmer lines at the right wavelengths; "No response" bypass works.

## M6 – Export
- Live FITS header preview, writer for the AVSpec file (ARAS removed by Matt), the **Check VSX name** button (verify the VSX endpoint first), pre-flight checks (airmass from coordinates + site + JD, ISOT times, header per FORMATS.md), target-only plot with Balmer + telluric markers, X zoom (chips, from/to, wheel, overview window), Å | nm, `.dat`/CSV, PNG, process log. Verify FORMATS.md items and record the results.
**Accept:** file opens in astropy and BASS/ISIS-style tools; header has every key listed in FORMATS.md and every key of the accepted `submitted/_v8055sgr_20260821_155_CSagan.fit` (see EXAMPLE_NOVA.md); filename follows the verified pattern; "no response applied" export works as an option (the submitted file turned out to be response-corrected but not normalised, see EXAMPLE_NOVA.md); Matt runs the AAVSO submission pre-check and reports back.

## M7 – Polish
- Run all, session reopen with lazy loading, error handling pass, keyboard navigation, window resizing, settings dialog, `Start_StarExtractor.bat`, README, optional PyInstaller build.
**Accept:** full run on real data in < 10 s with cached masters; reopening yesterday's session shows the right ribbon instantly; every budget in SPEC §8 met.

## OPEN items (ask Matt only for these)
0. ~~Publishing crop~~ **Decided:** 3900–7700 Å, so the O₂ A band stays in; AAVSO telluric lines are shown on the Export tab as a visual check.
1. ~~BASS coefficient string~~ **Resolved:** Matt supplied a real coefficients string and a full BASS export; see `docs/BASS_CALIBRATION.md` and `tests/fixtures/`.
2. ~~Response library~~ **Resolved:** Matt supplied the Pickles/HILIB templates in `starextractor/reference_spectra/` (108 `.dat` files plus README.HILIB). The odd `w…` and `r…` templates (metal-weak/metal-rich variants) were deleted by Matt, so only the standard spectral-type files remain.
3. ~~Reference result for validation~~ **Resolved:** `example_spectra/novasrg2026` holds raw frames, BASS stacks, 1D, response and the submitted AAVSO file; see `docs/EXAMPLE_NOVA.md`.
4. **Lamp frames:** will Matt take neon/lamp frames at all? (Affects whether smile and "Use a lamp frame" need to be fully built in v1.)
5. ~~ARAS file naming convention, upload route~~ **Dropped:** ARAS export removed (Matt, 2026-11-16). and the site-position keywords (see FORMATS.md VERIFY items; Claude Code researches, Matt confirms). The filename pattern in EXAMPLE_NOVA.md is a hypothesis to check against the AAVSO guide.
6. **Observer details** for the first run: AAVSO obscode, registered site and equipment names (stored in settings only).
