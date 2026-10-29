"""SQLite layer for the NBA model. Idempotent upserts, one db file."""
import sqlite3

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    espn_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT,
    position TEXT
);
CREATE TABLE IF NOT EXISTS game_logs (
    espn_id INTEGER NOT NULL REFERENCES players(espn_id),
    game_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'espn',
    season INTEGER NOT NULL,
    season_type TEXT,
    game_date TEXT,
    home_away TEXT,
    opponent TEXT,
    result TEXT,
    home_score INTEGER, away_score INTEGER,
    minutes REAL, fgm INTEGER, fga INTEGER, fg_pct REAL,
    fg3m INTEGER, fg3a INTEGER, fg3_pct REAL,
    ftm INTEGER, fta INTEGER, ft_pct REAL,
    reb REAL, oreb REAL, dreb REAL, ast REAL, blk REAL, stl REAL,
    tov REAL, pf REAL, pts REAL, plus_minus REAL,
    PRIMARY KEY (espn_id, game_id, source)
);
CREATE INDEX IF NOT EXISTS idx_gamelog_date ON game_logs(game_date);
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    abbrev TEXT,
    name TEXT
);
CREATE TABLE IF NOT EXISTS team_games (
    team_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    game_date TEXT,
    matchup TEXT,
    home_away TEXT,
    wl TEXT,
    pts REAL, fga REAL, fta REAL, oreb REAL, reb REAL, tov REAL,
    PRIMARY KEY (team_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_team_games_date ON team_games(game_date);
"""


def db_path():
    """The database file, read from settings each time so tests can point it elsewhere."""
    return settings.data_dir / "nba.db"


def connect():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path())
    cols = [r[1] for r in con.execute("PRAGMA table_info(game_logs)").fetchall()]
    if cols and "source" not in cols:
        # migrate pre-source table: rebuild with new schema, keep espn rows
        con.executescript(
            "ALTER TABLE game_logs RENAME TO game_logs_old;"
            "DROP INDEX IF EXISTS idx_gamelog_date;"
        )
        con.executescript(SCHEMA)
        old_cols = [r[1] for r in con.execute("PRAGMA table_info(game_logs_old)").fetchall()]
        col_list = ",".join(old_cols)  # every old column exists in the new schema
        con.execute(
            f"INSERT OR IGNORE INTO game_logs({col_list},source) "
            f"SELECT {col_list},'espn' FROM game_logs_old"
        )
        con.execute("DROP TABLE game_logs_old")
        con.commit()
    else:
        con.executescript(SCHEMA)
    return con


def upsert_player(con, espn_id, name, team=None, position=None):
    con.execute(
        "INSERT INTO players(espn_id,name,team,position) VALUES(?,?,?,?) "
        "ON CONFLICT(espn_id) DO UPDATE SET name=excluded.name, "
        "team=COALESCE(excluded.team,team), "
        "position=COALESCE(excluded.position,position)",
        (espn_id, name, team, position),
    )


def upsert_game(con, row):
    cols = list(row)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("espn_id", "game_id", "source"))
    con.execute(
        f"INSERT INTO game_logs({','.join(cols)}) "
        f"VALUES({','.join('?' * len(cols))}) "
        f"ON CONFLICT(espn_id,game_id,source) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )


def upsert_team(con, team_id, abbrev, name):
    con.execute(
        "INSERT INTO teams(team_id,abbrev,name) VALUES(?,?,?) "
        "ON CONFLICT(team_id) DO UPDATE SET abbrev=excluded.abbrev, "
        "name=excluded.name",
        (team_id, abbrev, name),
    )


def upsert_team_game(con, row):
    cols = list(row)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("team_id", "game_id"))
    con.execute(
        f"INSERT INTO team_games({','.join(cols)}) "
        f"VALUES({','.join('?' * len(cols))}) "
        f"ON CONFLICT(team_id,game_id) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
