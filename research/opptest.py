"""Decisive gate: does archetype-conditioned opponent adjustment beat both
no adjustment and plain overall opponent adjustment, walk-forward?
u001d additive residual model, two-level shrinkage.
"""
import collections

from app import store
import numpy as np

K_OPP = 200.0
K_ARCH = 150.0
WINDOW = 20
MIN_PRIOR = 5
STATS = ("pts", "reb", "ast")


def run(stat):
    con = store.connect()
    rows = con.execute(f"""
        select l.espn_id, a.arch, l.opponent, l.season, l.game_date, l.{stat}
        from game_logs l
        join player_archetypes a on a.espn_id = l.espn_id
        where l.source='official' and l.minutes>0
        order by l.espn_id, l.game_date
    """).fetchall()

    per_player = collections.defaultdict(list)
    for r in rows:
        per_player[r[0]].append(r)

    opp_sum = collections.defaultdict(float)
    opp_n = collections.defaultdict(float)
    arch_sum = collections.defaultdict(float)
    arch_n = collections.defaultdict(float)

    preds = []  # (base, opp_adj, arch_adj, actual)
    for pid, games in per_player.items():
        hist = []
        for pid2, arch, opp, sea, date, val in games:
            if len(hist) >= MIN_PRIOR:
                base = float(np.mean(hist[-WINDOW:]))
                ko = opp_sum[opp] / (opp_n[opp] + K_OPP)
                ka = arch_sum[(opp, arch)] / (arch_n[(opp, arch)] + K_ARCH)
                preds.append((base, base + ko, base + ka, float(val)))
            hist.append(float(val))
            resid = float(val) - (float(np.mean(hist[-WINDOW:])) if len(hist) >= MIN_PRIOR else float(val))
            opp_sum[opp] += resid; opp_n[opp] += 1.0
            arch_sum[(opp, arch)] += resid; arch_n[(opp, arch)] += 1.0

    arr = np.array(preds)
    out = {}
    for i, name in enumerate(("base", "opp", "arch")):
        err = np.abs(arr[:, i] - arr[:, 3])
        out[name] = float(err.mean())
    out["n"] = len(arr)
    return out


if __name__ == "__main__":
    print("walk-forward MAE (lower is better) — additive opponent residuals")
    print(f"{'stat':5} {'n':>6} {'base':>7} {'opp':>7} {'arch':>7}  verdict")
    for s in STATS:
        r = run(s)
        best = min(r["opp"], r["arch"])
        verdict = "arch wins" if r["arch"] < r["opp"] else "opp wins"
        gain = (r["base"] - best) / r["base"] * 100
        print(f"{s:5} {r['n']:6d} {r['base']:7.3f} {r['opp']:7.3f} {r['arch']:7.3f}  "
              f"{verdict} (best gain vs base {gain:+.1f}%)")
