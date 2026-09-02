"""
app.py — web front end for the APV Photo Mechanic code replacement generator.

Nothing is stored server-side. Every request is: pick a league -> pick a game ->
pull rosters from the data feed -> hand back text. The browser turns that text
into a .txt download locally, so there are no files on disk and no uploads.

Run locally:  python app.py        then open http://127.0.0.1:5000
Run hosted:   gunicorn wsgi:app    (see wsgi.py)

Two environment variables switch on the hosted behaviour. Both are unset locally,
so nothing below changes how the app behaves on your own machine:

    BETA_PASSCODE   if set, visitors must enter this once before using the app
    SECRET_KEY      signs the session cookie; set to any long random string
"""

import os
import re
import time
from collections import deque
from datetime import datetime

from flask import (
    Flask, jsonify, redirect, render_template, render_template_string,
    request, session, url_for,
)

import espn

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-dev-key-not-a-secret")

# --- private beta gate -----------------------------------------------------
# One shared passcode, not user accounts. Enough to keep the beta among people
# you invited, which also keeps request volume low — the upstream feed is far
# likelier to start refusing a server that looks like a crawler than one that
# looks like a handful of photographers.
BETA_PASSCODE = os.environ.get("BETA_PASSCODE")

UNLOCK_PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Access code</title>
<style>
 @import url('https://fonts.googleapis.com/css2?family=Jost:wght@300;400;500&display=swap');
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#fff;color:#2d2d2d;
      font:15px/1.6 'Jost','Century Gothic',Futura,sans-serif}
 form{width:300px;text-align:center}
 h1{font-size:15px;font-weight:300;letter-spacing:.16em;text-transform:uppercase;margin:0 0 22px}
 input{width:100%;padding:10px 12px;border:1px solid #e6e2df;border-radius:2px;
       font-family:inherit;font-size:14px;text-align:center;letter-spacing:.1em}
 input:focus{outline:none;border-color:#fd1a1a}
 button{width:100%;margin-top:10px;padding:11px;border:0;border-radius:2px;background:#2d2d2d;
        color:#fff;font-family:inherit;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
        cursor:pointer}
 button:hover{background:#fd1a1a}
 .err{color:#d11f1f;font-size:12.5px;margin-top:14px;min-height:18px}
</style>
<form method=post>
  <h1>Access code</h1>
  <input name=code type=password autofocus autocomplete="current-password">
  <button type=submit>Enter</button>
  <div class=err>{{ error }}</div>
</form>"""


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    if not BETA_PASSCODE:
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        if request.form.get("code", "") == BETA_PASSCODE:
            session["beta"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Not that one."
    return render_template_string(UNLOCK_PAGE, error=error)


# --- light rate limiting ---------------------------------------------------
# Protects the upstream feed from one enthusiastic tab, and this server from
# anyone who finds the URL. Deliberately in-memory: a beta runs on one instance,
# and a dependency-free limiter is one less thing to break on a Sunday.
# NOTE: each gunicorn worker keeps its own counter, so the effective ceiling is
# _RATE_MAX x worker count. Two workers x 45 = ~90/min per visitor, which is what
# this is tuned for. Change the worker count and this number should move with it.
_HITS = {}
_RATE_MAX = 45          # requests per window, per IP, PER WORKER
_RATE_WINDOW = 60.0     # seconds


def _rate_limited(ip):
    now = time.time()
    q = _HITS.setdefault(ip, deque())
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_MAX:
        return True
    q.append(now)
    if len(_HITS) > 2000:                      # keep the dict from growing forever
        for k in [k for k, v in _HITS.items() if not v or now - v[-1] > 600]:
            _HITS.pop(k, None)
    return False


@app.before_request
def _guard():
    if request.endpoint in ("unlock", "static"):
        return None
    if BETA_PASSCODE and not session.get("beta"):
        return redirect(url_for("unlock"))
    if request.path.startswith("/api/"):
        ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
              .split(",")[0].strip())
        if _rate_limited(ip):
            return jsonify({"error": "Too many requests just now — give it a minute."}), 429
    return None


@app.route("/")
def index():
    return render_template(
        "index.html",
        leagues=[
            {
                "key": k,
                "label": v["label"],
                "default_window": v["default_window_days"],
            }
            for k, v in espn.LEAGUES.items()
        ],
        formats=espn.CODE_FORMATS,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/api/schedule")
def api_schedule():
    league = request.args.get("league", "")
    date = (request.args.get("date") or "").replace("-", "")
    days = request.args.get("days")
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    if league not in espn.LEAGUES:
        return jsonify({"error": f"Unknown league '{league}'"}), 400
    if not re.fullmatch(r"\d{8}", date):
        return jsonify({"error": "Date must be a valid calendar date."}), 400
    try:
        games = espn.get_schedule(league, date, days)
    except espn.ESPNError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"games": games})


@app.route("/api/teams")
def api_teams():
    league = request.args.get("league", "")
    try:
        return jsonify({"teams": espn.get_teams(league)})
    except espn.ESPNError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/diagnose")
def api_diagnose():
    """Self-test: hits every ESPN endpoint the app uses and reports what happened."""
    league = request.args.get("league", "nfl")
    if league not in espn.LEAGUES:
        return jsonify({"error": f"Unknown league '{league}'"}), 400
    try:
        return jsonify(espn.diagnose(league))
    except espn.ESPNError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/generate", methods=["POST"])
def api_generate():
    body = request.get_json(silent=True) or {}
    league = body.get("league", "")
    code_format = body.get("format", "full_with_number")
    include_coaches = bool(body.get("include_coaches", True))

    try:
        cfg = espn.league_cfg(league)
        home_team = espn.get_team_by_id(league, body.get("home_id", ""))
        away_team = espn.get_team_by_id(league, body.get("away_id", ""))
        if not home_team or not away_team:
            return jsonify({"error": "Could not resolve both teams for that game."}), 400

        home_prefix = (body.get("home_prefix") or home_team["abbreviation"]).strip()
        away_prefix = (body.get("away_prefix") or away_team["abbreviation"]).strip()

        home, away, combined = espn.build_for_matchup(
            league,
            home_team,
            away_team,
            home_prefix,
            away_prefix,
            code_format,
            include_coaches,
        )
    except espn.ESPNError as e:
        return jsonify({"error": str(e)}), 502

    stamp = datetime.now().strftime("%Y%m%d")
    fp = cfg["file_prefix"]

    def team_file(team):
        return f"{fp}_{team['name'].replace(' ', '_')}_{stamp}.txt"

    return jsonify(
        {
            "combined": {
                "filename": (
                    f"{fp}_{home_prefix.upper()}_vs_{away_prefix.upper()}_{stamp}.txt"
                ),
                "content": combined,
            },
            "home": {
                "team": home_team["name"],
                "prefix": home_prefix,
                "filename": team_file(home_team),
                "content": home["content"],
                "players": home["players"],
                "codes": home["codes"],
                "entries": home["entries"],
                "log": home["log"],
            },
            "away": {
                "team": away_team["name"],
                "prefix": away_prefix,
                "filename": team_file(away_team),
                "content": away["content"],
                "players": away["players"],
                "codes": away["codes"],
                "entries": away["entries"],
                "log": away["log"],
            },
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
