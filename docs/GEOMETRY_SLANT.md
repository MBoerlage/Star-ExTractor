# Optional slant correction (Geometry step)

Terms: **tilt** = the whole spectrum is rotated against the sensor rows (already handled). **Slant** = after tilt is fixed, lines of constant wavelength (slit images: sky lines, telluric/lamp lines, dust on the slit) are not exactly perpendicular to the dispersion direction, so vertical features lean (a shear). ISIS calls this "Slant". **Smile** = those lines are curved. Slant is usually small on a fixed instrument.

## Why it matters
Shear shifts a row at distance dy from the spectrum row by k*dy pixels (k = tan of the slant angle). For slant 1 deg, k = 0.0175: a 21-row target band smears lines by about 0.4 px at its edges and sky bands 40 rows away are offset by about 0.7 px, which weakens sky-line subtraction and slightly broadens lines. Negligible for point sources at small slant, relevant for wide bands or larger slant. Always show this estimated impact next to the measured angle.

## Detection (automatic, with fallback)
- Use the sky/background rows (exclude the spectrum band and its wings) of the stacked image. Take row blocks (e.g. 8 blocks of about 100 rows) above and below the spectrum, high-pass each block's column profile (subtract a wide median/Gaussian) to remove the continuum, and cross-correlate each block against a reference block (sub-pixel via parabola on the peak) to get the horizontal shift per block.
- Fit shift against row distance from the spectrum row with a straight line; slope k = tan(slant). Report angle, uncertainty (from the fit), number of blocks used and a quality value.
- If features are too weak or the fit is poor (uncertainty larger than the value, or fewer than 4 good blocks), say "Slant could not be measured" and offer **Draw angle** (user draws a line along a leaning streak, same interaction as the tilt tool, mind the screen aspect ratio: convert using image pixels, not screen pixels).
- The slant of a fixed instrument barely changes: store it in the saved geometry (with tilt and hot pixels) and offer "Use saved geometry"; the measurement from the cleanest stack (usually the longest target stack) can be saved.

## Application
- Optional, default OFF with a checkbox "Correct slant" and a Detect / Draw angle / Angle field card under Tilt, plus the estimated-impact line. Do not auto-apply without the user ticking it.
- Compose tilt and slant into ONE affine transform about the spectrum row (pivot at the detected spectrum row, so the spectrum itself does not move and its wavelength alignment is untouched) and resample once (scipy.ndimage.affine_transform, order 1 or 3). Never rotate then shear in two passes (double interpolation blurs).
- Original | Corrected toggle and the process log must report both numbers. Corrected image caches under the same hash as tilt (angle, slant, pivot row).

## As built (2026-11-17)
- `core/geometry.detect_slant` on the tilt-straightened frame (flip + hot pixels first): blocks of 100 rows, ±40 rows kept clear of the spectrum, up to 8 per side; the block profile is the median over its rows on the lit columns (±64 px), high-passed (minus a σ=12 px Gaussian). Neighbouring blocks are cross-correlated against each other (±25 px, parabola sub-pixel); a single reference profile smeared out at large slant. Each pair with correlation ≥ 0.3 and no edge peak gives a slope shift/row-distance; k = weighted mean (weights correlation × distance²), σ from their scatter.
- "Could not be measured" when fewer than 4 good pairs, or σ > max(|angle|, 0.05°). The 0.05° floor lets a well-measured slant of ~0° count instead of always failing the "uncertainty larger than the value" rule.
- `straighten(img, tilt, slant, centre)`: output→input matrix R·S, S = [[1, 0], [tan(slant), 1]] about (spectrum row, x centre); slant 0 gives R exactly (byte-identical to the old rotation). Header keys `GEO_SLNT`, `GEO_SSRC`; the stage refuses an input that already has `GEO_TILT`.
- Session params: `slant_on` (default false), per image `slant`/`slant_source`, `saved_slant` from the instrument profile (`slant_deg`, `slant_date`).

## Tests
- Synthetic frame with known tilt and slant (vertical sky lines leaning by 0.5 deg and 1.5 deg): detection recovers the angle within 0.05 deg; after correction the lines are vertical within 0.05 deg and the spectrum row/profile is unchanged within 0.1 px.
- No-feature frame: reports "could not be measured", never a random angle.
- Composite transform equals tilt-only when slant = 0 (byte-identical to the current output).
- Saved geometry round trip includes slant; applying twice is prevented (the corrected image is not re-corrected).
- Perf: correction on 3840x2160 within the existing Geometry budget (report the number).
