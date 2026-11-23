# Changelog

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
