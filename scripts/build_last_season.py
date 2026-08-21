"""Score last season the way Liga Record would have, match by match.

WHY. The model has never seen a full season of Liga Record scoring. It has
four rounds of this one, and four rounds cannot tell a good forward from a
forward who had a good fortnight. Liga Record publishes no history at all —
last season's scores are simply gone, and §10.2 says the marks behind them
were never for discussion.

They are not gone, though. §10.3 pays for goals, cards, clean sheets, wins and
absences, and zerozero publishes every one of those per match. Only Record's
own mark is missing, and that is the flattest term in the whole table — it
stands in from zerozero's rating, measured to within 0.44 points on rounds held
back from the fit. The spiky part of a score is computed, not guessed.

WHAT COMES OUT. For every player in `data/players.json`, every league match of
2025/26: the events, the objective points, the mark, and the total. From that
falls out form across the season, a real prior for each player, and a season
the model can be argued with against.

WHAT IT CANNOT DO. Players promoted with Marítimo and Académico played in the
second division last season and have no top-flight record; they come back
empty, and that is reported rather than papered over. Player of the Week
(§10.3(k)) goes to the round's highest scorer, and this file holds only this
season's market, so the real winner may be someone who has since left: it is
flagged for the best of those covered and deliberately kept out of the points.

    python scripts/build_last_season.py             # everyone on file
    python scripts/build_last_season.py --limit 30  # a taste, for trying it
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import OpenFootballClient, SiteError  # noqa: E402
from liga_record_mcp.source.zerozero import (  # noqa: E402
    ZeroZeroClient,
    ZeroZeroError,
    ZeroZeroPlayer,
)
from liga_record_mcp.stats import (  # noqa: E402
    PLAYER_OF_THE_WEEK_BONUS,
    clubs_in_calendar,
    fill_absent_rounds,
    reconstruct_points,
)

HISTORY_PATH = ROOT / "data" / "players.json"
OUT_PATH = ROOT / "data" / "last-season.json"
CACHE_DIR = ROOT / "data" / "zerozero-cache"

#: zerozero's id for 2025/26, the last completed season.
LAST_SEASON = 155

#: zerozero numbers its seasons; openfootball names them. Written out rather
#: than derived, because an arithmetic relationship between two sites' internal
#: identifiers is a coincidence, and one that would fetch the wrong season in
#: silence the year it stopped holding.
ARCHIVE_TAGS: dict[int, str] = {
    154: "2024-25",
    155: "2025-26",
    156: "2026-27",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--season", type=int, default=LAST_SEASON)
    parser.add_argument("--archive", help="openfootball season tag, e.g. 2025-26")
    parser.add_argument(
        "--out", type=Path, help="where to write, so an older season does not "
        "overwrite the one the model is using"
    )
    parser.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        raise SystemExit("no data/players.json — run build_player_history.py first")
    known = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["players"]
    wanted = list(known.items())[: args.limit] if args.limit else list(known.items())

    zero = ZeroZeroClient(CACHE_DIR, pause=args.pause)
    print(f"{len(wanted)} players, season {args.season}")

    # The calendar of the competition we actually mean, from an archive that
    # only ever covers Portugal. Without it "D1" means any country's first
    # division and a Swiss August is scored as a Portuguese one.
    archive_tag = args.archive or ARCHIVE_TAGS.get(args.season)
    if archive_tag is None:
        raise SystemExit(
            f"no archive tag known for zerozero season {args.season} — "
            "pass --archive, e.g. --archive 2025-26"
        )
    try:
        calendar = [
            {
                "round": f.round_number,
                "date": f.date,
                "home_goals": f.home_goals,
                "away_goals": f.away_goals,
            }
            for f in OpenFootballClient().season_fixtures(archive_tag)
        ]
    except SiteError as exc:
        raise SystemExit(f"could not read the {archive_tag} calendar: {exc}")
    print(f"{len(calendar)} Primeira Liga matches in the {archive_tag} archive")

    players: dict[str, dict] = {}
    empty, failed, unscored = [], [], []

    for n, (player_id, row) in enumerate(wanted, 1):
        position = Position(row["position"])
        # The slug is decorative: zerozero resolves a player by his numeric id
        # alone, so a stored id is enough and an accented name cannot break it.
        handle = ZeroZeroPlayer(
            zid=row["zerozero_id"],
            path=f"/jogador/p/{row['zerozero_id']}",
            name=row["name"],
            club=row["club"],
        )
        try:
            matches = zero.matches(handle, args.season)
        except ZeroZeroError as exc:
            failed.append((row["name"], str(exc)))
            continue

        if not matches:
            # Promoted with Marítimo or Académico, or abroad, or not yet a
            # professional. An empty season is a fact about him, not a failure.
            empty.append((row["name"], row["club"]))

        built = []
        for match in matches:
            if match.round_number is None:
                continue
            if match.scored is None or match.conceded is None:
                # No scoreline parsed. Scoring it as 0-0 would pay a keeper two
                # points for a clean sheet that may not have happened, and
                # nothing downstream could tell that apart from a real one.
                unscored.append((row["name"], match.round_number))
                continue
            scored = reconstruct_points(
                position,
                conceded=match.conceded,
                won=match.scored > match.conceded,
                used=match.used,
                rating=match.rating,
                goals=match.goals,
                minutes=match.minutes,
                red_card=match.red_card,
                second_yellow=match.yellow_cards >= 2,
            )
            built.append(
                {
                    "round": match.round_number,
                    "date": match.date,
                    "club": match.club,
                    "opponent": match.opponent,
                    "at_home": match.at_home,
                    "scored": match.scored,
                    "conceded": match.conceded,
                    "used": match.used,
                    "minutes": match.minutes,
                    "goals": match.goals,
                    "assists": match.assists,
                    "yellow_cards": match.yellow_cards,
                    "red_card": match.red_card,
                    "rating": match.rating,
                    "objective": scored["objective"],
                    "mark": scored["mark"],
                    "mark_source": scored["mark_source"],
                    "points": scored["points"],
                    "player_of_the_week": False,
                }
            )

        players[player_id] = {
            "name": row["name"],
            "club": row["club"],
            "position": row["position"],
            "zerozero_id": row["zerozero_id"],
            "matches": built,
        }
        if n % 25 == 0 or n == len(wanted):
            print(f"  {n}/{len(wanted)} — {len(empty)} with no top-flight season")

    # Which of the clubs seen are actually Primeira Liga clubs, decided by
    # whether their matches turn up in the archive's calendar rather than by a
    # list of names — last season's league included AVS and Tondela, who are
    # not in this one, and a name list would have to be maintained backwards.
    verdicts = clubs_in_calendar(
        (
            {
                "club": m["club"],
                "round": m["round"],
                "date": m["date"],
                "home_goals": m["scored"] if m["at_home"] else m["conceded"],
                "away_goals": m["conceded"] if m["at_home"] else m["scored"],
            }
            for player in players.values()
            for m in player["matches"]
        ),
        calendar,
    )
    members = {club for club, verdict in verdicts.items() if verdict["member"]}
    dropped = 0
    absent = 0
    for player in players.values():
        keeping = [m for m in player["matches"] if m["club"] in members]
        dropped += len(player["matches"]) - len(keeping)
        # The weeks he was injured or left out have no row here, and cost a
        # manager -1 each. Without them a season is divided by the weeks that
        # went well.
        filled = fill_absent_rounds(keeping)
        absent += len(filled) - len(keeping)
        player["matches"] = filled

    rounds: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for player_id, player in players.items():
        for index, match in enumerate(player["matches"]):
            rounds[match["round"]].append((player_id, index))

    # §10.3(k): five points to the round's highest scorer. It is a property of
    # the round, not of a player, so it can only be settled once every match of
    # that round is on the table — and here the table is partial. These are
    # this season's market, and a round of last season also had players who
    # have since left, whom nobody asked us to look up.
    #
    # So the flag marks the best of those covered, and the five points are kept
    # out of `points`. Folding them in would hand a bonus to one of our players
    # every single round, including the rounds someone else won, and it would
    # land on the top of the distribution where it does the most damage.
    # Whoever reads this file can add `PLAYER_OF_THE_WEEK_BONUS` if they want
    # it; nobody can subtract it back out.
    awarded = 0
    for round_number, entries in rounds.items():
        best, leaders = None, []
        for player_id, index in entries:
            points = players[player_id]["matches"][index]["points"]
            if best is None or points > best:
                best, leaders = points, [(player_id, index)]
            elif points == best:
                leaders.append((player_id, index))
        # A tie leaves it undecidable and guessing would put five points on the
        # wrong man, so a tied round goes without.
        if len(leaders) == 1:
            player_id, index = leaders[0]
            players[player_id]["matches"][index]["player_of_the_week"] = True
            awarded += 1

    covered = {pid: p for pid, p in players.items() if p["matches"]}
    out_path = args.out or OUT_PATH
    out_path.write_text(
        json.dumps(
            {
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "season": args.season,
                "rounds_seen": sorted(rounds),
                "players_per_round": {
                    str(r): len(entries) for r, entries in sorted(rounds.items())
                },
                "player_of_the_week_bonus": PLAYER_OF_THE_WEEK_BONUS,
                "player_of_the_week_flagged": awarded,
                "player_of_the_week_in_points": False,
                "players": players,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    total_matches = sum(len(p["matches"]) for p in players.values())
    print()
    print(f"wrote {out_path}")
    print(f"  {len(covered)} players with a top-flight season, {total_matches} matches")
    coverage = (
        sum(len(e) for e in rounds.values()) / len(rounds) if rounds else 0
    )
    print(f"  {len(rounds)} rounds, {coverage:.0f} players covered per round")
    print(
        f"  {absent} rounds added at -1 for players who were not in the squad "
        "(§10.3(i)) — a manager pays for those weeks too"
    )
    print(
        f"  Player of the Week flagged in {awarded} rounds, and NOT added to "
        "points — the real winner may be someone who has since left"
    )
    print(f"  {zero.requests_made} new reads")
    if empty:
        print(f"\nno top-flight season ({len(empty)}) — second division, or abroad:")
        for name, club in empty[:15]:
            print(f"  {name[:22]:<22} {club}")
        if len(empty) > 15:
            print(f"  ... and {len(empty) - 15} more")
    outsiders = sorted(
        (club for club, v in verdicts.items() if not v["member"]),
        key=lambda club: -verdicts[club]["matches"],
    )
    if outsiders:
        print(f"\ndropped {dropped} matches from {len(outsiders)} clubs outside the Primeira Liga:")
        for club in outsiders[:12]:
            v = verdicts[club]
            print(f"  {club[:22]:<22} {v['matches']:>3} matches, {v['share']:.0%} in the calendar")
        if len(outsiders) > 12:
            print(f"  ... and {len(outsiders) - 12} more")
    if unscored:
        print(f"\nskipped for having no scoreline ({len(unscored)}):")
        for name, round_number in unscored[:10]:
            print(f"  {name[:22]:<22} round {round_number}")
    if failed:
        print(f"\nfailed to read ({len(failed)}):")
        for name, why in failed[:10]:
            print(f"  {name[:22]:<22} {why[:56]}")


if __name__ == "__main__":
    main()
