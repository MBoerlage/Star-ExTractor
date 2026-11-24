# Star ExTractor – specification

## 1. Goal
Take lights of a science target and a reference star (usually an A0V star), plus calibration frames, and produce a wavelength-calibrated, response-corrected, normalised 1D spectrum (intensity vs Å or nm) with a complete FITS header, ready for AAVSO AVSpec and ARAS/BeSS. One fixed instrument (Star'Ex LR, ASI585MM Pro, 2.9 µm pixels). Quick: a typical night's data should go from "files picked" to "spectrum on screen" in well under a minute of compute.

## 2. Workflow
Five-step ribbon, one screen per step, plus a separate Export tab (`Reduce | Export`).

| # | Step | Output file (in the pair folder `Processed\<night>_<Target>__<Ref>\`; 2D files in `work\`) | Works on |
|---|---|---|---|
| 1 | Select | `Target_S.fit`, `Reference_S.fit` (the pair: stacked, calibrated 2D) | two stacks (Acquisition's Stack selected, an older session or another program) |
| 2 | Geometry | `Target_G.fit`, `Reference_G.fit` | stacks only |
| 3 | 1D Extract | `Target_X.fit`, `Reference_X.fit` (1D, pixel axis) | geometry-corrected stacks |
| 4 | Wavelength | `Target_W.fit`, `Reference_W.fit` (1D, linear Å axis) | 1D profiles |
| 5 | Response | `Target_R.fit`, `Reference_R.fit`, `response.fit` | 1D, wavelength-calibrated |
| – | Export | `<obj>_<date>_<time>.fit` (+ `.dat`/CSV, PNG, log) | `Target_R.fit` |

Rules: stacking happens first (step 1); steps 2–5 never see individual sub-frames. Changing any step invalidates all later steps (ribbon turns them grey) until re-run. A step runs on the target and the reference together ("Run both") or one at a time ("Run this image"). "Run all" (runs every step that has valid inputs) is hidden for now (2026-11-20): the button and its shortcuts come back with the settings flag `ui.show_run_all` (default off); the action itself stays.

Ribbon states: green = done, blue = current (selected; neutral), red = missing data, grey = not run.

**Acquisition tab** (`docs/ACQUISITION_TAB.md`): polls the capture folder, analyses each new sub in a worker (strip read, ~15 ms per 3840×2160 sub), keeps Target/Reference good-subs sets, recommends exposure / number of subs, charts the stack SNR, writes `acquisition_sets.json` (with per-set notes) and its own `acquisition.json` to `Stacks\<night>\`, and can stack the ticked subs of a set (Stack selected → `Stacks\<night>\<Name>_target_S.fit` / `<Name>_ref_S.fit`, the night of the first sub) with its own Pre Processing settings page (Masters location, master tolerances). The spectrum / sky rows chosen there are written into the stack header (SX* keywords, `docs/DATA.md`). To stack the raw lights of an old session, point Acquisition at their folder with **Process existing files** and use Stack selected.

**Data root (2026-11-18, `docs/DATA_ARCHITECTURE.md`):** one folder chosen at the first start (Settings › Data locations: the root and Stacks, Masters, Processed, Published, each overridable, with a status per path and Create missing folders). Every path inside the root is stored relative to it. A missing or read-only location is reported at start-up with Choose another location; nothing is created elsewhere. Every reduce step records its parameters and results in the pair's `logbook.json`; Wavelength and Response also write `wavelength.json` / `response.json`. Export › Publish saves into the pair folder (`export\`); **Promote to Published** then copies the AVSpec file with `submission.json` into `Published\<year>\<night>_<Target>_v<N>\` (a new version each time, never overwritten; the logbook stays in Processed).

**Night mode (2026-11-19, `docs/THEMES.md`):** a switch in the top row (left of ⚙, Ctrl+Shift+N) changes the palette live between `default` (the mockup look) and `night` (red-filter safe: dark, warm, no green or blue, states by brightness and glyph); a third palette `red` (only the red channel) is chosen in settings.json for now. Settings key `ui.palette` = default | night | red, applied before the first window. Images use a black → accent map at night; plots add line styles; dialogs are Qt-drawn in the palette. `tools/check_palette.py` checks contrast normally and through a simulated red filter.

**Library tab (2026-11-19, `docs/LIBRARY_TAB.md`):** every night and pair under the data root (from the pair logbooks, never pixels): summary counts, filters, a progress report per pair from `logbook.steps`, notes, Open in Reduce (Select's Load pair), Promote to Published, and Create pair for a target stack without a reference.

**Reduce › Select is a pair selector (2026-11-18; the step was called Files until 2026-11-19):** pick the stacked target and the stacked reference (**Browse…**, opening in the most recent night of Stacks when the box is empty, else in the current file's folder, or **Use Acquisition stacks** fills both boxes from what Acquisition stacked last (its `last_stacks` record, `core/last_stacks.py`); it never scans a folder and never loads; only one role recorded, nothing recorded (then a labelled, confirmed offer of the stacks Acquisition recorded in the most recent night of Stacks), or a missing / non-stack file each say so and leave that box alone), see what each header says (size, stack, time, camera, position, rows) with a preview of the spectrum rows, a **Checks** card (sizes, names, rows, camera setup, time gap > 3 h, sky distance > 20°, same file; only ✗ blocks), then **Load pair** (right of Use Acquisition stacks): the pair folder `Processed\<night>_<Target>__<Ref>\` is made (or reused when its logbook names the same two stacks; another pair with the same name gets `_2`), the stacks are copied to `work\Target_S.fit` / `work\Reference_S.fit` (the unchanged contract of the later steps), `logbook.json` is created or updated (stacks with SHA256, names, rows, camera, masters used, the sets' notes) and the Reduce session moves into that folder; names/positions/rows go into the session (`params.files.pairs`, a one-item list so a night queue can follow), and the Files chevron shows the pair ("AG Dra + HD 145454"). Reading order per stack: the header's SX* block, then the sets-file entry whose `stack_file` is that file, then standard keywords (OBJECT, EXPTIME, DATE-OBS, GAIN, OFFSET, CCD-TEMP, XBINNING, RA/DEC or OBJCTRA/OBJCTDEC). The rows become 1D Extract's first guess. The Files stage key is the loaded file's fingerprint plus its rows (and the hot-pixel dark for Geometry), so a different pair invalidates Geometry onwards and reloading the same pair does not. Run all skips Files when a pair is loaded and otherwise stops with "Load a pair in Files first." Files no longer picks lights, builds masters or stacks.

**Plan tab** (before Reduce; `docs/PLAN_TAB.md`): target name → Check with SIMBAD (RA/Dec J2000, V, object type; one request per click) or RA/Dec typed by hand; the N nearest good or ok stars (default 10) from the bundled reference-star list (`data/reference_stars.csv`, our own selection from SIMBAD by starex_refcat v0.1.0: 1595 B and A dwarfs, V ≤ 6.5, each graded good / ok / poor; poor stars only with Include poor-grade stars; a Faintest V setting; nearest first within the chosen grades, never by distance alone; emission-line stars are never offered; `docs/REFERENCE_STARS.md`). The output is an **observing plan** (target name, V, RA, Dec next to reference name, V, RA, Dec) as text or JSON, to copy or save (`observing_plan.txt/.json`). It is kept in `session.json` (`params.plan`) but is **not linked** to Reduce: it does not fill in Files or Response (Matt, 2026-11-16). A different target clears the reference choice.

## 3. Data model
`Session` (folder `…\<date>\`, file `session.json`):
- `inputs`: target and reference name, coordinates (+ source), SIMBAD result, set by Select's Load pair. Sessions from before 2026-11-18 also carry the light lists and stack settings of the old Files step; they are still read (their Files key is computed the old way until a pair is loaded).
- `params.files.pairs`: `[{target: {source, file, name, name_src, ra, dec, pos_src, rows, rows_src, sp_type}, reference: {…}}]`, one item for now.
- `stack`: method (`sigma_clip_mean` default | `median`), σ (3.0), align (off).
- `calibration`: chosen master dark / bias / flat per image set (auto-picked, user can override).
- `params[stage]`: every parameter of every stage.
- `status[stage]`: `valid | stale | missing | error`, input hash, output path, timestamp, one-line summary shown in the ribbon (e.g. `Tilt 0.3° · smile fixed`).
- `log`: appended text, mirrored to `log.txt`.

`InstrumentProfile` (`%APPDATA%\StarExtractor\instrument_starex_lr.json`): flip flags, last tilt and smile (radius, y0), spectrum row, dispersion polynomial (+ date), FWHM/R, last band rows, default crop and normalisation window, observer/site/equipment strings for export. "Use saved geometry" and "Refine the saved solution (shift only)" are the defaults, because the instrument is fixed.

Master library: the Masters location (`<data root>\Masters\`, Settings › Data locations), masters in `<camera>\<year>\`, one `index.json` at the top: per master kind (bias, dark, flat), exposure, gain, offset, temperature, binning, size, number of frames, date, path (relative), SHA256. A missing index is rebuilt from the FITS headers. Matching: size and binning exact, gain/offset within tolerance, nearest exposure, then nearest temperature, then nearest in time. Darks are stored bias-subtracted when a bias master exists for the same gain/offset/temperature.

## 4. Calibration rules (verified in the earlier SpecINTI work)
- Must match exactly: binning and image size.
- Gain within 20 %, offset within 50 %, temperature within a tolerance (default ±3 °C). Tolerances are editable; the check can be turned off. A mismatch is a visible warning with an "Use anyway" action, never a silent pass.
- Dark of a different exposure is allowed **only if a bias is available**: `cal = (L − B) − (D_raw − B) · t_L / t_D`. A dark with no bias is valid only when `t_D == t_L` (error otherwise: the bias would be scaled and subtracted 1.5×, about 1000 ADU too low in the earlier test).
- Auto-pick: for each light set choose the dark with the closest exposure among masters whose gain/offset match; show "Dark 90 s · exact match" or "Dark 10 s · scaled ×1.5".
- Flats are optional and off by default (Star'Ex LR slit spectra rarely need them).
- **Known case in the sample data (measured, see `EXAMPLE_NOVA.md`):** darks/bias are gain 251/offset 20 (median ~2070 ADU), lights gain 200/offset 3 (median ~380 ADU), so raw subtraction goes negative. "Match pedestal" (default ON when gain/offset differ beyond tolerance) uses `dark_signal = master_dark - master_bias` (pedestal-free) and leaves the light pedestal intact; optional gain-ratio scaling of that signal (default OFF, verify ZWO gain formula). Flag as a warning chip in Files, log what was done. 1D sky-band subtraction also removes any residual constant pedestal.
- Hot pixels: built from the dark master (pixels above median + k·MAD, default k=8, plus pixels at 65520), replaced by a local median. Done in step 2 ("Pixels" panel).

## 5. Stage algorithms

### 5.1 Stack (step 1)
- Read frames via memmap (`>i2` + BZERO), process in row strips (e.g. 128 rows) so peak memory stays small and cache-friendly. Calibrate each strip then combine across frames: sigma-clip mean (default, σ=3, 2–3 iterations) or median. Result float32.
- Per-frame table: time (UT), exposure, peak ADU, flag "near saturation" if peak ≥ 60 000, "Use" checkbox. Unticked frames are excluded; "Remove unticked" drops them from the list.
- Align frames: off by default (fixed slit). If on, estimate Y shift per frame from the row-sum profile (sub-pixel cross-correlation) and shift before combining.
- Write FITS with header copied from the first used frame plus `NCOMBINE`, `STACKMTH`, `HISTORY` lines for darks/bias used.
- Target name: free text; "Verify with SIMBAD" (CDS Sesame, offline-safe: failure shows "offline" and offers From header / Enter by hand RA/Dec). Star name from header shown as hint.

### 5.2 Geometry (step 2)
- Flip X / Flip Y options (profile default) so wavelength increases to the right.
- **Tilt, Detect:** split the frame into ~16 column blocks over the lit part of the spectrum, find the spectrum centre row per block (smoothed centroid of the brightest ridge), robust linear fit (Theil–Sen or RANSAC) → angle. Report angle and residual.
- **Tilt, Draw angle:** user presses at one end of the spectrum, drags, releases; angle = atan2(Δy, Δx) in **real pixel units** (the Y zoom must not change it). Show the drawn line, the angle, then switch the view to Corrected. Buttons: Preview, Set values, Redraw. Angle field is editable.
- **Apply tilt:** rotate about the spectrum centre with `scipy.ndimage.affine_transform` (order 1, float32). Rotation applies to the full frame.
- **Smile** (curvature of lines along Y) – needs a lamp frame; optional. Model `x' = x + (y − y0)² / (2R)`, radius R px and Y position y0. When a saved geometry exists and "Use saved geometry" is on, apply it and show "Saved values still fit this night. Residual tilt 0.02°". If the measured residual tilt exceeds 0.05°, advise re-detecting.
- **Pixels:** hot-pixel removal (see §4), optional.
- Y zoom (view only): chips Full height | Fit to spectrum | ×2 | ×4 | ×8 | ×16; wheel over the row ruler; full-height 50 px frame strip with a draggable amber window; X stays fixed. Original | Corrected toggle; Result strip shows the corrected rows under the main view.

### 5.3 1D Extract (step 3)
- Three bands in rows: Spectrum, Sky A, Sky B, each Top/Bottom. Drag edges in the image or type rows; "Auto-place from the brightest row" (smoothed row-sum maximum within the lit X range; spectrum width ≈ 2× FWHM; skies placed either side outside the wings, inside the lit slit area).
- Extraction: "Sum of rows" (default) over the spectrum band. Sky = mean of Sky A and Sky B per column (optionally a per-column linear fit across A and B evaluated at the spectrum centre). Subtract. Show Pre-extraction | Sky-subtracted views and the 1D profile ("Profile from the blue band").
- "Copy bands to the reference stack": same rows, same slit position.
- Output: 1D FITS, pixel axis (CRPIX1=1, CRVAL1=1, CDELT1=1), `BUNIT='ADU'`.

### 5.4 Wavelength (step 4)
- Default mode **Refine the saved solution (shift only)**: predict Balmer line pixels from the saved polynomial, measure true centres (Gaussian fit in a window around each predicted line; skip lines that fail), compute the pixel shift (median of residuals), apply. Modes: **Fit freely from these lines**, **Use a lamp frame instead**.
- Balmer lines (air, Å): Hα 6562.852, Hβ 4861.33, Hγ 4340.47 (BASS values), Hδ 4101.74, Hε 3970.07 (standard air values). A0V reference shows them all in absorption. Auto-find for "Fit freely": continuum-normalise, find absorption minima with `scipy.signal.find_peaks`, match to the line list by RANSAC on a linear guess (dispersion from the profile ±15 %). User can Add/remove lines and drag a marker.
- Calibration points table: `# | Line | Pixel | Wavelength | Error` where Error = fit − entered, delete row, unit selector Å | nm. Fit order Linear | Quadratic | Cubic (Cubic from 5 lines, never the default; locked to the saved order when refining). Total error, RMS, dispersion (Å/px).
- **BASS-style coefficients box:** comma-separated, **offset first, always nm**, `f(x) = a + b·x + c·x²`; Copy and "Paste to apply" (parse, validate, set the solution, update table). Also shows linear FITS terms CRVAL1 (Å) and CDELT1 (Å/px). (The "Copy linear FITS terms" button and the "Export calibration data (.xls)" export were removed at Matt's request, 2026-11-16.) The chart can show the profile against px or Å. **VERIFIED** against Matt's real BASS export: layout, precision, pixel origin 0 and the export file format are in `docs/BASS_CALIBRATION.md`, with fixtures in `tests/fixtures/`. Follow that document exactly. The instrument profile is seeded with that solution (dispersion about 1.2 Å/px, not the 1.85 shown in the mockups).
- Resolution: FWHM of Hβ (Gaussian fit, Å), `R = λ / FWHM`; both saved to the header (`BSS_ESRP`, `BSS_SRPW`).
- Resample to a **linear** wavelength grid (CRVAL1/CDELT1), flux-conserving. Output `_W.fit`.
- Fit-order guard: never above (lines − 2). With 4 lines, order ≤ 2. With only 2 lines, linear only.
- **Target calibration (2026-11-19, `docs/WAVELENGTH.md`):** the target's solution is the reference solution plus a measured correction D(x) = s + k(x − x0) + q(x − x0)² (λ_target(x) = λ_ref(x − D(x))), from the target's sky emission lines (Sky A / Sky B rows of `work/Target_G.fit`), its telluric bands matched against the reference star, interstellar lines and stellar lines with one common velocity. Auto = shift only unless a scale is clearly better and within limits; modes Shift only, Shift and scale, Manual (the old target adjustment). The manual shift is applied after the fit. The card shows the anchors (Use ticks), residuals, the sky spectrum, the target − reference difference with a verdict, and the reference's own sky-line check. Without the frames or anchors the old behaviour applies and the card says so. Logbook metrics and three HISTORY lines; no SX* keywords.

### 5.5 Response (step 5)
- Library: Matt's local copy of the Pickles (1998) flux library (HILIB/UVILIB) in `starextractor/reference_spectra/` (see DATA.md): 108 templates (after Matt removed the odd metal-weak/rich ones) as 2-column text `wavelength Å, flux`, 3000–10000 Å, 5 Å steps, normalised to 1 at 5556 Å, about **10 Å resolution**. Pick the template from the reference star's SIMBAD spectral type (e.g. `A0Va` → `a0v.dat`; map type + luminosity class to the file name; fall back to the nearest type and tell the user). Let the user override from a dropdown ("Pickles · A0V"). Parse files with numpy; do not bundle anything else.
- Build: `R(λ) = observed_ref(λ) / library(λ)`. **Resolution mismatch:** the library is about 10 Å, coarser than our spectra (R≈820 at Hβ is about 6 Å FWHM). Do not try to sharpen the library: smooth the observed reference to the library resolution (Gaussian, σ from FWHM ≈ 10 Å) before dividing, and rescale; Balmer cores stay masked anyway. Masks: Balmer lines and telluric bands (from `data/aavso_telluric_lines.csv`: O₂ a 6278, H₂O near Hα 6532–6587, O₂ B 6867–6924, H₂O 7168 Å, and O₂ A 7594–7697 Å if the crop includes it). Smooth masked response (Savitzky–Golay or smoothing spline; "Smoothing" low / medium / high) → the pink "smoothed response" curve; this is what is applied.
- Airmass correction (optional, default on): `corrected = obs_target / R · 10^(0.4·k(λ)·(X_t − X_r))` with a standard extinction table k(λ) (e.g. Kitt Peak/Palomar-type; document the source) and X from header AIRMASS, else computed from site, time and coordinates.
- Normalise at a window (default 5500–5600 Å, divide by mean), crop (default **3900–7700 Å**, so the O₂ A telluric band at 7594–7697 Å stays in).
- Show: observed reference, library, raw response, smoothed response (each with a checkbox); corrected Target and Reference. Buttons: "Save response" (reusable file) and "No response" (skip, for diagnostics).
- Saved response can be reloaded for later nights as "Use saved response" (add if time allows).

### 5.6 Export (separate tab)
- Observer code (AAVSO obscode), name, site name, lat/long/elevation, instrument name, spectrograph, telescope, camera. "Remember for future sessions". Values flow into the FITS header preview, shown live.
- Spectrum to publish: **target only**. Zoom X (chips Full | Blue Balmer | Hα + telluric | O₂ A band | Custom, from/to inputs, wheel over the axis, draggable window in a whole-spectrum strip), Balmer markers and telluric markers from `data/aavso_telluric_lines.csv` (AAVSO's own validation list: shaded bands for H₂O near Hα 6532–6587, O₂ B 6867–6924, O₂ A 7594–7697; dotted bandheads O₂ a 6278.3 and H₂O 7168; one baseline tick per listed line) as toggles, used as a **visual quality check**: dips in the spectrum should sit on the ticks, Å | nm toggle, peak readout (Δ from Hα rest wavelength). Purpose: eyeball alignment of features with Balmer/telluric lines. Telluric bands do not move with the star.
- **Object name card (AVSpec only):** OBJNAME field (defaults to the target name, editable) with a **Check VSX name** button = online VSX lookup, one request per click, status line with the result. This is the only network use outside SIMBAD (Plan, Acquisition) and geocoding; everywhere else the tool is SIMBAD or manual only.
- A card for AAVSO AVSpec (ARAS export removed by Matt, 2026-11-16: spectra can be mailed to ARAS.) with pre-flight checks (see `FORMATS.md`). AVSpec rules to honour: no heliocentric/Doppler correction, telluric features stay in the published file, ISOT times with DATE-OBS + DATE-END + EXPTIME, CUNIT1 'Angstrom', airmass 1–5, OBJNAME resolvable in VSX. The tool writes a compliant file and checks the header; **uploading happens on the database websites** with the prepared file. The VSX name check is optional. The X zoom is a view only: the file always holds the Response crop.
- **Publish** (redesigned 2026-11-17) is one card:
  - **Header row:** target · spectral type · V · observation date, and a chip with the last Test result: "N of M checks pass", "Not tested", amber when warnings were acknowledged.
  - **2×2 tiles:** AAVSO AVSpec (the FITS plus `<name>_log.txt`), 1D FITS (the same writer, `<name>_1d.fit`, for your archive), CSV, PNG (the plot). Each tile shows its file name, its own Save button run in a worker, and a status line "Not saved" or "Saved HH:MM · size". When saved, the tile gets a green border, an "Open folder" link, and "Save again", which overwrites the same name. A failed save shows the error in red and logs it.
  - **What blocks AVSpec:** test errors block AVSpec until fixed; test warnings block it until "I have read the N warnings" is ticked in the AVSpec tile itself (`QUALITY_CHECKS.md`). Nothing from the test goes into a header. The other tiles are never blocked.
  - **Saved state** is kept per session and target, and resets when the spectrum changes.
  - **Confetti:** an optional ~1 s burst over the saved tile ("Celebrate saves" in Settings; off by default with reduced motion).
  - **Footer:** Open export folder, Copy path, and the note "Upload the AVSpec file at AAVSO yourself, the app does not upload."

## 6. UI behaviour
See `UI.md`. Top bar: logo, Plan, Library, pair chip, night switch, ⚙ (Run all hidden behind `ui.show_run_all`). Ribbon below tabs. Every step screen has: header (step n of 5, title, description, input → output file chips, ◀ 1/2 ▶, Run this image / Run both / Run, Params ▾), a main viewer card, a right column of controls, and a Log panel.

## 7. Architecture
- **core/** pure functions `stage(inputs, params, ctx) -> result`, no globals. Each returns arrays plus a small summary dict for the ribbon.
- **Job runner:** `QThreadPool` + `Job(fn, cancel_token, progress_cb)`. One active job per stage; new request cancels the old one. Results delivered by signal to the UI thread.
- **Cache:** in-memory LRU of float32 arrays (cap ~1.5 GB, configurable) + on-disk stage files. Key = hash(input file stat/size/mtime or content hash, params). Reopening a session loads status from `session.json` and arrays lazily.
- **Viewer:** `pyqtgraph.ImageItem` in a `ViewBox` with `setMouseEnabled(x=False, y=True)` for Y-zoom views; keep a downsampled mip chain for Full-height view; levels from percentiles cached per image; optional OpenGL (`useOpenGL`) behind a setting. Row ruler, frame strip and draggable bands are overlay items, not image redraws.
- Plots (1D): pyqtgraph `PlotDataItem` with `setClipToView(True)` and `setDownsampling(auto=True)`.
- Theme: dark, as in the mockups (colours in `ui/theme.py`: bg `#0E1116`, panel `#12161C`, line `#2A313B`, text `#E8EBF0`, muted `#A0A9B6`, amber `#F2A33A`, teal `#62D0C6`, green for done `#1F5A3A`).

## 6a. Plan › Observing conditions (2026-11-20)
Location (planning only, never in data files), date, observation window (local time, the computer's zone) and minimum
altitude; a polar sky plot and an altitude chart with the Moon and twilight bands; a summary line; Date, Window and
Visible in the observing plan. Positions from `core/sky.py` (numpy, offline; accuracy and rules in
`docs/PLAN_TAB.md`). One network call, on a click only: **Use my location** (`https://ipwho.is/`, 5 s).

## 6b. Plan › Planned pairs, the Moon, dialogs and help (2026-11-21)
* The polar plot draws the Moon's track and a "moon now" ring from the altitude chart's samples (`docs/PLAN_TAB.md`).
* **Planned pairs**: a list of target + reference pairs in the Plan tab (settings `plan.pairs`, schema version 1);
  the Target card and candidates are its editor; Visible and Moon separation are computed per row, never stored; the
  observing plan covers all pairs; Open plan… reads a saved JSON plan (replace or merge).
* **Load for acquisition** sets the Acquisition names (`name_source` "plan") and coordinates, never a folder; asks
  before clearing subs or a watch of another target (`docs/ACQUISITION_TAB.md`).
* **Dialogs**: one shared helper (`ui/dialogs.py`) styles every dialog from the palette, with our own toolbar icons;
  Directory mode shows the FITS files greyed (`docs/THEMES.md` › Dialogs).
* **Help me**: the workflow guide (`resources/help.md`) from the top row or F1.

## 7a. Start-up (2026-11-20)
- **Order:** `starextractor/app.py` imports only `sys`, `os`, `time` and PySide6 at top level (and the package
  `__init__` is cheap: `__version__` is read on first use). The GUI path creates QApplication, shows the splash
  (`ui/splash.py`, stdlib + PySide6 only) and paints it, and only then imports the app modules and builds the window.
  A test checks that numpy, scipy, astropy, pyqtgraph and `ui.main_window` are not loaded when the splash is up.
- **Nothing heavy before the first paint of the window:** no numpy/scipy/astropy/pyqtgraph import on the GUI thread,
  no network, no IERS download, no catalogue or folder scan (Library, Stacks, Masters). The page the app reopens on
  (Plan, Library, Acquisition) is shown as its light stub for the first paint and built right after it; Reduce ›
  Select is built right after the first paint. The data-location check runs once the splash is gone.
- **Warm-up:** while the window is being built, a daemon thread imports the non-Qt modules the first page needs
  (numpy, scipy and the matching `core` modules, `app.WARM_FIRST`); after Select is built the existing warm-up
  imports pyqtgraph and the other step modules. Third-party and core modules only, so the import locks cannot
  deadlock against the GUI thread.
- **Splash:** frameless, on top, never focused (`WA_ShowWithoutActivating`, `WindowDoesNotAcceptFocus`), centred on
  the screen with the mouse. The art from pre-scaled files at its native pixel size (`resources/splash.png` 800 × 448
  below a device pixel ratio of 1.5, else `splash@2x.png` 1600 × 896; built by `tools/make_splash.py`). Version and a
  status line (Loading libraries → Preparing window → Ready) are painted over the bottom with the font file itself
  (no system font enumeration). At most 4 `processEvents` calls. Closes on the first paint of the window with a
  150 ms opacity fade (none with reduced motion), never with a delay; closed at once before any dialog (first run,
  data locations, a start-up error). Night and red palettes (`ui.palette`, read straight from settings.json): 60 %
  black overlay. A missing image: no splash, one line in the Log panel.
- **When it shows:** Settings › Start-up › *Show splash screen* (`ui.splash`, default on) and `--no-splash`. Never with
  `--selftest`, `--selftest-gui`, `--make-example`, `--version`, on the offscreen platform, under pytest, or with
  `STAREX_NO_SPLASH=1` (tools/screenshot.py sets it); `STAREX_SPLASH_FORCE=1` shows it anyway (tests).
- **Frozen build:** the PyInstaller bootloader splash shows the same 1x image before Python starts and is closed once
  the Qt splash is visible (`docs/PACKAGING.md`).
- **Timings:** one Log panel line per start (`startup: splash 280 ms, window 1050 ms, ready 1730 ms`, ms from the
  process creation, not written to the pair's log file). `STAREX_STARTUP_LOG=<file>` appends them as JSON and exits
  when ready; `tools/startup_bench.py` runs that N times and compares with `tools/startup_baseline.json`.
- **Budgets (this machine, real start, median of 7):** splash visible < 300 ms from process start (measured 278);
  window and ready not later than before the splash (measured: window 1879 → 1054 ms, ready 1938 → 1733 ms).

## 7b. Validation and errors
- Plain-language messages; each has a fix action (e.g. "Reference needs a dark" → "Pick a dark…", "Create master…").
- Never crash on bad input: unreadable FITS, mixed sizes, missing keywords all produce a row-level warning.

## 8. Performance budget (3840 × 2160 frames, mid-range Windows laptop, SSD)
| Action | Budget |
|---|---|
| Scan a folder of 30 frames (headers only) | < 0.5 s |
| Open a session (restore state, show ribbon) | < 0.5 s, no pixel reads |
| Build a master from 11 frames (sigma clip) | < 3 s |
| Stack 15 lights with masters | < 4 s |
| Tilt detect | < 0.5 s |
| Apply tilt/smile/hot pixels to one full frame | < 1.0 s |
| 1D extraction | < 0.1 s |
| Balmer fit + resample | < 0.3 s |
| Response build + apply | < 0.3 s |
| Y-zoom, pan, wheel, Original/Corrected toggle | no recompute; ≥ 60 fps |
| Band drag, line-marker drag, X-zoom on plots | ≥ 60 fps |
| Run all (cached masters) | < 10 s |
| App start: splash visible (from process start) | < 0.3 s |
| App start: window shown (`tools/perf.py`) | < 1.0 s |

## 9. Out of scope (v1)
Multiple instruments, echelle, flux calibration to absolute units, telluric removal by model, automatic upload to AVSpec/ARAS, radial-velocity measurement.
