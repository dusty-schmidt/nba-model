"""ESPN public API client for NBA data. Endpoints verified live 2026-10-28.
stats.nba.com is Akamai-blocked from this IP; ESPN is the data backbone."""
import re, time
import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/json"})

def _get(url, **params):
    for attempt in range(4):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after retries: {url}")

def search_player(query, limit=5):
    """Resolve a player name to espn id. Numeric id lives in uid as ~a:NNN."""
    d = _get("https://site.web.api.espn.com/apis/search/v2",
             query=query, limit=limit, sport="basketball", league="nba")
    out = []
    for block in d.get("results", []):
        if block.get("type") != "player":
            continue
        for c in block.get("contents", []):
            m = re.search(r"~a:(\d+)", c.get("uid", ""))
            if m:
                out.append({"id": m.group(1), "name": c["displayName"],
                            "team": c.get("subtitle", "")})
    return out

def get_gamelog(athlete_id, season):
    """season = ending year: 2026 -> 2025-26, 2027 -> 2026-27."""
    return _get(f"https://site.api.espn.com/apis/common/v3/sports/basketball/nba/"
                f"athletes/{athlete_id}/gamelog", season=season)
