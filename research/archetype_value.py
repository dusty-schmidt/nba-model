"""Does archetype membership add predictive value for next-season impact?

Target: next-season plus-minus per 36 minutes (official game_logs).
Features: this season's per-36 box rates + this season's pm36.
Compare: base features vs base + archetype one-hot, 5-fold CV on 2025 -> 2026 pairs.
Limitation: n is small (~300); a noisy proxy for impact, not a true net rating.
"""
import sqlite3

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app import store

DB = store.db_path()
MIN_MINUTES = 500


def rates(con, season):
    rows = con.execute(
        "select espn_id, sum(minutes), sum(pts), sum(reb), sum(ast), sum(stl), "
        "sum(blk), sum(tov), sum(fg3m), sum(plus_minus) from game_logs "
        "where source='official' and season=? group by espn_id", (season,)).fetchall()
    out = {}
    for r in rows:
        m = r[1] or 0
        if m >= MIN_MINUTES:
            out[r[0]] = [36.0 * x / m for x in r[2:]]
    return out


def main():
    con = sqlite3.connect(DB)
    arch = dict(con.execute("select espn_id, arch from player_archetypes").fetchall())
    a = rates(con, 2025)
    b = rates(con, 2026)
    ids = sorted(set(a) & set(b) & set(arch))
    print(f"paired players with 500+ min both seasons and an archetype: {len(ids)}")

    X_base = np.array([a[i] for i in ids])
    y = np.array([b[i][7] for i in ids])  # next-season pm36
    k = max(arch.values()) + 1
    onehot = np.zeros((len(ids), k))
    for n, i in enumerate(ids):
        onehot[n, arch[i]] = 1.0
    X_arch = np.hstack([X_base, onehot])

    print(f"target pm36: mean {y.mean():.2f} sd {y.std():.2f}")

    cv = KFold(n_splits=5, shuffle=True, random_state=0)
    results = {}
    for name, X in (("base", X_base), ("base+archetype", X_arch)):
        model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
        pred = cross_val_predict(model, X, y, cv=cv)
        results[name] = (r2_score(y, pred), mean_absolute_error(y, pred))
    naive = np.full_like(y, y.mean())
    print(f"naive mean: MAE {mean_absolute_error(y, naive):.3f}")
    for name, (r2, mae) in results.items():
        print(f"{name:16s} out-of-fold R2 {r2:+.4f} MAE {mae:.3f}")
    d_r2 = results["base+archetype"][0] - results["base"][0]
    print(f"archetype increment in R2: {d_r2:+.4f}")


if __name__ == "__main__":
    main()
