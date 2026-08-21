"""Which way of estimating a player predicts the next round best.

Everything measured so far has been measured on one season — the same season
the model was tuned on, which is the one place a model is guaranteed to look
good. This asks the same question of a year it has never seen.

It also asks it more directly than the squad backtests do. Those need prices,
a budget and a transfer rule, and every one of those is an assumption that can
carry the result. This needs none of them: stand before matchday M knowing
only what came before, predict what every player will score, and compare with
what he did. Whichever estimate is closest wins, and nothing else is in the way.

Three estimates are compared, and the two baselines matter as much as the
model:

    the league average    what you would say knowing nothing about the player
    form only             his own record, shrunk toward his club and position
    plays x returns       the same, split into whether he plays and what he
                          returns when he does — with and without minutes

A model that cannot beat the league average is not a model.

    python scripts/measure_projection_accuracy.py
    python scripts/measure_projection_accuracy.py --season data/season-2024-25.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.backtest import (  # noqa: E402
    ABSENT,
    shrunk_projection,
    two_part_projection,
)
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY, LAST_MATCHDAY  # noqa: E402

SEASON_PATH = ROOT / "data" / "last-season.json"

ALL_MATCHDAYS = list(range(1, LAST_MATCHDAY + 1))

#: Predictions start once there is something to predict from. Before this the
#: estimates are all the same number and comparing them measures nothing.
FIRST_PREDICTED = FIRST_SCORING_MATCHDAY

#: How far either side of a round the oracle may look.
#:
#: Three, because three is the width that makes the oracle HARDEST to beat —
#: swept over both seasons, it scores 1.548 and 1.493 where four scores 1.563
#: and 1.509. A ceiling is only worth quoting if it is the tightest one
#: available, and the temptation runs the other way: a loose oracle flatters
#: the model, and at four the model had started beating it, which says nothing
#: about the model and everything about a handicapped comparison.
ORACLE_WINDOW = 3


def load(path: Path):
    if not path.exists():
        raise SystemExit(f"no {path} — run scripts/build_last_season.py first")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    players = loaded["players"]

    points, minutes, cells = {}, {}, {}
    for player_id, player in players.items():
        if not player["matches"]:
            continue
        points[player_id] = {m: ABSENT for m in ALL_MATCHDAYS}
        minutes[player_id] = {m: 0 for m in ALL_MATCHDAYS}
        clubs: dict[str, int] = defaultdict(int)
        for match in player["matches"]:
            matchday = int(match["round"])
            points[player_id][matchday] = float(match["points"])
            minutes[player_id][matchday] = int(match.get("minutes") or 0)
            if match.get("club"):
                clubs[match["club"]] += 1
        cells[player_id] = (
            max(clubs, key=clubs.get) if clubs else "",
            player["position"],
        )
    return points, minutes, cells, loaded.get("season")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=Path, default=SEASON_PATH)
    args = parser.parse_args()

    points, minutes, cells, season = load(args.season)
    print(f"{len(points)} players, season {season}, from {args.season.name}")

    # The ceiling for anything that works from a rate, and getting it right
    # took two attempts. A player's average over the whole season is NOT the
    # ceiling: it is one fixed number, and the model beat it — because the
    # model adapts, and knows in March that a man stopped playing in January.
    #
    # So the oracle has to adapt too. It is given his form in the rounds either
    # side of the one being predicted, from the future as well as the past, and
    # never that round itself. No causal model can have that. Whatever error
    # survives it is not the model failing; it is football — the same player,
    # in the same form, scores 0 one week and 12 the next.
    #
    # Without this line the other numbers cannot be read at all. An error of
    # 1.59 sounds poor beside zero and excellent beside the most anyone could
    # manage.
    def oracle(matchday: int) -> dict[str, float]:
        window = range(matchday - ORACLE_WINDOW, matchday + ORACLE_WINDOW + 1)
        view = {}
        for player_id, by_matchday in points.items():
            nearby = [
                by_matchday[m]
                for m in window
                if m in by_matchday and m != matchday
            ]
            view[player_id] = statistics.mean(nearby) if nearby else 0.0
        return view

    signals = {
        "the league average": None,
        "an oracle that knows his form": oracle,
        "form only": lambda upto: shrunk_projection(points, cells, upto=upto),
        "plays x returns": lambda upto: two_part_projection(
            points, minutes, cells, upto=upto
        ),
        "plays x returns, minutes": lambda upto: two_part_projection(
            points, minutes, cells, upto=upto, minutes_weighted=True
        ),
    }

    errors: dict[str, list[float]] = defaultdict(list)
    for matchday in range(FIRST_PREDICTED, LAST_MATCHDAY + 1):
        earlier = [
            value
            for by_matchday in points.values()
            for m, value in by_matchday.items()
            if m < matchday
        ]
        average = statistics.mean(earlier) if earlier else 0.0
        views = {
            name: (build(matchday) if build else None)
            for name, build in signals.items()
        }
        for player_id, by_matchday in points.items():
            truth = by_matchday[matchday]
            for name, view in views.items():
                estimate = average if view is None else view[player_id]
                errors[name].append(abs(estimate - truth))

    print()
    print(f"  {'estimate':<28}{'error':>8}{'vs average':>12}")
    baseline = statistics.mean(errors["the league average"])
    for name in signals:
        mean_error = statistics.mean(errors[name])
        share = (baseline - mean_error) / baseline
        note = "" if name == "the league average" else f"{share:>11.1%}"
        print(f"  {name:<28}{mean_error:>8.3f}{note}")

    floor = statistics.mean(errors["an oracle that knows his form"])
    model = statistics.mean(errors["plays x returns"])
    print()
    print(f"  {len(errors['form only']):,} player-rounds predicted")
    print(
        f"  of the {baseline - floor:.3f} that is knowable at all, the model "
        f"gets {baseline - model:.3f} — {(baseline - model) / (baseline - floor):.0%}"
    )

    # And how much of the round-to-round spread it actually accounts for.
    actual, guessed = [], []
    for matchday in range(FIRST_PREDICTED, LAST_MATCHDAY + 1):
        view = signals["plays x returns"](matchday)
        for player_id, by_matchday in points.items():
            actual.append(by_matchday[matchday])
            guessed.append(view[player_id])
    mean_a, mean_g = statistics.mean(actual), statistics.mean(guessed)
    cov = sum((a - mean_a) * (g - mean_g) for a, g in zip(actual, guessed)) / len(actual)
    sa, sg = statistics.pstdev(actual), statistics.pstdev(guessed)
    r = cov / (sa * sg) if sa and sg else 0.0
    print(
        f"  a round scores {mean_a:.2f} on average, give or take {sa:.2f}; "
        f"the model correlates r = {r:.3f} (r2 = {r * r:.3f})"
    )


if __name__ == "__main__":
    main()
