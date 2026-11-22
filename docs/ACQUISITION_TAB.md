# Acquisition tab: live sub monitor, good-subs sets, hand-off to Reduce

Ported in spirit (not in code) from SpectroInspector (the author's earlier tool; GitHub code is identical to the local copy, one README-only commit ahead). Do NOT import from it or copy its code. Reimplement the logic below in `core/` with our speed rules (SPEC section 8): nothing image-related runs on the UI thread.

## Scope
IN (Matt, 2026-11-17):
1. Folder polling of the incoming images.
2. Automatic selection of the spectrum rows (the same detection the Extract stage uses).
3. Saturation-prevention plot for the latest sub, with an exposure recommendation versus the SNR target.
4. Automatic list of good subs, for two sets: **Target** and **Reference**.
5. SNR estimate of the stacked good subs (chart).
6. Hand-off to Reduce › Select through the stack files themselves (rows, name, position in the header; the sets file as a fallback). Since 2026-11-18 Files only picks the two stacks.
7. (2026-11-18) Stack selected: stack the ticked subs of a set into `Stacks\<night>\`, with a Pre Processing settings tab (masters and stacking settings), and notes per set. The project folder is gone: the tab keeps its state in `Stacks\<night>\acquisition.json`.

OUT: slit diagnostics (FWHM, centroid, asymmetry, slit quality, FWHM-based flagging), gain advice, wavelength calibration tab, calibration-frame set (darks/bias are NOT part of the sets; may come later).

Mockup: the "Acquisition tab" widget from the design conversation (watch strip, latest-sub saturation plot, auto rows, recommendation card, good-subs table, stack SNR chart). Numbers in it are illustrative.

## 1. Polling (core/watcher.py, no Qt in the logic)
- Poll the watched folder every 2 s (setting 1-10 s) in a background thread. Match `*.fits`, `*.fit` case-insensitively. Existing files at start are known, not processed (option "Process existing files").
- **File must be stable before reading:** size unchanged and mtime older than 1 poll, or it fails to open and is retried next poll (capture software writes the file over a second or more). Never read a half-written file.
- Emits one event per new stable file, in order. Burst of several files: queue them, process sequentially in a worker, never block the UI.
- Each set (Target, Reference) has its own folder, saved in the session. One set is watched at a time: Start watching on the other set's tab pauses the first. New subs join the set whose watcher found them; switching tabs only changes what is shown. Interval and "Process existing files" (on: Start watching first loads what is already there) persist in settings.json.

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

## 6. Layout and sets: Target and Reference (manual by design)
- Header: "Collecting for [Target] [Reference]" and, at the right, [Pre Processing settings]: tabs attached to the box below (the selected tab has the box background and the blue outline). The tab, never the OBJECT header, decides the set (Matt: OBJECT is often wrong in the capture software); OBJECT is shown as information in the table only. A hint chip appears when OBJECT or exposure changes between subs ("Looks like a new object. Switch to Reference?"); it never switches by itself.
- Source box per set: Folder [Browse…] [Start watching / Pause] [Clear all]; every N s, Process existing files; status line; Name [SIMBAD] [Header] (Header takes OBJECT from the first FITS file of the set's folder as the name only); Coordinates from SIMBAD | From header | Enter by hand with RA/Dec (editable only by hand). Per-set values and the selected tab are saved in the session.
- Below, 60/40: the Sub card (ADU profile | Sub image, unchanged) on the left; Recommendation above Good subs on the right. Then Stack SNR (left) and Logbook notes (right), then the Log strip.
- Each sub row: checkbox (included), right-click menu (Move to Reference/Target, Exclude, Copy list, Save list…), flags as chips. Unticking a flagged sub is remembered as a user override (`user_override`), so later flag recalculations do not re-tick it.
- Good subs header: "Good subs   Target · N of M used". Under it [Stack selected] and [Open folder] (`Stacks\<night>\`) and the result line.

### Stack selected
- Stacks the ticked subs of the shown set with `core.files_stage.spec_from` + `stack_job` (→ `core.stack.run_stack`, the one stack writer): `Stacks\<night of the first sub>\<Name>_target_S.fit` / `<Name>_ref_S.fit` (core/naming.py; a different set with the same name gets `_2`, the same set is replaced). The header carries the SX* block (rows, number and exposure of subs, name, raw folder SXRAWDIR, masters used SXDARK/SXBIAS/SXFLAT with SHA256; `docs/DATA.md`), OBJECT = the name, RA/DEC when known. Worker with progress and Cancel. Result line, e.g. "✓ Stacked 9 subs → Target_S.fit · calibrated with Dark 240 s − Bias 1 ms · sigma-clip mean 3.0 σ", also logged.
- Blocked (no suitable dark, or another calibration error): nothing is written; the calibration message with a link to Pre Processing settings. Stacks location missing or not writable: says so, with a link to Settings › Data locations.
- The rows locked for the set are saved with the stack (session `params.acq.stacked.<set>` and the sets file); Reduce > Extract uses them as its first guess and looks for the spectrum near that row (flipped with Flip Y) instead of the frame's brightest row.
- Rows saved in the stack: the hint under the result says "Rows saved in the stack file". When the rows of a stacked set change, only the row keywords of its stack are rewritten (worker, header-only, temp file + replace; the pixels are untouched) and the sets file is updated; meanwhile the hint says "Rows changed since stacking: updating stack file…". A missing or locked stack logs a warning and the sets file keeps the rows.
- Reduce › Select is not changed by stacking: pick the stacks there (Use latest stacks) and Load pair.

### Pre Processing settings (shared by both sets)
- Masters folder (the same setting as Settings › Data locations › Masters) and the read-only Stacks folder of the night with Open folder.
- Master tolerances (gain ±%, offset ±%, temperature ±°C, warn on/off), moved here from Settings (2026-11-18). Just above Stacking.
- Calibration frames: the master library table, and Create masters with Darks / Bias / Flats bins each with its own button: `ui/widgets/masters_panel.py`, bound to the one library (Reduce › Select no longer has a library).
- Stacking: method (Sigma-clip mean / Median / Mean), rejection σ, Dark and Bias pickers, Match pedestal, Scale dark signal by gain ratio, Use a flat master, Align frames. Stored with the session (`params.acq.stack`, `params.acq.cal`).

### Logbook
- **Notes for this set**: one free-text box per set (Target / Reference), saved about 1 s after typing stops (and on set switch and close) into `acquisition_sets.json` (`notes`). When Reduce › Select loads the pair, the notes of both sets are added to the pair logbook (they stay in the sets file).

## 7. Hand-off file: acquisition_sets.json
- Saved in the **session (project) folder** (decision: Matt, 2026-11-17), autosaved on every change. There is no Save button and no copy next to the images (2026-11-18).
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
    "exposure_s": 300, "gain": 200,
    "stack_file": "B:\...\2026-08-20\Target_S.fit",
    "rows": { "row": 1067.0, "tilt_deg": 0.12, "x_centre": 1920.0, "spec": [-6, 6], "sky_a": [-30, -15], "sky_b": [15, 30] }
  },
  "reference": { "name": "HD 163955", "name_source": "typed", "good": [], "excluded": [], "exposure_s": 30, "gain": 200 },
  "qc": { "SgrNov32026/Sgrnov32026_00001.fits": { "peak_fill": 0.80, "snr": 36, "size": 16591680, "mtime": 1787282892 } }
}
```
- Paths relative to `folder`; size and mtime recorded so a changed or missing file is detectable. `name_source` is "typed", "header" (older files: "plan", "clipboard"). `stack_file` and `rows` are optional: written once Stack selected made a stack; `ra`, `dec`, `sp_type` are optional too (the set's position and SIMBAD spectral type). Reduce › Select reads the entry whose `stack_file` is the stack it is given when that stack's header lacks the name, position or rows. Reading must tolerate missing optional keys and unknown extra keys.

## 8. Reduce › Select (pair selector, 2026-11-18)
- Select picks the two stacks and loads the pair (docs/SPEC.md); the Use suggestion / Load sets file / Scan folder buttons, the lights tables, the stack settings and the library are gone from Files.
- **Old data:** point Acquisition at the old lights folder with **Process existing files**, then Stack selected; or Browse… to stacks made by another program in Files (rows are then placed automatically in 1D Extract).
- Select never writes the sets file. The coupling between the tabs is the stack files (SX* header block) plus the sets file as a fallback.

## Theme (decided)
No red night palette (Matt, 2026-11-17). The Acquisition tab uses the same theme as the rest of the app. Do not add a night-mode toggle.

## Tests
1. core/watcher: new file detected once; half-written file (growing size) is not read until stable; case-insensitive extensions; filter pattern; existing files not processed by default.
2. core/subs on the nova example (`example_spectra/novasrg2026/SgrNov32026`, 6 x 300 s, and `hd163955`, 10 x 30 s): peak and fill computed; a synthetic saturated column sets peak_fill >= 1.0 and the saturated flag; a synthetic dim sub is flagged by the SNR rule; hot pixel in the rows does not set the peak (second-highest rule).
3. G/R: SharpCap header values used when present (EGAINSAV, RDNOISE); table fallback for a NINA-style header; gain 200 boundary never interpolated across.
4. Rows: auto rows near 1070 for the nova stack and near 1042 for the reference; lock persists; a 5 px shift raises the hint, does not move the rows.
5. SNR: quadrature stack matches a direct stack of synthetic subs within 5 %; projection reaches the target at the expected n.
6. Sets: save/load round trip; unknown keys tolerated; missing files flagged; user_override survives recalculation; OBJECT header never assigns a role.
7. Select (tests/test_files_pair.py): two stacks fill both cards and all checks pass; an old stack without keywords warns and needs a name; sizes / same file / time gap / distance; Load pair feeds Geometry and Extract; a different pair invalidates, the same does not; the SX* row convention and header-only updates; no SX* downstream; Use latest stacks; the pairs list; the preview in a worker.
8. UI (pytest-qt): the tab decides where the next new file goes; no image work on the UI thread (a slow fake sub does not block interaction).
10. Redesign (tests/test_acq_redesign.py): per-set state separate and restored; one watcher at a time, subs join its set; Process existing files on/off; Clear all; SIMBAD (mocked) / Header / by hand; Stack selected equals the core stack for the same inputs (header and SX* block included); blocked without a dark or a Stacks location; one Masters setting; set notes autosave; sets file with stack_file (relative to the data root).
9. Perf (tools/perf.py): per-sub analysis under 100 ms on a 3840x2160 frame; report the measured number.

## Double-lobed profiles (2026-11-19)
`core/extract.auto_place` (used by `subs.detect_rows`, `subs.detect_rows_in`, `extract.auto_place_in`, `extract_stage.auto_place_job` and `resolve_bands`) finds every significant lobe near the main peak on an interquartile-mean row profile (not quantised, robust to hot pixels and emission-line columns) and merges neighbours that belong to one ring-shaped star image. The constants at the top of `core/extract.py`: NSIGMA_LOBE 5, LOBE_MIN_FRAC 0.15, VALLEY_MIN_FRAC 0.15, MAX_LOBE_SEP_FWHM 4, SOURCE_SEARCH_FWHM 10, EDGE_FRAC 0.10, EDGE_NOISE_SIGMA 2, EDGE_PAD 2, LOBE_SMOOTH 1.5 rows. One lobe = exactly the single-peak rows as before. `Rows.shape` is "single" or "double-lobed"; the per-set `lobe_mode` (auto | single | all) is in the sets file.

## Planned pair from Plan (2026-11-21)

**Strip.** After Plan › **Load for acquisition**, a slim strip at the top of the page (under the description, accent
rule on its left): "Planned pair: <target> + <reference> (from Plan)" and, at the right, the link **Change in Plan**
(switches to Plan with that row selected). The names stay editable; when they no longer match the pair, an amber
"edited" chip appears in the strip and Plan drops its "In Acquisition" chip. Clear all removes the strip.

**What loading sets.** Names (`name_source` "plan"), coordinates with the source control on *SIMBAD* or *Enter by
hand* and a "from plan" tag, a note line "From Plan: <name> · <type>". Never a folder: the capture folders are still
chosen here. A pair without a reference sets only the target.

**When Acquisition is busy.** Subs or a running watch for a different target or reference: a question first (Yes:
pause the watch, Clear all, empty the folder fields, then load; No: nothing). Same pair or empty sets: no question,
folders kept. Code: `AcquisitionScreen.load_planned_pair`; tests: `tests/test_planned_pairs.py`.

**Browse….** The folder picker shows the chosen folder's FITS files too (greyed out, not selectable) so it is easy
to confirm it is the right capture folder ("FITS files and folders"); only folders can be chosen
(`docs/THEMES.md` › Dialogs).
