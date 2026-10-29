"""Walk-forward baseline projection model + evaluation harness.

Dumbest useful model, evaluated honestly. Every game is predicted using only
strictly earlier games (no leakage). Methods:
  naive_prev - season-to-date average; falls back to prior-season average
  rollN      - mean of last N raw values (N = 5, 10, 20)
  decN       - projected minutes (mean last N) x per-36 rate (mean last N)
  decay20    - exponentially weighted mean of last 20 (tau = 7 games)

The winner becomes the yardstick every fancier model must beat.

Usage: uv run python -m research.baseline
"""
import math
import sqlite3

from app import store

STATS = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m"]
PLAYERS = [(1629029, "Luka Doncic"), (203999, "Nikola Jokic")]
COLS = ["game_date", "season", "minutes"] + STATS
METHODS = ["naive_prev", "roll5", "roll10", "roll20",
           "dec5", "dec10", "dec20", "decay20"]


def load(con, pid):
    rows = con.execute(
        f"select {', '.join(COLS)} from game_logs "
        "where espn_id=? and source='official' order by game_date",
        (pid,),
    ).fetchall()
    return [dict(zip(COLS, r)) for r in rows]


def wmean(vals, weights=None):
    if not vals:
        return None
    if weights is None:
        return sum(vals) / len(vals)
    return sum(v * w for v, w in zip(vals, weights)) / sum(weights)


def predict_naive(prior, cur_season, stat):
    same = [p[stat] for p in prior
            if p["season"] == cur_season and p[stat] is not None]
    if same:
        return wmean(same)
    prev = [p[stat] for p in prior
            if p["season"] == cur_season - 1 and p[stat] is not None]
    return wmean(prev) if prev else None


def predict(prior, stat, method):
    if method.startswith("roll"):
        pool = prior[-int(method[4:]):]
        return wmean([p[stat] for p in pool if p[stat] is not None])
    if method.startswith("dec") and method != "decay20":
        pool = prior[-int(method[3:]):]
        mins = [p["minutes"] for p in pool if p["minutes"]]
        if not mins:
            return None
        pm = wmean(mins)
        rates = [36.0 * p[stat] / p["minutes"] for p in pool
                 if p["minutes"] and p[stat] is not None]
        if not rates:
            return None
        return pm * wmean(rates) / 36.0
    if method == "decay20":
        pool = prior[-20:]
        vals = [p[stat] for p in pool if p[stat] is not None]
        if not vals:
            return None
        ws = [math.exp(-(len(vals) - 1 - k) / 7.0)
              for k in range(len(vals))]
        return wmean(vals, ws)
    raise ValueError(method)


def evaluate(rows):
    errs = {m: {s: [] for s in ["minutes"] + STATS} for m in METHODS}
    n_eval = 0
    for i, cur in enumerate(rows):
        prior = rows[:i]
        if not prior:
            continue
        n_eval += 1
        for m in METHODS:
            for s in ["minutes"] + STATS:
                if m == "naive_prev":
                    pred = predict_naive(prior, cur["season"], s)
                else:
                    pred = predict(prior, s, m)
                if pred is None or cur[s] is None:
                    continue
                errs[m][s].append(abs(pred - cur[s]))
    return n_eval, errs


def mae(err_list):
    return sum(err_list) / len(err_list) if err_list else None


def report(name, n_eval, errs, pooled):
    print(f"\n=== {name} - {n_eval} walk-forward games ===")
    print(f"{'stat':8s}" + "".join(f"{m:>11}" for m in METHODS))
    for s in ["minutes"] + STATS:
        cells = ""
        for m in METHODS:
            v = mae(errs[m][s])
            cells += f"{v:11.2f}" if v is not None else f"{'-':>11}"
        print(f"{s:8s}{cells}")
    for m in METHODS:
        for s in ["minutes"] + STATS:
            pooled[m][s].extend(errs[m][s])


def main():
    con = sqlite3.connect(store.db_path())
    pooled = {m: {s: [] for s in ["minutes"] + STATS} for m in METHODS}
    total_games = 0
    for pid, name in PLAYERS:
        rows = load(con, pid)
        n_eval, errs = evaluate(rows)
        total_games += n_eval
        report(f"{name} ({pid})", n_eval, errs, pooled)
    report("POOLED (both players)", total_games, pooled,
           {m: {s: [] for s in pooled[m]} for m in METHODS})
    print("\nbest method per stat (pooled MAE):")
    for s in STATS:
        best = min((mae(pooled[m][s]), m) for m in METHODS)
        print(f"  {s:5s} -> {best[1]:11s} {best[0]:.2f}")


if __name__ == "__main__":
    main()
