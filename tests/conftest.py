"""A small store in a temporary data folder, so tests never touch data/ (or the network)."""
import pytest
from fastapi.testclient import TestClient

from app import store
from app.archetypes import service as archetypes
from app.config import settings
from app.main import app


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    archetypes._cache.clear()
    con = store.connect()
    store.upsert_player(con, 1, "Luka Dončić", "DAL", "G")
    store.upsert_player(con, 2, "Nikola Jokić", "DEN", "C")
    store.upsert_team(con, 10, "DAL", "Dallas")
    store.upsert_team(con, 20, "DEN", "Denver")
    for n in range(3):
        for pid, team, opp, pts in ((1, 10, "DEN", 30 + n), (2, 20, "DAL", 25)):
            store.upsert_game(con, {"espn_id": pid, "game_id": 100 + n, "source": "official",
                                    "season": 2026, "game_date": f"2025-11-0{n + 1}",
                                    "home_away": "H", "opponent": opp, "minutes": 36.0,
                                    "fgm": 10, "fga": 20, "fg3m": 3, "fg3a": 8, "ftm": 5,
                                    "fta": 6, "reb": 9, "oreb": 1, "ast": 8, "stl": 1,
                                    "blk": 1, "tov": 3, "pts": pts})
            store.upsert_team_game(con, {"team_id": team, "game_id": 100 + n, "season": 2026,
                                         "game_date": f"2025-11-0{n + 1}", "pts": 110,
                                         "fga": 88, "fta": 22, "oreb": 10, "reb": 44, "tov": 13})
    con.execute("CREATE TABLE player_archetypes (espn_id INTEGER PRIMARY KEY, "
                "arch INTEGER NOT NULL, ref_season INTEGER NOT NULL, ref_minutes REAL NOT NULL)")
    con.execute("INSERT INTO player_archetypes VALUES (2, 3, 2025, 2000)")
    con.commit()
    return con


@pytest.fixture
def client(con):
    return TestClient(app, raise_server_exceptions=False)
