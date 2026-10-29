"""Live over/under probabilities for a player's next game.

Implements the strategy validated walk-forward in projections.py:

  mean    gradient boosting on strictly-prior-game features for
          pts/reb/ast; plain 20-game rolling mean for fg3m, where the
          booster fitted shot noise and lost to the rolling mean
  spread  the player's own residual sd against the rolling mean,
          shrunk toward their archetype's pooled residual sd
          (K=25 pseudo-games)
  tails   empirical residual distribution, used for the discrete stat

Models train once over all available history and are cached in
process. Live projection may use full history; only the backtest has
to stay blind.
"""
import collections
import threading
import unicodedata
from statistics import NormalDist

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

import store

ND = NormalDist()
WINDOW = 20
MIN_PRIOR = 10
K_SD = 25.0
EPS = 1e-3
EMP_MIN = 200
STATS = ("pts", "reb", "ast", "fg3m")
LEARNED = {"pts": True, "reb": True, "ast": True, "fg3m": False}

_lock = threading.Lock()
_cache = {}


def _features(hist, stat, arch, home):
    """Feature row identical to projections.features()."""
    values = np.array([h[stat] for h in hist[-WINDOW:]], dtype=float)
    mins = np.array([h["mins"] for h in hist[-WINDOW:]], dtype=float)
    roll20 = float(values.mean())
    roll10 = float(values[-10:].mean()) if len(values) >= 10 else roll20
    roll5 = float(values[-5:].mean()) if len(values) >= 5 else roll20
    sd20 = float(values.std(ddof=1)) if len(values) > 2 else 0.0
    return [
        roll20, roll10, roll5, sd20, roll5 - roll20,
        float(mins.mean()), float(mins[-5:].mean()), float(mins[-1]),
        float(bool(home)), float(arch),
    ], roll20


def _load(con):
    rows = con.execute("""
        select l.espn_id, l.game_date, l.home_away, l.minutes,
               l.pts, l.reb, l.ast, l.fg3m, a.arch
        from game_logs l
        left join player_archetypes a on a.espn_id = l.espn_id
        where l.source='official' and l.minutes > 0
        order by l.espn_id, l.game_date
    """).fetchall()
    per = collections.defaultdict(list)
    for pid, date, ha, mins, pts, reb, ast, fg3m, arch in rows:
        per[pid].append({
            "date": date,
            "home": ha == "H",
            "mins": float(mins or 0.0),
            "pts": float(pts or 0.0),
            "reb": float(reb or 0.0),
            "ast": float(ast or 0.0),
            "fg3m": float(fg3m or 0.0),
            "arch": -1 if arch is None else int(arch),
        })
    return per


def train(con):
    """Fit boosters and pool residual statistics once, then cache."""
    with _lock:
        if _cache.get("ready"):
            return _cache
        per = _load(con)
        out = {"models": {}, "arch_sd": {}, "emp": {},
               "pooled": {}, "player_resid": {}}
        for stat in STATS:
            X, y, base_rows = [], [], []
            sq = collections.defaultdict(float)
            n = collections.defaultdict(int)
            emp = collections.defaultdict(list)
            for pid, games in per.items():
                hist = []
                resid = []
                for g in games:
                    if len(hist) >= MIN_PRIOR:
                        feats, roll20 = _features(
                            hist, stat, g["arch"], g["home"])
                        X.append(feats)
                        y.append(g[stat] - roll20)
                        base_rows.append(roll20)
                        r = g[stat] - roll20
                        sq[g["arch"]] += r * r
                        n[g["arch"]] += 1
                        if len(emp[g["arch"]]) < 4000:
                            emp[g["arch"]].append(r)
                        resid.append(r)
                    hist.append(g)
                if len(resid) >= 5:
                    out["player_resid"][(pid, stat)] = resid[-400:]
            if LEARNED[stat] and len(X) >= 1000:
                model = HistGradientBoostingRegressor(
                    max_iter=150, learning_rate=0.06, max_depth=4,
                    min_samples_leaf=40, random_state=42)
                model.fit(np.array(X, dtype=float), np.array(y, dtype=float))
                out["models"][stat] = model
            out["pooled"][stat] = {a: float(np.sqrt(sq[a] / n[a]))
                                   for a in n if n[a] >= 20}
            out["arch_sd"][stat] = {a: float(np.sqrt(sq[a] / n[a]))
                                     for a in n if n[a] >= 20}
            out["emp"][stat] = {a: np.sort(np.array(v, dtype=float))
                                for a, v in emp.items() if len(v) >= EMP_MIN}
        _cache.clear()
        _cache.update(out)
        _cache["ready"] = True
        return _cache


def resolve(con, name):
    """Player id for a name, preferring the row that has official logs.

    players holds duplicate rows for the same human: an ASCII spelling
    with zero official games and the accented real one. Pick by log
    count so the lookup cannot land on the empty row.
    """
    def norm(text):
        stripped = unicodedata.normalize("NFKD", text or "")
        return "".join(c for c in stripped
                       if not unicodedata.combining(c)).lower()

    target = norm(name)
    rows = [pid for pid, nm in con.execute(
        "select espn_id, name from players").fetchall()
        if norm(nm) == target]
    if not rows:
        rows = [pid for pid, nm in con.execute(
            "select espn_id, name from players").fetchall()
            if target in norm(nm)]
    best = None
    for pid in rows:
        n = con.execute(
            "select count(*) from game_logs where espn_id=? and "
            "source='official' and minutes>0", (pid,)).fetchone()[0]
        if best is None or n > best[1]:
            best = (pid, n)
    return best[0] if best and best[1] > 0 else None


def project(con, pid):
    """Next-game projection and spread for one player."""
    c = train(con)
    history = _load(con).get(pid) or []
    if len(history) < MIN_PRIOR + 1:
        return None
    arch = history[-1]["arch"]
    home = history[-1]["home"]
    out = {"pid": pid, "arch": arch, "games": len(history), "stats": {}}
    for stat in STATS:
        feats, roll20 = _features(history[:-1], stat, arch, home)
        if stat in c["models"]:
            proj = roll20 + float(c["models"][stat].predict(
                np.array([feats], dtype=float))[0])
        else:
            proj = roll20
        arch_sd = c["arch_sd"][stat].get(arch)
        if arch_sd is None and c["arch_sd"][stat]:
            arch_sd = float(np.median(list(c["arch_sd"][stat].values())))
        arch_sd = arch_sd or 1.0
        resid = c["player_resid"].get((pid, stat))
        if resid and len(resid) >= 5:
            w = len(resid) / (len(resid) + K_SD)
            own = float(np.var(resid, ddof=1))
            sd = float(np.sqrt(w * own + (1.0 - w) * arch_sd * arch_sd))
        else:
            sd = arch_sd
        out["stats"][stat] = {"proj": proj, "sd": sd}
    return out


def p_over(mu, sd, line, emp=None):
    """P(stat > line) from a normal, or an empirical residual sample."""
    if emp is not None and len(emp) >= EMP_MIN:
        return float((emp > (line - mu)).mean())
    if not sd or sd <= 0:
        return 0.5
    return 1.0 - ND.cdf((line - mu) / sd)


if __name__ == "__main__":
    con = store.connect()
    for name in ("Luka Doncic", "Nikola Jokic", "Rudy Gobert"):
        pid = resolve(con, name)
        if pid is None:
            print(name, "not found")
            continue
        res = project(con, pid)
        if res is None:
            print(name, "insufficient history")
            continue
        parts = []
        for stat in STATS:
            d = res["stats"][stat]
            parts.append("%s %.1f+-%.1f" % (stat, d["proj"], d["sd"]))
        print("%-16s arch %2d | %s" % (name, res["arch"], "  ".join(parts)))
        pts = res["stats"]["pts"]
        line = round(pts["proj"] * 2) / 2.0
        print("   P(pts > %.1f) = %.3f" % (
            line, p_over(pts["proj"], pts["sd"], line)))
