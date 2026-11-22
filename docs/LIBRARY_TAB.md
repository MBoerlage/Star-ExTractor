# Library tab (2026-11-19)

A browser of every night and target + reference pair under the data root: a summary, a progress report per pair,
notes, and actions to reopen or publish a pair. It reads pair logbooks, file stats and FITS headers only; the one
pixel read is the 1D spectrum thumbnail, in a worker. Code: `core/library.py` (no Qt), `ui/screens/library.py`.

## Placement
The last tab of the top row, on the far right after the flexible space, immediately left of the pair chip; same
style and active state as Plan / Acquisition / Reduce / Export (`TabRow.add_detached`). Nothing else in the top bar
moved. Same theme as the rest of the app (follows the night mode switch since 2026-11-19, `docs/THEMES.md`); the progress dots differ by fill
and brightness (filled bright = done, open dim ring = not yet), not only by colour.

## Layout
1. Header: **Library**, "Every night and pair under <data root>", the time of the last scan, **Rescan**, **Open data
   folder**.
2. Summary cards: Nights, Pairs, Published, Need attention (amber when above 0).
3. Filter row: search (target, reference, notes; Ctrl+F) and chips All | Stacked | Reduced | Published | Needs
   attention.
4. Table (QTableView + model): Target, Reference, Progress, Latest note; grouped under night rows, newest first.
   Progress = three dots (stacked, reduced, published) + "Stacked" / "Reduced" / "Published vN" (amber with "!"
   when the pair needs attention). A target stack without a pair shows the amber "no reference".
5. Detail panel: "<Target> + <Reference>", night and pair folder, status chip ("Reduced · not published",
   "Published v1 · date"); left the progress report (Stacked, Geometry, 1D extract, Wavelength, Response, QC,
   Published: icon, short result, local time; "no record" for logbooks without step records); right the spectrum
   (Target_R.fit, else Target_W.fit, downsampled to ≤ 600 points, wavelength axis) and the Notes box; buttons
   **Open in Reduce** (primary), **Promote to Published** / **Promote as vN** (disabled with a tooltip without an
   AVSpec file in the pair), **Show in Explorer**.
6. An unpaired target stack: its stack facts, **Pick a reference** (the other stacks of that night: reference stacks
   first; the sets file's reference preselected) and **Create pair and open in Reduce**.
7. No pairs: "No pairs yet. Stack a target in Acquisition, then load a pair in Select." A missing or unwritable data
   root: a message and **Open Settings › Data locations**.

## Rules (all in core)
- Pair status (dots, chips) is `logbook.derive_status` (stacked: both stacks exist; reduced: a 1D result exists;
  published: a Published record).
- Needs attention (`logbook.attention`, plus the unpaired stacks): a stack with no reference, unacknowledged QC
  warnings, a failed or stale step in a reduced pair, a published record whose file is gone.
- Step records: `logbook.steps[name]` = {state done | stale | failed | skipped, finished_utc, summary, metrics}; old
  logbooks without them are derived from the files that exist (`logbook.step_states`).

## Behaviour
- The scan (`library.scan_job`) runs in a worker when the tab opens, on Rescan, and when a Reduce step, a failure,
  a QC run, a save or a promotion is recorded (`MainWindow.pairs_changed`). The selection, chip and search survive
  rescans and restarts (settings `library`).
- Notes autosave about a second after typing through `logbook.set_notes`, which re-reads the file and changes only
  `notes`; Select › Pair notes and the Library follow each other (`MainWindow.notes_changed`).
- **Open in Reduce** calls `MainWindow.load_pair` (the same function as Select › Load pair) with the pair's stacks,
  then opens the first Reduce step that is not done. A pair whose stacks moved opens its folder's session.
- **Promote to Published** calls `core.publish.promote` after a confirmation showing the source file and the
  destination folder; the row then reads Published vN.
- **Create pair and open in Reduce** calls `MainWindow.load_pair` with the chosen target and reference stacks.
- Keyboard: Up/Down, Enter (Open in Reduce), Ctrl+F; context menu with the three actions.
- 100 pairs scan in well under 500 ms (logbooks and stats only); opening the tab never waits for the scan.

## Synthetic library
`python tools/synth.py OUT --library` writes 6 nights and 11 pairs in every state (stacked only, old logbook,
reduced, acknowledged / unacknowledged warning, stale, failed, published v1 / v2, published file gone) plus a target
stack with no reference and a night of stacks only. Used by tests/test_library.py and the screenshots 25*.
