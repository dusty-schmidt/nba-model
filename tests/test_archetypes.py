"""archetypes: the routes' input checks and errors (fitting itself needs the real store)."""
from app.archetypes.service import _role_name


def test_hub_bigs_are_not_called_rim_protectors():
    assert _role_name({"reb36": 1.5, "ast36": 1.2, "rimd36": 1.5}) == "hub big"


def test_k_out_of_range_is_400(client):
    assert client.get("/api/archetypes/preview?k=40").json() == {"error": "k out of range"}
    r = client.post("/api/archetypes/assign", json={"k": 30})
    assert (r.status_code, r.json()) == (400, {"error": "k out of range"})


def test_apply_body_is_optional_and_checked(client):
    r = client.post("/api/archetypes/apply", json={"k": 1})
    assert r.status_code == 400


def test_quality_without_a_scan_is_404(client):
    assert client.get("/api/archetypes/quality").status_code == 404


def test_a_crash_answers_with_its_message(client):
    r = client.get("/api/archetypes")  # no archetype meta in the data folder yet
    assert r.status_code == 500 and "archetypes_2025.json" in r.json()["error"]
