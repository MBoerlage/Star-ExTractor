# Spectrum quality checks (Export tab, "Test" step)

Goal: before publishing to AAVSO AVSpec, flag a bad wavelength calibration or poor data, the way the AVSpec validation team does by eye (they use the telluric list in the manual, appendix A.1). The AAVSO automated checker only validates the header and that the data opens, so these checks are our own and advisory.

## Decisions (Matt, 2026-11-17)
- Quality checks run **only when the Test chevron is clicked** on the Export tab (not on tab open, not on every edit). The existing cheap header checks keep their current behaviour.
- Warnings are **never written to the FITS header**. Acknowledged warnings go into the export log (`<name>_log.txt`, the process log that is not submitted) and the session log only.
- Updated (Matt, 2026-11-17, Publish redesign): `err` (the AAVSO header/data checks) always blocks the AVSpec save until fixed in Collect data, with no override. `warn` blocks the AVSpec save until acknowledged with one tick, "I have read the N warnings", in the AAVSO AVSpec tile of Publish (Matt: it unlocks the AVSpec save only). The other Publish tiles (1D FITS, CSV, PNG) are never blocked.

## Where it lives
- `core/quality.py` (no Qt): pure functions, input = wavelength array, flux array, optional wavelength-stage result, optional frame stats; output = list of `QCResult`.
- `QCResult`: `id`, `title`, `status` in {`pass`, `warn`, `info`, `na`}, `value` (number or short text), `detail` (one line), `threshold`.
- `ui/screens/export.py`: the Test step gets a second block "Spectrum checks" under the existing AVSpec checks, filled when Test is clicked. Plot overlay shows detected telluric dips (small markers) next to the telluric lines already drawn.
- Thresholds are in `settings.json` under `qc` with the defaults below, editable in the settings dialog.

## Checks
| id | Check | Method | Warn when (default) |
|---|---|---|---|
| tell_align | Telluric alignment | Target spectrum, telluric features kept. For each feature group in range (O2 B ~6870 A, O2 A ~7600 A, H2O ~7190 A and the weak H2O near Ha), divide by a linear local continuum, find the dip position (parabola on the minimum, or cross-correlation with the line list convolved to the instrument resolution), compare with the expected position. Report each offset and the median. Skip a feature if it is outside the cropped range, too shallow (depth < 5 %) or inside a masked region. Telluric lines are fixed in the Earth frame, so this does not depend on the star. | abs(median offset) > 2.0 A; strong warn > 4.0 A |
| balmer_consistency | Balmer consistency | Measure line centres for Ha, Hb, Hg (Gaussian/centroid within +-15 A of the expected wavelength; emission or absorption, whichever is present). Convert offsets to km/s. If all share one shift, report "shared shift X km/s (stellar velocity, not calibration)". If shifts differ or trend with wavelength, warn. Report-only mode for nova or P Cyg-like emission (user toggle on the card, default auto: report only when the line is not a clean absorption/emission peak). | shifts differ by > 3 A, or slope across lines > 1 A per 1000 A |
| fit_quality | Calibration fit | From the Wavelength stage: RMS in px, number of lines, order, dispersion vs expected 1.20 A/px (+-5 %). | RMS > 0.5 px, quadratic from exactly 3 lines (no validation), dispersion off by > 5 % |
| snr | Signal to noise | Median flux / local scatter (after subtracting a smooth fit) in 5500-5600 A, or the nearest clean window if cropped. | S/N < 20 (setting) |
| sanity | Data sanity | NaN or inf; fraction of negative samples; edge spikes (response near 0 at 3900 or 7700 A); single-sample outliers (> 6 sigma vs a median filter); saturated input frames (from Files stage stats). | any NaN/inf; negatives > 2 %; spikes; saturation |
| header_data | Header vs data | NAXIS1 x CDELT1 reproduces the wavelength range; CRVAL1 equals the first sample's wavelength; CUNIT1 Angstrom. | any mismatch |
| resolution | Resolution | Measured FWHM from the Wavelength stage vs the stated resolving power (default 500). | differ by > 50 % |

Status `na` = the check cannot apply (feature out of range, no wavelength-stage result); shown greyed, never a warning.

## Overriding
- One tick in the AAVSO AVSpec tile of Publish (not in the Test step), "I have read the N warnings", shown only when there are warnings and no errors. Until it is ticked the AVSpec tile in Publish does not save and shows why; once ticked the Publish chip turns amber ("N of M checks pass · N warnings acknowledged").
- The tick resets on every Test run and whenever the data changes (re-reduced spectrum or edited header fields: the data signature the Test step already uses).
- The acknowledgement and the warning texts go to the session log, and so into `<name>_log.txt` next to the AVSpec file. Nothing goes into the FITS header, no HISTORY line.
- Errors cannot be acknowledged.

## Reference values measured on the accepted spectrum
From `example_spectra/novasrg2026/SgrNov3_2026_stacked_1D.fit` (accepted by AAVSO, BASS calibration, R ~ 500). Deepest samples on a linear local continuum: O2 A band 7602 A (~21 % of continuum), O2 B band 6873 A (~66 %), H2O band 7187 A (~57 %), weak H2O near Ha 6520 A (~86 %). These are single measurements on one spectrum, so use them as expectations to check the detector against, then tune the tolerance. Do not hard-code them as truth: derive expected positions from the telluric CSV and convolve to the instrument resolution, then compare the result with these values.

## Tests
1. The accepted nova spectrum passes with no `warn` on tell_align, fit_quality-independent checks, snr, sanity and header_data.
2. Same spectrum with the wavelength axis shifted by +3 A: tell_align warns. Shifted by +0.5 A: it does not.
3. Same spectrum with a stretch (CDELT1 +3 %): tell_align or fit checks warn.
4. White noise: snr and sanity warn; tell_align reports `na` or info (features too shallow).
5. A spectrum with NaNs and a spike at 3900 A: sanity warns and names them.
6. UI (pytest-qt): quality results appear only after clicking Test; opening the tab or editing a field does not run them; Acknowledge + Publish writes the note to the log and does not touch the FITS header (assert the header has no QC keys or HISTORY lines from QC).
7. Both thresholds and all QC text are in settings/log only; the exported FITS is byte-identical with and without acknowledged warnings.
