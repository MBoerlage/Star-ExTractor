@echo off
rem Creates a local venv on first run, installs the app, then starts it.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First run: creating virtual environment...
  py -3.11 -m venv .venv || py -3 -m venv .venv || goto :err
  .venv\Scripts\python -m pip install --upgrade pip || goto :err
  .venv\Scripts\python -m pip install -e .[dev] || goto :err
)
.venv\Scripts\python -m starextractor.app %*
if errorlevel 1 pause
exit /b
:err
echo Setup failed. Is Python 3.11 or newer installed?
pause
