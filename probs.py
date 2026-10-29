"""Probability layer: projection -> P(over line).

Walk-forward. Every variance estimate uses only strictly prior games.
Scores normal-based sd models against a nonparametric archetype
residual distribution, with Brier and log loss at lines set in
units of the global sd so every stat is comparable.
"""
import collections
import numpy as np

import store

WINDOW = 20
MIN_PRIOR = 8
MIN_VAR_PRIOR = 5
STATS = ("pts", "reb", "ast", "fg3m")
Z_LINES = (-1.0, -0.5, 0.0, 0.5, 1.0)
K_SD = 25.0           # shrinkage count toward archetype sd
EPS = 1e-3
MAX_EMP = 800         # cap stored residuals per archetype


def load(stat):
    con = store.connect()
    rows = con.execute(f"""
        select l.espn_id, a.arch, l.game_date, l.{stat}
        from game_logs l
        join player_archetypes a on a.espn_id = l.espn_id
        where l.source='official' and l.minutes>0 and l.season in (2025,2026)
        order by l.espn_id, l.game_date
    """).fetchall()
    per = collections.defaultdict(list)
    for pid, arch, date, val in rows:
        per[pid].append((arch, val))
    return per


def evaluate(stat):
    per = load(stat)
    # online pooled sd trackers
    g_sq, g_n = 0.0, 0.0
    a_sq = collections.defaultdict(float)
    a_n = collections.defaultdict(float)
    a_res = collections.defaultdict(list)

    records = []   # (proj, sd_global, sd_player, sd_arch, emp_sorted, actual, arch)
    for pid, games in per.items():
        hist = []
        resid = []
        for arch, val in games:
            v = float(val)
            if len(hist) >= MIN_PRIOR:
                proj = float(np.mean(hist[-WINDOW:]))
                sd_g = np.sqrt(g_sq / g_n) if g_n > 0 else None
                # player sd, shrunk toward archetype then global
                sd_a = np.sqrt(a_sq[arch] / a_n[arch]) if a_n[arch] >= 20 else sd_g
                if len(resid) >= MIN_VAR_PRIOR:
                    pv = float(np.var(resid, ddof=1))
                    w = len(resid) / (len(resid) + K_SD)
                    sd_p = np.sqrt(w * pv + (1 - w) * sd_a ** 2)
                else:
                    sd_p = sd_a
                emp = np.sort(np.array(a_res[arch])) if a_n[arch] >= 40 else None
                if sd_g and sd_g > 0:
                    records.append((proj, sd_g, sd_p, sd_a, emp, v, arch))
            # update trackers with this game's surprise
            if len(hist) >= MIN_PRIOR:
                r = v - float(np.mean(hist[-WINDOW:]))
                g_sq += r * r
                g_n += 1
                a_sq[arch] += r * r
                a_n[arch] += 1
                if len(a_res[arch]) < MAX_EMP:
                    a_res[arch].append(r)
                resid.append(r)
            hist.append(v)

    rec = np.array([(r[0], r[1], r[2], r[3], r[5]) for r in records])
    emps = [r[4] for r in records]
    proj, sd_g, sd_p, sd_a, actual = (rec[:, i] for i in range(5))
    n = len(rec)

    from statistics import NormalDist
    nd = NormalDist()

    def pnorm(mu, sd, line):
        if sd is None or sd <= 0:
            return 0.5
        return 1.0 - nd.cdf((line - mu) / sd)

    models = {"global-normal": None, "player-normal": None,
              "arch-normal": None, "arch-empirical": None}
    brier = {k: 0.0 for k in models}
    lloss = {k: 0.0 for k in models}
    counts = 0

    for i in range(n):
        gsd = sd_g[i]
        lines = [proj[i] + z * gsd for z in Z_LINES]
        emp = emps[i]
        for line in lines:
            outcome = 1.0 if actual[i] > line else 0.0
            ps = {
                "global-normal": pnorm(proj[i], sd_g[i], line),
                "player-normal": pnorm(proj[i], sd_p[i], line),
                "arch-normal": pnorm(proj[i], sd_a[i], line),
                "arch-empirical": (float((emp > (line - proj[i])).mean())
                                   if emp is not None else pnorm(proj[i], sd_a[i], line)),
            }
            for k, p in ps.items():
                p = min(max(p, EPS), 1 - EPS)
                brier[k] += (p - outcome) ** 2
                lloss[k] -= (outcome * np.log(p) + (1 - outcome) * np.log(1 - p))
            counts += 1

    return (n, counts, {k: brier[k] / counts for k in models},
            {k: lloss[k] / counts for k in models})


if __name__ == "__main__":
    print("probability calibration, walk-forward (lower is better)")
    print(f"{'stat':5} {'games':>6} {'bets':>7} | "
          f"{'model':16} {'brier':>7} {'logloss':>8}")
    totals = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for s in STATS:
        n, c, b, l = evaluate(s)
        best = min(b, key=b.get)
        for k in b:
            totals[k][0] += b[k] * c
            totals[k][1] += l[k] * c
            totals[k][2] += c
            mark = " <best" if k == best else ""
            print(f"{s:5} {n:6d} {c:7d} | {k:16} {b[k]:7.4f} {l[k]:8.4f}{mark}")
        print()
    print("pooled across stats:")
    for k, (bs, ls, c) in sorted(totals.items(), key=lambda kv: kv[1][0]):
        print(f"  {k:16} brier {bs/c:.4f}  logloss {ls/c:.4f}  n={c}")
