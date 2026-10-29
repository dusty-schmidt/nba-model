"""Pull the tracking-measure family (PtMeasureType) from stats.nba.com.

Probe verdict (probes since deleted; findings in README "Advanced stats layer"):
all 8 PtMeasureType measures return
200 league-wide through the bridge - Drives, Defense, Passing, CatchShoot,
PullUpShot, Rebounding, SpeedDistance, Efficiency. This is the
drives-to-the-rim and rim-defense data the owner asked for; the old
guessed endpoints (leaguedashptdrives, synergyplaytype) are dead paths.

Also cleans the bogus player_defend category rows: DEF_CATEGORY was
silently ignored (every 'category' snapshot was a byte-identical Overall
copy). Rim defense now comes from PtMeasureType=Defense DEF_RIM_*
columns instead; player_defend keeps only the Overall snapshots.

Table: player_tracking(season, espn_id, ...) - one row per
player-season, columns picked from each measure response.

Usage: /opt/venv/bin/python pt_pull.py
"""
import json
from pathlib import Path

from nba_official import fetch_many, get_browser_page
import store

RAW = Path("data/raw")

PARAMS = (
    "College=&Conference=&Country=&DateFrom=&DateTo=&Division="
    "&DraftPick=&DraftYear=&GameScope=&Height=&ISTRound=&LastNGames=0"
    "&LeagueID=00&Location=&Month=0&OpponentTeamID=0&Outcome=&PORound=0"
    "&PerMode=PerGame&PlayerExperience=&PlayerOrTeam=Player"
    "&PlayerPosition=&PtMeasureType={ptm}&Season={season}"
    "&SeasonSegment=&SeasonType=Regular+Season&StarterBench=&TeamID=0"
    "&VsConference=&VsDivision=&Weight="
)

MEASURES = [
    ("Drives", {
        "DRIVES": "drives", "DRIVE_PTS": "drive_pts",
        "DRIVE_FGA": "drive_fga", "DRIVE_FG_PCT": "drive_fg_pct",
        "DRIVE_PASSES": "drive_passes", "DRIVE_AST": "drive_ast",
        "DRIVE_TOV": "drive_tov", "MIN": "min",
        "PLAYER_NAME": "name"}),
    ("Defense", {
        "DEF_RIM_FGA": "def_rim_fga", "DEF_RIM_FG_PCT": "def_rim_fg_pct"}),
    ("Passing", {
        "PASSES_MADE": "passes_made",
        "PASSES_RECEIVED": "passes_received",
        "SECONDARY_AST": "secondary_ast",
        "POTENTIAL_AST": "potential_ast"}),
    ("CatchShoot", {
        "CATCH_SHOOT_FGA": "catch_shoot_fga",
        "CATCH_SHOOT_PTS": "catch_shoot_pts",
        "CATCH_SHOOT_FG3A": "catch_shoot_fg3a"}),
    ("PullUpShot", {
        "PULL_UP_FGA": "pull_up_fga", "PULL_UP_PTS": "pull_up_pts",
        "PULL_UP_FG3A": "pull_up_fg3a"}),
    ("Rebounding", {
        "OREB_CHANCE_PCT": "oreb_chance_pct",
        "DREB_CHANCE_PCT": "dreb_chance_pct"}),
    ("SpeedDistance", {
        "DIST_MILES": "dist_miles", "AVG_SPEED": "avg_speed"}),
    ("Efficiency", {
        "PAINT_TOUCH_PTS": "paint_touch_pts",
        "POST_TOUCH_PTS": "post_touch_pts",
        "ELBOW_TOUCH_PTS": "elbow_touch_pts"}),
]

ALL_COLS = ["min", "drives", "drive_pts", "drive_fga", "drive_fg_pct",
    "drive_passes", "drive_ast", "drive_tov", "def_rim_fga",
    "def_rim_fg_pct", "passes_made", "passes_received", "secondary_ast",
    "potential_ast", "catch_shoot_fga", "catch_shoot_pts",
    "catch_shoot_fg3a", "pull_up_fga", "pull_up_pts", "pull_up_fg3a",
    "oreb_chance_pct", "dreb_chance_pct", "dist_miles", "avg_speed",
    "paint_touch_pts", "post_touch_pts", "elbow_touch_pts"]


def ensure_tables(con):
    cols_sql = ", ".join(f"{c} REAL" for c in ALL_COLS)
    con.execute(
        "CREATE TABLE IF NOT EXISTS player_tracking ("
        "season INTEGER NOT NULL, espn_id INTEGER NOT NULL, "
        f"{cols_sql}, PRIMARY KEY(season, espn_id))")
    con.commit()


def pull_season(page, nba_season, ours):
    merged = {}
    for ptm, colmap in MEASURES:
        path = ("/stats/leaguedashptstats?"
                + PARAMS.format(ptm=ptm, season=nba_season))
        out = fetch_many(page, [path])[0]
        if out["status"] != 200:
            print(f"[{ptm} {nba_season}] HTTP {out['status']}, skipped")
            continue
        (RAW / f"pt_{ptm}_{ours}.json").write_text(out["body"])
        rs = json.loads(out["body"])["resultSets"][0]
        for row in rs["rowSet"]:
            r = dict(zip(rs["headers"], row))
            pid = r.get("PLAYER_ID")
            if pid is None:
                continue
            d = merged.setdefault(pid, {})
            for k, v in colmap.items():
                val = r.get(k)
                if val is not None:
                    d[v] = val
        print(f"[{ptm} {nba_season}] merged {len(rs['rowSet'])} rows")
    return merged


def store_season(con, ours, merged):
    con.execute("DELETE FROM player_tracking WHERE season=?", (ours,))
    cols_sql = ", ".join(ALL_COLS)
    ph = ", ".join("?" * len(ALL_COLS))
    names = 0
    rows = 0
    for pid, d in merged.items():
        name = d.get("name")
        if name:
            con.execute(
                "INSERT INTO players(espn_id, name) VALUES(?,?) "
                "ON CONFLICT(espn_id) DO NOTHING", (pid, name))
            names += 1
        con.execute(
            f"INSERT INTO player_tracking(season, espn_id, {cols_sql}) "
            f"VALUES(?,?,{ph})",
            (ours, pid,
             *[d.get(c) for c in ALL_COLS]))
        rows += 1
    con.commit()
    print(f"[season {ours}] stored {rows} players ({names} named)")


def main():
    con = store.connect()
    ensure_tables(con)
    bogus = con.execute(
        "select count(*) from player_defend where category!='Overall'"
    ).fetchone()[0]
    con.execute("DELETE FROM player_defend WHERE category!='Overall'")
    con.commit()
    print(f"cleaned {bogus} bogus defend category rows")
    pw, browser, page = get_browser_page()
    try:
        for nba_season, ours in (("2024-25", 2025), ("2025-26", 2026)):
            merged = pull_season(page, nba_season, ours)
            store_season(con, ours, merged)
    finally:
        browser.close()
        pw.stop()
    checks = [
        ("drive volume leader", "order by drives desc"),
        ("rim defense leader", "order by def_rim_fga desc"),
        ("pull-up 3 volume", "order by pull_up_fg3a desc"),
        ("paint touch pts", "order by paint_touch_pts desc"),
    ]
    for label, order in checks:
        rows = con.execute(
            "select p.name, t.drives, t.def_rim_fga, t.pull_up_fg3a, "
            "t.paint_touch_pts from player_tracking t "
            "join players p on p.espn_id=t.espn_id "
            f"where t.season=2025 and t.min > 15 {order} limit 3").fetchall()
        print(f"--- {label} ---")
        for r in rows:
            print(f"  {r[0]}: drives={r[1]} rim_fga={r[2]} "
                  f"pull3={r[3]} paint={r[4]}")


if __name__ == "__main__":
    main()
