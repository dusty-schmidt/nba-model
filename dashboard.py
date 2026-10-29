"""NBA Model Lab - Flask JSON API + single-file mobile UI.

Binds 127.0.0.1:8099 inside the container; tailnet access via
`tailscale serve` (tailnet-only, deliberately NOT public).

Run: cd /a0/usr/workdir/nba-model && /opt/venv/bin/python dashboard.py
"""
import json
import unicodedata
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from sklearn.decomposition import PCA

import archetypes as arch
import store

app = Flask(__name__, static_folder="static")


def fold(s):
    """Lowercase and strip diacritics so 'doncic' matches 'Dončić'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()
STATS = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m"]
BASELINE = {"label": "roll20/dec20 walk-forward MAE, Doncic+Jokic n=2 (2026-10-28)",
            "pts": 7.30, "reb": 2.94, "ast": 2.86, "stl": 1.11,
            "blk": 0.68, "tov": 1.63, "fg3m": 1.35, "minutes": 3.74}
_cache = {}
META_PATH = Path("data/archetypes_2025.json")


def rnd(v, n=1):
    return round(v, n) if v is not None else None


def stat_wl(s):
    return s if s in STATS + ["minutes", "fg3a", "oreb", "fta"] else "pts"


@app.errorhandler(Exception)
def on_err(e):
    return jsonify(error=str(e)), 500


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/state")
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
    meta = json.loads(META_PATH.read_text()) if META_PATH.exists() else {}
    return jsonify(players=players, games=games, team_games=tgames,
                   seasons=seasons, k=meta.get("k"), baseline=BASELINE)


@app.get("/api/players")
def players():
    q = request.args.get("q", "").strip()
    con = store.connect()
    rows = con.execute(
        "select p.espn_id, p.name, count(l.game_id) g, avg(l.pts) ppg "
        "from players p left join game_logs l on l.espn_id = p.espn_id "
        "and l.source='official' group by p.espn_id "
        "having count(l.game_id) > 0 order by g desc",
    ).fetchall()
    qf = fold(q)
    rows = [r for r in rows if qf in fold(r[1])][:25]
    return jsonify([{"espn_id": r[0], "name": r[1], "g": r[2],
                      "ppg": rnd(r[3])} for r in rows])


@app.get("/api/prob/<int:pid>")
def prob(pid):
    """Next-game projection plus over/under probabilities per stat.

    Strategy is the walk-forward winner from projections.py: learned
    mean for pts/reb/ast, plain roll20 for fg3m, per-player shrunk
    spread. Live use may train on full history.
    """
    import live_probs
    con = store.connect()
    name = con.execute("select name from players where espn_id=?",
                       (pid,)).fetchone()
    res = live_probs.project(con, pid)
    if res is None:
        return jsonify(error="insufficient history"), 404
    out = {"pid": pid, "name": name[0] if name else str(pid),
           "arch": res["arch"], "games": res["games"], "stats": {}}
    for stat, d in res["stats"].items():
        mu, sd = d["proj"], d["sd"]
        center = round(mu * 2) / 2.0
        lines = [round(center + off * 0.5, 1) for off in (-2, -1, 0, 1, 2)]
        out["stats"][stat] = {
            "proj": rnd(mu, 2), "sd": rnd(sd, 2),
            "lines": [{"line": ln,
                       "p": rnd(live_probs.p_over(mu, sd, ln), 3)}
                      for ln in lines],
        }
    return jsonify(out)


@app.get("/api/player/<int:pid>")
def player(pid):
    con = store.connect()
    p = con.execute("select name from players where espn_id=?",
                    (pid,)).fetchone()
    if not p:
        return jsonify(error="unknown player"), 404
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
    return jsonify(name=p[0], seasons=seasons, per36=per36, recent=recent,
                   projection=proj, arch=a[0] if a else None, spark=spark,
                   latest_season=latest)


@app.get("/api/leaders")
def leaders():
    season = request.args.get("season", 2026, type=int)
    stat = stat_wl(request.args.get("stat", "pts"))
    mg = request.args.get("min_games", 3, type=int)
    con = store.connect()
    rows = con.execute(
        f"select p.espn_id, p.name, avg(l.{stat}) v, count(*) g, "
        "avg(l.minutes) m from game_logs l join players p on "
        "p.espn_id=l.espn_id where l.source='official' and l.season=? "
        "group by l.espn_id having count(*) >= ? order by v desc limit 25",
        (season, mg),
    ).fetchall()
    return jsonify(season=season, stat=stat,
                   rows=[{"espn_id": r[0], "name": r[1], "v": rnd(r[2]),
                          "g": r[3], "min": rnd(r[4])} for r in rows])


@app.get("/api/archetypes")
def archetypes():
    if "arch" in _cache:
        return jsonify(_cache["arch"])
    con = store.connect()
    meta = json.loads(META_PATH.read_text())
    k = meta["k"]
    rows = con.execute(
        "select a.arch, a.espn_id, a.ref_minutes, p.name from "
        "player_archetypes a join players p on p.espn_id=a.espn_id "
        "order by a.arch, a.ref_minutes desc").fetchall()
    clusters = {a: [] for a in range(k)}
    for (arch_, pid, mins, name) in rows:
        clusters.setdefault(arch_, []).append(
            {"espn_id": pid, "name": name, "minutes": mins})
    out = []
    for a in range(k):
        ms = clusters.get(a, [])
        centroid = dict(zip(meta["features"],
                             [round(c, 2) for c in meta["centers"][a]]))
        out.append({"arch": a, "size": len(ms), "centroid": centroid,
                    "top": ms[:10]})
    try:
        profs = arch.load_profiles(con)
        lmap = {r[0]: r[1] for r in con.execute(
            "select espn_id, arch from player_archetypes")}
        labels = [lmap.get(p["espn_id"], 0) for p in profs]
        descs = {d["arch"]: d for d in arch.describe_clusters(
            con, profs, meta["centers"], labels, k)}
    except Exception as e:
        descs = {}
        print("describe failed:", e, flush=True)
    for c in out:
        d = descs.get(c["arch"], {})
        c["name"] = d.get("name", "cluster " + str(c["arch"]))
        c["desc"] = d.get("desc", "")
    _cache["arch"] = {"k": k, "clusters": out}
    return jsonify(_cache["arch"])


@app.get("/api/archetypes/scatter")
def scatter():
    if "scatter" in _cache:
        return jsonify(_cache["scatter"])
    con = store.connect()
    profs = arch.load_profiles(con)
    Xz = arch.scale_profiles(profs)
    pca = PCA(n_components=2)
    xy = pca.fit_transform(Xz)
    labels = {r[0]: r[1] for r in con.execute(
        "select espn_id, arch from player_archetypes")}
    names = {r[0]: r[1] for r in con.execute(
        "select espn_id, name from players")}
    pts = [{"espn_id": p["espn_id"],
            "name": names.get(p["espn_id"], str(p["espn_id"])),
            "x": round(float(x), 3), "y": round(float(y), 3),
            "arch": labels.get(p["espn_id"]), "minutes": p["minutes"]}
           for p, (x, y) in zip(profs, xy)]
    _cache["scatter"] = {
        "points": pts,
        "var": [round(float(v), 3) for v in pca.explained_variance_ratio_]}
    return jsonify(_cache["scatter"])


@app.post("/api/archetypes/scan")
def run_scan():
    """Fit-index tables. ?stab=1 adds bootstrap stability (slow)."""
    deep = request.args.get("stab") == "1"
    key = "scan_deep" if deep else "scan"
    if key not in _cache:
        con = store.connect()
        profs = arch.load_profiles(con)
        Xz = arch.scale_profiles(profs)
        km_rows, gmm_rows = arch.scan(Xz, stability=deep)
        _cache[key] = {"km": km_rows, "gmm": gmm_rows}
    return jsonify(rows=_cache[key]["km"], gmm=_cache[key]["gmm"])


@app.post("/api/archetypes/assign")
def run_assign():
    body = request.get_json(silent=True) or {}
    k = int(body.get("k", 5))
    if not 2 <= k <= 12:
        return jsonify(error="k out of range"), 400
    con = store.connect()
    profs = arch.load_profiles(con)
    Xz = arch.scale_profiles(profs)
    res = arch.assign(con, profs, Xz, k)
    _cache.clear()
    slim = {"k": res["k"], "n_players": res["n_players"],
            "clusters": [{"arch": c["arch"], "size": c["size"],
                          "avg_min_per_g": c["avg_min_per_g"],
                          "centroid": c["centroid"],
                          "members": c["members"][:10]}
                         for c in res["clusters"]]}
    return jsonify(slim)


@app.get("/api/archetypes/quality")
def quality():
    """Quality-per-k curve + auto pick, straight from persisted meta."""
    if not META_PATH.exists():
        return jsonify(error="no archetype meta yet"), 404
    meta = json.loads(META_PATH.read_text())
    km, gmm = meta.get("km"), meta.get("gmm")
    if not km or not gmm:
        return jsonify(error="no scan in meta - run optimize first"), 404
    best = min(gmm, key=lambda r: r["bic"])
    return jsonify(current_k=meta.get("k"), method=meta.get("method"),
                   auto_k=best["k"], auto_bic=best["bic"],
                   km=km, gmm=gmm)


@app.post("/api/archetypes/optimize")
def run_optimize():
    con = store.connect()
    profs = arch.load_profiles(con)
    Xz = arch.scale_profiles(profs)
    res = arch.optimize(con, profs, Xz)
    _cache.clear()
    return jsonify(k=res["k"], method=res["method"],
                   n_players=res["n_players"],
                   clusters=[{"arch": c["arch"], "size": c["size"],
                              "avg_min_per_g": c["avg_min_per_g"],
                              "centroid": c["centroid"],
                              "members": c["members"][:10]}
                             for c in res["clusters"]])


@app.get("/api/archetypes/preview")
def preview_k():
    """Fit any k without writing; plain-English cluster descriptions."""
    k = int(request.args.get("k", 10))
    if not 2 <= k <= 14:
        return jsonify(error="k out of range"), 400
    con = store.connect()
    profs = arch.load_profiles(con)
    Xz = arch.scale_profiles(profs)
    centers, labels = arch.preview(profs, Xz, k)
    clusters = arch.describe_clusters(con, profs, centers, labels, k)
    return jsonify(k=k, clusters=clusters)


@app.post("/api/archetypes/apply")
def apply_preview():
    body = request.get_json(silent=True) or {}
    k = int(body.get("k", 10))
    if not 2 <= k <= 14:
        return jsonify(error="k out of range"), 400
    con = store.connect()
    profs = arch.load_profiles(con)
    Xz = arch.scale_profiles(profs)
    res = arch.apply_k(con, profs, Xz, k)
    _cache.clear()
    return jsonify(k=res["k"], method=res["method"])


if __name__ == "__main__":
    print("NBA Model Lab on http://127.0.0.1:8099", flush=True)
    app.run(host="127.0.0.1", port=8099, threaded=True)
