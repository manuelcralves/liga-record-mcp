"""Search the fixture adjustment's position weights, without fooling ourselves.

WHY THESE FOUR NUMBERS. `adjust_for_fixture` rescales what a player returns by
`share * defensive + (1 - share) * attacking`, where share is how much of his
return comes from keeping the ball out rather than putting it in. The four
values in use — 1.00, 0.85, 0.50, 0.10 — were set by eye, and a ridge handed
the same three inputs got more than twice as much out of them. Half of that gap
turned out to be APPEARANCE_FLOOR, retuned from 2.0 to 1.0 and worth +0.010.

THE OTHER HALF IS NOT THERE, and this script is how that gets checked rather
than argued. The finding is written beside DEFENSIVE_SHARE in stats.py and this
reproduces it: the curves are nearly flat, the two seasons disagree about
forwards by more than the whole effect, and nothing carries past tune.py's bar.
Kept because the constants carry measured numbers that nothing could re-run —
a recorded result with no way to reproduce it is a claim, not a measurement.

COORDINATE-WISE, NOT A GRID, and that is the whole discipline. Four parameters
at five values each is 625 combinations, and the best of 625 on one season is
mostly the luckiest — the search creates the luck it then reports. Sweeping one
position at a time, holding the rest, is twenty evaluations instead. Fewer
chances to find noise, and the curve per position is readable: an optimum in
the middle of its range is a measurement, one at the edge is the range saying
it was too narrow.

THE PROTOCOL, the same one tune.py follows:

    1. every value is scored on the TUNING season
    2. the best per position is taken, and nothing else about the search is used
    3. that one set is scored on the HELD-OUT season, once
    4. what it does there is the honest number

Run both directions. A gain that appears tuning on 2025/26 and vanishes tuning
on 2024/25 was never there.

    python scripts/tune_fixture.py
    python scripts/tune_fixture.py --fine     # a denser sweep, slower
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

sys.path[:0] = [str(ROOT / "scripts")]
from measure_projection_accuracy import load  # noqa: E402

from liga_record_mcp.backtest import adjusted_projection  # noqa: E402
from liga_record_mcp.models import (  # noqa: E402
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    Position,
)
from liga_record_mcp.stats import DEFENSIVE_SHARE  # noqa: E402

SEASONS = {
    "2025/26": (ROOT / "data" / "last-season.json", ROOT / "data" / "season-2024-25.json"),
    "2024/25": (ROOT / "data" / "season-2024-25.json", None),
}

#: Ranges chosen from what the position is, not from the number in use — a grid
#: centred on the current value can only confirm it. A keeper's return is almost
#: all clean sheet and rating; a forward's is almost all goals.
COARSE = {
    Position.GK: (0.6, 0.75, 0.85, 1.0),
    Position.DEF: (0.4, 0.55, 0.7, 0.85, 1.0),
    Position.MID: (0.1, 0.25, 0.4, 0.55, 0.7),
    Position.FWD: (0.0, 0.1, 0.25, 0.4, 0.55),
}
#: The coarse sweep put GK and DEF on its bottom edge in BOTH directions, which
#: is the range saying it was too narrow rather than the model saying it found
#: something. This reaches below it until the curve turns.
#:
#: Extending after seeing that is legitimate — the edge is visible on the tuning
#: season alone — and the held-out season is still touched twice per direction.
WIDER = {
    Position.GK: (0.0, 0.15, 0.3, 0.45, 0.6, 0.8),
    Position.DEF: (0.0, 0.1, 0.2, 0.3, 0.4, 0.55),
    Position.MID: (0.0, 0.1, 0.25, 0.4, 0.55),
    Position.FWD: (0.0, 0.1, 0.25, 0.4, 0.55),
}
FINE = {
    Position.GK: (0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    Position.DEF: (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    Position.MID: (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7),
    Position.FWD: (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5),
}


def correlation(season, shares) -> float:
    """How well this set of weights ranks players, over a whole season.

    Correlation and not mean error, for the reason tune.py records at length:
    an eleven is picked by ranking players against each other, and about half
    the market does not play in a given round, so a confident -1 about everyone
    absent buys a great deal of error and no better advice at all.
    """
    points, minutes, cells, _, table, cards = season
    actual, guess = [], []
    for matchday in range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1):
        view = adjusted_projection(
            points,
            minutes,
            cells,
            upto=matchday,
            fixtures=table,
            cards=cards,
            shares=shares,
        )
        for player_id, by_matchday in points.items():
            if matchday in by_matchday:
                actual.append(by_matchday[matchday])
                guess.append(view[player_id])
    n = len(actual)
    mean_a, mean_g = statistics.mean(actual), statistics.mean(guess)
    covariance = sum((a - mean_a) * (g - mean_g) for a, g in zip(actual, guess)) / n
    spread_a, spread_g = statistics.pstdev(actual), statistics.pstdev(guess)
    return covariance / (spread_a * spread_g) if spread_a and spread_g else 0.0


def sweep(season, grid, current) -> tuple[dict, list[str]]:
    """One position at a time, holding the others where they are."""
    best = dict(current)
    lines = []
    for position, values in grid.items():
        scored = [(correlation(season, {**best, position: v}), v) for v in values]
        top = max(scored)[1]
        shape = "  ".join(
            f"{v:.2f}:{r:.4f}" + ("*" if v == top else " ") for r, v in
            sorted(scored, key=lambda rv: rv[1])
        )
        edge = top in (min(values), max(values))
        lines.append(
            f"    {position.value:<4} {shape}"
            + ("   <- no limite do intervalo" if edge else "")
        )
        best[position] = top
    return best, lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fine", action="store_true")
    parser.add_argument("--wider", action="store_true", help="reach past the edge")
    args = parser.parse_args()

    grid = WIDER if args.wider else (FINE if args.fine else COARSE)
    loaded = {}
    for name, (path, archive) in SEASONS.items():
        if not path.exists():
            raise SystemExit(f"no reconstruction at {path}")
        loaded[name] = load(path, archive if archive and archive.exists() else None)

    current = dict(DEFENSIVE_SHARE)
    print(f"em uso hoje: " + ", ".join(f"{p.value} {v:.2f}" for p, v in current.items()))
    print(f"{sum(len(v) for v in grid.values())} avaliações por época, uma posição de cada vez")

    for tune_on, test_on in (("2025/26", "2024/25"), ("2024/25", "2025/26")):
        found, lines = sweep(loaded[tune_on], grid, current)

        # Below this line the held-out season is touched exactly twice: once for
        # what the search found, once for what is in use. Any more and it stops
        # being held out.
        out_found = correlation(loaded[test_on], found)
        out_current = correlation(loaded[test_on], current)
        in_current = correlation(loaded[tune_on], current)
        in_found = correlation(loaded[tune_on], found)

        print()
        print(f"  afinado em {tune_on}, reportado em {test_on}")
        print("\n".join(lines))
        print(f"    encontrado:  " + ", ".join(f"{p.value} {v:.2f}" for p, v in found.items()))
        print(f"      em {tune_on}:  r {in_found:.4f}   (em uso: {in_current:.4f})")
        print(f"      em {test_on}:  r {out_found:.4f}   (em uso: {out_current:.4f})")
        gain = out_found - out_current
        print(
            f"    transporta:  {gain:+.4f} de correlação"
            + ("  — vale a pena" if gain > 0.003 else "  — nada que sobreviva")
        )


if __name__ == "__main__":
    main()
