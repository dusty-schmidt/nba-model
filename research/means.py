"""Does a learned mean model beat roll20, walk-forward?

Features come only from strictly prior games. The model predicts the
residual against roll20, so the baseline signal is never discarded.
Train on everything before a month, predict that month, month by month.
"""
import collections
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from app import store

STATS = ("pts", "reb", "ast", "fg3m")
MIN_PRIOR = 10
WINDOW = 20


def load(stat):
    con = store.connect()
    rows = con.execute(f"""
        select l.espn_id, a.arch, l.game_date, l.home_away,
               l.minutes, l.pts, l.reb, l.ast, l.fg3m
        from game_logs l
        join player_archetypes a on a.espn_id = l.espn_id
        where l.source='official' and l.minutes>0
          and l.season in (2025, 2026)
        order by l.espn_id, l.game_date
    """).fetchall()
    per = collections.defaultdict(list)
    for pid, arch, date, ha, mins, pts, reb, ast, fg3m in rows:
        per[pid].append({
            "arch": arch, "date": date, "ha": ha,
            "mins": float(mins or 0.0), "pts": float(pts or 0.0),
            "reb": float(reb or 0.0), "ast": float(ast or 0.0),
            "fg3m": float(fg3m or 0.0),
        })
    return per


def build(per, stat):
    """One row per game with a full history: (month, feats, target, base)."""
    out = []
    for pid, games in per.items():
        hist = []
        for g in games:
            value = g[stat]
            if len(hist) >= MIN_PRIOR:
                w = hist[-WINDOW:]
                vals = np.array([h[stat] for h in w], float)
                mins = np.array([h["mins"] for h in w], float)
                roll20 = float(vals.mean())
                roll10 = float(vals[-10:].mean())
                roll5 = float(vals[-5:].mean())
                sd20 = float(vals.std(ddof=1)) if len(vals) > 2 else 0.0
                feats = [
                    roll20, roll10, roll5, sd20, roll5 - roll20,
                    float(mins.mean()), float(mins[-5:].mean()),
                    float(mins[-1]), float(g["ha"] == "H"),
                    float(g["arch"]),
                ]
                out.append((g["date"][:7], feats, value, roll20))
            hist.append(g)
    return out


def evaluate(stat):
    rows = sorted(build(load(stat), stat), key=lambda r: r[0])
    months = sorted({r[0] for r in rows})
    base_err, mod_err = [], []
    used_months = []
    for month in months[4:]:
        train = [r for r in rows if r[0] < month]
        test = [r for r in rows if r[0] == month]
        if len(train) < 3000 or len(test) < 50:
            continue
        X = np.array([r[1] for r in train], float)
        y = np.array([r[2] - r[3] for r in train], float)
        model = HistGradientBoostingRegressor(
            max_iter=150, learning_rate=0.06, max_depth=4,
            min_samples_leaf=40, random_state=42)
        model.fit(X, y)
        Xt = np.array([r[1] for r in test], float)
        act = np.array([r[2] for r in test], float)
        base = np.array([r[3] for r in test], float)
        pred = base + model.predict(Xt)
        base_err.extend(np.abs(base - act))
        mod_err.extend(np.abs(pred - act))
        used_months.append(month)
    if not base_err:
        return None
    return (len(base_err), float(np.mean(base_err)),
            float(np.mean(mod_err)), used_months[0], used_months[-1])


if __name__ == "__main__":
    print("walk-forward mean model vs roll20 (lower is better)")
    print(f"{'stat':5} {'n':>7} {'roll20':>8} {'model':>8} {'delta':>8}  window")
    for stat in STATS:
        r = evaluate(stat)
        if r is None:
            print(f"{stat:5} no evaluable months")
            continue
        n, base, mod, first, last = r
        verdict = "model wins" if mod < base else "baseline holds"
        pct = (mod - base) / base * 100
        print(f"{stat:5} {n:7d} {base:8.3f} {mod:8.3f} {pct:+7.1f}%  "
              f"{first}..{last}  {verdict}")
