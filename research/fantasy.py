"""Fantasy scoring: fantasy points per minute and game-level volatility.

Source: official game_logs only (source='official'). Scoring is an assumption
and lives in SCORING so it is one edit to change.

Volatility = game-level standard deviation of fantasy points, plus the
coefficient of variation (sd / mean) so high-volume and low-volume players
compare fairly.
"""
import math
import sqlite3
import sys

from app import store

DB = store.db_path()

# Default league scoring (assumed, not verified against a specific league).
SCORING = {"pts": 1.0, "reb": 1.2, "ast": 1.5, "stl": 3.0, "blk": 3.0, "tov": -1.0}


def fp_expr():
    return " + ".join(f"{w} * {c}" for c, w in SCORING.items())


def game_fp(con, season, min_minutes=1):
    """Per-game (espn_id, minutes, fantasy points) rows for one season."""
    q = (
        f"select espn_id, minutes, ({fp_expr()}) fp from game_logs "
        "where source='official' and season=? and minutes >= ?"
    )
    return con.execute(q, (season, min_minutes)).fetchall()


def summarize(con, season, min_games=10):
    """One dict per player: FP/min, mean FP/game, game-level SD and CV."""
    by = {}
    for pid, mins, fp in game_fp(con, season):
        by.setdefault(pid, []).append((mins, fp))
    names = dict(con.execute("select espn_id, name from players").fetchall())
    out = []
    for pid, games in by.items():
        n = len(games)
        if n < min_games:
            continue
        total_min = sum(m for m, _ in games)
        fps = [f for _, f in games]
        mean = sum(fps) / n
        var = sum((f - mean) ** 2 for f in fps) / (n - 1)
        sd = math.sqrt(var)
        out.append({
            "espn_id": pid,
            "name": names.get(pid, str(pid)),
            "g": n,
            "fp_per_min": round(sum(fps) / total_min, 3) if total_min else None,
            "fp_per_game": round(mean, 2),
            "fp_sd": round(sd, 2),
            "fp_cv": round(sd / mean, 3) if mean else None,
        })
    out.sort(key=lambda r: r["fp_per_min"] or 0, reverse=True)
    return out


def main():
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    con = sqlite3.connect(DB)
    rows = summarize(con, season)
    print(f"season {season}: {len(rows)} players with 10+ games")
    print(f"{'name':24s} {'g':>3s} {'fp/min':>7s} {'fp/g':>6s} {'sd':>5s} {'cv':>6s}")
    for r in rows[:15]:
        print(f"{r['name'][:24]:24s} {r['g']:>3d} {r['fp_per_min']:>7.3f} "
              f"{r['fp_per_game']:>6.2f} {r['fp_sd']:>5.2f} {r['fp_cv']:>6.3f}")


if __name__ == "__main__":
    main()
