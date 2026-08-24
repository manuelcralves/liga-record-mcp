"""The order to write down for zerozero's Final Table, and why that order.

THE GAME. Rank the eighteen from first to last before matchday 5 — the same
deadline that closes the fantasy squad. After that the entry is fixed, and only
chips move it: one a week to shift a club up to three places, plus three bonus
chips worth five places at matchdays 18, 24 and 29. Each chip is played blind,
opens when the previous round ends, and is lost if unused.

    exact  +25    one out  +5    two out  +2    three out  0    four+  -5
    champion  +60    each club correctly sent down  +25    top four exact  +40

WHY THIS IS NOT A SORT. The obvious answer is to order clubs by where they are
most likely to finish. It is wrong, and the scoring table is what makes it
wrong: +25 for an exact hit pays for conviction where a club's distribution is
sharp, and -5 beyond three places charges for it where the distribution is
flat. Two clubs with the same average finish are not worth the same place.

So the season is played out thousands of times, every club gets a probability
for every place, and the order is chosen by solving the assignment that
maximises expected points against that whole matrix.

    python scripts/predict_final_table.py
    python scripts/predict_final_table.py --draws 8000   # steadier, slower

WHAT THIS CANNOT TELL YOU. Nobody has a record of past Final Table entries, so
unlike everything else in this project there is no backtest that could settle
whether the method works — one season produces one final table. The numbers
below are reasoned from real results; the approach is not measured.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp import server as mcp  # noqa: E402
from liga_record_mcp.final_table import (  # noqa: E402
    RELEGATION_PLACES,
    TOP_FOUR,
    best_order,
    distribution,
    strengths,
    value_of,
)
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, OpenFootballClient  # noqa: E402


def league_table(attempts: int = 4) -> list[dict]:
    """The current table, retried — the ranking service drops requests."""
    for attempt in range(attempts):
        found = mcp.primeira_liga()
        if found.get("table"):
            return found["table"]
        if attempt + 1 < attempts:
            time.sleep(3)
    raise SystemExit("the league table came back empty every time")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    table = league_table()
    market = LigaRecordClient(timeout=60.0)
    remaining = [(f.home, f.away) for f in market.fixtures() if not f.played]
    strength = strengths(OpenFootballClient(timeout=60.0).club_records(), table)

    spread = distribution(table, remaining, strength, draws=args.draws, seed=args.seed)
    clubs = [row["club"] for row in table]
    order = best_order(spread, clubs=clubs)
    size = len(clubs)
    now = {row["club"]: row["position"] for row in table}

    played = table[0]["played"] if table else 0
    print(f"{len(remaining)} matches left, {args.draws} seasons played out")
    print(f"the entry locks at matchday {FIRST_SCORING_MATCHDAY}")
    print()
    print(f"  {'#':>3}  {'club':<15}{'exact':>7}{'top4':>7}{'down':>7}{'worth':>8}  now")
    for place, club in enumerate(order):
        odds = spread[club]
        exact = odds[place]
        top_four = sum(odds[:TOP_FOUR])
        down = sum(odds[size - RELEGATION_PLACES :])
        worth = value_of(club, place, spread, size)
        moved = now[club] - (place + 1)
        arrow = "" if moved == 0 else f"  {now[club]}{'↑' if moved > 0 else '↓'}"
        print(
            f"  {place + 1:>3}  {club:<15}{exact:>6.0%}{top_four:>7.0%}"
            f"{down:>7.0%}{worth:>8.1f}{arrow}"
        )

    total = sum(value_of(c, i, spread, size) for i, c in enumerate(order))
    print()
    print(f"  expected {total:.0f} points from the places and the single-club bonuses")
    print(f"  (the top-four bonus needs all four at once and is not in that figure)")
    print()
    print(
        f"  Only {played} rounds have been played, so this leans almost entirely on "
        "two completed"
    )
    print(
        "  seasons of real results. A club that rebuilt over the summer will look "
        "like the club"
    )
    print("  it was, and nothing here can see that.")


if __name__ == "__main__":
    main()
