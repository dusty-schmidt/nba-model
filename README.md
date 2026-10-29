# NBA projection model — data layer

Set up 2026-10-28. Purpose: player stat projections for props betting and fantasy.

## Data source decision

stats.nba.com and cdn.nba.com are **Akamai-blocked from this container's IP** (connection hangs
past 60s / bare 403 — verified twice with full browser headers). ESPN's public API answers in
~0.2s with full JSON, so it is the data backbone. `nba_api` remains installed in /opt/venv if
egress ever changes.

## Files

- `espn_client.py` — ESPN client: `search_player()` (v2 search, numeric id from uid `~a:NNN`)
  and `get_gamelog(id, season)`. Season = ending year: 2026 -> 2025-26, 2027 -> 2026-27.
- `store.py` — SQLite at `data/nba.db`. Tables: `players`, `game_logs` (PK espn_id+game_id,
  idempotent upserts). Stat columns: minutes, fgm/fga, fg3m/fg3a, ftm/fta, reb, ast, blk, stl,
  tov, pf, pts + game context (date, home/away, opponent, result, scores).
- `pull_player.py` — CLI: `python pull_player.py "<name or id>" [season...]` (default 2026 2027).
  Parses ESPN `names`->`stats` positional arrays; splits composite `a-b` fields; MM:SS minutes.
- `advanced_pull.py` — official Advanced (USG/TS/ratings, 569+582 rows) + Defend Overall
  snapshots via bridge. `player_advanced` + `player_defend` tables.
- `pt_pull.py` — the tracking-measure family via `leaguedashptstats?PtMeasureType=...`
  (Drives, Defense, Passing, CatchShoot, PullUpShot, Rebounding, SpeedDistance,
  Efficiency). `player_tracking` table, 8 measures x 2 seasons merged per player-season.
- `nba_official.py` — the official pull via the headless Chrome bridge (real page context; a
  plain requests call is Akamai-blocked). Modes for players-bulk, teams-bulk, advanced, tracking.
- `backfill_names.py` — re-pulls player names with proper diacritics through the bridge.
- `baseline.py` — the walk-forward yardstick: eight dumb methods, roll20/dec20 on 254 games.
- `opptest.py` — the decisive opponent test. Rejected opponent and archetype adjustments.
- `probs.py` — first calibration pass: four variance models scored on Brier/logloss.
- `means.py` — does a learned mean beat roll20? Monthly walk-forward, out-of-sample only.
- `projections.py` — the combined engine; this is where the shipping strategy was chosen.
- `live_probs.py` — live projection + over/under probabilities for a player's next game.
- `validate.py` — ESPN vs official cross-check (68 shared games, 0 mismatches).
- `dashboard.py` + `static/index.html` — the mobile UI and its JSON API.

Deleted 2026-10-28: six `probe_*.py` one-off endpoint probes (their findings live in the
Advanced stats layer section below) and `opponent_model.py` (superseded by `opptest.py`, which
is the version that produced the rejection evidence).

## Verified 2026-10-28 (updated)

- End-to-end pull: Doncic (espn 3945274), 65 rows 2025-26 + 4 rows 2026-27, 0% nulls on all key
  columns, season avg 33.0 ppg plausible.
- Gamelog `season` param returns full prior season history.
- **Official NBA.com bridge works** (`nba_official.py`): framework chromium (Chrome 149) +
  persistent profile (data/chrome-profile) passes Akamai; python requests and the downloaded
  headless_shell do NOT. Doncic official id 1629029, source-verified via commonallplayers with
  NFD diacritic normalization. Jokic (203999, 138 rows) also in store from first bridge run.
- **Cross-source validation PASSED** (`validate.py 3945274 1629029`): 68 shared games, 0
  mismatches on pts/minutes/reb/ast/shooting splits. ESPN dates are UTC tipoffs — converted to
  America/New_York before joining (official stores local game date).
- NBA.com season leaders corroborate: Doncic 36.0 ppg, 144 total (28+23+52+41), 52-high — all
  matching the store.

## Baseline model (2026-10-28)

- `baseline.py`: walk-forward harness, 8 dumb methods, zero leakage (each game predicted from
 strictly earlier games only). Official rows only.
- Pooled MAE, 254 games (Doncic + Jokic): pts 7.30 (dec20), reb 2.94 (roll20), ast 2.86 (naive),
 stl 1.11, blk 0.68, tov 1.63 (decay20), fg3m 1.35 (dec20), minutes 3.74.
- Read: 20-game windows beat 5-game windows on every stat (short-term form = noise for stars);
 exponential decay ~= roll20; minutes x per-36 decomposition does NOT beat plain rolling for
 these two iron men. Method spread is within noise — yardstick is ~7.3 pts MAE.
- Caveat: n=2 superstars with stable minutes/roles. Says nothing about role players.
- Any fancier model (opponent/teammate/rest adjustments) must beat these numbers or it's noise.

## Known gaps / next steps

- One ESPN-only game s2026 (2026-02-15; likely Play-In classification — official pull was Regular
  Season only). Check before modeling.
- **DNP games invisible in both sources**: rest/injury detection needs a team schedule/roster
  source before modeling back-to-backs.
- Official rows lack home/away scores (null); ESPN rows carry them.
- Store: 497 players with logs, 45,241 player-games, 5,046 team-games, 3 seasons.
- Defend dashboard category param is dead in this API generation (DEF_CATEGORY ignored, both
  encodings) — only Overall snapshots stored; rim defense comes from tracking Defense measure.
- Tracking/advanced rows are per-game season averages, not per-possession; fine for archetypes,
  keep in mind for projection features.

## Advanced stats layer (2026-10-28, v4)

Probing found the current API generation: `leaguedashptstats?PtMeasureType=<measure>` is the
live path (the old guessed endpoints — leaguedashptdrives, synergyplaytype — are dead).
8 measures work league-wide: Drives, Defense (DEF_RIM_FGA/FG_PCT), Passing, CatchShoot,
PullUpShot, Rebounding, SpeedDistance, Efficiency (paint/post/elbow touch pts).
`leaguedashplayerstats?MeasureType=Advanced` with the full param set returns official
USG/TS/OFF-DEF-NET ratings league-wide.

Archetypes v4 = 25 features: v3's 19 (per-36 production + mechanism ratios + official usg/ts/efg)
plus 6 tracking mechanics: drives36, drive_share, cs_share, pull_share, pass36, rimd36.

Ten-role model assigned (k=10, GMM BIC pick; k-means seed-stable only to k=7):
0 movement shooters · 1 stretch bigs · 2 hub stars · 3 two-way wings · 4 volume shooting wings ·
5 interior bigs · 6 pass-first connectors · 7 slasher playmakers · 8 scoring guards · 9 physical
paint bigs. Ten-role split is basketball-coherent: stretch vs interior bigs, connectors vs
slashers, specialist vs volume shooters all separated.

Verified: 16 tracking fetches (8 measures × 2 seasons) stored 569+582 player-seasons;
leader checks pass smell test (SGA/Trae/Cade drive volume; Mobley/Sarr/Wemby rim defense;
Edwards/Tatum/Harden pull-up 3s; Jokic/Zubac/Sabonis paint points). Dashboard serves k=10
(state, clusters, scatter all agree).

## Cluster-count accuracy (auto-k)

The dashboard's cluster-count chart plots two quality curves per k: k-means
silhouette (solid) and GMM BIC inverted (dashed, fully derivable at
/api/archetypes/quality) — automatically choose k, showing the drop-off, no manual k picking.
Auto pick = lowest GMM BIC, interior at k=10 for the 2025-26 reference season.

`POST /api/archetypes/optimize` runs the full scan and assigns via GMM labels
(~1.6s). `archetypes.py optimize` does the same from the CLI. Manual k remains
as an override under the dashboard's "manual override" foldout.

## Cluster names, preview and apply

Every cluster carries a plain-English name (`hub big`, `3-and-D wing`,
`rim-protecting big`, `primary scorer (usage)`) generated from centroid
z-scores vs league average. Rules in `_role_name` are ordered: big-man
identity is decided by usage/passing *before* rim-contest volume, so hub
bigs (Jokic, Sabonis, Adebayo) are not mislabelled rim protectors. Duplicate
names at the same k get a distinguishing trait suffix.

- `GET /api/archetypes/preview?k=N` — fits a GMM at any k 2-14, writes nothing
- `POST /api/archetypes/apply {k}` — persists that partition (method gmm-manual)

UI: the Archetypes tab has a k stepper that live-previews any cluster count
with names and top members, then applies it. Manual k-means stays under
"manual override".

## Dashboard (2026-10-28)

Flask JSON API + single-file mobile UI (no CDNs, no build step).

- Run: `/opt/venv/bin/python dashboard.py` (binds 127.0.0.1:8099)
- Phone/tailnet: https://gob-main.tail621cf5.ts.net (tailscale serve proxy, tailnet-only)
- Tabs: Players (league leaders + search + per-player cards with per-36, form sparkline, roll20 projection),
  Archetypes (cluster cards, PCA player map, interactive k-scan + re-cluster),
  Project (roll20 projections), Model (data coverage, baseline yardstick, roadmap)
- Endpoints: /api/state, /api/players?q=, /api/player/<id>, /api/leaders?stat=&season=,
  /api/archetypes, /api/archetypes/scatter, POST /api/archetypes/scan, POST /api/archetypes/assign {k}
- k-scan recheck (341 qualified players): only k=4 (sil .228, ARI .968) and k=5 (sil .220, ARI .975) are
  seed-stable; k>=7 seed-unstable. k=5 assigned: 3-and-D wings, scoring guards, hub bigs, rim protectors, defensive wings.

## Probability layer (validated 2026-10-28)

`live_probs.py` + `/api/prob/<pid>` + the Project tab's over/under card.

Strategy is the walk-forward winner from `projections.py`:

| layer | choice | evidence |
|---|---|---|
| mean, pts/reb/ast | HistGradientBoosting on prior-game features | 1.0% MAE gain over roll20 |
| mean, fg3m | plain 20-game rolling mean | booster LOST here (+0.3%): sparse discrete stat, fits shot noise |
| spread | player's own residual sd, shrunk to archetype (K=25) | pooled Brier 0.1711 vs 0.1773 naive = 3.5% better calibration |
| tails, fg3m | empirical archetype residual distribution | best in row: Brier 0.1638 |

Rejected on evidence, do not rebuild:

- **Opponent and opponent x archetype adjustments.** Walk-forward over 38,527
  player-games: roll20 4.928 MAE, +opponent 4.922, +archetype 4.924. The
  archetype layer loses to the plain one; all opponent adjustment together is
  worth ~0.1%. Split-half reliability of archetype-specific defensive ratings
  is +0.04..+0.39 against +0.52 for overall team defense — mostly noise.
- **Quality tiers via efficiency.** Archetype explains 27% of TS% variance but
  only 8.6% of impact variance; corr(TS%, impact) = 0.27. Rim protectors lead
  the league in TS (65.0%) at ~zero impact. Efficiency is role-conditional.

RotoGrinders ships eight hand-defined archetypes and states they replace
Defense vs Position. Their page carries no validation numbers. Same premise,
no evidence — ours is testing it and the additive form does not work.
