"""Pull league-wide advanced + defensive tables from stats.nba.com.

Round-3 probe verdict:
  leaguedashplayerstats?MeasureType=Advanced (exact full param set): 569 rows
    -> OFF/DEF/NET_RATING, USG_PCT, AST_PCT, OREB/DREB_PCT, TS_PCT, EFG_PCT,
       PACE, PIE, AST_TO, POSS (rate stats; per36 == pergame for these)
  leaguedashptdefend: 568 rows league-wide, per DEF_CATEGORY
    -> FREQ, D_FGA, D_FGM, D_FG_PCT, NORMAL_FG_PCT, PCT_PLUSMINUS
  drives / synergy tracking endpoints stay blocked (fetch-blocked,
  goto times out) - rim pressure proxied by ftr/oreb in archetypes.

Tables (snapshot replace per season, idempotent re-runs):
  player_advanced(season, espn_id, ...)   espn_id = OFFICIAL NBA PlayerID
  player_defend(season, espn_id, category, ...)

Usage: uv run --group ingest python -m app.ingest.advanced_pull
"""
import json

from app import store
from app.config import settings
from app.ingest.nba_official import fetch_many, get_browser_page

RAW = settings.data_dir / "raw"

FULL = (
    "College=&Conference=&Country=&Division=&DraftPick=&DraftYear="
    "&GameScope=&GameSegment=&Height=&LastNGames=0&LeagueID=00"
    "&Location=&Month=0&OpponentTeamID=0&Outcome=&PORound=0"
    "&PaceAdjust=N&Period=0&PlayerExperience=&PlayerPosition="
    "&PlusMinus=N&Rank=N&Season={season}&SeasonSegment="
    "&SeasonType=Regular+Season&ShotClockRange=&StarterBench="
    "&TeamID=0&VsConference=&VsDivision=&Weight="
)

ADV_KEYS = {
    "PLAYER_ID": "espn_id", "MIN": "min", "USG_PCT": "usg_pct",
    "TS_PCT": "ts_pct", "EFG_PCT": "efg_pct", "AST_PCT": "ast_pct",
    "OREB_PCT": "oreb_pct", "DREB_PCT": "dreb_pct",
    "OFF_RATING": "off_rating", "DEF_RATING": "def_rating",
    "NET_RATING": "net_rating", "AST_TO": "ast_to", "PACE": "pace",
    "PIE": "pie", "POSS": "poss",
}

DEF_KEYS = {
    "CLOSE_DEF_PERSON_ID": "espn_id", "FREQ": "freq", "D_FGA": "d_fga",
    "D_FGM": "d_fgm", "D_FG_PCT": "d_fg_pct",
    "NORMAL_FG_PCT": "normal_fg_pct", "PCT_PLUSMINUS": "pct_plusminus",
}


def rows_from(out):
    data = json.loads(out["body"])
    rs = data["resultSets"][0]
    return [dict(zip(rs["headers"], r)) for r in rs["rowSet"]]


def ensure_tables(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS player_advanced ("
        "season INTEGER NOT NULL, espn_id INTEGER NOT NULL, min REAL,"
        " usg_pct REAL, ts_pct REAL, efg_pct REAL, ast_pct REAL,"
        " oreb_pct REAL, dreb_pct REAL, off_rating REAL, def_rating REAL,"
        " net_rating REAL, ast_to REAL, pace REAL, pie REAL, poss REAL,"
        " PRIMARY KEY(season, espn_id))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS player_defend ("
        "season INTEGER NOT NULL, espn_id INTEGER NOT NULL,"
        " category TEXT NOT NULL, freq REAL, d_fga REAL, d_fgm REAL,"
        " d_fg_pct REAL, normal_fg_pct REAL, pct_plusminus REAL,"
        " PRIMARY KEY(season, espn_id, category))"
    )
    con.commit()


def upsert_players(con, rows):
    n = 0
    for r in rows:
        pid, name = r.get("PLAYER_ID"), r.get("PLAYER_NAME")
        if pid and name:
            con.execute(
                "INSERT OR IGNORE INTO players(espn_id, name) VALUES(?,?)",
                (pid, name),
            )
            n += 1
    return n


def pull_advanced(con, page):
    for nba_season, ours in (("2024-25", 2025), ("2025-26", 2026)):
        path = ("/stats/leaguedashplayerstats?MeasureType=Advanced"
                f"&PerMode=Per36&{FULL.format(season=nba_season)}")
        out = fetch_many(page, [path])[0]
        if out["status"] != 200:
            print(f"[adv {nba_season}] HTTP {out['status']}, skipped")
            continue
        (RAW / f"advanced_{ours}.json").write_text(out["body"])
        rows = rows_from(out)
        upsert_players(con, rows)
        con.execute("DELETE FROM player_advanced WHERE season=?", (ours,))
        for r in rows:
            vals = {v: r.get(k) for k, v in ADV_KEYS.items()}
            pid = vals.pop("espn_id")
            if pid is None:
                continue
            con.execute(
                "INSERT INTO player_advanced(season, espn_id, min, usg_pct,"
                " ts_pct, efg_pct, ast_pct, oreb_pct, dreb_pct, off_rating,"
                " def_rating, net_rating, ast_to, pace, pie, poss)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ours, pid, vals["min"], vals["usg_pct"], vals["ts_pct"],
                 vals["efg_pct"], vals["ast_pct"], vals["oreb_pct"],
                 vals["dreb_pct"], vals["off_rating"], vals["def_rating"],
                 vals["net_rating"], vals["ast_to"], vals["pace"],
                 vals["pie"], vals["poss"]),
            )
        con.commit()
        print(f"[adv {nba_season}] stored {len(rows)} rows")


def pull_defend(con, page):
    jobs = [
        ("2024-25", 2025, "Overall"),
        ("2024-25", 2025, "Less Than 6 Ft"),
        ("2024-25", 2025, "3 Pointers"),
        ("2025-26", 2026, "Overall"),
    ]
    for nba_season, ours, category in jobs:
        cat = category.replace(" ", "+")
        path = ("/stats/leaguedashptdefend?LeagueID=00&PerMode=PerGame"
                f"&Season={nba_season}&SeasonType=Regular+Season"
                f"&DEF_CATEGORY={cat}")
        out = fetch_many(page, [path])[0]
        if out["status"] != 200:
            print(f"[defend {category} {nba_season}] HTTP {out['status']}, skipped")
            continue
        (RAW / f"defend_{ours}_{cat}.json").write_text(out["body"])
        rows = rows_from(out)
        con.execute(
            "DELETE FROM player_defend WHERE season=? AND category=?",
            (ours, category),
        )
        for r in rows:
            vals = {v: r.get(k) for k, v in DEF_KEYS.items()}
            pid = vals.pop("espn_id")
            if pid is None:
                continue
            con.execute(
                "INSERT INTO player_defend(season, espn_id, category, freq,"
                " d_fga, d_fgm, d_fg_pct, normal_fg_pct, pct_plusminus)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (ours, pid, category, vals["freq"], vals["d_fga"],
                 vals["d_fgm"], vals["d_fg_pct"], vals["normal_fg_pct"],
                 vals["pct_plusminus"]),
            )
        con.commit()
        print(f"[defend {category} {nba_season}] stored {len(rows)} rows")


def main():
    con = store.connect()
    ensure_tables(con)
    pw, browser, page = get_browser_page()
    try:
        pull_advanced(con, page)
        pull_defend(con, page)
    finally:
        browser.close()
        pw.stop()
    luka = con.execute(
        "select usg_pct, ts_pct, def_rating, pie from player_advanced"
        " where espn_id=1629029 and season=2025").fetchone()
    print(f"sanity Luka 2024-25: usg={luka[0]} ts={luka[1]}"
          f" def_rtg={luka[2]} pie={luka[3]}")
    rim = con.execute(
        "select p.name, d.pct_plusminus from player_defend d"
        " join players p on p.espn_id=d.espn_id"
        " where d.season=2025 and d.category='Less Than 6 Ft'"
        " and d.pct_plusminus is not null order by d.pct_plusminus"
        " limit 5").fetchall()
    print("best rim suppressors (d6 pct_plusminus):")
    for name, v in rim:
        print(f"  {name}: {v:.1f}")


if __name__ == "__main__":
    main()
