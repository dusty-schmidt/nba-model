"""Cross-validate ESPN vs official game logs for a player.

ESPN stores tipoff timestamps (UTC); official stores the local game date.
Convert ESPN timestamps to America/New_York dates, join on (date, season),
compare every core stat on shared games.

Usage: python validate.py <espn_id> <official_id>
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3

ET = ZoneInfo("America/New_York")


def et_date(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    return dt.astimezone(ET).date().isoformat()


def main():
    espn_pid, off_pid = int(sys.argv[1]), int(sys.argv[2])
    con = sqlite3.connect("data/nba.db")
    c = con.cursor()
    espn = c.execute(
        "select game_date, season, pts, minutes, reb, ast, fgm, fga, "
        "fg3m, fg3a, ftm, fta from game_logs "
        "where espn_id=? and source='espn'", (espn_pid,)
    ).fetchall()
    off = c.execute(
        "select game_date, season, pts, minutes, reb, ast, fgm, fga, "
        "fg3m, fg3a, ftm, fta from game_logs "
        "where espn_id=? and source='official'", (off_pid,)
    ).fetchall()

    e_map = {}
    for r in espn:
        d = et_date(r[0])
        if (d, r[1]) in e_map:
            print(f"WARNING espn duplicate date {d} s{r[1]}")
        e_map[(d, r[1])] = r
    o_map = {}
    for r in off:
        if (r[0], r[1]) in o_map:
            print(f"WARNING official duplicate date {r[0]} s{r[1]}")
        o_map[(r[0], r[1])] = r

    shared = sorted(set(e_map) & set(o_map), key=lambda k: (k[1], k[0]))
    print(f"espn rows: {len(e_map)}, official rows: {len(o_map)}, "
          f"shared games: {len(shared)}")

    mism = []
    for k in shared:
        e, o = e_map[k], o_map[k]
        diffs = []
        for i, name in ((2, "pts"), (3, "min"), (4, "reb"), (5, "ast"),
                        (6, "fgm"), (7, "fga"), (8, "fg3m"),
                        (9, "fg3a"), (10, "ftm"), (11, "fta")):
            ev, ov = e[i], o[i]
            if ev is None and ov is None:
                continue
            tol = 0.02 if name == "min" else 0
            if ev is None or ov is None or abs(ev - ov) > tol:
                diffs.append((name, ev, ov))
        if diffs:
            mism.append((k, diffs))

    print(f"stat mismatches on shared games: {len(mism)}")
    for k, diffs in mism[:5]:
        print(" MISMATCH", k, diffs)
    if shared and not mism:
        print("VERIFIED: every shared game agrees on pts, minutes, reb, ast, "
              "and all shooting splits")

    only_e = sorted(set(e_map) - set(o_map))
    only_o = sorted(set(o_map) - set(e_map))
    print(f"espn-only games ({len(only_e)}):", [k for k in only_e][:8])
    print(f"official-only games ({len(only_o)}):", [k for k in only_o][:8])


if __name__ == "__main__":
    main()
