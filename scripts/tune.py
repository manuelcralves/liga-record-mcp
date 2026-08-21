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


def error(season, **kwargs) -> float:
    """Mean absolute error predicting each round from the ones before it."""
    points, minutes, cells = season
    total, n = 0.0, 0
    for matchday in range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1):
        view = two_part_projection(points, minutes, cells, upto=matchday, **kwargs)
        for player_id, by_matchday in points.items():
            total += abs(view[player_id] - by_matchday[matchday])
            n += 1
    return total / n if n else float("inf")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wide", action="store_true")
    args = parser.parse_args()

    missing = [name for name, path in SEASONS.items() if not path.exists()]
    if missing:
        raise SystemExit(f"no reconstruction for {', '.join(missing)}")
    loaded = {name: load(path) for name, path in SEASONS.items()}

    grid = WIDE if args.wide else NARROW
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
        scored = []
        for values in combinations:
            kwargs = dict(zip(names, values))
            scored.append((error(loaded[tune_on], **kwargs), kwargs))
        scored.sort(key=lambda row: row[0])
        best_error, best = scored[0]
        worst_error = scored[-1][0]

        # Everything below this line touches the held-out season exactly twice:
        # once for the winner, once for what is in use. Any more and the
        # held-out season stops being held out.
        winner_out = error(loaded[test_on], **best)
        current_out = error(loaded[test_on], **current)
        current_in = error(loaded[tune_on], **current)

        print()
        print(f"  tuned on {tune_on}, reported on {test_on}")
        print(f"    the grid on {tune_on} spans {best_error:.4f} to {worst_error:.4f}")
        print(f"    best there:      {best}")
        print(f"      on {tune_on}:  {best_error:.4f}   (in use: {current_in:.4f})")
        print(f"      on {test_on}:  {winner_out:.4f}   (in use: {current_out:.4f})")
        gain = current_out - winner_out
        print(
            f"    carried over:    {gain:+.4f}"
            + ("  — worth taking" if gain > 0.005 else "  — nothing that survives")
        )


if __name__ == "__main__":
    main()
