#!/usr/bin/env bash
# APV Code Replacement Generator - launcher (macOS / Linux)
#
# The virtual environment is created OUTSIDE this folder so Google Drive never
# syncs it. A venv is ~50MB of small, disposable, machine-specific files.
# To rebuild it, delete the directory printed below.
set -e
cd "$(dirname "$0")"

VENVDIR="${XDG_CACHE_HOME:-$HOME/.cache}/apv-roster-web/.venv"
PY="$VENVDIR/bin/python"

if [ ! -x "$PY" ]; then
  echo "Creating virtual environment..."
  echo "  $VENVDIR"
  python3 -m venv "$VENVDIR"
  "$PY" -m pip install --upgrade pip >/dev/null
fi

if ! "$PY" -c "import flask, requests" >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "$PY" -m pip install -r requirements.txt
fi

echo
echo "Starting server at http://127.0.0.1:5000  (Ctrl+C to stop)"
echo
exec "$PY" app.py
