"""Test the Final Table model the only way it can be tested.

I told Manuel there was no backtest for this — one season produces one final
table, so there is nothing to measure against. He pointed out that there is:
use 2024/25 to predict 2025/26, under the real rules, locking at matchday 5.
He was right and I was too quick.

WHAT THIS DOES. Takes the world as it stood at the 2025/26 lock — the whole of
2024/25, and that season's first four rounds, and nothing else — produces the
entry the model would have submitted, and scores it against the table 2025/26
actually finished with.

AND WHAT IT COMPARES AGAINST, because a score with nothing beside it says
nothing. Three answers anyone could give without a model:

    last year's table      the champion stays champion, everyone holds station
    the table at the lock  four rounds treated as the season
    alphabetical           a floor, not a strategy

If the model cannot beat all three it has no business being on the page.

    python scripts/backtest_final_table.py
    python scripts/backtest_final_table.py --draws 20000

ONE SEASON IS ONE NUMBER. This cannot say the model is good, only that it beat
the obvious alternatives once. Read it as a floor test — the kind that catches
a model doing something foolish — rather than as a measurement.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.final_table import (  # noqa: E402
    BONUS_ROUNDS,
    BONUS_REACH,
    LAST_CHIP_ROUND,
    WEEKLY_REACH,
    WORTH_MOVING,
    best_chip,
    best_order,
    distribution,
    score,
    strengths,
)
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import OpenFootballClient  # noqa: E402


class Record:
    """What `strengths` reads off a club's archive."""

    def __init__(self, matches=0, goals_for=0, goals_against=0):
        self.matches = matches
        self.goals_for = goals_for
        self.goals_against = goals_against


def records_from(fixtures) -> dict[str, Record]:
    out: dict[str, Record] = defaultdict(Record)
    for f in fixtures:
        for club, scored, conceded in (
            (f.home, f.home_goals, f.away_goals),
            (f.away, f.away_goals, f.home_goals),
        ):
            r = out[club]
            r.matches += 1
            r.goals_for += scored
            r.goals_against += conceded
    return dict(out)


def table_from(fixtures, clubs) -> list[dict]:
    """A league table from a set of played matches, in the shape the model wants."""
    stat = {c: {"played": 0, "points": 0, "goals_for": 0, "goals_against": 0} for c in clubs}
    for f in fixtures:
        for club, scored, conceded in (
            (f.home, f.home_goals, f.away_goals),
            (f.away, f.away_goals, f.home_goals),
        ):
            s = stat[club]
            s["played"] += 1
            s["goals_for"] += scored
            s["goals_against"] += conceded
            s["points"] += 3 if scored > conceded else (1 if scored == conceded else 0)
    ordered = sorted(
        clubs,
        key=lambda c: (
            -stat[c]["points"],
            -(stat[c]["goals_for"] - stat[c]["goals_against"]),
            -stat[c]["goals_for"],
            c,
        ),
    )
    return [
        {"club": c, "position": i + 1, **stat[c],
         "goal_difference": stat[c]["goals_for"] - stat[c]["goals_against"]}
        for i, c in enumerate(ordered)
    ]


def play_season(
    season, clubs, archive, *, lock, draws, weight, seed, chips=True,
    threshold=WORTH_MOVING,
) -> dict:
    """The entry, and then the whole season of chips played over it.

    NOTHING LOOKS FORWARD. At matchday r the state is built from rounds before
    r only, exactly as it would be on the Thursday: a chip opens when the
    previous round ends and shuts when the next begins, so the information
    available to it is the table up to that point and nothing else.
    """
    entries = []
    order, used, travelled = None, 0, 0

    rounds = [lock] + (list(range(lock + 1, LAST_CHIP_ROUND + 1)) if chips else [])
    for matchday in rounds:
        played = [f for f in season if f.round_number < matchday]
        remaining = [(f.home, f.away) for f in season if f.round_number >= matchday]
        table = table_from(played, clubs)
        strength = strengths(archive, table, recent_weight=weight)
        spread = distribution(table, remaining, strength, draws=draws, seed=seed)

        if order is None:
            order = best_order(spread, clubs=clubs)
            continue

        # The ordinary chip, and at three matchdays a bonus one on top of it.
        reaches = [WEEKLY_REACH]
        if matchday in BONUS_ROUNDS:
            reaches.append(BONUS_REACH)
        for reach in reaches:
            order, who, distance = best_chip(
                order, spread, reach=reach, threshold=threshold
            )
            if who is not None:
                used += 1
                travelled += distance
                entries.append((matchday, who, distance))

    return {"order": order, "chips": used, "places": travelled, "log": entries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--weight", type=float, default=1.0,
                        help="how many times over this season's matches count")
    parser.add_argument("--no-chips", action="store_true")
    args = parser.parse_args()

    client = OpenFootballClient(timeout=60.0)
    before = client.season_fixtures("2024-25")
    season = client.season_fixtures("2025-26")

    clubs = sorted({c for f in season for c in (f.home, f.away)})
    lock = FIRST_SCORING_MATCHDAY

    # THE WORLD AS IT STOOD AT THE LOCK. Everything after this line that touches
    # a round at or beyond the lock is a lookahead, and there is exactly one
    # such use below — the actual finish, which is what we are scoring against.
    played = [f for f in season if f.round_number < lock]
    remaining = [(f.home, f.away) for f in season if f.round_number >= lock]

    archive = records_from(before)
    at_lock = table_from(played, clubs)
    strength = strengths(archive, at_lock)
    spread = distribution(at_lock, remaining, strength, draws=args.draws, seed=args.seed)
    entry = best_order(spread, clubs=clubs)

    with_chips = play_season(
        season, clubs, archive, lock=lock, draws=args.draws,
        weight=args.weight, seed=args.seed,
    )

    actual = [row["club"] for row in table_from(season, clubs)]

    arrived = sorted(set(clubs) - set(archive))
    print(f"locked at matchday {lock}: {len(played)} matches played, {len(remaining)} to come")
    print(f"{len(arrived)} clubs promoted with no archive at all: {', '.join(arrived)}")
    print()

    ours = score(entry, actual)
    last_year = [row["club"] for row in table_from(before, sorted(archive))]
    # Promoted clubs go to the bottom of a "same as last year" entry, which is
    # what anyone filling that in would do with them.
    naive_last = [c for c in last_year if c in clubs] + arrived
    naive_now = [row["club"] for row in at_lock]
    alphabetical = sorted(clubs)

    random.Random(args.seed).shuffle(shuffled := list(clubs))

    print(f"  {'entry':<26}{'places':>8}{'champ':>7}{'down':>7}{'top4':>7}{'TOTAL':>8}")
    for name, order in (
        ("the model, locked", entry),
        ("the model, with chips", with_chips["order"]),
        ("last year's table", naive_last),
        ("the table at the lock", naive_now),
        ("alphabetical", alphabetical),
        ("one shuffle", shuffled),
    ):
        got = score(order, actual)
        mark = "  <-" if name.startswith("the model") else ""
        print(
            f"  {name:<26}{got['places']:>8}{got['champion']:>7}"
            f"{got['relegation']:>7}{got['top_four']:>7}{got['total']:>8}{mark}"
        )

    print()
    print(
        f"  {with_chips['chips']} chips used, {with_chips['places']} places moved "
        f"(the tiebreak rewards fewer of both)"
    )
    for matchday, who, distance in with_chips["log"][:8]:
        print(f"    matchday {matchday:>2}: {who} by {distance}")
    if len(with_chips["log"]) > 8:
        print(f"    ... and {len(with_chips['log']) - 8} more")

    exact = sum(1 for i, c in enumerate(entry) if actual[i] == c)
    within = sum(1 for i, c in enumerate(entry) if abs(actual.index(c) - i) <= 2)
    print()
    print(f"  the model placed {exact} of {len(clubs)} exactly, {within} within two")
    print(f"  champion: predicted {entry[0]}, actual {actual[0]}")
    print(f"  down:     predicted {', '.join(entry[-2:])}, actual {', '.join(actual[-2:])}")
    print()
    print("  One season is one number. This says the model beat the obvious")
    print("  alternatives once, not that it is good.")


if __name__ == "__main__":
    main()
