# Packaging and publishing on GitHub (milestone M8)

Goal: people without Python download a Windows installer (or zip) from the GitHub Releases page, double-click,
and run. Windows only for now. This file describes what is built (updated 2026-11-17 with the M8 build).

## One command
```
pip install -e ".[dev,build]"            # PyInstaller is in the "build" extra
winget install JRSoftware.InnoSetup      # Inno Setup 6 (ISCC.exe), per-user is fine
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Python .venv\Scripts\python.exe]
```
`build.ps1` produces, in order:
1. `dist\StarExtractor\`: PyInstaller **one-folder**, windowed build with the icon (`packaging/starex_extractor.spec`).
   Not one-file: that unpacks to a temp folder on every launch and slows the start. No UPX.
2. Runs `StarExtractor.exe --selftest` and `--selftest-gui` **on the frozen build** and stops on a failure.
3. `dist\release\StarExtractor-<version>-portable.zip`, `dist\release\StarExtractor-Setup-<version>.exe`
   (Inno Setup, `packaging/installer.iss`) and `dist\release\SHA256SUMS.txt`.

The version comes from `pyproject.toml` only: the package reads it from its installed metadata (bundled in the
build by `copy_metadata`), `build.ps1` reads it from the file for the installer, and the release workflow checks
that the tag (`v<version>`) matches.

## What is bundled (listed explicitly in the spec)
* `starex_reduce/reference_spectra/*` (Pickles library), `starex_reduce/data/*` (reference stars, telluric lines,
  extinction, camera table), `starex_reduce/resources/*` (logo SVGs, `starex.ico`), `starex_reduce/ui/icons`,
  `starex_reduce/ui/about` (splash).
* `astropy-iers-data` (so astropy never needs a download), the package metadata, every `starex_reduce` submodule
  (the screens are imported by name when first opened).
* A project hook `packaging/hooks/hook-astropy.py` replaces the contrib astropy hook: the app only reads and writes
  FITS, so only `astropy.io.fits`, `astropy.utils`, `units`, `constants`, `config`, `extern` and the unit-parser
  tables are collected (the contrib hook imports `astropy.visualization`, which needs matplotlib).
* Excluded: Qt WebEngine, Quick/QML, 3D, Multimedia, PDF, Charts and the other Qt modules the app does not use,
  tkinter, matplotlib, test packages. `PySide6.QtOpenGL` stays: pyqtgraph imports it for every plot.
* Icon: `starex_reduce/resources/starex.ico` (16-256 px, rebuilt by `tools/make_icon.py`) for the exe, the
  installer (`SetupIconFile`) and the uninstall entry (`UninstallDisplayIcon`). The Start-menu shortcut carries
  AppUserModelID `StarExTractor.App`, the same as the app sets, so the taskbar groups the window under it.

## User data
Never in the install folder: settings, instrument profile, master library, saved responses, VSX cache and
`error.log` live in `%APPDATA%\starex_reduce\` (the existing location; the master library folder can be changed in
Settings). Verified by running the frozen app (self-tests and normal start) from a folder the user cannot write to.

## Self-tests
* `StarExtractor.exe --selftest`: no window. Loads every bundled data file, writes the small synthetic example
  night (full width, 320 rows: 3 + 3 lights, 3 darks, 3 bias), builds the masters, stacks, runs Geometry, 1D
  Extract, Wavelength and Response, writes an AVSpec file and validates it (pre-flight checks and an astropy read
  back). Prints one line per step and `self-test PASSED`, exit code 0 (1 on any failure). A few seconds. It uses
  its own temporary settings folder, never the user's.
* `StarExtractor.exe --selftest-gui` (hidden): builds every screen offscreen; catches a Qt module or plugin missing
  from a build.
* `StarExtractor.exe --make-example <folder>` writes the same small night for the README walkthrough;
  `--version` prints the version. The windowed exe attaches to the console it was started from for these.

## Errors
Unhandled exceptions go to `%APPDATA%\starex_reduce\error.log` (full traceback) and a dialog with one plain line
and **Open log folder**.

## Splash screen: not added
Rule: add one only if the cold start of the frozen app is over about 1.5 s. Measured on the build machine (the
release build, no Python on PATH): warm starts show the window after 0.73-0.76 s (all screens ready after 1.43-1.45 s);
the first launch of a freshly copied folder showed it after 0.84-1.5 s (all screens after 1.7-5.2 s, most likely
Windows Defender scanning the new files). That is at the threshold, not over it, so there is no splash. Re-measure the
first launch after a reboot on a clean machine; add the splash if the window then takes clearly more than 1.5 s.

## Installer (Inno Setup, `packaging/installer.iss`)
Per-user install by default (`PrivilegesRequired=lowest`, into `%LOCALAPPDATA%\Programs\Star ExTractor`), with the
option to install for all users; Start-menu shortcut, optional desktop shortcut, uninstaller. The uninstaller asks
whether to remove `%APPDATA%\starex_reduce` (default: keep; silent uninstall keeps it). Output
`StarExtractor-Setup-<version>.exe`.

## GitHub workflows
* `.github/workflows/ci.yml`: push and pull request, `windows-latest`, Python 3.11, `ruff`, notices check,
  `pytest` (offscreen Qt). Tests that need `example_spectra` are marked `realdata` and skipped.
* `.github/workflows/release.yml`: on a tag `v*`, checks the tag against `pyproject.toml`, installs Inno Setup,
  runs `build.ps1` (build, frozen self-tests, installer, zip, SHA-256 sums) and creates the GitHub Release with
  the files and the tag message as notes (`gh release create`, `GITHUB_TOKEN`).
* Actions are pinned to release tags (`actions/checkout@v4.2.2`, `actions/setup-python@v5.3.0`); pin them to
  commit SHAs if you prefer.

## Windows warning and signing
Unsigned executables show a SmartScreen warning ("More info", then "Run anyway"); the README says so and lists the
checksums file. Signing is optional later.

## Repo hygiene
* Licence: MIT (`LICENSE`, `license = "MIT"` in `pyproject.toml`).
* `THIRD_PARTY_NOTICES.md` and the README *Credits* are generated by `tools/notices.py` from the app's credit roll
  (`starex_reduce/ui/about.py`) and the installed package metadata; CI fails when they are out of date.
* Not in the repository: `example_spectra/` (the real nova night, about 790 MB; download link in
  `docs/EXAMPLE_NOVA.md`), build output, sessions, settings, screenshots other than `screenshots/latest/`,
  `CLAUDE.md` and `BUILD_PROMPT.md` (local working notes).
* `CONTRIBUTING.md` and issue forms (`.github/ISSUE_TEMPLATE/`: bug report asking for the app version, Windows
  version and the log file; feature request).

## Acceptance (what still needs a person)
1. A clean Windows machine or Sandbox without Python: run the installer, start the app, run `--selftest`.
2. Cold start measured there (first launch after a reboot).
3. The packaged app on the synthetic example: every step, Publish the four files, uninstall.
4. A tag push on the real repository produces a Release with installer, zip and checksums.
