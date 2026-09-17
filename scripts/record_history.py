"""Build the round-by-round history of a team, and of a private league.

The standings services disagree about the past. The private-league one ignores
`round=` entirely and always answers with the latest state; the national one
honours it. So a league's history cannot be asked for directly — it has to be
assembled team by team through the national search, matching on team id because
names are not unique.

That is worth doing once. A position is only a fact about today; the shape of a
season is in how it moved. The team's own two rounds already say more than the
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

from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import LigaRecordClient  # noqa: E402
from liga_record_mcp.source.live import SiteError  # noqa: E402

HISTORY_PATH = ROOT / "data" / "history.json"
MY_TEAM_ID = 156412
#: A courtesy pause between requests. Nothing here is urgent, and the whole
#: league is sixty-odd calls against someone else's server.
PAUSE = 0.4

#: The four numbers a round is made of here. Equal on all four is the tell that
#: the site is still answering with the round before.
FIELDS = ("points_round", "points_total", "position", "position_round")


def scored_rounds(client: LigaRecordClient) -> list[int]:
    """Rounds with at least one completed match, oldest first."""
    return sorted({f.round_number for f in client.fixtures() if f.played})


def site_round(matchday: int) -> int:
    """The number the standings service calls this matchday.

    It renumbered when the official phase began: its round 1 is matchday 6, the
    same renumbering the weekly score emails use. Asked for a matchday number it
    answers with an empty row — team id 0, every number zero — so the question
    has to be put in its own terms.

    A trial matchday has no number here at all. That table was erased, and
    asking for round zero would fetch exactly the empty row that started this.
    """
    if matchday < FIRST_SCORING_MATCHDAY:
        raise ValueError(
            f"matchday {matchday} is from the trial phase, which the site erased"
        )
    return matchday - FIRST_SCORING_MATCHDAY + 1


def same_as_before(row: dict, previous: dict | None) -> bool:
    """Whether a team's row repeats the round before, number for number."""
    return previous is not None and all(
        row[field] == previous.get(field) for field in FIELDS
    )


def published(fetched: dict[str, dict], earlier: dict[str, dict]) -> bool:
    """Whether the site has really published this round, judged over the whole field.

    It publishes a round days before it folds it into the table, and until it
    does it answers for the new round with the old numbers. On 15/09/2026 that
    filed matchday 5's 52 points and 6326th place as matchday 6, where they
    stayed, because a round already on file was never fetched again.

    JUDGED OVER EVERY TEAM AND NEVER TEAM BY TEAM. One team repeating all four
    numbers is ordinary — a blank round with nobody moving past him — and
    refusing his row would drop a round that really happened. Forty teams
    repeating all four is the site, not the football.
    """
    return any(
        not same_as_before(row, earlier.get(key)) for key, row in fetched.items()
    )


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
            team=member["name"], page_size=50, round_number=site_round(round_number)
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
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="fetch rounds already on file again, for when what was filed was "
             "the site answering with the round before",
    )
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

    asked = args.rounds or scored_rounds(client)
    trial = [r for r in asked if r < FIRST_SCORING_MATCHDAY]
    wanted = [r for r in asked if r >= FIRST_SCORING_MATCHDAY]
    if trial:
        print(
            f"leaving the trial rounds {trial} alone: the site erased that table "
            "when the official phase began, so what is on file for them is all "
            "there will ever be"
        )
    print(f"rounds to cover: {wanted}")

    for round_number in wanted:
        key = str(round_number)
        stored = history["rounds"].setdefault(
            key, {"recorded_at": None, "teams": {}}
        )
        earlier = (history["rounds"].get(str(round_number - 1)) or {}).get("teams", {})
        missing = [
            m for m in members if args.refresh or str(m["id"]) not in stored["teams"]
        ]
        if not missing:
            print(f"  round {round_number}: already complete ({len(stored['teams'])})")
            continue

        found: dict[str, tuple[dict, dict]] = {}
        for member in missing:
            row = fetch_round(client, member, round_number)
            if row is not None:
                found[str(member["id"])] = (member, row)
            time.sleep(PAUSE)

        if found and not published({i: r for i, (_, r) in found.items()}, earlier):
            # NOTHING FILED. Every team came back with the previous round's
            # numbers, which is the site answering for a round it has not folded
            # into its table yet. Filing that put matchday 5 on file as matchday
            # 6 on 15/09/2026, and there it stayed, because a round already on
            # file was never fetched again.
            print(
                f"  round {round_number}: all {len(found)} teams are still on "
                f"round {round_number - 1} — the site has not published this one "
                "yet, nothing filed"
            )
            if not stored["teams"]:
                del history["rounds"][key]
            continue

        for team_id, (member, row) in found.items():
            stored["teams"][team_id] = {
                "name": member["name"],
                "user": member["user"],
                **row,
            }
        if found:
            stored["recorded_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        if not stored["teams"]:
            del history["rounds"][key]
        lost = len(missing) - len(found)
        print(
            f"  round {round_number}: +{len(found)} teams"
            + (f", {lost} not found" if lost else "")
        )

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
