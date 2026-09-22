"""When to spend §6.17's holiday rounds.

Three a season, never in the last three. A holiday round pays half the round
winner's score, rounded up, whatever the team does — so it is worth taking in
a week the team is expected to score less than that, and in no other. The
payout is much the same every week; what moves is the team: a squad with three
starters out, or a run of hard away games, is the week the floor is above it.

AN UNUSED HOLIDAY COSTS NOTHING, and one spent on an ordinary week costs
points. So the rule never spends one to avoid wasting it: near the end the bar
comes down to zero, never below.

THE BAR IS THE VALUE OF WAITING. With k holidays left and n rounds in which
they may still be used, taking one now gives up the chance to take it in a
better week later. If the weekly gain — payout less the team's expected score —
is drawn from a normal distribution, the value of k holidays over n rounds
follows by backward induction:

    V(k, n) = E[ max(g + V(k - 1, n - 1), V(k, n - 1)) ],   V(0, n) = V(k, 0) = 0

and a holiday is worth taking now exactly when the gain beats
V(k, n - 1) - V(k - 1, n - 1). That difference is never negative, and it falls
as the season runs out.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from functools import lru_cache

from .models import HOLIDAY_BLOCKED_LAST_ROUNDS, HOLIDAY_ROUNDS, LAST_MATCHDAY

#: The last round a holiday may be taken in: §6.17 closes the last three.
LAST_HOLIDAY_ROUND = LAST_MATCHDAY - HOLIDAY_BLOCKED_LAST_ROUNDS

#: How much a squad's expected round moves from one week to the next — the
#: spread of the gains still to come, which is what waiting is worth.
#:
#: Measured on 22/09/2026 by `scripts/measure_holiday_timing.py`, 64 paths of
#: each reconstructed season, rounds 6 to 31, the sheet played out by
#: `squad_value` on each round's fixture-adjusted values:
#:
#:                                  2025/26   2024/25
#:     knowing nothing of absences    3.55      3.27
#:     knowing who did not play       3.90      4.43
#:
#: The page knows the bulletin — injuries and bans, not rotation — which sits
#: between the two readings, so this is their middle.
#:
#: The same measurement is why the rule is on the page at all. Against
#: spending the three at random, the weeks it chose scored 11.8 and 9.1 points
#: below their path's average week knowing nothing of absences (13.6 and 18.3
#: knowing them), with standard errors under 2; on this season's payout that is
#: +7 to +9 a season, on fewer than two holidays.
WEEKLY_SPREAD = 3.8


def _above(mean: float, spread: float, bar: float) -> float:
    """E[max(g - bar, 0)] for g ~ N(mean, spread²)."""
    if spread <= 0:
        return max(mean - bar, 0.0)
    z = (mean - bar) / spread
    density = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    tail = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return (mean - bar) * tail + spread * density


@lru_cache(maxsize=None)
def season_value(chips: int, rounds: int, mean: float, spread: float) -> float:
    """V(k, n): what k holidays over n rounds are worth, spent well."""
    if chips <= 0 or rounds <= 0:
        return 0.0
    keep = season_value(chips, rounds - 1, mean, spread)
    spend = season_value(chips - 1, rounds - 1, mean, spread)
    # max(g + spend, keep) = keep + max(g - (keep - spend), 0)
    return keep + _above(mean, spread, keep - spend)


def holiday_bar(chips: int, rounds: int, mean: float, spread: float) -> float:
    """The gain this week has to beat for a holiday to be worth spending on it.

    `rounds` counts this week. `mean` and `spread` describe the gain in a week
    ahead: the payout less the team's expected score, and how much that
    expected score moves from week to week.
    """
    if chips <= 0 or rounds <= 0:
        return math.inf
    return season_value(chips, rounds - 1, mean, spread) - season_value(
        chips - 1, rounds - 1, mean, spread
    )


def holiday_advice(
    *,
    expected_score: float,
    expected_payout: float,
    typical_score: float,
    spread: float,
    used: Iterable[int],
    round_number: int,
) -> dict:
    """Spend one of §6.17's holidays this round, keep it, or say why not.

    `expected_score` is what the team is expected to make this round, and
    `typical_score` what it is expected to make in an ordinary one — the gap
    between them is what a holiday is for. `expected_payout` is half the round
    winner's score as the season has run so far.
    """
    used = sorted(set(used))
    left = HOLIDAY_ROUNDS - len(used)
    base = {
        "round": round_number,
        "left": max(left, 0),
        "used": used,
        "expected_score": expected_score,
        "expected_payout": expected_payout,
        "gain": expected_payout - expected_score,
        "bar": None,
    }
    if round_number > LAST_HOLIDAY_ROUND:
        return {**base, "verdict": "fora"}
    if left <= 0:
        return {**base, "verdict": "gastas"}
    rounds = LAST_HOLIDAY_ROUND - round_number + 1
    bar = holiday_bar(left, rounds, expected_payout - typical_score, spread)
    verdict = "usa" if base["gain"] > bar else "guarda"
    return {**base, "bar": bar, "rounds": rounds, "verdict": verdict}
