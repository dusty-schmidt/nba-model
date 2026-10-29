"""Build player archetypes from 2024-25 profiles (k-means + GMM lens).

v4: owner asked for drives-to-the-rim and defensive rate stats so new
role structure can emerge. Data now in the store:
 player_tracking (pt_pull.py): drives, drive_pts, catch_shoot_pts,
   pull_up_pts, passes_made, def_rim_fga ... per-game tracking averages
 player_advanced (advanced_pull.py): official USG/TS/ratings - usg/ts
   already computed from box+team joins; ratings are quality, not role.

New mechanism features (how production is assembled):
 drives36    36*DRIVES/min_pg        rim-attack volume rate
 drive_share DRIVE_PTS/pts_pg        share of scoring from rim attacks
 cs_share    CATCH_SHOOT_PTS/pts_pg   off-ball movement scoring share
 pull_share  PULL_UP_PTS/pts_pg       self-created scoring share
 pass36      36*PASSES_MADE/min_pg   hub/connector rate
 rimd36      36*DEF_RIM_FGA/min_pg   rim-protecting contest rate

Full 25: pts36 reb36 oreb36 dreb36 ast36 stl36 blk36 tov36 fta36 fg3a36
 atr usg ts efg ftr ast_tov 3share ftm_share pm36
 + drives36 drive_share cs_share pull_share pass36 rimd36

Players without a tracking row are dropped (count printed), not
zero-filled: a missing row means no trackable minutes.

Lenses: k-means (silhouette + 2-seed ARI) and GMM (BIC/AIC, diag).
If k-means still caps near 5-7 while GMM BIC prefers 8-9, the ceiling
is the lens, not the league. Reference season = 2025 (2024-25).
Minutes/game NOT a feature: archetypes are role, not rotation slot.

Modes:
 scan            both scan tables, no writes
 optimize        auto k = GMM BIC minimum, assign via GMM labels
 assign <k>      manual k-means at k -> player_archetypes + json

Usage: /opt/venv/bin/python archetypes.py scan|optimize|assign [k]
"""
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.metrics import (adjusted_rand_score, calinski_harabasz_score,
    davies_bouldin_score, silhouette_score)
from sklearn.preprocessing import StandardScaler

import store

REF_SEASON = 2025
MIN_MINUTES = 200.0
MIN_GAMES = 10
K_RANGE = range(4, 13)
RANDOM_STATE = 42
FEATURES = ["pts36", "reb36", "oreb36", "dreb36", "ast36", "stl36",
    "blk36", "tov36", "fta36", "fg3a36", "atr", "usg", "ts",
    "efg", "ftr", "ast_tov", "3share", "ftm_share", "pm36",
    "drives36", "drive_share", "cs_share", "pull_share", "pass36",
    "rimd36"]


def load_tracking(con):
    rows = con.execute(
        "select espn_id, min, drives, drive_pts, catch_shoot_pts, "
        "pull_up_pts, passes_made, def_rim_fga from player_tracking "
        "where season=?", (REF_SEASON,)).fetchall()
    return {r[0]: r for r in rows}


def load_profiles(con):
    rows = con.execute(
        "select l.espn_id, count(*) g, sum(l.minutes), sum(l.pts), "
        "sum(l.reb), sum(l.oreb), sum(l.ast), sum(l.stl), sum(l.blk), "
        "sum(l.tov), sum(l.fta), sum(l.fg3a), sum(l.fga), sum(l.fgm), "
        "sum(l.fg3m), sum(l.ftm), sum(l.plus_minus), "
        "100.0 * sum((l.fga + 0.44*l.fta + l.tov) * 48.0) "
        "/ nullif(sum(l.minutes * (t.fga + 0.44*t.fta + t.tov)), 0) usg "
        "from game_logs l join team_games t "
        "on l.game_id = t.game_id and l.season = t.season "
        "and t.team_id != (select team_id from teams where abbrev = l.opponent) "
        "where l.source='official' and l.season=? group by l.espn_id",
        (REF_SEASON,),
    ).fetchall()
    tracking = load_tracking(con)
    out = []
    dropped = 0
    for (pid, g, minutes, pts, reb, oreb, ast, stl, blk, tov, fta,
            fg3a, fga, fgm, fg3m, ftm, pm, usg) in rows:
        if not minutes or minutes < MIN_MINUTES or g < MIN_GAMES:
            continue
        if usg is None:
            continue
        tr = tracking.get(pid)
        if tr is None or not tr[1] or tr[1] <= 0:
            dropped += 1
            continue
        m36 = 36.0 / minutes
        fga_ = fga or 0.0
        fta_ = fta or 0.0
        fg3a_ = fg3a or 0.0
        pts_ = pts or 0.0
        denom = fga_ + 0.44 * fta_
        ts = 100.0 * pts_ / (2 * denom) if denom else 0.0
        efg = ((fgm or 0.0) + 0.5 * (fg3m or 0.0)) / fga_ if fga_ else 0.0
        atr = fg3a_ / fga_ if fga_ else 0.0
        ftr = fta_ / fga_ if fga_ else 0.0
        ast_tov = (ast or 0.0) / max(tov or 0.0, 1.0)
        three_share = 3.0 * (fg3m or 0.0) / pts_ if pts_ else 0.0
        ftm_share = (ftm or 0.0) / pts_ if pts_ else 0.0
        pm36 = m36 * (pm or 0.0)
        dreb = (reb or 0.0) - (oreb or 0.0)
        feats = [(v or 0.0) * m36 for v in
            (pts_, reb, oreb, dreb, ast, stl, blk, tov, fta_, fg3a_)]
        feats += [atr, usg, ts, efg, ftr, ast_tov, three_share,
            ftm_share, pm36]
        _, tmin, tdrives, tdrive_pts, tcs_pts, tpull_pts, tpasses, trim_fga = tr
        mpg = tmin
        pts_pg = pts_ / g if g else 0.0
        drives36 = 36.0 * (tdrives or 0.0) / mpg
        drive_share = (tdrive_pts or 0.0) / pts_pg if pts_pg else 0.0
        cs_share = (tcs_pts or 0.0) / pts_pg if pts_pg else 0.0
        pull_share = (tpull_pts or 0.0) / pts_pg if pts_pg else 0.0
        pass36 = 36.0 * (tpasses or 0.0) / mpg
        rimd36 = 36.0 * (trim_fga or 0.0) / mpg
        feats += [drives36, drive_share, cs_share, pull_share, pass36,
            rimd36]
        out.append({"espn_id": pid, "minutes": minutes, "games": g,
            "features": feats})
    print(f"{len(out)} players qualified; {dropped} dropped "
        "(no tracking row)")
    return out


def scale_profiles(profs):
    return StandardScaler().fit_transform(
        np.array([p["features"] for p in profs]))


def _icl(gm, Xz):
    """BIC + 2 * classification entropy: BIC that punishes fuzzy overlap."""
    resp = gm.predict_proba(Xz)
    ent = -(resp * np.log(resp + 1e-12)).sum()
    return gm.bic(Xz) + 2.0 * ent


def _stability(Xz, k, n_boot=12, frac=0.8, seed=RANDOM_STATE):
    """Bootstrap ARI: refit GMM on subsamples, ARI vs the full-data partition.

    This is the real question the fit indices do not answer: does the
    partition survive resampling, or is it an artifact of these 341 rows?
    """
    rng = np.random.default_rng(seed)
    base = GaussianMixture(n_components=k, covariance_type="diag", n_init=5,
        reg_covar=1e-4, random_state=seed).fit(Xz)
    base_lab = base.predict(Xz)
    n = len(Xz)
    scores = []
    for _ in range(n_boot):
        idx = rng.choice(n, size=int(n * frac), replace=False)
        gm = GaussianMixture(n_components=k, covariance_type="diag", n_init=3,
            reg_covar=1e-4, random_state=seed).fit(Xz[idx])
        scores.append(adjusted_rand_score(base_lab[idx], gm.predict(Xz[idx])))
    return float(np.mean(scores)), float(np.min(scores))


def scan(Xz, k_range=K_RANGE, stability=True):
    km_rows = []
    for k in k_range:
        km1 = KMeans(n_clusters=k, n_init=25, random_state=RANDOM_STATE).fit(Xz)
        km2 = KMeans(n_clusters=k, n_init=25, random_state=0).fit(Xz)
        km_rows.append({
            "k": k,
            "silhouette": round(float(silhouette_score(Xz, km1.labels_)), 3),
            "inertia": round(float(km1.inertia_), 1),
            "ari": round(float(adjusted_rand_score(km1.labels_, km2.labels_)), 3),
            "ch": round(float(calinski_harabasz_score(Xz, km1.labels_)), 1),
            "db": round(float(davies_bouldin_score(Xz, km1.labels_)), 3),
        })
    gmm_rows = []
    for k in k_range:
        gm = GaussianMixture(n_components=k, covariance_type="diag",
            n_init=5, reg_covar=1e-4,
            random_state=RANDOM_STATE).fit(Xz)
        row = {
            "k": k,
            "bic": round(float(gm.bic(Xz)), 1),
            "aic": round(float(gm.aic(Xz)), 1),
            "icl": round(float(_icl(gm, Xz)), 1),
        }
        if stability:
            mean_ari, min_ari = _stability(Xz, k)
            row["stab"] = round(mean_ari, 3)
            row["stab_min"] = round(min_ari, 3)
        gmm_rows.append(row)
    return km_rows, gmm_rows



def player_name(con, pid):
    r = con.execute("select name from players where espn_id=?",
        (pid,)).fetchone()
    return r[0] if r else str(pid)


def persist(con, profs, labels, centers, k, method, scan=None):
    """Write labels to player_archetypes + meta json; return summary."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS player_archetypes ("
        "espn_id INTEGER PRIMARY KEY, arch INTEGER NOT NULL, "
        "ref_season INTEGER NOT NULL, ref_minutes REAL NOT NULL)"
    )
    clusters = []
    for a in range(k):
        members = sorted((p for p, l in zip(profs, labels) if l == a),
            key=lambda p: -p["minutes"])
        named = [{"espn_id": p["espn_id"],
            "name": player_name(con, p["espn_id"]),
            "minutes": round(p["minutes"])} for p in members]
        centroid = {f: round(float(c), 2)
            for f, c in zip(FEATURES, centers[a])}
        clusters.append({"arch": a, "size": len(members),
            "avg_min_per_g": round(
                sum(p["minutes"] for p in members)
                / max(sum(p["games"] for p in members), 1), 1),
            "centroid": centroid, "members": named})
    for p, l in zip(profs, labels):
        con.execute(
            "INSERT INTO player_archetypes(espn_id, arch, ref_season, "
            "ref_minutes) VALUES(?,?,?,?) ON CONFLICT(espn_id) DO UPDATE "
            "SET arch=excluded.arch, ref_season=excluded.ref_season, "
            "ref_minutes=excluded.ref_minutes",
            (p["espn_id"], int(l), REF_SEASON, p["minutes"]),
        )
    con.commit()
    meta = {"features": FEATURES, "centers": centers.tolist(), "k": k,
        "n_players": len(profs), "method": method}
    if scan:
        meta["km"], meta["gmm"] = scan
    else:
        old = {}
        if Path("data/archetypes_2025.json").exists():
            try:
                old = json.loads(
                    Path("data/archetypes_2025.json").read_text())
            except (ValueError, OSError):
                old = {}
        if "km" in old:  # manual re-cluster keeps last scan valid
            meta["km"], meta["gmm"] = old["km"], old["gmm"]
    Path("data/archetypes_2025.json").write_text(json.dumps(meta, indent=1))
    return {"k": k, "n_players": len(profs), "method": method,
        "clusters": clusters}


def assign(con, profs, Xz, k):
    scaler = StandardScaler().fit(
        np.array([p["features"] for p in profs]))
    km = KMeans(n_clusters=k, n_init=25, random_state=RANDOM_STATE).fit(Xz)
    centers = scaler.inverse_transform(km.cluster_centers_)
    return persist(con, profs, km.labels_, centers, k, "kmeans")


PHRASE = {
    "pts36": "scoring volume",
    "reb36": "rebounding",
    "ast36": "passing",
    "usg": "usage",
    "ts": "efficiency",
    "ftr": "free-throw drawing",
    "ast_tov": "ball security",
    "pm36": "on-court impact",
    "drive_share": "driving",
    "cs_share": "spot-up shooting",
    "pull_share": "pull-up shooting",
    "pass36": "pass volume",
    "rimd36": "rim contests",
}
SAME = {"ast36": "passing", "pass36": "passing"}


def _role_name(z):
    """Plain-English role from centroid z-scores vs league average.

    Order matters: big-man identity is decided by usage and passing
    before rim-contest volume, otherwise hub bigs (Jokic, Sabonis)
    get labelled rim protectors just for standing near the rim.
    """
    g = lambda f: z.get(f, 0.0)
    if g("reb36") > 0.7:
        if g("ast36") > 0.7 or g("usg") > 0.8:
            return "hub big"
        if g("cs_share") > 0.5 and g("rimd36") < 0.5:
            return "stretch big"
        if g("rimd36") > 0.7 and g("usg") < 0.0:
            return "rim-protecting big"
        return "interior big"
    if g("usg") > 0.9 and g("pull_share") > 0.3:
        return "primary scorer"
    if g("usg") > 0.5 and g("ast36") > 0.5:
        return "scoring playmaker"
    if g("ast36") > 0.7:
        return "pass-first guard"
    if g("drive_share") > 0.7:
        return "slasher"
    if g("cs_share") > 0.6 and g("pull_share") < 0.0:
        return "3-and-D wing"
    if g("cs_share") > 0.5:
        return "catch-and-shoot wing"
    if g("pm36") > 0.5:
        return "glue wing"
    if g("ts") < -0.4 and g("usg") < 0.2 and g("ast36") < 0.2:
        return "defensive wing"
    return "rotation player"


def describe_clusters(con, profs, centers, labels, k):
    """Plain-English label + top traits per cluster, vs league average."""
    X = np.array([p["features"] for p in profs], dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    out = []
    for a in range(k):
        idx = [i for i, l in enumerate(labels) if l == a]
        z = dict(zip(FEATURES, (np.array(centers[a], dtype=float) - mu) / sd))
        seen, ranked = set(), []
        for f, v in sorted(z.items(), key=lambda fv: -abs(fv[1])):
            if f not in PHRASE or abs(v) < 0.6:
                continue
            key = SAME.get(f, PHRASE[f])
            if key in seen:
                continue
            seen.add(key)
            ranked.append((f, v))
            if len(ranked) == 3:
                break
        desc = "; ".join(("high " if v > 0 else "low ") + PHRASE[f]
                         for f, v in ranked) or "league-average profile"
        members = sorted((profs[i] for i in idx), key=lambda p: -p["minutes"])
        out.append({"arch": a, "size": len(idx), "name": _role_name(z),
                    "desc": desc,
                    "trait": ranked[0][0] if ranked else None,
                    "top": [player_name(con, p["espn_id"])
                            for p in members[:6]]})
    counts = {}
    for c in out:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    for c in out:
        tr = c.pop("trait", None)
        if counts[c["name"]] > 1 and tr in PHRASE:
            c["name"] = c["name"] + " (" + PHRASE[tr] + ")"
    return out

def preview(profs, Xz, k):
    gm = GaussianMixture(n_components=k, covariance_type="diag",
                         n_init=5, reg_covar=1e-4,
                         random_state=RANDOM_STATE).fit(Xz)
    scaler = StandardScaler().fit(
        np.array([p["features"] for p in profs]))
    return scaler.inverse_transform(gm.means_), gm.predict(Xz)


def apply_k(con, profs, Xz, k):
    centers, labels = preview(profs, Xz, k)
    return persist(con, profs, labels, centers, k, "gmm-manual")


def optimize(con, profs, Xz):
    """Auto k: GMM BIC minimum; assign via the same GMM's labels.

    BIC is the criterion because it only rewards structure that pays
    its parameter cost - silhouette would drag k back down to ~4. The
    GMM fit is deterministic (fixed seed, n_init=5), so re-runs give
    the same partition, unlike k-means past k=7 (ARI ~0.5).
    """
    km_rows, gmm_rows = scan(Xz)
    if min(gmm_rows, key=lambda r: r["bic"])["k"] == max(K_RANGE):
        km2, gmm2 = scan(Xz, range(max(K_RANGE) + 1, max(K_RANGE) + 5))
        km_rows += km2
        gmm_rows += gmm2
    best = min(gmm_rows, key=lambda r: r["bic"])
    k = best["k"]
    gm = GaussianMixture(n_components=k, covariance_type="diag",
        n_init=5, reg_covar=1e-4,
        random_state=RANDOM_STATE).fit(Xz)
    scaler = StandardScaler().fit(
        np.array([p["features"] for p in profs]))
    centers = scaler.inverse_transform(gm.means_)
    labels = gm.predict(Xz)
    return persist(con, profs, labels, centers, k, "gmm-bic",
        scan=(km_rows, gmm_rows))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    con = store.connect()
    profs = load_profiles(con)
    if not profs:
        raise SystemExit("no qualified players - run pt_pull.py first")
    Xz = scale_profiles(profs)
    if mode == "scan":
        km_rows, gmm_rows = scan(Xz)
        print(f"{'k':>3} {'sil':>7} {'inertia':>9} {'ARI':>6}"
            f" | {'BIC':>9} {'AIC':>9}")
        for a, b in zip(km_rows, gmm_rows):
            print(f"{a['k']:3d} {a['silhouette']:7.3f} {a['inertia']:9.1f} "
                f"{a['ari']:6.3f} | {b['bic']:9.1f} {b['aic']:9.1f}")
        best = min(gmm_rows, key=lambda r: r["bic"])
        print(f"\nk-means best silhouette: "
            f"k={max(km_rows, key=lambda r: r['silhouette'])['k']}")
        print(f"GMM BIC minimum: k={best['k']} (BIC {best['bic']})")
        print("next: archetypes.py assign <k>")
    elif mode == "optimize":
        res = optimize(con, profs, Xz)
        for c in res["clusters"]:
            print(f"\narchetype {c['arch']} - {c['size']} players, "
                f"~{c['avg_min_per_g']} min/g avg")
            print("  top members: " + "; ".join(
                m["name"] for m in c["members"][:8]))
        print(f"\nauto k={res['k']} ({res['method']}) -> "
            f"{res['n_players']} assignments persisted")
    elif mode == "assign":
        k = int(sys.argv[2]) if len(sys.argv) > 2 else 8
        res = assign(con, profs, Xz, k)
        for c in res["clusters"]:
            print(f"\narchetype {c['arch']} - {c['size']} players, "
                f"~{c['avg_min_per_g']} min/g avg")
            print("  centroid: " + ", ".join(
                f"{f}={v}" for f, v in c["centroid"].items()))
            print("  top members: " + "; ".join(
                m["name"] for m in c["members"][:8]))
        print(f"\nwrote {res['n_players']} assignments "
            f"(k={k}) -> player_archetypes + data/archetypes_2025.json")
    else:
        raise SystemExit(f"unknown mode {mode!r} (scan|assign)")


if __name__ == "__main__":
    main()
