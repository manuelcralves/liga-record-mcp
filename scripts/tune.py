"""Search the model's parameters without fooling ourselves.

Sweeping a grid and keeping the best number is how a model gets worse while
appearing to get better. The best of two hundred combinations on one season is
partly the best combination and partly the luckiest, and nothing in the search
can tell those apart — the search is what created the luck.

THE PROTOCOL. Tune on one season. Report on the other, once.

    1. every combination is scored on the TUNING season
    2. the single best is taken, and nothing else about the search is used
    3. that one combination is scored on the HELD-OUT season, once
    4. what it did there is the honest number

If tuning helped, the winner beats the current parameters on the season it was
never shown. If it only found noise, it will not — and that is a result worth
having, because it says to stop turning knobs.

Run both ways round. A gain that appears tuning on 2025/26 and vanishes tuning
on 2024/25 was never there.

    python scripts/tune.py
    python scripts/tune.py --wide     # a bigger grid, slower
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.backtest import ABSENT, two_part_projection  # noqa: E402
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY, LAST_MATCHDAY  # noqa: E402
from liga_record_mcp.stats import (  # noqa: E402
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    ROTATION_WINDOW,
)

SEASONS = {
    "2025/26": ROOT / "data" / "last-season.json",
    "2024/25": ROOT / "data" / "season-2024-25.json",
}
ALL_MATCHDAYS = list(range(1, LAST_MATCHDAY + 1))

#: The grid. Deliberately small: every extra point is another chance for the
#: search to find noise, and a grid wide enough to guarantee an improvement on
#: the tuning season guarantees nothing at all on the other one.
NARROW = {
    "prior_strength": (6.0, 8.0, 10.0, 12.0, 16.0),
    "window": (3, 5, 8),
    "rotation_prior": (2.0, 4.0, 8.0),
}
WIDE = {
    "prior_strength": (4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 28.0),
    "window": (2, 3, 4, 5, 6, 8, 12),
    "rotation_prior": (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0),
}

#: The wide grid put its optimum on the boundary — window 2 and rotation prior
#: 1, the smallest of each — in both directions. An optimum at the edge is the
#: grid saying it is too small, not the model saying it has found something, so
#: this reaches past it until the curve turns.
#:
#: Extending after seeing that is legitimate: the boundary is visible on the
#: TUNING season alone, and the held-out season is still touched twice.
EDGE = {
    "prior_strength": (12.0, 20.0, 28.0, 40.0, 60.0, 90.0),
    "window": (1, 2, 3, 4),
    "rotation_prior": (0.25, 0.5, 1.0, 2.0),
}


def load(path: Path):
    loaded = json.loads(path.read_text(encoding="utf-8"))["players"]
    points, minutes, cells = {}, {}, {}
    for player_id, player in loaded.items():
        if not player["matches"]:
            continue
        points[player_id] = {m: ABSENT for m in ALL_MATCHDAYS}
        minutes[player_id] = {m: 0 for m in ALL_MATCHDAYS}
        clubs = defaultdict(int)
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
    return points, minutes, cells


def score(season, **kwargs) -> tuple[float, float]:
    """How well a parameter set predicts, by both measures that matter.

    Returns (correlation, mean absolute error) — and the ORDER matters, because
    the first is the one to optimise and the second is the one that is tempting.

    MEAN ERROR IS THE WRONG OBJECTIVE HERE, and it took being caught to see it.
    About half the market does not play in a given round, so an estimate that
    says -1 confidently about anyone absent last week is exactly right very
    often, and its mean error falls a long way. It is not better advice: an
    eleven is picked by RANKING players against each other, and a transfer by
    ranking a swap against no swap. Neither cares how close a number is in
    absolute terms.

    Tuned on mean error alone this search chose window 1 and a rotation prior
    of 0.25, which beat what is in use by 0.046 of error — and lost 0.007 of
    correlation on both seasons. That is a model made worse at its actual job
    while every number on the page said it had improved.
    """
    points, minutes, cells = season
    actual, guess = [], []
    for matchday in range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1):
        view = two_part_projection(points, minutes, cells, upto=matchday, **kwargs)
        for player_id, by_matchday in points.items():
            actual.append(by_matchday[matchday])
            guess.append(view[player_id])
    n = len(actual)
    if not n:
        return 0.0, float("inf")
    mean_a, mean_g = statistics.mean(actual), statistics.mean(guess)
    covariance = sum((a - mean_a) * (g - mean_g) for a, g in zip(actual, guess)) / n
    spread_a, spread_g = statistics.pstdev(actual), statistics.pstdev(guess)
    correlation = covariance / (spread_a * spread_g) if spread_a and spread_g else 0.0
    return correlation, sum(abs(a - g) for a, g in zip(actual, guess)) / n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wide", action="store_true")
    parser.add_argument("--edge", action="store_true", help="reach past the boundary")
    args = parser.parse_args()

    missing = [name for name, path in SEASONS.items() if not path.exists()]
    if missing:
        raise SystemExit(f"no reconstruction for {', '.join(missing)}")
    loaded = {name: load(path) for name, path in SEASONS.items()}

    grid = EDGE if args.edge else (WIDE if args.wide else NARROW)
    names = list(grid)
    combinations = list(itertools.product(*(grid[k] for k in names)))
    current = {
        "prior_strength": PRIOR_STRENGTH,
        "window": ROTATION_WINDOW,
        "rotation_prior": ROTATION_PRIOR,
    }
    print(f"{len(combinations)} combinations, two seasons")
    print(f"in use today: {current}")

    for tune_on, test_on in (("2025/26", "2024/25"), ("2024/25", "2025/26")):
        scored = [
            (score(loaded[tune_on], **dict(zip(names, values))), dict(zip(names, values)))
            for values in combinations
        ]
        # Sorted by correlation, descending. Not by error — see `score`.
        scored.sort(key=lambda row: -row[0][0])
        (best_r, best_mae), best = scored[0]

        # Everything below this line touches the held-out season exactly twice:
        # once for the winner, once for what is in use. Any more and the
        # held-out season stops being held out.
        out_r, out_mae = score(loaded[test_on], **best)
        cur_out_r, cur_out_mae = score(loaded[test_on], **current)
        cur_in_r, _ = score(loaded[tune_on], **current)

        print()
        print(f"  tuned on {tune_on}, reported on {test_on}")
        print(f"    best there:      {best}")
        print(f"      on {tune_on}:  r {best_r:.4f}   (in use: {cur_in_r:.4f})")
        print(
            f"      on {test_on}:  r {out_r:.4f}   (in use: {cur_out_r:.4f})"
            f"    error {out_mae:.4f} against {cur_out_mae:.4f}"
        )
        gain = out_r - cur_out_r
        print(
            f"    carried over:    {gain:+.4f} of correlation"
            + ("  — worth taking" if gain > 0.003 else "  — nothing that survives")
        )


if __name__ == "__main__":
    main()
