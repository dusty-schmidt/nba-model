"""Combined projection engine: learned mean + per-player variance.

Each half was validated separately: HistGradientBoosting on prior-game
features beats roll20 by about 1% MAE, and a player's own shrunk
variance calibrates better than a global sd. This checks they stack.

Walk-forward by month. For each month the mean model trains only on
prior months and the variance tracker holds only prior surprises.
The empirical distribution is kept per archetype for discrete stats,
where a normal is the wrong shape.
"""
import collections
from statistics import NormalDist

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

import store

STATS = ("pts", "reb", "ast", "fg3m")
# The learned mean beat roll20 on pts/reb/ast but LOST on fg3m: threes are
# sparse and discrete, so the gradient booster fits shot noise. Keep the
# plain rolling mean for those and let the variance layer do the work.
LEARNED_MEAN = {"pts": True, "reb": True, "ast": True, "fg3m": False}
WINDOW = 20
MIN_PRIOR = 10
MIN_VAR = 5
K_SD = 25.0
Z_LINES = (-1.0, -0.5, 0.0, 0.5, 1.0)
EPS = 1e-3
MAX_EMP = 1500
ND = NormalDist()


class Tracker:
    """Online surprise statistics, strictly prior games only."""

    def __init__(self):
        self.pooled_sq = 0.0
        self.pooled_n = 0
        self.arch_sq = collections.defaultdict(float)
        self.arch_n = collections.defaultdict(int)
        self.emp = collections.defaultdict(list)

    def add(self, arch, resid):
        self.pooled_sq += resid * resid
        self.pooled_n += 1
        self.arch_sq[arch] += resid * resid
        self.arch_n[arch] += 1
        if len(self.emp[arch]) < MAX_EMP:
            self.emp[arch].append(resid)

    def global_sd(self):
        if not self.pooled_n:
            return None
        return float(np.sqrt(self.pooled_sq / self.pooled_n))

    def arch_sd(self, arch):
        if self.arch_n[arch] < 20:
            return self.global_sd()
        return float(np.sqrt(self.arch_sq[arch] / self.arch_n[arch]))

    def empirical(self, arch):
        if len(self.emp[arch]) < 60:
            return None
        return np.sort(np.array(self.emp[arch], dtype=float))


def load():
    con = store.connect()
    rows = con.execute("""
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
            "arch": arch,
            "month": date[:7],
            "ha": ha,
            "mins": float(mins or 0.0),
            "pts": float(pts or 0.0),
            "reb": float(reb or 0.0),
            "ast": float(ast or 0.0),
            "fg3m": float(fg3m or 0.0),
        })
    return per


def features(hist, g, stat):
    values = np.array([h[stat] for h in hist[-WINDOW:]], dtype=float)
    mins = np.array([h["mins"] for h in hist[-WINDOW:]], dtype=float)
    roll20 = float(values.mean())
    roll10 = float(values[-10:].mean())
    roll5 = float(values[-5:].mean())
    sd20 = float(values.std(ddof=1)) if len(values) > 2 else 0.0
    feats = [
        roll20, roll10, roll5, sd20, roll5 - roll20,
        float(mins.mean()), float(mins[-5:].mean()), float(mins[-1]),
        float(g["ha"] == "H"), float(g["arch"]),
    ]
    return feats, roll20


def build_rows(per, stat):
    rows = []
    for pid, games in per.items():
        hist = []
        resid = []
        for g in games:
            if len(hist) >= MIN_PRIOR:
                feats, base = features(hist, g, stat)
                pvar = float(np.var(resid, ddof=1)) if len(resid) >= MIN_VAR else None
                rows.append({
                    "month": g["month"],
                    "f": feats,
                    "base": base,
                    "act": g[stat],
                    "arch": g["arch"],
                    "pvar": pvar,
                    "nres": len(resid),
                })
            hist.append(g)
            if len(hist) > MIN_PRIOR:
                prior = np.array([h[stat] for h in hist[-1 - WINDOW:-1]], dtype=float)
                resid.append(g[stat] - float(prior.mean()))
    rows.sort(key=lambda r: r["month"])
    return rows


def pnorm(mu, sd, line):
    if not sd or sd <= 0:
        return 0.5
    return 1.0 - ND.cdf((line - mu) / sd)


def evaluate(per, stat):
    rows = build_rows(per, stat)
    months = sorted({r["month"] for r in rows})
    trk = Tracker()
    models = ("roll20+global", "roll20+player", "learned+player", "learned+emp")
    brier = {k: 0.0 for k in models}
    lloss = {k: 0.0 for k in models}
    counted = 0

    for month in months[3:]:
        train = [r for r in rows if r["month"] < month]
        test = [r for r in rows if r["month"] == month]
        learned = None
        if LEARNED_MEAN[stat] and len(train) >= 3000 and len(test) >= 50:
            X = np.array([r["f"] for r in train], dtype=float)
            y = np.array([r["act"] - r["base"] for r in train], dtype=float)
            model = HistGradientBoostingRegressor(
                max_iter=150, learning_rate=0.06, max_depth=4,
                min_samples_leaf=40, random_state=42)
            model.fit(X, y)
            Xt = np.array([r["f"] for r in test], dtype=float)
            learned = np.array([r["base"] for r in test], dtype=float) + model.predict(Xt)

        for i, r in enumerate(test):
            gsd = trk.global_sd()
            asd = trk.arch_sd(r["arch"])
            if r["pvar"] is not None and asd:
                w = r["nres"] / (r["nres"] + K_SD)
                psd = float(np.sqrt(w * r["pvar"] + (1.0 - w) * asd * asd))
            else:
                psd = asd
            emp = trk.empirical(r["arch"])
            mu_learn = learned[i] if learned is not None else r["base"]

            if gsd and gsd > 0:
                for z in Z_LINES:
                    line = r["base"] + z * gsd
                    outcome = 1.0 if r["act"] > line else 0.0
                    probs = {
                        "roll20+global": pnorm(r["base"], gsd, line),
                        "roll20+player": pnorm(r["base"], psd, line),
                        "learned+player": pnorm(mu_learn, psd, line),
                        "learned+emp": (
                            float((emp > (line - mu_learn)).mean())
                            if emp is not None else pnorm(mu_learn, psd, line)),
                    }
                    for key, p in probs.items():
                        p = min(max(p, EPS), 1.0 - EPS)
                        brier[key] += (p - outcome) ** 2
                        lloss[key] -= (outcome * np.log(p) + (1.0 - outcome) * np.log(1.0 - p))
                    counted += 1

        for r in test:
            trk.add(r["arch"], r["act"] - r["base"])

    if not counted:
        return None
    return (counted,
            {k: brier[k] / counted for k in models},
            {k: lloss[k] / counted for k in models})


if __name__ == "__main__":
    print("combined engine, walk-forward Brier (lower is better)")
    print(f"{'stat':5} {'bets':>7} {'model':16} {'brier':>7} {'logloss':>8}")
    totals = collections.defaultdict(lambda: [0.0, 0.0, 0])
    per_all = load()
    for stat in STATS:
        out = evaluate(per_all, stat)
        if out is None:
            print(f"{stat:5} nothing evaluable")
            continue
        counted, brier, lloss = out
        best = min(brier, key=brier.get)
        for key in brier:
            mark = " <best" if key == best else ""
            print(f"{stat:5} {counted:7d} {key:16} {brier[key]:7.4f} {lloss[key]:8.4f}{mark}")
            totals[key][0] += brier[key] * counted
            totals[key][1] += lloss[key] * counted
            totals[key][2] += counted
    print()
    print("pooled:")
    for key, (bs, ls, c) in sorted(totals.items(), key=lambda kv: kv[1][0]):
        print(f"  {key:16} brier {bs / c:.4f}  logloss {ls / c:.4f}  n={c}")
