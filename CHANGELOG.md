# Changelog

## Unreleased

**Reduce › Select**
- Select no longer warns when the loaded stacks differ from the planned pair ("Target: the Plan says
  X, Select says Y."): observing another star than planned is common.

**Help**
- "Version 0.1: what to expect" is now "What to expect in this version" and points to Help › User
  Feedback instead of GitHub issues; Select explains that loading another pair than planned is fine.

## 0.3.0 (2026-11-24): graded reference stars and User Feedback

**Plan: a new reference-star list**
- The bundled list is now our own selection from SIMBAD (starex_refcat v0.1.0, preset balanced): 1595 bright B and
  A main-sequence stars down to V 6.5, each graded good (106), ok (351) or poor (1138) with the reasons.
- The candidates table lists the nearest good and ok stars by default; **Include poor-grade stars** adds the rest
  in the same distance order, with the nearest good-or-ok one marked **best**. A line under the table says when a
  closer star is graded poor, and why.
- The **Grade** column (✓ good, ok, ! poor; the reasons as tooltip; faint and verified marks) replaces Flux ref.
- **Faintest V** setting (2.0 to 6.5, default 6.5).
- The chosen reference's grade goes into planned pairs and the observing plan (text and JSON); older plans and
  pairs look it up by name.

**Help: User Feedback**
- A **User Feedback** tab in the Help window: send a problem, idea or question (with your optional email and, if
  you tick it, the application log with folder paths, user and computer names and email addresses removed). **See
  exactly what is sent** shows the exact text; a reference comes back. When sending fails, the message can be saved
  as a zip to send later; **Save as file…** works at any time. Nothing goes online until you press Send.

**Credits and Help**
- About, README and THIRD_PARTY_NOTICES credit the sources of the new list (SIMBAD, VizieR, the AAVSO VSX, the
  Washington Double Star Catalog, Pecaut and Mamajek 2013) and no longer the old list.
- New Help section **Where the reference stars come from**: sources, rules, grades, coverage, and the limits.

## 0.2.1 (2026-11-23): from Acquisition to Reduce

**Reduce › Select**
- **Use Acquisition stacks** (was Use latest stacks) fills both boxes with the target and reference you stacked last in
  Acquisition, then runs the checks; it never loads the pair by itself. It no longer guesses from file names or folder
  dates. If only one set is stacked, or a file is missing or not a stack, it says so and leaves that box alone. With
  nothing recorded yet it offers the stacks of the most recent night in the Stacks folder, to confirm with Use these.
- Browse… starts in the most recent night of the Stacks folder.

**Acquisition**
- After both sets are stacked, a strip says "Both stacks are ready." with **Open in Reduce** (Select filled and
  checked, focus on Load pair) and **Not now**.

**Fixes**
- Response showed "Auto · A0V" right after Load pair although the reference star's type was known (the response
  itself used the right template, e.g. A3V). The screens now fill after the pair is stored.

## 0.2.0 (2026-11-23): reliable wavelength calibration

**Wavelength**
- The Balmer lines are found by a blind pattern search over the reference profile, so a camera that moved between
  nights no longer leads to wrong lines. Telluric bands, Na D, He I and Ca II K are never taken for Balmer lines.
- The saved solution is used only after it is checked against the profile; when it does not fit, the step says so
  (with the night it came from) and fits freely.
- A quality gate: a solution that is too poor or does not increase across the detector fails the step, and nothing is
  passed on to the target.
- Line centres are re-centred and cleaned of hot pixels, with an uncertainty per line; the fit is weighted and leaves
  out at most one outlier, marked in the table. Emission cores and blends are flagged.
- Every RMS is labelled with the fit it belongs to; the table has columns for uncertainty, FWHM, fit / my position
  and flags; a line that is not found says why. Hζ and Hη can be added as anchors.
- Markers keep the wavelength order (they stop 15 px before a neighbour); the table is always in wavelength order.
- **Cubic** fit order, as a choice with 5 or more lines (never the default).
- A manual target shift is reset, with a note, when the reference solution changes.

Every processed pair's Wavelength step (and the steps after it) is out of date after the update: run it again.

## 0.1.0 (2026-11-21): first public release

Star ExTractor 0.1 reduces Star'Ex LR (with a ZWO ASI585MM Pro) slit spectra on Windows, from the observing plan to
an AVSpec file for the AAVSO Spectroscopy Database. No Python needed: download the installer from the Releases page.

**Plan**
- Target lookup with SIMBAD (or RA/Dec by hand) and the nearest reference stars from a bundled catalogue, with a flux
  reference flag.
- Observing conditions: location, date, observing window, minimum altitude; a polar sky plot with the target's and
  the Moon's paths and an altitude chart.
- Planned pairs: a list of target + reference pairs for the night (visible window, Moon separation, sort by transit)
  that loads straight into Acquisition; an observing plan as text or JSON to copy, save and open again.

**Acquisition**
- Watches the capture folder (SharpCap, N.I.N.A.); every sub is checked for saturation and signal to noise as it
  arrives; a logbook per night.
- Master darks and bias in a master library; stacking of the selected subs.

**Reduce**
- Select (the latest stacks or any two), Geometry (tilt and smile), 1D Extract, Wavelength (Balmer lines of the
  reference plus a target-specific shift from sky and telluric lines) and Response (against Pickles templates, or
  a saved response).

**Export**
- Collect the observer, site and equipment data once; AVSpec pre-flight checks; publish the AVSpec FITS file (and
  1D FITS, CSV, PNG). One click VSX name check.

**Around it**
- Library of every night and pair with progress, notes and published versions.
- Night mode without green or blue light, and a pure-red palette.
- Help me (F1): the whole workflow guide inside the app.
- Windows installer (per user, no admin rights), portable zip, `--selftest` and `--make-example`.

**Known limitations**
- Windows only, one instrument. The installer is not code-signed (SmartScreen asks once). *Run all* is hidden: run
  the Reduce steps one by one. Uploading to AVSpec is done by you on the AAVSO website.
