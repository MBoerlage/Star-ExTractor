# Data architecture (2026-11-18)

One data root, four fixed locations, one logbook per target + reference pair. Single machine, Windows first.

```
<data root>                                   e.g. D:\StarExtractorData (Settings › Data locations)
  Stacks\<night>\                             one folder per observing night
      <Target>_target_S.fit                   Acquisition › Stack selected (SX* header block)
      <Ref>_ref_S.fit
      acquisition_sets.json                   the night's sets: good/excluded subs, rows, names, notes
      acquisition.json                        the Acquisition tab's own state for that night
  Masters\<camera>\<year>\                    dark/bias/flat masters (Create masters)
  Masters\index.json                          every master: header facts, path, SHA256 (rebuilt if missing)
  Processed\<night>_<Target>__<Ref>\          one folder per pair (Reduce › Select › Load pair)
      logbook.json                            the pair's single source of truth (docs/DATA.md)
      session.json, log.txt                   the Reduce flow's state and log
      Target_X.fit … Target_R.fit             1D results (and Reference_*)
      wavelength.json, response.json          step results
      export\                                 AVSpec, FITS, CSV, PNG saved by Export › Publish
      work\                                   Target_S.fit, Reference_S.fit, *_G.fit, cache\
  Published\<year>\<night>_<Target>_v<N>\     Promote to Published: AVSpec file + submission.json
%APPDATA%\StarExtractor\                      settings.json, window state, instrument profile, VSX cache
```

## Principles
- **Raw data is never touched.** Raw captures stay wherever the camera wrote them; the app only reads them and
  records their folder (absolute) in the stack header (`SXRAWDIR`) and the sets file.
- **Folders and headers are the truth.** logbook.json and the FITS headers say what a file is. **Names are for
  people:** the app never parses meaning back out of a folder or file name (`core/naming.py` only builds them).
- **Every path inside the data root is stored relative to it** (logbooks, the sets file, index.json), so the root
  can move or be copied to another drive and still resolve. Only paths outside it (raw folders) are absolute.
- **work\ is disposable.** Deleting a pair's `work\` reclaims space and loses nothing important: Load pair copies
  the stacks back and every later step recomputes.
- **Published is a copy.** Promote copies the AVSpec file with `submission.json`; the pair folder stays complete.
  A version folder is never overwritten; the logbook is not copied.
- **No silent fallback.** A missing or read-only location (an unplugged drive) is reported at start-up with
  "Choose another location"; the app never creates a folder somewhere else instead.

## Shared line catalogue (core/linelists.py, 2026-11-19)
One catalogue of sky, telluric, interstellar and stellar lines (air Å, kind, weight, blend flag) used by the sky-line
detector and the target calibration (docs/WAVELENGTH.md) and, later, the line-marker chooser. Nothing else keeps
its own copy; the Balmer values come from core/wavelength.py.

## Naming (core/naming.py)
- `sanitize_name`: spaces dropped, letters and digits kept (case kept), any other character → `_`, repeated
  `_` collapsed (a double underscore is the pair separator), no leading/trailing `_`, never empty ("Unnamed").
  "AG Dra" → `AGDra`, "HD 145454" → `HD145454`, "V-799 Sgr" → `V_799Sgr`.
- `observing_night`: the local calendar date of the evening; before local noon belongs to the previous date
  (machine time zone; the stack's DATE-OBS of the first sub).
- Stacks `<Name>_target_S.fit` / `<Name>_ref_S.fit`; pair `<night>_<Target>__<Ref>`; published
  `<year>\<night>_<Target>_v<N>`; AVSpec file names carry a compact date (YYYYMMDD).
- Collisions: a different set that would get an existing stack name gets `_2`, `_3`, … (compared by SXNAME and
  SXRAWDIR); re-stacking the same set replaces its file. A different pair with the same folder name gets `_2`;
  the same pair (same two stack files in its logbook) reuses its folder and logbook.

## scan_pairs (used by the Library tab)
`core/logbook.scan_pairs(data_root)` returns one `PairSummary` per pair (night, target, reference, status, notes
preview) from the logbooks and file stats only, never pixels (20 pairs well under 200 ms).

## The Library tab (2026-11-19)
The Library (docs/LIBRARY_TAB.md) reads Stacks (unpaired target stacks, the night's other stacks as reference
candidates, the sets file's reference), Processed (the pair logbooks) and Published (whether published files still
exist). Masters are not shown. It never parses meaning from a name: roles of unpaired stacks come from the
`_target_S` / `_ref_S` suffix only because there is no logbook yet, and names from the SX* header.
