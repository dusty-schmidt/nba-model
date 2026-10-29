# nba-model

NBA player stat projections for props betting and fantasy

## What it does

Pulls NBA box scores and tracking stats (ESPN, and NBA.com through a headless browser) into a SQLite store, groups players into archetypes, and projects each player's next game with over/under probabilities. A mobile dashboard shows players, leaders, archetypes and projections. For the owner's props bets and fantasy picks.

The research behind every modelling choice, and the ideas rejected on evidence (do not rebuild
them), is in `research/README.md`. Read it before changing the model.

## Run it

- **The live dashboard is always on** at http://mothership:8100 (`~/lab/services/nba-model`): within
  5 minutes of a push to `main`, it runs the new commit if its tests pass, and keeps the last good
  one if they don't. So a push is a release; work in progress stays on a branch or unpushed.
- A dev copy while you work: `uv run uvicorn app.main:app --reload --port 8101`, or
  `docker compose up --build` (also :8101). The dashboard at `/`, API docs at `/docs`, health at
  `/health`.
- Settings come from environment variables (`app/config.py`); `.env.example` lists them. Copy it
  to `.env` (never committed) to change them.
- `./test` runs the tests (`uv run pytest`).

## The data

The data is never in git: `nba.db` (the SQLite store: players, game logs, teams, advanced,
tracking, archetypes), `archetypes_2025.json` (the chosen partition), `raw/` (the pull cache, so
an interrupted pull resumes) and `chrome-profile/` (the browser profile that passes Akamai).

There is one copy, the live service's: `~/lab/services/nba-model/data` on mothership, backed up
hourly with the rest of `/home/ds`. The desk clone's `.env` sets `DATA_DIR` to it, so pulls and
research read and write the same store the dashboard shows. That means an archetype re-fit from
a dev server changes the live dashboard too. The live service caches its fitted models in memory,
so after a pull run `docker restart nba-model-live` to show the new data. Elsewhere (GOB's copy, a
cloud session) `data/` starts empty.

- **ESPN** is the backbone (fast public JSON): `uv run python -m app.ingest.pull_player "<name or
  espn id>" [season...]`.
- **NBA.com** blocks plain requests (Akamai), so these drive a real Chromium page:
  `uv run --group ingest python -m app.ingest.<nba_official|advanced_pull|pt_pull|backfill_names>`.
  Only some Chromium builds pass; `CHROME_PATH` names one (see `app/ingest/nba_official.py`).
- **Archetypes** are refitted with `uv run python -m app.archetypes.service scan|optimize|assign [k]`
  (or from the dashboard's Archetypes tab).

Seasons are named by their ending year: 2026 is 2025-26.

## Layout

Code is grouped by feature: everything about one thing lives in one folder under `app/`, so adding
or removing a feature touches one place.

- `app/main.py`: the FastAPI app, `/health`, the dashboard page, and each feature's router. Every
  error answers as `{"error": "..."}`, which the dashboard shows.
- `app/config.py`: settings from environment variables.
- `app/store.py`: the SQLite store (schema and idempotent upserts), shared by every feature.
- `app/players/`: players, their cards and the league leaders (`/api/state`, `/api/players`,
  `/api/player/<id>`, `/api/leaders`).
- `app/projections/`: next-game projections and over/under probabilities (`/api/prob/<id>`).
- `app/archetypes/`: player roles by clustering (`/api/archetypes/...`); `service.py` is the model
  and its CLI.
- `app/ingest/`: the data pulls (ESPN, NBA.com); run by hand, not by the service.
- `app/static/index.html`: the mobile dashboard, one file, no build step.
- `research/`: the walk-forward experiments behind the model, each runnable
  (`uv run python -m research.<name>`); `research/README.md` has their results.
- `tests/`: pytest against a small temporary store, no network.
- `Dockerfile`, `compose.yaml`: how it runs in the lab.

Each feature folder has `router.py` (HTTP routes, thin) and `service.py` (the logic), plus
`schemas.py` where a route takes a body. A new feature copies one and adds its router in `main.py`.
