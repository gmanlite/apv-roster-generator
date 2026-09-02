"""
espn.py — core data + code-replacement logic for the Photo Mechanic roster generator.

Ported verbatim (behaviour-wise) from apv_code_replacement.py. Contains no UI code:
everything here is pure "fetch from ESPN -> return structured text", so it can be
driven by a web request, a CLI, or a test.

Output format is byte-identical to the Tkinter version:
    tab-delimited, \n line endings, one row per code, plus a lowercase twin row
    for case-insensitive matching in Photo Mechanic.
"""

import csv
import re
import time
from io import StringIO

import requests

TIMEOUT = 15

# ESPN's site API is undocumented and sits behind a bot filter that returns 403 for
# clients that announce themselves as tools. An earlier build of this app sent
# "APV-Roster-Generator/2.0" and got exactly that. Present as a browser instead.
#
# HEADER_SETS is tried in order until one is accepted. Browser-shaped first; the
# plain python-requests default second, because that is what the original desktop
# app sent and it worked for years, so it is a genuine fallback rather than a guess.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/",
    "Origin": "https://www.espn.com",
}

HEADER_SETS = (BROWSER_HEADERS, None)  # None = leave the session defaults alone

# ---------------------------------------------------------------------------
# League configuration
# ---------------------------------------------------------------------------
# kind         : drives position naming and the duplicate-jersey suffix scheme.
# roster_style : 'grouped' -> ESPN has a /roster endpoint already split by position
#                'flat'    -> roster arrives as one list via ?enable=roster and we
#                             group it ourselves. Basketball is the odd one out.
LEAGUES = {
    "nfl": {
        "label": "NFL",
        "sport": "football",
        "path": "nfl",
        "kind": "football",
        "roster_style": "grouped",
        "file_prefix": "NFL",
        "scoreboard_params": {"limit": 100},
        "default_window_days": 7,
    },
    "college-football": {
        "label": "NCAA Football",
        "sport": "football",
        "path": "college-football",
        "kind": "football",
        "roster_style": "grouped",
        "file_prefix": "NCAA",
        "scoreboard_params": {"limit": 300},
        "default_window_days": 7,
    },
    "mens-college-basketball": {
        "label": "NCAA Men's Basketball (D1)",
        "sport": "basketball",
        "path": "mens-college-basketball",
        "kind": "basketball",
        "roster_style": "flat",
        "file_prefix": "NCAABB",
        "scoreboard_params": {"groups": 50, "limit": 400},
        "default_window_days": 1,
    },
    "nba": {
        "label": "NBA",
        "sport": "basketball",
        "path": "nba",
        "kind": "basketball",
        "roster_style": "flat",
        "file_prefix": "NBA",
        "scoreboard_params": {"limit": 100},
        "default_window_days": 1,
    },
    "mlb": {
        "label": "MLB",
        "sport": "baseball",
        "path": "mlb",
        "kind": "baseball",
        "roster_style": "grouped",
        "file_prefix": "MLB",
        "scoreboard_params": {"limit": 100},
        "default_window_days": 1,
    },
    "nhl": {
        "label": "NHL",
        "sport": "hockey",
        "path": "nhl",
        "kind": "hockey",
        "roster_style": "grouped",
        "file_prefix": "NHL",
        "scoreboard_params": {"limit": 100},
        "default_window_days": 1,
    },
}

BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN runs a second, older API alongside the site one. It is more verbose (records
# are linked by $ref rather than inlined) but it still carries coach data, which the
# site API quietly stopped returning. See get_head_coach.
CORE_BASE = "https://sports.core.api.espn.com/v2/sports"

CODE_FORMATS = [
    ("full_with_number", "Prefix+Jersey#  ->  Team position Player Name (Jersey#)"),
    ("full_no_number", "Prefix+Jersey#  ->  Team position Player Name"),
    ("position_with_number", "Prefix+Jersey#  ->  position Player Name (Jersey#)"),
    ("position_only", "Prefix+Jersey#  ->  position Player Name"),
]


class ESPNError(RuntimeError):
    pass


_session = requests.Session()

# Simple in-process cache so a page reload doesn't re-pull 350 college teams.
_cache = {}
_CACHE_TTL = 60 * 30  # 30 minutes for team lists
_ROSTER_TTL = 60 * 5  # 5 minutes for rosters/coaches


def _cached(key, ttl, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    return value


def _describe(url, params):
    """
    Short, host-free label for an endpoint, used in error messages.

    Deliberately drops the scheme and domain: these strings surface in the UI, and
    the provider's name does not belong on screen. What is useful to a person
    reading an error is which call failed, not where it lives.
    """
    path = url.split("://", 1)[-1]
    path = path.split("/", 1)[-1] if "/" in path else path
    for marker in ("/sports/", "sports/"):
        if marker in path:
            path = path.split(marker, 1)[1]
            break
    if params:
        bits = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{path}?{bits}"
    return path


def _get(url, params=None):
    """
    GET + parse JSON, with one bare-headers retry.

    Errors are raised as ESPNError with the HTTP status included, because "502 from
    ESPN" and "your machine has no internet" are very different problems and the UI
    should say which one happened.
    """
    last = None
    for headers in HEADER_SETS:
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT, headers=headers)
        except requests.Timeout:
            last = f"timed out after {TIMEOUT}s"
            continue
        except requests.ConnectionError as e:
            raise ESPNError(
                "Could not reach the data service. Check your internet connection, "
                "or whether a firewall or VPN is blocking it."
            ) from e
        except requests.RequestException as e:
            raise ESPNError(f"Data request failed: {e}") from e

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                raise ESPNError(
                    "The data service answered but the body was not JSON "
                    f"({_describe(url, params)}). The endpoint may have changed."
                ) from e

        last = f"HTTP {r.status_code}"
        if r.status_code not in (403, 429):
            break  # only the "you look like a bot" codes are worth retrying

    hint = ""
    if last in ("HTTP 403", "HTTP 429"):
        hint = (
            " The request was refused. That is a bot filter, not a bad address - "
            "it usually clears on its own; a VPN or corporate network makes it more "
            "likely."
        )
    raise ESPNError(f"Connection returned {last} for {_describe(url, params)}.{hint}")


def league_cfg(league):
    cfg = LEAGUES.get(league)
    if not cfg:
        raise ESPNError(f"Unknown league '{league}'")
    return cfg


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def get_teams(league):
    """Full alphabetical team list for a league (used by the manual picker)."""
    cfg = league_cfg(league)

    def load():
        url = f"{BASE}/{cfg['sport']}/{cfg['path']}/teams"
        data = _get(url, params={"limit": 1000})
        try:
            teams_data = data["sports"][0]["leagues"][0]["teams"]
        except (KeyError, IndexError) as e:
            raise ESPNError(f"Unexpected team-list payload for {league}: {e}") from e

        teams = []
        for entry in teams_data:
            info = entry["team"]
            abbrev = info.get("abbreviation") or info.get("shortDisplayName") or ""
            teams.append(
                {
                    "id": str(info["id"]),
                    "name": info["displayName"],
                    "abbreviation": abbrev,
                    "nickname": info.get("name", info["displayName"]),
                    "location": info.get("location", ""),
                }
            )
        teams.sort(key=lambda t: t["name"])
        return teams

    return _cached(f"teams:{league}", _CACHE_TTL, load)


def get_team_by_id(league, team_id):
    team_id = str(team_id)
    for t in get_teams(league):
        if t["id"] == team_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Schedule / scoreboard
# ---------------------------------------------------------------------------

def get_schedule(league, start_date, days=None):
    """
    Games for a league over a date window.

    start_date : 'YYYYMMDD'
    days       : window size; 1 = that day only, 7 = that day + next six.
                 Falls back to the league default (7 for football, 1 for the
                 daily-schedule sports).

    Returns a list of dicts the front end can drop straight into a <select>.
    """
    cfg = league_cfg(league)
    if days is None:
        days = cfg["default_window_days"]
    days = max(1, min(int(days), 14))

    if not re.fullmatch(r"\d{8}", str(start_date)):
        raise ESPNError("Date must be in YYYYMMDD form")

    if days == 1:
        dates = str(start_date)
    else:
        end = _shift_date(str(start_date), days - 1)
        dates = f"{start_date}-{end}"

    params = dict(cfg["scoreboard_params"])
    params["dates"] = dates

    url = f"{BASE}/{cfg['sport']}/{cfg['path']}/scoreboard"
    data = _get(url, params=params)

    games = []
    for event in data.get("events", []):
        comps = event.get("competitions") or []
        if not comps:
            continue
        competitors = comps[0].get("competitors", [])
        home = away = None
        for c in competitors:
            team = c.get("team", {})
            side = {
                "id": str(team.get("id", "")),
                "name": team.get("displayName", "Unknown"),
                "abbreviation": (
                    team.get("abbreviation") or team.get("shortDisplayName") or ""
                ),
            }
            if c.get("homeAway") == "home":
                home = side
            elif c.get("homeAway") == "away":
                away = side
        if not home or not away:
            continue

        status = (
            comps[0].get("status", {}).get("type", {}).get("shortDetail")
            or event.get("status", {}).get("type", {}).get("shortDetail")
            or ""
        )
        games.append(
            {
                "id": str(event.get("id", "")),
                "label": f"{away['name']} at {home['name']}",
                "date": event.get("date", ""),
                "status": status,
                "home": home,
                "away": away,
            }
        )

    games.sort(key=lambda g: (g["date"], g["label"]))
    return games


def _shift_date(yyyymmdd, delta_days):
    from datetime import datetime, timedelta

    d = datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=delta_days)
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Rosters and coaches
# ---------------------------------------------------------------------------

def _group_flat_roster(data):
    """Turn a flat athlete list (?enable=roster) into football-style position groups."""
    athletes = (data.get("team") or {}).get("athletes") or []
    position_groups = {}
    for athlete in athletes:
        position_info = athlete.get("position", {})
        pos_abbrev = (
            position_info.get("abbreviation", "UNK")
            if isinstance(position_info, dict)
            else "UNK"
        )
        if pos_abbrev not in position_groups:
            position_groups[pos_abbrev] = {"position": position_info, "items": []}
        position_groups[pos_abbrev]["items"].append(athlete)
    return {"athletes": list(position_groups.values())}


def get_team_roster(team_id, league):
    """
    Fetch a roster, normalised to {'athletes': [ {position, items:[...]} , ... ]}.

    The feed exposes rosters two different ways and neither is reliable for every
    league on every day — a hosted deployment hit a 404 on the /roster path for an
    NFL team that answers perfectly well from a home connection. So both are tried.

        /teams/{id}/roster        already grouped by position
        /teams/{id}?enable=roster one flat list, grouped here

    The preferred order depends on the sport, but either path can satisfy any
    league, so a failure or an empty answer on one falls through to the other.
    Only if both fail does the error surface.
    """
    cfg = league_cfg(league)
    team_id = str(team_id)

    def load():
        base = f"{BASE}/{cfg['sport']}/{cfg['path']}/teams/{team_id}"

        def grouped():
            return _get(f"{base}/roster")

        def flat():
            return _group_flat_roster(_get(base, params={"enable": "roster"}))

        order = (flat, grouped) if cfg["roster_style"] == "flat" else (grouped, flat)

        last_error = None
        for attempt in order:
            try:
                data = attempt()
            except ESPNError as e:
                last_error = e
                continue
            if data.get("athletes"):
                return data

        if last_error:
            raise last_error
        return {"athletes": []}

    return _cached(f"roster:{league}:{team_id}", _ROSTER_TTL, load)


def get_head_coach(team_id, league):
    """
    Head coach — or manager, in baseball — for a team.

    The site API used to expose this via ?enable=roster,coaches. It no longer does:
    that call still returns a roster but the 'coaches' key is simply absent, which
    is why every generated file was silently missing its HC line. ESPN's *core* API
    still carries the data, behind a two-step lookup:

        .../seasons/{season}/teams/{id}/coaches   -> a list of $ref links
        .../seasons/{season}/coaches/{coachId}    -> the actual record

    For every league this app supports that collection holds exactly one entry, the
    head coach, so there is no title to filter on — and no title in the payload
    either, which is why the label is derived from the sport instead.
    """
    cfg = league_cfg(league)
    team_id = str(team_id)
    title = "manager" if cfg["kind"] == "baseball" else "head coach"

    def load():
        from datetime import datetime as _dt

        # Season labelling differs by sport and rolls over mid-year, so try the
        # current season and then the previous one before giving up.
        year = _dt.now().year
        for season in (year, year - 1):
            url = (
                f"{CORE_BASE}/{cfg['sport']}/leagues/{cfg['path']}"
                f"/seasons/{season}/teams/{team_id}/coaches"
            )
            try:
                data = _get(url)
            except ESPNError:
                continue

            items = data.get("items") or []
            if not items:
                continue
            ref = (items[0] or {}).get("$ref")
            if not ref:
                continue

            try:
                coach = _get(ref.replace("http://", "https://", 1))
            except ESPNError:
                continue

            name = " ".join(
                p for p in (coach.get("firstName"), coach.get("lastName")) if p
            ).strip()
            name = name or coach.get("displayName") or coach.get("fullName") or ""
            if name:
                return {"fullName": name, "title": title}

        return None

    return _cached(f"headcoach:{league}:{team_id}", _ROSTER_TTL, load)


# ---------------------------------------------------------------------------
# Position naming
# ---------------------------------------------------------------------------

# Position abbreviations collide across sports, so the maps MUST be per-sport.
# 'C' is center / center / catcher / center. 'G' is guard / goalie. 'P' is punter
# / pitcher. 'SS' is strong safety / shortstop. 'F' is forward / forward. 'D' is
# defense. Looking any of these up in a shared table produces confidently wrong
# captions, which is worse than none.
POSITION_MAPS = {
    "football": {
        "QB": "quarterback",
        "RB": "running back",
        "FB": "fullback",
        "WR": "wide receiver",
        "TE": "tight end",
        "OL": "offensive lineman",
        "OT": "offensive tackle",
        "OG": "offensive guard",
        "G": "guard",
        "C": "center",
        "DL": "defensive lineman",
        "DE": "defensive end",
        "DT": "defensive tackle",
        "NT": "nose tackle",
        "EDGE": "edge",
        "LB": "linebacker",
        "ILB": "inside linebacker",
        "OLB": "outside linebacker",
        "MLB": "middle linebacker",
        "CB": "cornerback",
        "S": "safety",
        "SS": "strong safety",
        "FS": "free safety",
        "DB": "defensive back",
        "K": "kicker",
        "PK": "place kicker",
        "P": "punter",
        "LS": "long snapper",
        "KR": "kick returner",
        "PR": "punt returner",
    },
    "basketball": {
        "PG": "guard",
        "SG": "guard",
        "G": "guard",
        "SF": "forward",
        "PF": "forward",
        "F": "forward",
        "C": "center",
    },
    "baseball": {
        "P": "pitcher",
        "SP": "starting pitcher",
        "RP": "relief pitcher",
        "C": "catcher",
        "1B": "first baseman",
        "2B": "second baseman",
        "3B": "third baseman",
        "SS": "shortstop",
        "IF": "infielder",
        "LF": "left fielder",
        "CF": "center fielder",
        "RF": "right fielder",
        "OF": "outfielder",
        "DH": "designated hitter",
        "UT": "utility player",
        "PH": "pinch hitter",
        "PR": "pinch runner",
    },
    "hockey": {
        "C": "center",
        "LW": "left wing",
        "RW": "right wing",
        "W": "wing",
        "F": "forward",
        "D": "defenseman",
        "G": "goaltender",
    },
}


def get_position_name(position_abbrev, kind="football"):
    """Full position name for an abbreviation, interpreted for the given sport."""
    if not position_abbrev:
        return ""
    table = POSITION_MAPS.get(kind, POSITION_MAPS["football"])
    return table.get(position_abbrev, position_abbrev.lower())


# Duplicate-jersey suffixes. When two players share a number, the suffix has to
# tell them apart at a glance from the sideline, so each sport uses the split its
# people actually think in: O/D in football, G/F/C in basketball,
# pitcher/catcher/infield/outfield in baseball, F/D/G in hockey.
FOOTBALL_OFFENSE = {"QB", "RB", "FB", "WR", "TE", "OL", "OT", "OG", "G", "C"}
FOOTBALL_DEFENSE = {
    "DL", "DE", "DT", "NT", "EDGE", "LB", "ILB", "OLB", "MLB", "CB", "S", "SS", "FS", "DB",
}
FOOTBALL_SPECIAL = {"K", "PK", "P", "LS", "KR", "PR"}

HOOPS_GUARDS = {"PG", "SG", "G"}
HOOPS_FORWARDS = {"SF", "PF", "F"}
HOOPS_CENTERS = {"C"}

BASEBALL_PITCHERS = {"P", "SP", "RP"}
BASEBALL_CATCHERS = {"C"}
BASEBALL_INFIELD = {"1B", "2B", "3B", "SS", "IF"}
BASEBALL_OUTFIELD = {"LF", "CF", "RF", "OF"}

HOCKEY_FORWARDS = {"C", "LW", "RW", "W", "F"}
HOCKEY_DEFENSE = {"D"}
HOCKEY_GOALIES = {"G"}


def _duplicate_suffix(kind, pos_abbrev):
    """Suffix appended when two players on one roster share a jersey number."""
    if kind == "football":
        if pos_abbrev in FOOTBALL_OFFENSE:
            return "O"
        if pos_abbrev in FOOTBALL_DEFENSE:
            return "D"
        return pos_abbrev  # special teams / unknown -> position abbreviation

    if kind == "basketball":
        if pos_abbrev in HOOPS_GUARDS:
            return "G"
        if pos_abbrev in HOOPS_FORWARDS:
            return "F"
        if pos_abbrev in HOOPS_CENTERS:
            return "C"
        return pos_abbrev

    if kind == "baseball":
        if pos_abbrev in BASEBALL_PITCHERS:
            return "P"
        if pos_abbrev in BASEBALL_CATCHERS:
            return "C"
        if pos_abbrev in BASEBALL_INFIELD:
            return "IF"
        if pos_abbrev in BASEBALL_OUTFIELD:
            return "OF"
        return pos_abbrev

    if kind == "hockey":
        if pos_abbrev in HOCKEY_FORWARDS:
            return "F"
        if pos_abbrev in HOCKEY_DEFENSE:
            return "D"
        if pos_abbrev in HOCKEY_GOALIES:
            return "G"
        return pos_abbrev

    return pos_abbrev


# ---------------------------------------------------------------------------
# Code replacement generation
# ---------------------------------------------------------------------------

def generate_code_replacement(
    team_info,
    roster_data,
    custom_prefix,
    code_format,
    include_coaches,
    league,
):
    """Return the tab-delimited code-replacement text for one team."""
    cfg = league_cfg(league)
    kind = cfg["kind"]

    log = []
    replacements = []  # (code, replacement, pos_abbrev, name)
    team_name = team_info["name"]
    team_prefix = (custom_prefix or team_info.get("abbreviation") or "").strip()

    # --- head coach (or manager, in baseball) -----------------------------
    # The code stays PREFIX+HC in every sport so muscle memory carries across,
    # but the caption uses the sport's own word for the job, so an MLB file reads
    # "manager Aaron Boone" rather than miscalling him a head coach.
    if include_coaches:
        head_coach = get_head_coach(team_info["id"], league)
        if head_coach:
            code = f"{team_prefix}HC"
            coach_title = head_coach["title"]
            if code_format in ("full_with_number", "full_no_number"):
                replacement = f"{team_name} {coach_title} {head_coach['fullName']}"
            else:
                replacement = f"{coach_title} {head_coach['fullName']}"
            replacements.append((code, replacement, "HC", head_coach["fullName"]))
        else:
            log.append("No head coach listed for this team.")

    # --- players ----------------------------------------------------------
    position_groups = roster_data.get("athletes", []) or []
    skipped_no_jersey = 0

    for group in position_groups:
        group_position_info = group.get("position", {})
        group_position_abbrev = (
            group_position_info.get("abbreviation", "")
            if isinstance(group_position_info, dict)
            else ""
        )

        for athlete in group.get("items", []) or []:
            name = athlete.get("fullName") or athlete.get("displayName") or "Unknown"
            jersey = str(athlete.get("jersey", "") or "").strip()
            if not jersey:
                skipped_no_jersey += 1
                continue

            position_abbrev = group_position_abbrev
            player_pos = athlete.get("position")
            if isinstance(player_pos, dict):
                position_abbrev = player_pos.get("abbreviation", position_abbrev)

            position = get_position_name(position_abbrev, kind)
            code = f"{team_prefix}{jersey}"

            if code_format == "full_with_number":
                replacement = f"{team_name} {position} {name} ({jersey})"
            elif code_format == "full_no_number":
                replacement = f"{team_name} {position} {name}"
            elif code_format == "position_with_number":
                replacement = f"{position} {name} ({jersey})"
            elif code_format == "position_only":
                replacement = f"{position} {name}"
            else:
                raise ESPNError(f"Unknown code format '{code_format}'")

            replacements.append((code, replacement, position_abbrev, name))

    player_count = len([r for r in replacements if r[2] != "HC"])
    if skipped_no_jersey:
        log.append(f"Skipped {skipped_no_jersey} athlete(s) with no jersey number.")

    # --- duplicate jersey numbers ----------------------------------------
    code_counts = {}
    for code, replacement, pos_abbrev, name in replacements:
        code_counts.setdefault(code, []).append(name)
    duplicates = {c for c, items in code_counts.items() if len(items) > 1}

    final = []
    for code, replacement, pos_abbrev, name in replacements:
        if code in duplicates:
            unique_code = f"{code}{_duplicate_suffix(kind, pos_abbrev)}"
            log.append(f"Duplicate jersey: {code} -> {unique_code} for {name} ({pos_abbrev})")
            final.append((unique_code, replacement))
        else:
            final.append((code, replacement))
    replacements = final

    # --- sort: head coach first, then jersey number, then suffix ----------
    def sort_key(item):
        code = item[0]
        sortable = code[len(team_prefix):] if code.startswith(team_prefix) else code
        if sortable == "HC":
            return (0, 0, "")
        m = re.match(r"^(\d+)([A-Za-z]*)$", sortable)
        if m:
            return (1, int(m.group(1)), m.group(2))
        return (2, 0, sortable)

    replacements.sort(key=sort_key)

    # --- write tab-delimited output, with lowercase twins -----------------
    output = StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    entries = 0
    for code, replacement in replacements:
        writer.writerow([code.upper(), replacement])
        entries += 1
        if code.upper() != code.lower():
            writer.writerow([code.lower(), replacement])
            entries += 1

    return {
        "content": output.getvalue(),
        "players": player_count,
        "codes": len(replacements),
        "entries": entries,
        "duplicates": len(duplicates),
        "log": log,
    }


def diagnose(league="nfl"):
    """
    Hit every endpoint the app depends on and report what each one did.

    This exists so "it isn't working" can be turned into a specific answer without
    reading a stack trace: it distinguishes no-internet, ESPN-refusing-us, and
    ESPN-changed-its-schema, and names which call broke.
    """
    cfg = league_cfg(league)
    results = []

    def step(name, fn, describe):
        try:
            value = fn()
            results.append({"step": name, "ok": True, "detail": describe(value)})
            return value
        except ESPNError as e:
            results.append({"step": name, "ok": False, "detail": str(e)})
        except Exception as e:  # schema surprises land here
            results.append(
                {"step": name, "ok": False, "detail": f"{type(e).__name__}: {e}"}
            )
        return None

    _cache.clear()

    teams = step(
        "Team list",
        lambda: get_teams(league),
        lambda t: f"{len(t)} teams, first is {t[0]['name']} ({t[0]['abbreviation']})",
    )

    from datetime import datetime as _dt

    step(
        "Schedule",
        lambda: get_schedule(league, _dt.now().strftime("%Y%m%d"), 14),
        lambda g: (
            f"{len(g)} games in the next 14 days"
            + (f", e.g. {g[0]['label']}" if g else " (none scheduled — off-season?)")
        ),
    )

    if teams:
        tid, tname = teams[0]["id"], teams[0]["name"]
        roster = step(
            "Roster",
            lambda: get_team_roster(tid, league),
            lambda r: (
                f"{sum(len(g.get('items', []) or []) for g in r.get('athletes', []))}"
                f" athletes for {tname}"
            ),
        )
        step(
            "Head coach",
            lambda: get_head_coach(tid, league),
            lambda c: (
                f"{c['title']} {c['fullName']} for {tname}"
                if c
                else f"none listed for {tname}"
            ),
        )
        if roster is not None:
            step(
                "Code generation",
                lambda: generate_code_replacement(
                    teams[0], roster, teams[0]["abbreviation"],
                    "full_with_number", False, league,
                ),
                lambda g: f"{g['codes']} codes, {g['entries']} output lines",
            )

    _cache.clear()
    return {"league": cfg["label"], "results": results}


def build_for_matchup(
    league,
    home_team,
    away_team,
    home_prefix,
    away_prefix,
    code_format,
    include_coaches,
):
    """Generate both sides of a matchup. Returns home/away results plus combined text."""
    home_roster = get_team_roster(home_team["id"], league)
    home = generate_code_replacement(
        home_team, home_roster, home_prefix, code_format, include_coaches, league
    )

    away_roster = get_team_roster(away_team["id"], league)
    away = generate_code_replacement(
        away_team, away_roster, away_prefix, code_format, include_coaches, league
    )

    combined = (
        f"# {home_team['name']} (Home)\n{home['content']}"
        f"\n# {away_team['name']} (Away)\n{away['content']}"
    )
    return home, away, combined
