# Sample data and what it tells us

Real frames live one folder up, in `..\projects\` (not part of the repository).

| Folder | Content |
|---|---|
| `projects\Lights\Science\` | `Sheliak_00015…00029.fits` (15 frames) |
| `projects\Lights\ReferenceStar\` | `HD174602_00001…00011.fits` (11 frames) |
| `projects\calibration\darks\` | 11 darks, 10 s, gain 251, offset 20 (NINA) |
| `projects\calibration\bias\` | 11 bias, 0.001 s, gain 251, offset 20 (NINA) |
| `projects\output\` | Alioth/Phecda frames renamed for SpecINTI; ignore |

## Header facts (verified from the files)
- Camera `ZWO ASI585MM Pro`, 3840 × 2160, BITPIX 16, **BZERO 32768**, pixel 2.9 µm, bin 1×1, ROWORDER TOP-DOWN.
- Lights: SharpCap 4.1, `IMAGETYP='Light'`, EXPTIME 15 s, **GAIN 250, OFFSET 3**, SET-TEMP −11, CCD-TEMP −7.7, OBJECT set, `DATE-OBS` UTC. No RA/Dec, no site keywords.
- Darks and bias: NINA 3.2, `IMAGETYP='DARK'` / `'BIAS'` (upper case), **GAIN 251, OFFSET 20**, SET-TEMP −10, with SITELAT/SITELONG of the observer's site, SITEELEV 0.0, TELESCOP 'TS optics', FOCALLEN 790.
- Frame-type keyword differs by software: normalise `Light/LIGHT`, `Dark/DARK`, `Bias/BIAS/Offset`, `Flat/FLAT`.

## Measured on `Sheliak_00015.fits` and `HD174602_00001.fits`
- Medians: light ≈ 1008 ADU, dark ≈ 2048, bias ≈ 2064. The dark/bias offset (20) is higher than the lights' (3), so a plain dark subtraction leaves the light about 1000 ADU too low. Sky-band subtraction in the 1D extraction removes a constant pedestal, so the final spectrum is not hurt, but the 2D view is. See SPEC §4 for how the tool handles this.
- Spectrum is horizontal, centred near row **1330** (Sheliak peak row 1331, reference 1345; FWHM about 6 px in Y for the science star).
- Tilt: spectrum centre moves from row 1336 at x=600 to row 1327 at x=3200, so about −9 px over 2600 px, **about −0.2°**. Detection must find this.
- Bright signal on Sheliak spans about x = 1100–2530; the reference star spans about x = 810–2420 (colder, fainter blue end).
- Hot pixels reach 65520 ADU (16-bit max); expect them in the dark master.
- The usable band for publishing is **3900–7700 Å** (decided; keeps the O₂ A telluric band). Dispersion is about **1.2 Å/px** (from Matt's BASS fit of `Gam Cas_00004.fits`: 348.4–811 nm over the 3840 px; see `BASS_CALIBRATION.md`). Do not use the 1.85 Å/px shown in the mockups. With that dispersion the bright region of Sheliak (x about 1100–2530) is roughly 4860–6560 Å.

## What is missing from the sample set
- No neon/lamp frame, no flats, no Hα-emission science target. Tests for those use synthetic frames.
- No known-good reduced spectrum to compare against. Matt can reduce the same data in SpecINTI/BASS and supply a comparison spectrum later (OPEN item in BUILD_PLAN).

## Reference (library) spectra: `starex_reduce\reference_spectra\`
- Supplied by Matt: Pickles (1998, PASP 110, 863) templates from the HILIB/UVILIB set plus `README.HILIB`, with the metal-weak/metal-rich `w…` and `r…` variants removed (the folder now holds only the standard `<type><class>.dat` files). Same set ISIS/BASS users know.
- Format: plain text, two columns `wavelength(Å)  flux`, 3000–10000 Å in 5 Å steps (1401 lines), flux normalised to 1.0 at 5556 Å. Resolution about **10 Å**, coarser than our data (see SPEC §5.5).
- File names: `<type><class>.dat`, e.g. `a0v`, `a0iii`, `b8v`, `g2v`, `k5iii`, `m2.5v`. Parse whatever `*.dat` files are present; never assume a particular file exists (fall back to the nearest type).
- The reference star in the sample set (HD174602) and Vega-type A0V stars map to `a0v.dat`. Coverage includes the whole publishing band 3900–7700 Å.
- Wavelength regions contaminated by atmospheric absorption were generally zeroed or interpolated in the library, per the README. Treat exact zeros as invalid and interpolate over them.
- Cite Pickles 1998 in the app's About box.

## Worked example
A complete real-world reduction (raw, BASS stacked, 1D, response, submitted AAVSO file) is in example_spectra/novasrg2026. See docs/EXAMPLE_NOVA.md. Use it for acceptance tests; never modify it.

Calibration frames for the nova example (bias/ and darks/ in example_spectra/novasrg2026) are gain 251/offset 20, lights gain 200/offset 3: see EXAMPLE_NOVA.md 'Calibration mismatch'. Earlier notes saying lights were gain 250 are superseded.

## Stack keywords (SX* block, written by the stack writer since 2026-11-18)
Every stack written by `core/stack.py: run_stack` (Acquisition's Stack selected, the self-test) carries this block,
so a stack describes itself without the sets file, on another machine, or in a later batch queue.

| Keyword | Type | Meaning |
|---|---|---|
| `SXVER` | int | version of the SX* block, starts at 1 |
| `SXNSUB` | int | number of subs stacked |
| `SXSUBEXP` | float | exposure of one sub, s (only when all subs share it) |
| `SXNAME` | str | object name chosen in Acquisition |
| `SXRAWDIR` | str | folder of the raw subs (absolute; the common folder when they came from several) |
| `SXDARK` / `SXDARKH` | str | dark master: path inside the Masters folder / its SHA256 |
| `SXBIAS` / `SXBIASH` | str | bias master: path / SHA256 |
| `SXFLAT` / `SXFLATH` | str | flat master: path / SHA256 (only with a flat) |
| `SXROW1` / `SXROW2` | int | spectrum band, first / last row |
| `SXSKA1` / `SXSKA2` | int | sky A band, first / last row |
| `SXSKB1` / `SXSKB2` | int | sky B band, first / last row |

- **Rows convention:** 1-based, inclusive, counted along the image Y axis (NAXIS2) exactly as the pixels are stored in
  the stack file. The stack is not flipped or rotated, so this is the camera frame, the frame Geometry reads; FITS
  row r is numpy row r − 1. The bands are those Acquisition draws: round(ridge row) + each offset, at the column
  where the rows were locked (`x_centre`). 1D Extract mirrors them with the Flip Y setting.
- A band with no selection has no keywords (never zeros).
- The same file also gets OBJECT (= the name), RA/DEC (deg) when known, and keeps the first sub's DATE-OBS, GAIN,
  OFFSET, CCD-TEMP, XBINNING/YBINNING. EXPTIME stays the exposure of one sub with NCOMBINE subs and
  `EXPTIME2 = "n x t s"` (Export multiplies them; the total is EXPTIME × NCOMBINE).
- Changing the rows in Acquisition after stacking rewrites only the row keywords (header-only, atomic).
- The SX* block is private: `write_float32` drops it from every later file (Geometry onwards) and Export's
  header builder strips it, so it never reaches an AVSpec or other exported file.


## Data root and naming (2026-11-18)
The folder layout, principles and naming rules are in `docs/DATA_ARCHITECTURE.md`. Short version:
`Stacks\<night>\<Name>_target_S.fit`, `Masters\<camera>\<year>\`, `Processed\<night>_<Target>__<Ref>\`,
`Published\<year>\<night>_<Target>_v<N>\`. Names are made by `core/naming.py` (`sanitize_name`: "AG Dra" → AGDra,
"V-799 Sgr" → V_799Sgr) and are for people only; the logbook and the headers are the truth.

**Observing night:** the local calendar date of the evening; a time before local noon belongs to the previous date
(`naming.observing_night`, the machine's time zone, DST handled by the zone). For a stack: DATE-OBS of the first sub.

## Pair logbook: `Processed\<pair>\logbook.json` (schema_version 1)
Written atomically (temp file + replace). Fields this version does not know are kept on every rewrite. Paths inside
the data root are relative to it.

| Field | Content |
|---|---|
| `schema_version` | 1 |
| `pair_id` | the folder name |
| `night` | YYYY-MM-DD (observing night of the target stack) |
| `created_utc`, `updated_utc` | ISO UTC |
| `target`, `reference` | `name`, `stack_file` (relative), `sha256`, `nsub`, `exp_s`, `date_obs_utc`, `ra`, `dec` (deg), `position_source`, `rows` {`spectrum`, `sky_a`, `sky_b`: [first, last] 1-based or null}, `raw_dir` |
| `camera` | `gain`, `offset`, `temp_c`, `binning` [x, y] (from the target stack) |
| `masters_used` | [{`file` (relative), `sha256`, `kind`}] from SXDARK/SXBIAS/SXFLAT of both stacks |
| `processing` | per step (`geometry`, `extract`, `wavelength`, `response`, `export`): `utc`, `summary`, `params`, `results` (numbers and short lists only), `outputs`; export: `file`, `sha256` |
| `status` | `stacked`, `reduced`, `published`: false or the UTC time it happened |
| `published` | [{`version`, `date_utc`, `file` (relative), `sha256`, `avspec_reference`}] |
| `notes` | free text (Select › Pair notes; the sets' notes are added once on Load pair) |

`core/logbook.derive_status` recomputes stacked / reduced / published from the files that exist;
`core/logbook.scan_pairs(data_root)` lists every pair from the logbooks alone (for a future Library tab).

`Published\<year>\<night>_<Target>_v<N>\submission.json`: pair_id, night, target, reference, version, date_utc,
file, sha256 (of the copy), source_sha256, avspec_reference, app, app_version.

`Stacks\<night>\acquisition_sets.json` additionally carries per set `notes`, `ra`, `dec`, `sp_type`, and
`stack_file` relative to the data root.

## Pair logbook schema 2 (2026-11-19)
`schema_version` 2 adds (a schema-1 logbook is migrated on load: empty `steps`, `qc` = {warnings: []}):

| Field | Content |
|---|---|
| `steps` | {stacked, geometry, extract, wavelength, response, qc, export, publish: {`state` done / stale / failed / skipped, `finished_utc`, `summary` (one line), `metrics` (small dict: tilt_deg, slant_deg, rows, rows_source, fit, rms_A, nlines, library, version …; wavelength also: the target calibration's shift_px, scale, quad, model, target_rms_A, n_anchors {sky, telluric, ism, stellar}, v_kms, sky_lines_found, sky_ref_residual_A, star_slit_offset_px, delta_center_A, delta_span_A, delta_max_dev_A, scale_pct, verdict, see docs/WAVELENGTH.md)}} |
| `qc` | {`utc`, `warnings`: [{`text`, `acknowledged`}]}: the last AVSpec test's warnings |

Writers: every finished Reduce step (`pair_steps.record_step` via `MainWindow._stage_done`), a failed step
(`MainWindow._on_failed` → failed), an upstream change (`pair_steps.sync_stale` → stale, coalesced on session status
changes), Export › Test (`qc`, and acknowledged when "I have read the warnings" is ticked), Export › Publish's AVSpec
save (`export`), Promote (`publish`). Every writer re-reads the file and changes only its own part.

## The planning location is not data (2026-11-20)
Plan › Observing conditions keeps the observing location in the settings only (`settings.json` → `plan.location`:
lat, lon, source, user_set; plus `plan.from`, `plan.to`, `plan.min_alt`). It is **never** written to a logbook, a FITS
header, an export or published file, a sets file, `session.json` or the observing plan text/JSON. The site used for
airmass and the AVSpec header is still the Export › Collect site (`settings.site` and the frame headers), unchanged.
