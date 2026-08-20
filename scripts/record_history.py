"""Build the round-by-round history of a team, and of a private league.

The standings services disagree about the past. The private-league one ignores
`round=` entirely and always answers with the latest state; the national one
honours it. So a league's history cannot be asked for directly — it has to be
assembled team by team through the national search, matching on team id because
names are not unique.

That is worth doing once. A position is only a fact about today; the shape of a
season is in how it moved. Manuel's own two rounds already say more than the
current table does: 1246th after round one, 6598th after round two.

Rounds already on file are never refetched, so a later run costs one round's
worth of requests rather than the whole season's.

    python scripts/record_history.py                 # every round scored so far
    python scripts/record_history.py --round 3       # just one
    python scripts/record_history.py --me-only       # skip the other 29
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.source import LigaRecordClient  # noqa: E402
from liga_record_mcp.source.live import SiteError  # noqa: E402

HISTORY_PATH = ROOT / "data" / "history.json"
MY_TEAM_ID = 156412
#: A courtesy pause between requests. Nothing here is urgent, and the whole
#: league is sixty-odd calls against someone else's server.
PAUSE = 0.4


def scored_rounds(client: LigaRecordClient) -> list[int]:
    """Rounds with at least one completed match, oldest first."""
    return sorted({f.round_number for f in client.fixtures() if f.played})


def league_members(client: LigaRecordClient, guid: str) -> list[dict]:
    rows, _ = client.standings(league_guid=guid, page_size=50)
    return [{"id": r.team_id, "name": r.team_name, "user": r.user_name} for r in rows]


def fetch_round(client: LigaRecordClient, member: dict, round_number: int) -> dict | None:
    """One team's standing after one round, or None if it cannot be found.

    The search is by name and can return several teams — "Melro" matches two —
    so the row is chosen by id, never by position in the results.
    """
    try:
        rows, _ = client.standings(
            team=member["name"], page_size=50, round_number=round_number
        )
    except SiteError:
        return None
    for row in rows:
        if row.team_id == member["id"]:
            return {
                "points_round": row.points_round,
                "points_total": row.points_total,
                "position": row.position,
                "position_round": row.position_round,
            }
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, action="append", dest="rounds")
    parser.add_argument("--me-only", action="store_true")
    args = parser.parse_args()

    guid = os.environ.get("LIGA_RECORD_LEAGUE")
    client = LigaRecordClient(timeout=90.0)

    history = (
        json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if HISTORY_PATH.exists()
        else {"rounds": {}}
    )

    if args.me_only or not guid:
        members = [{"id": MY_TEAM_ID, "name": "Melro", "user": "manuelcralves"}]
        if not guid and not args.me_only:
            print("LIGA_RECORD_LEAGUE is not set — recording this team only")
    else:
        members = league_members(client, guid)
        print(f"league: {len(members)} teams")

    wanted = args.rounds or scored_rounds(client)
    print(f"rounds to cover: {wanted}")

    for round_number in wanted:
        key = str(round_number)
        stored = history["rounds"].setdefault(
            key, {"recorded_at": None, "teams": {}}
        )
        missing = [m for m in members if str(m["id"]) not in stored["teams"]]
        if not missing:
            print(f"  round {round_number}: already complete ({len(stored['teams'])})")
            continue

        found = 0
        for member in missing:
            row = fetch_round(client, member, round_number)
            if row is not None:
                stored["teams"][str(member["id"])] = {
                    "name": member["name"],
                    "user": member["user"],
                    **row,
                }
                found += 1
            time.sleep(PAUSE)

        stored["recorded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        skipped = len(missing) - found
        note = f", {skipped} not found" if skipped else ""
        print(f"  round {round_number}: +{found} teams{note}")

    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    mine = [
        (r, data["teams"][str(MY_TEAM_ID)])
        for r, data in sorted(history["rounds"].items(), key=lambda kv: int(kv[0]))
        if str(MY_TEAM_ID) in data["teams"]
    ]
    if mine:
        print(f"\nwrote {HISTORY_PATH}\n")
        print(f"  {'round':<7}{'points':>8}{'total':>8}{'national':>11}")
        for round_key, row in mine:
            print(
                f"  {round_key:<7}{row['points_round']:>8}{row['points_total']:>8}"
                f"{row['position']:>11,}".replace(",", " ")
            )


if __name__ == "__main__":
    main()
