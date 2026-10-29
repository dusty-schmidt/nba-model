"""players: search, a player's card, the leaders, and the store summary."""
from app.players.service import fold


def test_fold_drops_accents_and_case():
    assert fold("Dončić") == "doncic"


def test_search_matches_without_accents(client):
    rows = client.get("/api/players?q=doncic").json()
    assert [r["name"] for r in rows] == ["Luka Dončić"]
    assert rows[0]["g"] == 3 and rows[0]["ppg"] == 31.0


def test_state_counts_official_rows(client):
    s = client.get("/api/state").json()
    assert (s["players"], s["games"], s["seasons"], s["k"]) == (2, 6, [2026], None)


def test_player_card(client):
    d = client.get("/api/player/2").json()
    assert d["name"] == "Nikola Jokić" and d["arch"] == 3
    assert d["projection"]["pts"] == {"mean": 25.0, "std": 0.0, "n": 3}
    assert d["per36"]["pts36"] == 25.0


def test_unknown_player_answers_in_the_dashboards_error_shape(client):
    r = client.get("/api/player/999")
    assert (r.status_code, r.json()) == (404, {"error": "unknown player"})


def test_leaders_only_sort_by_known_stats(client):
    d = client.get("/api/leaders?stat=name;drop table players&season=2026").json()
    assert d["stat"] == "pts" and d["rows"][0]["name"] == "Luka Dončić"
