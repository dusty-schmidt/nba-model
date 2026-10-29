"""Pull one player's game logs into the store.
Usage: uv run python -m app.ingest.pull_player "<name or espn id>" [season...]
Season = ending year: 2026 -> 2025-26, 2027 -> 2026-27. Default: 2026 2027."""
import sys
from app import store
from app.ingest.espn_client import get_gamelog, search_player

# ESPN 'names' field -> our columns; composite 'a-b' fields split on the dash
SPLIT = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
         "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
         "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta")}
SINGLE = {"minutes": "minutes", "totalRebounds": "reb", "assists": "ast",
          "blocks": "blk", "steals": "stl", "turnovers": "tov",
          "fouls": "pf", "points": "pts"}

def _num(s):
    if s in (None, "", "-"):
        return None
    s = str(s)
    if ":" in s:  # minutes in MM:SS
        m, sec = s.split(":")
        return round(int(m) + int(sec) / 60, 2)
    return float(s)

def _split(s):
    if isinstance(s, str) and "-" in s:
        a, b = s.split("-", 1)
        try:
            return int(a), int(b)
        except ValueError:
            return None, None
    return None, None

def _scalar(v):
    if isinstance(v, dict):
        for k in ("abbreviation", "displayName", "name"):
            if v.get(k):
                return v[k]
        return str(v)[:60]
    return v

def parse_event(pid, season, stype, names, e, gamectx):
    stats = e.get("stats") or []
    m = dict(zip(names, stats))
    if "minutes" not in m:
        return None
    gid = int(e["eventId"])
    g = gamectx.get(str(gid)) or gamectx.get(gid) or {}
    atvs = str(_scalar(g.get("atVs", "")))
    row = {"espn_id": pid, "game_id": gid, "season": season,
           "season_type": stype, "game_date": g.get("gameDate"),
           "home_away": "H" if atvs.lower().startswith("vs") else
                        ("A" if atvs.startswith("@") else None),
           "opponent": _scalar(g.get("opponent")),
           "result": _scalar(g.get("gameResult")),
           "home_score": g.get("homeTeamScore"), "away_score": g.get("awayTeamScore")}
    for src, col in SINGLE.items():
        row[col] = _num(m.get(src))
    for src, (c1, c2) in SPLIT.items():
        a, b = _split(m.get(src))
        row[c1], row[c2] = a, b
    return row

def main():
    who = sys.argv[1]
    seasons = [int(s) for s in sys.argv[2:]] or [2026, 2027]
    if who.isdigit():
        pid, name, team = int(who), str(who), None
    else:
        hits = search_player(who)
        if not hits:
            sys.exit(f"no player found for {who!r}")
        if len(hits) > 1:
            print("matches:", "; ".join(f"{h['id']} {h['name']} ({h['team']})" for h in hits))
        pid, name, team = int(hits[0]["id"]), hits[0]["name"], hits[0]["team"]
    con = store.connect()
    store.upsert_player(con, pid, name, team)
    n = 0
    for season in seasons:
        d = get_gamelog(pid, season)
        names = d["names"]
        events = d.get("events", {})
        for st in d.get("seasonTypes", []):
            stype = st.get("displayName", "")
            if "Preseason" in stype:
                continue
            for cat in st.get("categories", []):
                for e in cat.get("events", []):
                    row = parse_event(pid, season, stype, names, e, events)
                    if row:
                        store.upsert_game(con, row)
                        n += 1
    con.commit()
    print(f"upserted {n} game rows for {name} (espn {pid}) -> {store.DB}")

if __name__ == "__main__":
    main()
