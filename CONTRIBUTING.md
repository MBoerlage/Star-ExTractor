# Contributing

Thank you for helping. Bug reports with a log file are the most useful contribution.

## Reporting a bug
Use the *Bug report* issue form. Please include the app version (About window, or `StarExtractor.exe --version`),
your Windows version and `error.log` from `%APPDATA%\starex_reduce\` (the error window has *Open log folder*).
Never attach files that contain your AAVSO observer code or site if you do not want them public.

## Working on the code
```
git clone <this repository>
cd <repository>
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\ruff check .
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m starex_reduce.app
```
* Windows and Python 3.11 or newer. Tests run headless (`QT_QPA_PLATFORM=offscreen` in CI).
* Keep the core (`starex_reduce/core`) free of Qt; long work runs in workers, never on the UI thread.
* `ruff check .` and `pytest -q` must pass. Add a test for every fix.
* No new runtime dependencies without discussing them first in an issue.
* Network use stays limited to what the README lists (SIMBAD/Sesame, one VSX lookup per click).
* If you change the credit roll (`starex_reduce/ui/about.py`) or a dependency, run `python tools/notices.py`.

## Building the installer
`pip install -e ".[dev,build]"`, install Inno Setup 6, then
`powershell -ExecutionPolicy Bypass -File packaging\build.ps1`. See `docs/PACKAGING.md`.
