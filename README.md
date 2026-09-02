# APV Code Replacement Generator — web edition

The Tkinter app, rebuilt as a small Flask web app. Pick a league, pick a game off the
schedule, hit generate, download the tab-delimited code replacement file, load it into
Photo Mechanic yourself. No photos, no uploads, nothing written to disk on the server.

## Running it

**Windows:** double-click `run.bat`. First run creates a virtualenv and installs Flask;
after that it just starts and opens the browser.

**macOS / Linux:** `chmod +x run.sh && ./run.sh`

### Where the virtualenv lives

Deliberately **not** in this folder. This project sits in Google Drive, and a venv is
~50MB of small, disposable, machine-specific files that Drive would sync forever for no
benefit. So the launchers put it in a local cache instead:

- Windows: `%LOCALAPPDATA%\apv-roster-web\.venv`
- macOS / Linux: `~/.cache/apv-roster-web/.venv`

Delete that directory to force a clean rebuild on next launch. The launchers also skip
the install step entirely once the dependencies are present, so second and later starts
are instant.

**Manual:**

```
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

## What changed from the desktop version

| Desktop | Web |
| --- | --- |
| Three tabs (NFL / NCAA FB / NCAA MBB) | One league dropdown, plus **NBA** |
| Two team dropdowns you filled in yourself | Date + schedule → pick the actual game; teams and prefixes auto-fill |
| Native save dialog | Browser download, filenames unchanged |
| Console output tab | Inline preview pane, counts, and a notes list for duplicates / missing data |
| — | "Pick teams manually" escape hatch for practices, media days, and anything not on a schedule |

The **window** control next to the date matters more than it looks. Football defaults to a
7-day window because NFL and college weeks straddle Thursday through Monday, so one date
picker click gets you the whole slate. Basketball defaults to a single day because there
are 100+ games on a Tuesday in January.

## What did not change

The generation logic is a straight port. Same four code formats, same
`PREFIX + jersey` codes, same `PREFIX + HC` head coach entry, same duplicate-jersey
handling (`O`/`D` for football, `G`/`F`/`C` for basketball, position abbreviation for
special teams), same sort order, same tab-delimited output with an uppercase row and a
lowercase twin row for case-insensitive matching.

`test_output_parity.py` locks that behaviour in with stubbed ESPN responses — run
`python test_output_parity.py` and it checks all of it offline in about a second.

## Files

```
app.py                   Flask routes: /, /api/schedule, /api/teams, /api/generate
espn.py                  All ESPN calls + code-replacement generation (no UI, testable)
templates/index.html     Front end — one file, no build step, no dependencies
test_output_parity.py    Offline behaviour checks
requirements.txt         Flask, requests
run.bat / run.sh         Launchers
```

## Notes

- ESPN's public API needs no key, but it is undocumented and can change shape. Every
  network call raises a readable error that surfaces in the UI rather than crashing.
- Team lists cache for 30 minutes, rosters for 5, so flipping between games in one
  session is fast and stays polite to ESPN.
- Rosters go stale in-season. If a jersey number looks wrong, wait a minute for the
  5-minute cache to expire, or restart the server.
- Nothing about this is per-user, so if you ever want it on a shared box for the rest of
  the desk, it will work as-is behind any WSGI server — change the `app.run` host to
  `0.0.0.0` or front it with waitress/gunicorn.
