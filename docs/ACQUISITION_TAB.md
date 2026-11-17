# Acquisition tab: live sub monitor, good-subs sets, hand-off to Reduce

Ported in spirit (not in code) from SpectroInspector (the author's earlier tool; GitHub code is identical to the local copy, one README-only commit ahead). Do NOT import from it or copy its code. Reimplement the logic below in `core/` with our speed rules (SPEC section 8): nothing image-related runs on the UI thread.

## Scope
IN (Matt, 2026-11-17):
1. Folder polling of the incoming images.
2. Automatic selection of the spectrum rows (the same detection the Extract stage uses).
3. Saturation-prevention plot for the latest sub, with an exposure recommendation versus the SNR target.
4. Automatic list of good subs, for two sets: **Target** and **Reference**.
5. SNR estimate of the stacked good subs (chart).
6. Hand-off of the sets to Reduce > Files as a suggestion (never required).

OUT: logbook, slit diagnostics (FWHM, centroid, asymmetry, slit quality, FWHM-based flagging), gain advice, wavelength calibration tab, calibration-frame set (darks/bias are NOT part of the sets; may come later).

Mockup: the "Acquisition tab" widget from the design conversation (watch strip, latest-sub saturation plot, auto rows, recommendation card, good-subs table, stack SNR chart). Numbers in it are illustrative.

## 1. Polling (core/watcher.py, no Qt in the logic)
- Poll the watched folder every 2 s (setting 1-10 s) in a background thread. Match `*.fits`, `*.fit` case-insensitively, optional filename filter. Existing files at start are known, not processed (option "Process existing files").
- **File must be stable before reading:** size unchanged and mtime older than 1 poll, or it fails to open and is retried next poll (capture software writes the file over a second or more). Never read a half-written file.
- Emits one event per new stable file, in order. Burst of several files: queue them, process sequentially in a worker, never block the UI.
- Settings: watched folder, filter, interval, pause/resume. Persist in settings.json.

## 2. Per-sub analysis (core/subs.py, no Qt)
Input: file path, current rows (or None), camera info. Reads only what it needs (memmap, big-endian int16 + BZERO, strip of rows around the spectrum) and returns a `SubRecord`:
`path, name, time, exptime, gain, temp, peak_adu, peak_col, peak_fill (peak / linearity limit), sat_cols, continuum_snr, n_continuum, col_max (array, decimated for plot), sky (array), rows, flags (list of reasons), role ('target'|'reference'|None), included (bool), user_override (bool)`.
- **Hot-pixel-filtered column maximum:** second-highest pixel per column inside the target rows (falls back to max for one row).
- **Linearity limit:** 80 % of full scale. ASI585MM Pro: 12-bit ADC stored as 16-bit (value x 16), so full scale is 65520, limit 52428. Setting for the fraction. Saturation = columns above the limit.
- **SNR per column:** `signal_e = max(spec,0)*G; noise_e = sqrt(signal_e + N*(bg_e + R^2))`, G in e-/16-bit ADU, R in e-, N = number of target rows, spec = target sum minus sky x N. Continuum SNR = median over continuum columns (columns not dominated by lines; take the central 50 % of the sorted continuum estimate or a median-filter-defined continuum, document the choice).
- **G and R source:** use the frame header first (SharpCap writes EGAINSAV = e-/ADU at the saved bit depth, e.g. 0.06081 at gain 200, and RDNOISE e-, e.g. 3.65). Fall back to the ASI585 table (gain slider to e-/16-bit ADU, read noise), interpolating but never across the HCG boundary at gain 200 (read noise jumps from about 4 e- to about 1 e-). NINA frames have no EGAINSAV: use the table.
- **Auto flags** (reasons shown as chips, never hide the sub): saturated (peak_fill >= 1.0), SNR below mean minus 2 sigma of included subs (needs 3+ subs, sigma setting), fewer than 10 continuum columns. No FWHM flag.
- Performance target per sub: under 100 ms, off the UI thread. Report the measured time in the status line and in tools/perf.py.

## 3. Automatic spectrum rows
- Reuse the Extract stage's auto-placement (core/extract.py) on the first usable sub: collapse a strip, find the spectrum row, place target band and sky bands above and below. Lock the rows for the rest of the night; show chip "Auto, locked from sub N" and a **Re-detect** button.
- Re-check cheaply on each sub: if the profile centroid moved more than 3 px from the locked rows, show a hint chip "Spectrum moved by X px. Re-detect?" and keep the old rows until the user clicks. Never move rows silently.
- Use the saved instrument geometry (tilt) if available; the rows only need to be right to a few pixels for monitoring.
- Rows are editable (numeric fields) and persist for the session.

## 4. Latest-sub saturation plot and recommendation
- Plot: hot-pixel-filtered peak ADU per column (white), sky per pixel (dim), dashed linearity-limit line with label, saturated columns shaded, marker and label at the peak. X is pixel column (wavelength axis not needed here). Zoom/pan as the viewer widget; new subs replace the data without resetting zoom.
- **Recommendation card**, two parts:
  - Saturation headroom: peak as % of the limit, and the longest exposure that keeps the peak under a safety fraction (setting, default 95 % of the limit). Assumes linear scaling with exposure; say so in the tooltip.
  - SNR: current stack SNR from the good subs, the target SNR (setting), and how many more subs at the current exposure, or at the longest safe exposure, reach the target (assume per-sub SNR scales with the square root of exposure when signal-limited).
- If peak_fill >= 1.0, show an unmistakable "Saturated" state and recommend a shorter exposure instead of a longer one.

## 5. Stack SNR chart
- Good subs only, per set (Target / Reference tabs). Stack SNR for n subs is the quadrature sum of per-sub continuum SNRs (state this assumption). Plot measured (solid) against n, the sqrt(N) reference from sub 1 (dotted), a projection to the number of subs needed (dashed, using the mean per-sub SNR), the target line, and the n at which it is reached.
- Keep the transparency hint: three consecutive subs below 0.7 or above 1.5 of the sqrt(N) reference shows a warning chip "Transparency or guiding event?".

## 6. Sets: Target and Reference (manual by design)
- Two name fields, **Target** and **Reference**, each with a **From Plan** button (fills from the Plan tab's current target/reference) and a shared **Paste** button that reads the clipboard and understands the Observing plan text block and the plan JSON (see PLAN_TAB.md), filling both names. Typing is always allowed. The OBJECT header is NOT used to assign subs (Matt: it is often wrong in the capture software); it may be shown as information in the table only.
- A segmented switch "Collecting for: Target | Reference" decides which set new subs join. Matt flips it by hand. A hint chip appears when the header OBJECT or exposure changes between subs ("Looks like a new object. Switch to Reference?"); it never switches by itself.
- Each sub row: checkbox (included), role move menu ("Move to Reference/Target", "Exclude"), flags as chips. Unticking a flagged sub is remembered as a user override (`user_override`), so later flag recalculations do not re-tick it.
- Two tabs on the good-subs table: Target (n of m) and Reference (n of m), each with total exposure. Buttons: Copy list, Save list, **Save sets** (writes the file below; autosave on every change as well).

## 7. Hand-off file: acquisition_sets.json
- Saved in the **session folder** by default (decision: Matt, 2026-11-17). Option "Also save next to the images" (off by default; never modifies existing files). Autosaved on every change.
- Plain, human-editable JSON, version 1:
```json
{
  "version": 1,
  "night": "2026-08-20",
  "folder": "B:\\...\\novasrg2026",
  "target": {
    "name": "V8055 Sgr", "name_source": "plan",
    "good": ["SgrNov32026/Sgrnov32026_00001.fits"],
    "excluded": [ { "file": "SgrNov32026/Sgrnov32026_00003.fits", "reason": "SNR low" } ],
    "exposure_s": 300, "gain": 200
  },
  "reference": { "name": "HD 163955", "name_source": "typed", "good": [], "excluded": [], "exposure_s": 30, "gain": 200 },
  "qc": { "SgrNov32026/Sgrnov32026_00001.fits": { "peak_fill": 0.80, "snr": 36, "size": 16591680, "mtime": 1787282892 } }
}
```
- Paths relative to `folder`; size and mtime recorded so a changed or missing file is detectable. `name_source` is "plan", "typed" or "clipboard". Reading must tolerate missing optional keys and unknown extra keys.

## 8. Reduce > Files changes (suggestion only)
- If `acquisition_sets.json` exists for the session, Files shows one line at the top: "From Acquisition: Target V8055 Sgr, 5 good subs. Reference HD 163955, 9 good subs." with a **Use suggestion** button. The button ticks those files in the existing manual lists and fills the target name (and reference name). Everything stays editable. Nothing is applied automatically.
- Without a sets file Files behaves exactly as it does today (manual selection of folders and frames).
- **Old data:** two buttons in Files:
  - **Load sets file...** opens any acquisition_sets.json.
  - **Scan folder...** runs the same core/subs.py analysis on any folder of FITS files (in a worker, with progress and cancel), groups the subs for the user to assign to Target / Reference / Ignore (header OBJECT and exposure shown as hints only), flags them, and lets the user confirm. Result is shown as the same suggestion line.
- Files never writes back into acquisition_sets.json. A chip shows the provenance ("sets file from 2026-08-21", "folder scan") and missing/changed files in red.
- The only coupling between the two tabs is the sets file format plus `core/subs.py`. Acquisition does not import Files code and vice versa.

## Theme (decided)
No red night palette (Matt, 2026-11-17). The Acquisition tab uses the same theme as the rest of the app. Do not add a night-mode toggle.

## Tests
1. core/watcher: new file detected once; half-written file (growing size) is not read until stable; case-insensitive extensions; filter pattern; existing files not processed by default.
2. core/subs on the nova example (`example_spectra/novasrg2026/SgrNov32026`, 6 x 300 s, and `hd163955`, 10 x 30 s): peak and fill computed; a synthetic saturated column sets peak_fill >= 1.0 and the saturated flag; a synthetic dim sub is flagged by the SNR rule; hot pixel in the rows does not set the peak (second-highest rule).
3. G/R: SharpCap header values used when present (EGAINSAV, RDNOISE); table fallback for a NINA-style header; gain 200 boundary never interpolated across.
4. Rows: auto rows near 1070 for the nova stack and near 1042 for the reference; lock persists; a 5 px shift raises the hint, does not move the rows.
5. SNR: quadrature stack matches a direct stack of synthetic subs within 5 %; projection reaches the target at the expected n.
6. Sets: save/load round trip; unknown keys tolerated; missing files flagged; user_override survives recalculation; OBJECT header never assigns a role.
7. Files: Use suggestion ticks the right files and fills names; Files works with no sets file; Scan folder on the nova folder groups by user assignment and produces the same suggestion; Files never modifies the sets file (compare bytes).
8. UI (pytest-qt): switching "Collecting for" changes where the next new file goes; Paste fills both names from an Observing plan text block and from the plan JSON; no image work on the UI thread (a slow fake sub does not block interaction).
9. Perf (tools/perf.py): per-sub analysis under 100 ms on a 3840x2160 frame; report the measured number.
