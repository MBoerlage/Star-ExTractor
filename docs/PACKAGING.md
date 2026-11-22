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

The version comes from `pyproject.toml` only: a source run reads the file, the frozen build the bundled metadata
folder (`starextractor-<version>.dist-info`, from `copy_metadata`), both on first use of `__version__` so the
package import stays cheap; `build.ps1` reads it from the file for the installer, and the release workflow checks
that the tag (`v<version>`) matches.

## What is bundled (listed explicitly in the spec)
* `starextractor/reference_spectra/*` (Pickles library), `starextractor/data/*` (reference stars, telluric lines,
  extinction, camera table), `starextractor/resources/*` (logo SVGs, `starex.ico`, the splash images), `starextractor/ui/icons`,
  `starextractor/ui/about` (splash).
* `astropy-iers-data` (so astropy never needs a download), the package metadata, every `starextractor` submodule
  (the screens are imported by name when first opened).
* A project hook `packaging/hooks/hook-astropy.py` replaces the contrib astropy hook: the app only reads and writes
  FITS, so only `astropy.io.fits`, `astropy.utils`, `units`, `constants`, `config`, `extern` and the unit-parser
  tables are collected (the contrib hook imports `astropy.visualization`, which needs matplotlib).
* Excluded: Qt WebEngine, Quick/QML, 3D, Multimedia, PDF, Charts and the other Qt modules the app does not use,
  tkinter, matplotlib, test packages. `PySide6.QtOpenGL` stays: pyqtgraph imports it for every plot.
* Icon: `starextractor/resources/starex.ico` (16-256 px, rebuilt by `tools/make_icon.py`) for the exe, the
  installer (`SetupIconFile`) and the uninstall entry (`UninstallDisplayIcon`). The Start-menu shortcut carries
  AppUserModelID `StarExTractor.App`, the same as the app sets, so the taskbar groups the window under it.

## User data
Never in the install folder: settings, instrument profile, saved responses, VSX cache and `error.log` live in
`%APPDATA%\StarExtractor\` (before the rename `%APPDATA%\starex_reduce\`, copied once and left in place); the data
(stacks, masters, processed pairs, published files) lives under the data root chosen at the first start. The
uninstaller asks before removing either settings folder (a silent uninstall keeps them) and never touches the data
root. Verified by running the frozen app (self-tests and normal start) from a folder the user cannot write to.

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
Unhandled exceptions go to `%APPDATA%\StarExtractor\error.log` (full traceback) and a dialog with one plain line
and **Open log folder**.

## Splash screen (2026-11-20): two stages
**Stage 1, the PyInstaller bootloader splash** (`Splash(...)` in `packaging/starex_extractor.spec`): the bootloader
shows `starextractor/resources/splash.png` (800 × 448, the same image as stage 2) before Python starts. It is
centred on the screen with the mouse (`center="active"`, as stage 2) and drawn at its real pixel size: the exe's
manifest (`packaging/StarExtractor.manifest`, PyInstaller's default plus per-monitor-v2 DPI awareness, what Qt sets
anyway) stops Windows from stretching it. `app.py` closes it with `pyi_splash.close()` once the Qt splash is on
screen; while stage 1 is up, the Qt splash uses the same 1x image at its native size (also at 150 % and more), so the
hand-off is seamless (measured: same size, within 1 px).
* **Needs Tcl/Tk** in the building Python (PyInstaller bundles `tcl86t.dll`, `tk86t.dll` and the minimal Tcl data,
  about 3 MB). Without it the spec prints `WARNING: bootloader splash left out (...)` and builds without stage 1;
  the build never fails for it. The image limit is set to 800 × 448 (`max_img_size`; PyInstaller's default is 760 ×
  480 and it cannot resize without Pillow, which is not a dependency).
* **Turn it off:** `build.ps1 -NoBootSplash` (or `STAREX_BOOT_SPLASH=0` for a direct PyInstaller run). On by default.
* Stage 1 appears before the settings can be read, so Settings › *Show splash screen* and `--no-splash` hide only
  stage 2; with them, stage 1 is closed as soon as Python runs. The command-line modes (`--selftest`, `--version`, …)
  close it at once.

**Stage 2, the Qt splash** (`starextractor/ui/splash.py`, every run): see `docs/SPEC.md` › Start-up.

**Assets** (`tools/make_splash.py`, from `Prism casting a horizontal stellar spectrum.png`, the About image):
`resources/splash.png` 800 × 448, 441 KB; `resources/splash@2x.png` 1600 × 896, 1384 KB (PNG, strongest zlib).
Decoding them takes 3 ms and 7 ms. Both are in the bundle through the `resources` folder.

**Measured** (this machine, 7 runs each interleaved, medians, ms from the launch; `tools/startup_bench.py --exe`
and a window-detection script):

| Frozen build | first thing on screen | window | ready |
|---|---|---|---|
| before the splash | the main window, 1363 | 1363 | - |
| stage 1 + 2 | bootloader splash, 135 (Qt splash 463) | 1270 | 2048 |
| stage 2 only (`-NoBootSplash`) | Qt splash, 316 | 1142 | 1924 |
| no splash at all | the main window, 996 | 1108 | 1865 |

Stage 1 costs about 120 ms to the window (the bootloader loads Tcl/Tk before Python); the window still comes sooner
than before the splash work. "Ready" here includes building the page the app reopens on (Plan on this machine).

## Installer (Inno Setup, `packaging/installer.iss`)
Per-user install by default (`PrivilegesRequired=lowest`, into `%LOCALAPPDATA%\Programs\Star ExTractor`), with the
option to install for all users; Start-menu shortcut, optional desktop shortcut, uninstaller. The uninstaller asks
whether to remove `%APPDATA%\StarExtractor` (default: keep; silent uninstall keeps it). Output
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
  (`starextractor/ui/about.py`) and the installed package metadata; CI fails when they are out of date.
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
