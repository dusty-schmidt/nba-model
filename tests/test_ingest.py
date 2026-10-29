"""ingest: parsing ESPN's game log rows (the pulls themselves need the network)."""
from app.ingest.pull_player import _num, _split, parse_event
from app.projections.service import p_over


def test_minutes_and_composite_fields():
    assert _num("34:30") == 34.5 and _num("-") is None
    assert _split("9-17") == (9, 17) and _split("x") == (None, None)


def test_parse_event_reads_home_and_opponent():
    row = parse_event(1, 2026, "regular", ["minutes", "points"],
                      {"eventId": "7", "stats": ["30:00", "21"]},
                      {"7": {"atVs": "vs", "opponent": {"abbreviation": "DEN"}}})
    assert (row["game_id"], row["home_away"], row["opponent"], row["minutes"], row["pts"]) == \
        (7, "H", "DEN", 30.0, 21.0)


def test_p_over_is_a_half_at_the_mean():
    assert p_over(20.0, 5.0, 20.0) == 0.5 and p_over(20.0, 0, 25.0) == 0.5
