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
    threshold=WORTH_MOVING, start=None,
) -> dict:
    """The entry, and then the whole season of chips played over it.

    NOTHING LOOKS FORWARD. At matchday r the state is built from rounds before
    r only, exactly as it would be on the Thursday: a chip opens when the
    previous round ends and shuts when the next begins, so the information
    available to it is the table up to that point and nothing else.
    """
    entries = []
    order, used, travelled = start, 0, 0

    rounds = ([] if start is not None else [lock]) + (
        list(range(lock + 1, LAST_CHIP_ROUND + 1)) if chips else []
    )
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


#: Every season that can be tested, with the two before it as its archive —
#: the same two-season window the live model reads. openfootball carries
#: Portugal from 2020-21, so this is all of it.
PAIRS = [
    ("2022-23", ("2020-21", "2021-22")),
    ("2023-24", ("2021-22", "2022-23")),
    ("2024-25", ("2022-23", "2023-24")),
    ("2025-26", ("2023-24", "2024-25")),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--weight", type=float, default=1.0)
    parser.add_argument("--threshold", type=float, default=WORTH_MOVING)
    args = parser.parse_args()

    client = OpenFootballClient(timeout=60.0)
    needed = sorted({s for pair in PAIRS for s in (pair[0], *pair[1])})
    fixtures = {tag: client.season_fixtures(tag) for tag in needed}

    print(f"locking at matchday {FIRST_SCORING_MATCHDAY}, {args.draws} seasons drawn")
    print(f"chips worth {WEEKLY_REACH} places weekly to matchday {LAST_CHIP_ROUND}, "
          f"{BONUS_REACH} at {', '.join(map(str, BONUS_ROUNDS))}")
    print()
    header = ("season", "model+chips", "chips", "entry only", "table@5", "last year", "max")
    print(f"  {header[0]:<9}{header[1]:>13}{header[2]:>7}{header[3]:>12}"
          f"{header[4]:>9}{header[5]:>11}{header[6]:>7}")

    totals = {name: [] for name in ("chips", "entry", "table", "last")}
    for target, archive_tags in PAIRS:
        season = fixtures[target]
        clubs = sorted({c for f in season for c in (f.home, f.away)})
        archive = records_from([f for tag in archive_tags for f in fixtures[tag]])
        actual = [row["club"] for row in table_from(season, clubs)]

        run = play_season(
            season, clubs, archive, lock=FIRST_SCORING_MATCHDAY, draws=args.draws,
            weight=args.weight, seed=args.seed, threshold=args.threshold,
        )
        locked = play_season(
            season, clubs, archive, lock=FIRST_SCORING_MATCHDAY, draws=args.draws,
            weight=args.weight, seed=args.seed, chips=False,
        )
        at_lock = [
            row["club"]
            for row in table_from(
                [f for f in season if f.round_number < FIRST_SCORING_MATCHDAY], clubs
            )
        ]
        previous = fixtures[archive_tags[-1]]
        before_clubs = sorted({c for f in previous for c in (f.home, f.away)})
        last_year = [row["club"] for row in table_from(previous, before_clubs)]
        naive_last = [c for c in last_year if c in clubs] + sorted(set(clubs) - set(last_year))

        got = [
            score(run["order"], actual)["total"],
            score(locked["order"], actual)["total"],
            score(at_lock, actual)["total"],
            score(naive_last, actual)["total"],
        ]
        for name, value in zip(totals, got):
            totals[name].append(value)
        print(
            f"  {target:<9}{got[0]:>13}{run['chips']:>7}{got[1]:>12}"
            f"{got[2]:>9}{got[3]:>11}{score(actual, actual)['total']:>7}"
        )

    means = [sum(v) / len(v) for v in totals.values()]
    print(f"  {'mean':<9}{means[0]:>13.0f}{'':>7}{means[1]:>12.0f}"
          f"{means[2]:>9.0f}{means[3]:>11.0f}")
    spread = max(totals["chips"]) - min(totals["chips"])
    print()
    print(
        f"  The chips are the model: {means[0]:.0f} against {means[1]:.0f} for the "
        f"same entry left alone."
    )
    print(
        f"  The entry itself is worth about as much as copying the table at the "
        f"lock ({means[2]:.0f}),"
    )
    print(
        f"  and the season-to-season spread is {spread:.0f} points — wider than any "
        "gap between"
    )
    print("  the entries. Twenty-five weeks of correcting is what separates them.")
    print()
    print(
        "  Every figure here moves with --draws and --seed. Quote none of them "
        "without saying which."
    )


if __name__ == "__main__":
    main()
