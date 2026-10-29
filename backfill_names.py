"""One-shot: backfill real player names into the players table.

The first bulk run stored 'id NNNN' because commonallplayers' name field is
DISPLAY_FIRST_LAST, not PLAYER. Parser fixed in nba_official.py; this script
re-pulls the roster list through the bridge and upserts names.

Usage: /opt/venv/bin/python backfill_names.py
"""
import store
from nba_official import get_browser_page, pull_all_player_ids


def main():
 con = store.connect()
 pw, browser, page = get_browser_page()
 try:
  players = pull_all_player_ids(page)
  for pid, name in players:
   store.upsert_player(con, pid, name)
  con.commit()
  print(f"backfilled names for {len(players)} players")
  bad = con.execute(
   "select count(*) from players where name like 'id %'"
  ).fetchone()[0]
  print(f"remaining bad names: {bad}")
  for probe in (1629029, 203999):
   r = con.execute(
    "select espn_id, name from players where espn_id=?", (probe,)
   ).fetchone()
   print(f"probe {probe} -> {r}")
 finally:
  browser.close()
  pw.stop()


if __name__ == "__main__":
 main()
