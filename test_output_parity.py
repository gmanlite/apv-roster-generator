"""
Offline check that the web app's code-replacement output matches the rules the
Tkinter version used. No network: ESPN responses are stubbed.

Run:  python test_output_parity.py
"""

import espn

# --- stub ESPN --------------------------------------------------------------

# ESPN's core API answers coaches in two hops: a collection of $ref links, then the
# coach record itself. Both are stubbed so the walk is exercised, not bypassed.
FAKE_COACH_LIST = {
    "count": 1,
    "items": [{"$ref": "http://sports.core.api.espn.com/v2/.../coaches/17553?lang=en"}],
}
FAKE_COACH = {"id": "17553", "firstName": "Andy", "lastName": "Reid", "experience": 27}

FAKE_FOOTBALL_ROSTER = {
    "athletes": [
        {
            "position": {"abbreviation": "QB"},
            "items": [
                {"fullName": "Patrick Mahomes", "jersey": "15", "position": {"abbreviation": "QB"}},
                {"fullName": "No Number Guy", "jersey": "", "position": {"abbreviation": "QB"}},
            ],
        },
        {
            "position": {"abbreviation": "WR"},
            "items": [
                {"fullName": "Rashee Rice", "jersey": "4", "position": {"abbreviation": "WR"}},
            ],
        },
        {
            "position": {"abbreviation": "CB"},
            "items": [
                # duplicate jersey 4 -> defensive player gets a D suffix, offense gets O
                {"fullName": "Jaylen Watson", "jersey": "4", "position": {"abbreviation": "CB"}},
                {"fullName": "Trent McDuffie", "jersey": "22", "position": {"abbreviation": "CB"}},
            ],
        },
        {
            "position": {"abbreviation": "K"},
            "items": [
                {"fullName": "Harrison Butker", "jersey": "7", "position": {"abbreviation": "PK"}},
            ],
        },
    ]
}

FAKE_HOOPS_TEAM = {
    "team": {
        "athletes": [
            {"fullName": "Cooper Flagg", "jersey": "2", "position": {"abbreviation": "F"}},
            {"fullName": "Tyrese Proctor", "jersey": "5", "position": {"abbreviation": "G"}},
            {"fullName": "Khaman Maluach", "jersey": "5", "position": {"abbreviation": "C"}},
        ]
    }
}


def fake_get(url, params=None):
    params = params or {}
    if "sports.core.api.espn.com" in url:
        return FAKE_COACH_LIST if url.endswith("/coaches") else FAKE_COACH
    if url.endswith("/roster"):
        return FAKE_FOOTBALL_ROSTER
    if params.get("enable") == "roster":
        return FAKE_HOOPS_TEAM
    raise AssertionError("unexpected call: " + url)


espn._get = fake_get
espn._cache.clear()
_real_get_head_coach = espn.get_head_coach

TEAM = {"id": "12", "name": "Kansas City Chiefs", "abbreviation": "KC"}
DUKE = {"id": "150", "name": "Duke Blue Devils", "abbreviation": "DUKE"}

failures = []


def check(label, got, want):
    if got == want:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}\n       got:  {got!r}\n       want: {want!r}")


print("\nNFL, full_with_number, coaches on")
r = espn.generate_code_replacement(
    TEAM, FAKE_FOOTBALL_ROSTER, "KC", "full_with_number", True, "nfl"
)
lines = r["content"].split("\n")
expected = [
    "KCHC\tKansas City Chiefs head coach Andy Reid",
    "kchc\tKansas City Chiefs head coach Andy Reid",
    # duplicate 4s sort by suffix: D before O
    "KC4D\tKansas City Chiefs cornerback Jaylen Watson (4)",
    "kc4d\tKansas City Chiefs cornerback Jaylen Watson (4)",
    "KC4O\tKansas City Chiefs wide receiver Rashee Rice (4)",
    "kc4o\tKansas City Chiefs wide receiver Rashee Rice (4)",
    # jersey 7 is unique, so no position suffix is added
    "KC7\tKansas City Chiefs place kicker Harrison Butker (7)",
    "kc7\tKansas City Chiefs place kicker Harrison Butker (7)",
    "KC15\tKansas City Chiefs quarterback Patrick Mahomes (15)",
    "kc15\tKansas City Chiefs quarterback Patrick Mahomes (15)",
    "KC22\tKansas City Chiefs cornerback Trent McDuffie (22)",
    "kc22\tKansas City Chiefs cornerback Trent McDuffie (22)",
    "",
]
check("head coach row first", lines[0], expected[0])
check("lowercase twin present", lines[1], expected[1])
check(
    "duplicate jersey O/D split",
    [l for l in lines if l.startswith("KC4")],
    [expected[2], expected[4]],
)
check("unique jersey gets no suffix", lines[6], expected[6])
check("sorted by jersey number", lines, expected)
check("tab delimited", "\t" in lines[0], True)
check("players counted (no-jersey skipped)", r["players"], 5)
check("skip note recorded", any("no jersey" in n for n in r["log"]), True)

print("\nNFL, position_only, coaches off")
r2 = espn.generate_code_replacement(
    TEAM, FAKE_FOOTBALL_ROSTER, "KC", "position_only", False, "nfl"
)
check("no coach row", "KCHC" in r2["content"], False)
check("position-only text", r2["content"].split("\n")[0], "KC4D\tcornerback Jaylen Watson")

print("\nCustom prefix overrides abbreviation")
r3 = espn.generate_code_replacement(
    TEAM, FAKE_FOOTBALL_ROSTER, "CHIEFS", "full_no_number", False, "nfl"
)
check("prefix applied", r3["content"].startswith("CHIEFS4D\t"), True)
check("no (jersey) in full_no_number", "(4)" in r3["content"].split("\n")[0], False)

print("\nBasketball grouping + G/F/C duplicate suffixes")
espn._cache.clear()
roster = espn.get_team_roster("150", "mens-college-basketball")
r4 = espn.generate_code_replacement(
    DUKE, roster, "DUKE", "full_with_number", False, "mens-college-basketball"
)
hoops = r4["content"].split("\n")
check(
    "flat roster grouped and dupes suffixed",
    [l for l in hoops if l.startswith("DUKE5")],
    [
        "DUKE5C\tDuke Blue Devils center Khaman Maluach (5)",
        "DUKE5G\tDuke Blue Devils guard Tyrese Proctor (5)",
    ],
)
check("unique hoops jersey untouched", hoops[0], "DUKE2\tDuke Blue Devils forward Cooper Flagg (2)")

print("\nNBA uses the same basketball path")
espn._cache.clear()
nba_roster = espn.get_team_roster("13", "nba")
check("nba roster normalised", len(nba_roster["athletes"]), 3)

print("\nCombined-file layout")
espn._cache.clear()
home, away, combined = espn.build_for_matchup(
    "nfl", TEAM, dict(TEAM, id="7", name="Denver Broncos", abbreviation="DEN"),
    "KC", "DEN", "full_with_number", False,
)
check("combined header format", combined.split("\n")[0], "# Kansas City Chiefs (Home)")
check("away header present", "\n# Denver Broncos (Away)\n" in combined, True)

print("\nPosition names are interpreted per sport (shared abbreviations collide)")
check("C: football", espn.get_position_name("C", "football"), "center")
check("C: basketball", espn.get_position_name("C", "basketball"), "center")
check("C: baseball", espn.get_position_name("C", "baseball"), "catcher")
check("C: hockey", espn.get_position_name("C", "hockey"), "center")
check("G: basketball", espn.get_position_name("G", "basketball"), "guard")
check("G: hockey", espn.get_position_name("G", "hockey"), "goaltender")
check("P: football", espn.get_position_name("P", "football"), "punter")
check("P: baseball", espn.get_position_name("P", "baseball"), "pitcher")
check("SS: football", espn.get_position_name("SS", "football"), "strong safety")
check("SS: baseball", espn.get_position_name("SS", "baseball"), "shortstop")
check("D: hockey", espn.get_position_name("D", "hockey"), "defenseman")
check("unknown falls back to lowercase", espn.get_position_name("XYZ", "hockey"), "xyz")

print("\nDuplicate suffixes use each sport's own split")
check("football offense", espn._duplicate_suffix("football", "WR"), "O")
check("football defense", espn._duplicate_suffix("football", "CB"), "D")
check("football special teams", espn._duplicate_suffix("football", "PK"), "PK")
check("basketball guard", espn._duplicate_suffix("basketball", "PG"), "G")
check("baseball pitcher", espn._duplicate_suffix("baseball", "SP"), "P")
check("baseball catcher", espn._duplicate_suffix("baseball", "C"), "C")
check("baseball infield", espn._duplicate_suffix("baseball", "2B"), "IF")
check("baseball outfield", espn._duplicate_suffix("baseball", "CF"), "OF")
check("hockey forward", espn._duplicate_suffix("hockey", "LW"), "F")
check("hockey defense", espn._duplicate_suffix("hockey", "D"), "D")
check("hockey goalie", espn._duplicate_suffix("hockey", "G"), "G")

print("\nMLB: grouped roster, catcher not center, manager not head coach")
MLB_ROSTER = {
    "athletes": [
        {"position": "Pitchers", "items": [
            {"fullName": "Gerrit Cole", "jersey": "45", "position": {"abbreviation": "SP"}},
            {"fullName": "Luke Weaver", "jersey": "30", "position": {"abbreviation": "RP"}},
        ]},
        {"position": "Catchers", "items": [
            {"fullName": "Austin Wells", "jersey": "28", "position": {"abbreviation": "C"}},
        ]},
        {"position": "Infielders", "items": [
            # jersey 30 collides with the reliever above -> P vs IF suffixes
            {"fullName": "Jazz Chisholm Jr.", "jersey": "30", "position": {"abbreviation": "2B"}},
        ]},
        {"position": "Outfielders", "items": [
            {"fullName": "Aaron Judge", "jersey": "99", "position": {"abbreviation": "RF"}},
        ]},
    ]
}
espn._cache.clear()
espn.get_head_coach = lambda tid, lg: {"fullName": "Aaron Boone", "title": "manager"}
YANKS = {"id": "10", "name": "New York Yankees", "abbreviation": "NYY"}
mlb = espn.generate_code_replacement(
    YANKS, MLB_ROSTER, "NYY", "full_with_number", True, "mlb"
)
lines = mlb["content"].split("\n")
check("manager captioned correctly", lines[0], "NYYHC\tNew York Yankees manager Aaron Boone")
check("catcher not center", "NYY28\tNew York Yankees catcher Austin Wells (28)" in lines, True)
check("right fielder", "NYY99\tNew York Yankees right fielder Aaron Judge (99)" in lines, True)
check("starting pitcher", "NYY45\tNew York Yankees starting pitcher Gerrit Cole (45)" in lines, True)
check(
    "duplicate 30 split P vs IF",
    sorted(l for l in lines if l.startswith("NYY30")),
    [
        "NYY30IF\tNew York Yankees second baseman Jazz Chisholm Jr. (30)",
        "NYY30P\tNew York Yankees relief pitcher Luke Weaver (30)",
    ],
)

print("\nNHL: goalie is not a guard, centers are centers")
NHL_ROSTER = {
    "athletes": [
        {"position": "Centers", "items": [
            {"fullName": "Auston Matthews", "jersey": "34", "position": {"abbreviation": "C"}},
        ]},
        {"position": "Left Wings", "items": [
            {"fullName": "Matthew Knies", "jersey": "23", "position": {"abbreviation": "LW"}},
        ]},
        {"position": "Defensemen", "items": [
            {"fullName": "Morgan Rielly", "jersey": "44", "position": {"abbreviation": "D"}},
            # collides with the winger above -> F vs D
            {"fullName": "Chris Tanev", "jersey": "23", "position": {"abbreviation": "D"}},
        ]},
        {"position": "Goalies", "items": [
            {"fullName": "Joseph Woll", "jersey": "60", "position": {"abbreviation": "G"}},
        ]},
    ]
}
espn._cache.clear()
espn.get_head_coach = lambda tid, lg: {"fullName": "Craig Berube", "title": "head coach"}
LEAFS = {"id": "10", "name": "Toronto Maple Leafs", "abbreviation": "TOR"}
nhl = espn.generate_code_replacement(
    LEAFS, NHL_ROSTER, "TOR", "full_with_number", True, "nhl"
)
nl = nhl["content"].split("\n")
check("head coach still head coach", nl[0], "TORHC\tToronto Maple Leafs head coach Craig Berube")
check("goaltender not guard", "TOR60\tToronto Maple Leafs goaltender Joseph Woll (60)" in nl, True)
check("center", "TOR34\tToronto Maple Leafs center Auston Matthews (34)" in nl, True)
check("defenseman", "TOR44\tToronto Maple Leafs defenseman Morgan Rielly (44)" in nl, True)
check(
    "duplicate 23 split D vs F",
    sorted(l for l in nl if l.startswith("TOR23")),
    [
        "TOR23D\tToronto Maple Leafs defenseman Chris Tanev (23)",
        "TOR23F\tToronto Maple Leafs left wing Matthew Knies (23)",
    ],
)

print("\nHead coach comes from the core API, in two hops")
espn._cache.clear()
espn.get_head_coach = _real_get_head_coach   # undo the stubs above
calls = []
def tracking_get(url, params=None):
    calls.append(url)
    return fake_get(url, params)
espn._get = tracking_get
hc = espn.get_head_coach("12", "nfl")
check("resolved a name", hc["fullName"], "Andy Reid")
check("title for football", hc["title"], "head coach")
check("two hops", len(calls), 2)
check("hop 1 is the team coaches collection", calls[0].endswith("/teams/12/coaches"), True)
check("hop 1 uses the core API", "sports.core.api.espn.com" in calls[0], True)
check("hop 2 upgraded http -> https", calls[1].startswith("https://"), True)
check("cached, no refetch", (espn.get_head_coach("12", "nfl"), len(calls))[1], 2)

espn._cache.clear()
check("title for baseball is manager", espn.get_head_coach("10", "mlb")["title"], "manager")
espn._cache.clear()
check("title for hockey is head coach", espn.get_head_coach("10", "nhl")["title"], "head coach")

print("\nMissing coach degrades to a note, not a crash")
espn._cache.clear()
espn._get = lambda url, params=None: (
    {"items": []} if "sports.core.api.espn.com" in url else fake_get(url, params)
)
check("returns None", espn.get_head_coach("99", "nfl"), None)
espn._cache.clear()
r_nohc = espn.generate_code_replacement(
    TEAM, FAKE_FOOTBALL_ROSTER, "KC", "full_with_number", True, "nfl"
)
check("no HC row emitted", "KCHC" in r_nohc["content"], False)
check("players still generated", r_nohc["players"], 5)
check("logged the gap", any("No head coach" in n for n in r_nohc["log"]), True)
espn._get = fake_get

print("\nEvery configured league is coherent")
for key, cfg in espn.LEAGUES.items():
    ok = (
        cfg["kind"] in espn.POSITION_MAPS
        and cfg["roster_style"] in ("grouped", "flat")
        and bool(cfg["file_prefix"])
        and bool(cfg["sport"])
    )
    check(f"{key} config", ok, True)
check("six leagues", len(espn.LEAGUES), 6)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {failures}")
    raise SystemExit(1)
print("All checks passed.")
