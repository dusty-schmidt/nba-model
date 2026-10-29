"""Official stats.nba.com via headless Chromium bridge.

stats.nba.com Akamai-blocks python TLS fingerprints but accepts fetches made
from inside a real nba.com page context (verified 2026-10-28). Uses the
framework chromium build + persistent profile (data/chrome-profile), which
pass Akamai; the downloaded headless_shell does not. CHROME_PATH (app/config.py)
names the build.

Modes:
  player <official_id>...   pull specific players, 3 seasons each
  players-bulk              pull ALL current-roster players (commonallplayers)
  teams                     pull all 30 teams' game logs (teamgamelog)
  all                       players-bulk then teams

Cache: data/raw/gamelog_{pid}_{season}.json, team_{tid}_{season}.json — reruns
skip cached files, so interrupted bulk runs resume for free.

Usage: uv run --group ingest python -m app.ingest.nba_official <mode> [args]
"""
import json
import sys
import time
from datetime import datetime
import random

from playwright.sync_api import sync_playwright

from app import store
from app.config import settings

RAW = settings.data_dir / "raw"
SITE = "https://www.nba.com/stats/players"
BASE = "https://stats.nba.com"
HDRS = {"Accept": "application/json, text/plain, */*"}
SEASONS = [2025, 2026, 2027]  # ending years: 2026 -> 2025-26


def fetch_json(page, path):
    js = (
        "(async () => {"
        "const ctl = new AbortController();"
        "const t0 = setTimeout(() => ctl.abort(), 30000);"
        "try {"
        f"const r = await fetch('{BASE}{path}', {{headers: {json.dumps(HDRS)}, signal: ctl.signal}});"
        "const t = await r.text();"
        "return JSON.stringify({status: r.status, body: t.substring(0, 5000000)});"
        "} finally { clearTimeout(t0); }"
        "})()"
    )
    out = None
    for attempt in range(3):
        try:
            out = json.loads(page.evaluate(js))
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2.0 * (attempt + 1))
    if out["status"] != 200:
        raise RuntimeError(f"{path} -> HTTP {out['status']}")
    return json.loads(out["body"])


def gamelog_path(player_id, season):
    return (f"/stats/playergamelog?PlayerID={player_id}&LeagueID=00"
            f"&Season={_season_str(season)}&SeasonType=Regular+Season")


def fetch_many(page, paths):
    """Fetch several API paths concurrently inside one page.evaluate.
    Returns list of {status, body} aligned with paths; status 0 on error."""
    js = (
        "(async () => {"
        "const paths = " + json.dumps(paths) + ";"
        "const hdrs = " + json.dumps(HDRS) + ";"
        "const out = await Promise.all(paths.map(async (p) => {"
        "const ctl = new AbortController();"
        "const t0 = setTimeout(() => ctl.abort(), 30000);"
        "try {"
        "const r = await fetch('" + BASE + "' + p, {headers: hdrs, signal: ctl.signal});"
        "const t = await r.text();"
        "return {status: r.status, body: t.substring(0, 5000000)};"
        "} catch (e) { return {status: 0, body: String(e)}; }"
        "finally { clearTimeout(t0); }"
        "}));"
        "return JSON.stringify(out);"
        "})()"
    )
    return json.loads(page.evaluate(js))


def get_browser_page():
    pw = sync_playwright().start()
    profile = settings.data_dir / "chrome-profile"
    browser = pw.chromium.launch_persistent_context(
        str(profile),
        executable_path=settings.chrome_path or None,
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-US",
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto(SITE, wait_until="load", timeout=90000)
    page.wait_for_timeout(8000)
    names = [c["name"] for c in browser.cookies("https://www.nba.com")]
    if "ak_bmsc" not in names:
        page.wait_for_timeout(5000)
    names = [c["name"] for c in browser.cookies("https://www.nba.com")]
    print(
        "cookies:", [n for n in names if n.startswith(("ak_", "bm_"))] or "NONE",
        flush=True,
    )
    return pw, browser, page


def _num(s):
    if s in (None, "", "-"):
        return None
    s = str(s)
    if ":" in s:  # minutes in MM:SS
        m, sec = s.split(":")
        return round(int(m) + int(sec) / 60, 2)
    return float(s)


def _date(s):
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10] or None


def _season_str(season):
    return f"{season - 1}-{str(season)[2:]}"  # 2026 -> "2025-26"


def _first_resultset(d):
    rs = d["resultSets"][0]
    return rs["headers"], rs.get("rowSet", [])


def parse_gamelog_rows(player_id, season, headers, rows):
    headers = [h.lower() for h in headers]
    out = []
    for r in rows:
        m = dict(zip(headers, r))
        matchup = str(m.get("matchup", ""))
        home_away = opp = None
        if " vs. " in matchup:
            home_away, opp = "H", matchup.split(" vs. ")[1]
        elif " @ " in matchup:
            home_away, opp = "A", matchup.split(" @ ")[1]
        out.append(
            {
                "espn_id": int(player_id),
                "game_id": int(m["game_id"]),
                "source": "official",
                "season": season,
                "season_type": "Regular Season",
                "game_date": _date(m.get("game_date")),
                "home_away": home_away,
                "opponent": opp,
                "result": m.get("wl"),
                "home_score": None,
                "away_score": None,
                "minutes": _num(m.get("min")),
                "fgm": _num(m.get("fgm")),
                "fga": _num(m.get("fga")),
                "fg_pct": _num(m.get("fg_pct")),
                "fg3m": _num(m.get("fg3m")),
                "fg3a": _num(m.get("fg3a")),
                "fg3_pct": _num(m.get("fg3_pct")),
                "ftm": _num(m.get("ftm")),
                "fta": _num(m.get("fta")),
                "ft_pct": _num(m.get("ft_pct")),
                "reb": _num(m.get("reb")),
                "oreb": _num(m.get("oreb")),
                "dreb": _num(m.get("dreb")),
                "ast": _num(m.get("ast")),
                "blk": _num(m.get("blk")),
                "stl": _num(m.get("stl")),
                "tov": _num(m.get("tov")),
                "pf": _num(m.get("pf")),
                "pts": _num(m.get("pts")),
                "plus_minus": _num(m.get("plus_minus")),
            }
        )
    return out


def pull_gamelog(page, player_id, season):
    path = (
        f"/stats/playergamelog?PlayerID={player_id}&LeagueID=00"
        f"&Season={_season_str(season)}&SeasonType=Regular+Season"
    )
    headers, rows = _first_resultset(fetch_json(page, path))
    return parse_gamelog_rows(player_id, season, headers, rows)


def pull_all_player_ids(page):
    """Current-roster players via commonallplayers (521 ids verified earlier)."""
    path = (
        "/stats/commonallplayers?IsOnlyCurrentSeason=1&LeagueID=00"
        f"&Season={_season_str(2027)}"
    )
    headers, rows = _first_resultset(fetch_json(page, path))
    headers = [h.lower() for h in headers]
    players = []
    for r in rows:
        m = dict(zip(headers, r))
        pid = m.get("person_id")
        if pid:
            players.append((int(pid), str(m.get("display_first_last") or f"id {pid}")))
    return players


def pull_team_gamelog(page, team_id, season):
    path = (
        f"/stats/teamgamelog?TeamID={team_id}&LeagueID=00"
        f"&Season={_season_str(season)}&SeasonType=Regular+Season"
    )
    headers, rows = _first_resultset(fetch_json(page, path))
    headers = [h.lower() for h in headers]
    out = []
    for r in rows:
        m = dict(zip(headers, r))
        out.append(
            {
                "team_id": team_id,
                "game_id": int(m["game_id"]),
                "season": season,
                "game_date": _date(m.get("game_date")),
                "matchup": m.get("matchup"),
                "wl": m.get("wl"),
                "pts": _num(m.get("pts")),
                "fga": _num(m.get("fga")),
                "fta": _num(m.get("fta")),
                "oreb": _num(m.get("oreb")),
                "tov": _num(m.get("tov")),
            }
        )
    return out


def pull_all_teams(page):
    path = (f"/stats/leaguestandings?LeagueID=00&Season={_season_str(2027)}"
            "&SeasonType=Regular+Season")
    headers, rows = _first_resultset(fetch_json(page, path))
    headers = [h.lower() for h in headers]
    teams = []
    seen = set()
    for r in rows:
        m = dict(zip(headers, r))
        tid = m.get("teamid")
        if tid and int(tid) not in seen:
            seen.add(int(tid))
            teams.append(
                (int(tid), "",
                 str(m.get("teamcity") or "") + " " + str(m.get("teamname") or ""))
            )
    return teams


def cached_fetch(cache_name, fetch_fn):
    cache = RAW / cache_name
    if cache.exists():
        return "cache", json.loads(cache.read_text())
    rows = fetch_fn()
    cache.write_text(json.dumps(rows))
    time.sleep(1.0 + random.random() * 0.4)  # polite spacing with jitter
    return "fetch", rows


def run_players_bulk(page, con):
    players = pull_all_player_ids(page)
    print(f"bulk: {len(players)} current-roster players", flush=True)
    for pid, name in players:
        store.upsert_player(con, pid, name)
    con.commit()
    done = fail = 0
    # upsert everything already cached (idempotent; recovers mid-run kills)
    for pid, _ in players:
        for season in SEASONS:
            cache = RAW / f"gamelog_{pid}_{season}.json"
            if cache.exists():
                for row in json.loads(cache.read_text()):
                    store.upsert_game(con, row)
                    done += 1
    con.commit()
    missing = [(pid, season) for pid, _ in players for season in SEASONS
               if not (RAW / f"gamelog_{pid}_{season}.json").exists()]
    print(f"missing fetches: {len(missing)} (batches of 6)", flush=True)
    B = 6
    for i in range(0, len(missing), B):
        batch = missing[i:i + B]
        try:
            results = fetch_many(page, [gamelog_path(p, s) for p, s in batch])
        except Exception as e:
            fail += len(batch)
            print(f"  BATCH FAIL: {e}", flush=True)
            time.sleep(3)
            continue
        for (pid, season), out in zip(batch, results):
            try:
                if out["status"] != 200:
                    raise RuntimeError(f"s{season} -> HTTP {out['status']}")
                headers, rows = _first_resultset(json.loads(out["body"]))
                parsed = parse_gamelog_rows(pid, season, headers, rows)
                (RAW / f"gamelog_{pid}_{season}.json").write_text(json.dumps(parsed))
                for row in parsed:
                    store.upsert_game(con, row)
                done += len(parsed)
            except Exception as e:
                fail += 1
                print(f"  FAIL {pid} s{season}: {e}", flush=True)
        con.commit()
        if (i // B) % 10 == 0:
            print(f"  progress: {i + len(batch)}/{len(missing)} fetches, "
                  f"{done} rows, {fail} fails", flush=True)
        time.sleep(1.0 + random.random() * 0.4)
    con.commit()
    print(f"players-bulk done: {done} rows, {fail} failed fetches", flush=True)


def run_teams(page, con):
    teams = pull_all_teams(page)
    print(f"teams: {len(teams)}", flush=True)
    for tid, abbrev, name in teams:
        store.upsert_team(con, tid, abbrev, name)
    done = 0
    for tid, abbrev, name in teams:
        for season in SEASONS:
            try:
                state, rows = cached_fetch(
                    f"team_{tid}_{season}.json",
                    lambda tid=tid, season=season: pull_team_gamelog(page, tid, season),
                )
                for row in rows:
                    store.upsert_team_game(con, row)
                done += len(rows)
            except Exception as e:
                print(f"  FAIL team {tid} {abbrev} s{season}: {e}", flush=True)
    con.commit()
    # standings lack abbrevs; derive from each team's own matchup strings
    con.execute(
        "UPDATE teams SET abbrev = (SELECT substr(matchup, 1, instr(matchup, ' ') - 1) "
        "FROM team_games WHERE team_id = teams.team_id "
        "AND matchup IS NOT NULL AND length(matchup) > 3 LIMIT 1) "
        "WHERE COALESCE(abbrev, '') = ''"
    )
    con.commit()
    print(f"teams done: {done} team-game rows", flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    RAW.mkdir(parents=True, exist_ok=True)
    con = store.connect()
    pw, browser, page = get_browser_page()
    try:
        if mode == "player":
            for pid in [int(x) for x in sys.argv[2:]]:
                for season in SEASONS:
                    try:
                        state, rows = cached_fetch(
                            f"gamelog_{pid}_{season}.json",
                            lambda pid=pid, season=season: pull_gamelog(page, pid, season),
                        )
                        for row in rows:
                            store.upsert_game(con, row)
                        print(f"{pid} s{season}: {len(rows)} rows ({state})", flush=True)
                    except Exception as e:
                        print(f"{pid} s{season}: FAIL {e}", flush=True)
            con.commit()
        elif mode == "players-bulk":
            run_players_bulk(page, con)
        elif mode == "teams":
            run_teams(page, con)
        elif mode == "all":
            run_players_bulk(page, con)
            run_teams(page, con)
        else:
            sys.exit(f"unknown mode {mode!r}")
    finally:
        browser.close()
        pw.stop()
    print(f"done -> {store.DB}", flush=True)


if __name__ == "__main__":
    main()
