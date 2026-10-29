"""players: the store's players, their seasons, form and the league leaders."""
import json
import unicodedata

from app import store
from app.archetypes.service import meta_path

STATS = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m"]
BASELINE = {"label": "roll20/dec20 walk-forward MAE, Doncic+Jokic n=2 (2026-10-28)",
            "pts": 7.30, "reb": 2.94, "ast": 2.86, "stl": 1.11,
            "blk": 0.68, "tov": 1.63, "fg3m": 1.35, "minutes": 3.74}


def fold(s):
    """Lowercase and strip diacritics so 'doncic' matches 'Dončić'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def rnd(v, n=1):
    return round(v, n) if v is not None else None


def stat_wl(s):
    return s if s in STATS + ["minutes", "fg3a", "oreb", "fta"] else "pts"


def state():
    con = store.connect()
    players = con.execute(
        "select count(*) from players p where exists (select 1 from "
        "game_logs l where l.espn_id=p.espn_id and l.source='official')"
    ).fetchone()[0]
    games = con.execute(
        "select count(*) from game_logs where source='official'").fetchone()[0]
    tgames = con.execute("select count(*) from team_games").fetchone()[0]
    seasons = [r[0] for r in con.execute(
        "select distinct season from game_logs where source='official' "
        "order by 1")]
    meta = json.loads(meta_path().read_text()) if meta_path().exists() else {}
    return dict(players=players, games=games, team_games=tgames,
                seasons=seasons, k=meta.get("k"), baseline=BASELINE)


def search(q):
    con = store.connect()
    rows = con.execute(
        "select p.espn_id, p.name, count(l.game_id) g, avg(l.pts) ppg "
        "from players p left join game_logs l on l.espn_id = p.espn_id "
        "and l.source='official' group by p.espn_id "
        "having count(l.game_id) > 0 order by g desc",
    ).fetchall()
    qf = fold(q.strip())
    rows = [r for r in rows if qf in fold(r[1])][:25]
    return [{"espn_id": r[0], "name": r[1], "g": r[2], "ppg": rnd(r[3])}
            for r in rows]


def player(pid):
    """A player's card: seasons, per-36 and rates, recent games, roll20 projection. None if unknown."""
    con = store.connect()
    p = con.execute("select name from players where espn_id=?",
                    (pid,)).fetchone()
    if not p:
        return None
    seasons = []
    for r in con.execute(
        "select season, count(*), avg(minutes), avg(pts), avg(reb), "
        "avg(ast), avg(stl), avg(blk), avg(tov), avg(fg3m) from game_logs "
        "where espn_id=? and source='official' group by season "
        "order by season", (pid,),
    ):
        seasons.append({"season": r[0], "g": r[1], "min": rnd(r[2]),
                        "pts": rnd(r[3]), "reb": rnd(r[4]), "ast": rnd(r[5]),
                        "stl": rnd(r[6]), "blk": rnd(r[7]), "tov": rnd(r[8]),
                        "fg3m": rnd(r[9])})
    latest = seasons[-1]["season"] if seasons else None
    per36 = None
    if latest:
        r = con.execute(
            "select 36.0*sum(l.pts)/sum(l.minutes), 36.0*sum(l.reb)/sum(l.minutes), "
            "36.0*sum(l.oreb)/sum(l.minutes), 36.0*sum(l.ast)/sum(l.minutes), "
            "36.0*sum(l.stl)/sum(l.minutes), 36.0*sum(l.blk)/sum(l.minutes), "
            "36.0*sum(l.fg3a)/sum(l.minutes), 36.0*sum(l.fta)/sum(l.minutes), "
            "sum(l.fg3a)*1.0/nullif(sum(l.fga),0), "
            "100.0*sum(l.pts)/(2*nullif(sum(l.fga)+0.44*sum(l.fta),0)), "
            "(sum(l.fgm)+0.5*sum(l.fg3m))*1.0/nullif(sum(l.fga),0), "
            "100.0*sum((l.fga+0.44*l.fta+l.tov)*48.0)"
            "/nullif(sum(l.minutes*(t.fga+0.44*t.fta+t.tov)),0), "
            "sum(l.minutes) from game_logs l join team_games t "
            "on l.game_id=t.game_id and l.season=t.season "
            "and t.team_id!=(select team_id from teams where abbrev=l.opponent) "
            "where l.espn_id=? and l.season=? and l.source='official' "
            "and l.minutes>0", (pid, latest),
        ).fetchone()
        keys = ["pts36", "reb36", "oreb36", "ast36", "stl36", "blk36",
                "fg3a36", "fta36", "atr", "ts", "efg", "usg", "minutes"]
        per36 = {k: rnd(v, 2) for k, v in zip(keys, r)}
    recent = [dict(zip(["date", "opp", "ha", "min", "pts", "reb", "ast",
                        "fg3m"],
                       [r[0], r[1], r[2]] + [rnd(v) for v in r[3:]]))
              for r in con.execute(
                  "select game_date, opponent, home_away, minutes, pts, "
                  "reb, ast, fg3m from game_logs where espn_id=? and "
                  "source='official' order by game_date desc limit 15",
                  (pid,),)]
    last20 = [dict(zip(["date"] + STATS + ["minutes"],
                       [r[0]] + [rnd(v) for v in r[1:]]))
              for r in con.execute(
                  "select game_date, pts, reb, ast, stl, blk, tov, fg3m, "
                  "minutes from game_logs where espn_id=? and "
                  "source='official' order by game_date desc limit 20",
                  (pid,),)]
    proj = {}
    for s in ["minutes"] + STATS:
        vals = [g[s] for g in last20 if g[s] is not None]
        if vals:
            mean = sum(vals) / len(vals)
            sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            proj[s] = {"mean": rnd(mean), "std": rnd(sd), "n": len(vals)}
    a = con.execute("select arch from player_archetypes where espn_id=?",
                    (pid,)).fetchone()
    spark = [g["pts"] for g in reversed(last20)]
    return dict(name=p[0], seasons=seasons, per36=per36, recent=recent,
                projection=proj, arch=a[0] if a else None, spark=spark,
                latest_season=latest)


def leaders(season, stat, min_games):
    stat = stat_wl(stat)
    con = store.connect()
    rows = con.execute(
        f"select p.espn_id, p.name, avg(l.{stat}) v, count(*) g, "
        "avg(l.minutes) m from game_logs l join players p on "
        "p.espn_id=l.espn_id where l.source='official' and l.season=? "
        "group by l.espn_id having count(*) >= ? order by v desc limit 25",
        (season, min_games),
    ).fetchall()
    return dict(season=season, stat=stat,
                rows=[{"espn_id": r[0], "name": r[1], "v": rnd(r[2]),
                       "g": r[3], "min": rnd(r[4])} for r in rows])
