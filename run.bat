@echo off
setlocal
REM APV Code Replacement Generator - launcher
REM
REM The virtual environment lives OUTSIDE this folder so Google Drive never syncs it
REM (a venv is ~50MB of small, disposable, machine-specific files).
REM
REM It is also deliberately NOT under %LOCALAPPDATA%. Microsoft Store builds of Python
REM run in an app container that silently redirects AppData writes into a per-package
REM LocalCache folder, so a venv requested there gets created somewhere else and the
REM launcher can never find it again. %USERPROFILE% is not redirected.
REM
REM To rebuild from scratch, delete the folder printed below.

cd /d "%~dp0"

REM Do not write .pyc files. __pycache__ in a Drive-synced folder is pure churn, and
REM stale bytecode can make an updated .py look like it never changed.
set "PYTHONDONTWRITEBYTECODE=1"

set "VENVDIR=%USERPROFILE%\.apv-roster-web\.venv"
set "PY=%VENVDIR%\Scripts\python.exe"

if not exist "%PY%" (
  echo Creating virtual environment...
  echo   %VENVDIR%
  py -3 -m venv "%VENVDIR%" 2>nul
  if not exist "%PY%" python -m venv "%VENVDIR%" 2>nul
  if not exist "%PY%" (
    echo.
    echo Could not create the virtual environment at:
    echo   %VENVDIR%
    echo.
    echo Most likely cause: Python was installed from the Microsoft Store, which
    echo sandboxes file writes and relocates them without telling you. Installing
    echo Python from python.org instead fixes it for good - tick "Add python.exe
    echo to PATH" during setup.
    echo.
    echo To check what you have:  python --version
    echo.
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip >nul 2>&1
)

REM Install dependencies only if they are actually missing.
"%PY%" -c "import flask, requests" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Dependency install failed. Check your internet connection.
    pause
    exit /b 1
  )
)

REM Clear any bytecode left behind by an earlier run, so the code on disk is the
REM code that runs. This is what makes "I updated the file" actually take effect.
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul

echo.
echo Starting server at http://127.0.0.1:5000
echo Press Ctrl+C in this window to stop it.
echo.
echo NOTE: if you just updated the app files, this window IS the restart -
echo Python loads the code once at startup, so edits need a fresh launch.
echo.
start "" http://127.0.0.1:5000
"%PY%" app.py
pause
