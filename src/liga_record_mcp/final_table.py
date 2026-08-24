"""Predicting where the eighteen finish, and what order to write them in.

A DIFFERENT PROBLEM FROM THE REST OF THIS PROJECT. Everything else estimates
players; this estimates clubs, and then does something no other part does — it
picks an ANSWER rather than a ranking, under a scoring table that is not linear
in how wrong you are.

    exact  +25      one out  +5      two out  +2
    three out  0        four or more  -5

    champion  +60      each directly relegated club  +25
    the top four complete and in order  +40

That table changes the answer. Ordering clubs by their most likely finish is
not optimal: a club whose distribution is flat is worth putting where it can
still land within two of somewhere, while +25 for an exact hit rewards
conviction about the clubs whose distributions are sharp. So the order is
chosen by assignment against the whole distribution, not by sorting means.

WHAT IT KNOWS. Two completed seasons of real results from openfootball, the
current table, and the remaining fixtures. It plays the rest of the season out
many times and counts where each club lands.

Nothing here is fitted to the game's history — nobody has a record of past
Final Table entries — so this is reasoned rather than measured, and says so.
Unlike the player model, there is no backtest that could settle it: one season
produces one final table.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

#: Points for being this many places out, and beyond that a flat penalty.
BY_DISTANCE = {0: 25, 1: 5, 2: 2, 3: 0}
TOO_FAR = -5

CHAMPION_BONUS = 60
RELEGATION_BONUS = 25
TOP_FOUR_BONUS = 40

#: Portugal sends two down directly and plays the third from bottom off. The
#: bonus reads "despromovida (diretamente)", so it is these two.
RELEGATION_PLACES = 2
TOP_FOUR = 4

#: How much a club's own record counts against the league average before it is
#: believed. Matched to PRIOR_STRENGTH's role in the player model: enough that
#: three rounds cannot make a champion of anyone.
CLUB_PRIOR_MATCHES = 12.0

#: Goals in the Primeira Liga, per club per match, either way. Used as the
#: prior mean and as the fallback for a club with no record at all.
LEAGUE_GOALS = 1.35

#: Home advantage as a multiplier on expected goals, applied to the home side
#: and its reciprocal-ish counterpart to the away side. Matches the factors the
#: player model already uses for the same purpose.
HOME_FACTOR = 1.10
AWAY_FACTOR = 0.91


#: GOALS AND NOT RESULTS, and that choice decides the champion.
#:
#: Across the two archived seasons Sporting and FC Porto have FIFTY WINS EACH,
#: and Sporting scores 2.60 a game against Porto's 1.93. Porto took the title by
#: winning the tight ones. A model reading results would keep them there; this
#: one reads goals and puts Sporting first.
#:
#: Measured on the fifteen clubs present in both seasons, using one season to
#: predict the next:
#:
#:                                 predicts wins   predicts goal difference
#:      goal difference                  0.882              0.907
#:      wins                             0.836              0.862
#:
#: Goals win on both targets. Fifteen clubs and one pair of seasons is a small
#: sample and the effect is not large, but it points the same way twice and
#: agrees with the settled finding in the wider game. It is recorded here
#: because the whole prediction turns on it.
def strengths(
    records: Mapping[str, Any],
    table: Sequence[Mapping[str, Any]],
    *,
    prior_matches: float = CLUB_PRIOR_MATCHES,
) -> dict[str, tuple[float, float]]:
    """Attack and defence for each club, as goals per match relative to nobody.

    Two sources, and they disagree on purpose. The archive is two full seasons
    — a large sample describing a squad that has since changed. This season is
    three rounds — the right squad, and far too little of it. Both go into the
    same shrinkage, which is what makes the weight between them a measurement
    of sample size rather than an opinion.
    """
    out: dict[str, tuple[float, float]] = {}
    current = {row["club"]: row for row in table}

    for club in set(records) | set(current):
        record = records.get(club)
        played = getattr(record, "matches", 0) or 0
        scored = getattr(record, "goals_for", 0) or 0
        conceded = getattr(record, "goals_against", 0) or 0

        row = current.get(club)
        if row:
            played += row["played"]
            scored += row["goals_for"]
            conceded += row["goals_against"]

        attack = (scored + LEAGUE_GOALS * prior_matches) / (played + prior_matches)
        defence = (conceded + LEAGUE_GOALS * prior_matches) / (played + prior_matches)
        out[club] = (attack, defence)
    return out


def expected_goals(
    home: str, away: str, strength: Mapping[str, tuple[float, float]]
) -> tuple[float, float]:
    """What each side is expected to score, before any is drawn.

    A club's attack meets the other's defence, both already expressed as goals
    per match, so the pair is divided by the league level to keep the product
    in goals rather than in goals squared.
    """
    home_attack, home_defence = strength.get(home, (LEAGUE_GOALS, LEAGUE_GOALS))
    away_attack, away_defence = strength.get(away, (LEAGUE_GOALS, LEAGUE_GOALS))
    return (
        home_attack * away_defence / LEAGUE_GOALS * HOME_FACTOR,
        away_attack * home_defence / LEAGUE_GOALS * AWAY_FACTOR,
    )


def _poisson(mean: float, draw: random.Random) -> int:
    """Knuth. Fine at these means, and it keeps the dependency list empty."""
    limit, count, product = math.exp(-mean), 0, 1.0
    while True:
        product *= draw.random()
        if product <= limit:
            return count
        count += 1


def play_out(
    table: Sequence[Mapping[str, Any]],
    remaining: Sequence[tuple[str, str]],
    strength: Mapping[str, tuple[float, float]],
    draw: random.Random,
) -> list[str]:
    """One possible rest-of-season, and the final table it produces.

    Ranked on points, then goal difference, then goals scored. The real rule
    puts head-to-head before goal difference in Portugal, which cannot be
    applied to a simulated season without simulating who beat whom — it is
    simulated here, but the difference only decides clubs level on points, and
    a tie broken the wrong way moves two clubs by one place.
    """
    points = {row["club"]: row["points"] for row in table}
    scored = {row["club"]: row["goals_for"] for row in table}
    against = {row["club"]: row["goals_against"] for row in table}

    for home, away in remaining:
        at_home, at_away = expected_goals(home, away, strength)
        goals_home, goals_away = _poisson(at_home, draw), _poisson(at_away, draw)
        scored[home] = scored.get(home, 0) + goals_home
        scored[away] = scored.get(away, 0) + goals_away
        against[home] = against.get(home, 0) + goals_away
        against[away] = against.get(away, 0) + goals_home
        if goals_home > goals_away:
            points[home] = points.get(home, 0) + 3
        elif goals_away > goals_home:
            points[away] = points.get(away, 0) + 3
        else:
            points[home] = points.get(home, 0) + 1
            points[away] = points.get(away, 0) + 1

    return sorted(
        points,
        key=lambda c: (-points[c], -(scored[c] - against[c]), -scored[c], c),
    )


def distribution(
    table: Sequence[Mapping[str, Any]],
    remaining: Sequence[tuple[str, str]],
    strength: Mapping[str, tuple[float, float]],
    *,
    draws: int = 4000,
    seed: int = 0,
) -> dict[str, list[float]]:
    """How often each club lands in each place, over many played-out seasons.

    Seeded, because a recommendation that changes between runs is not a
    recommendation — the same lesson the squad search cost this project a
    hundred and thirty-six points to learn.
    """
    clubs = [row["club"] for row in table]
    counts = {club: [0] * len(clubs) for club in clubs}
    draw = random.Random(seed)
    for _ in range(draws):
        for place, club in enumerate(play_out(table, remaining, strength, draw)):
            counts[club][place] += 1
    return {club: [n / draws for n in row] for club, row in counts.items()}


def score(predicted: Sequence[str], actual: Sequence[str]) -> dict[str, int]:
    """The game's own scoring, applied to a finished season.

    Relegation is read as SET MEMBERSHIP rather than exact place: a club
    predicted 17th that finishes 18th was still correctly predicted to go down,
    and the rule says "each directly relegated club correctly predicted"
    rather than naming a position. The places themselves are already scored by
    the distance table, so reading it the other way would pay twice for the
    same thing.
    """
    where = {club: place for place, club in enumerate(actual)}
    places = 0
    for place, club in enumerate(predicted):
        if club not in where:
            continue
        places += BY_DISTANCE.get(abs(place - where[club]), TOO_FAR)

    champion = CHAMPION_BONUS if predicted[:1] == actual[:1] else 0
    top_four = TOP_FOUR_BONUS if list(predicted[:TOP_FOUR]) == list(actual[:TOP_FOUR]) else 0
    down = set(actual[-RELEGATION_PLACES:]) & set(predicted[-RELEGATION_PLACES:])
    return {
        "places": places,
        "champion": champion,
        "relegation": RELEGATION_BONUS * len(down),
        "top_four": top_four,
        "total": places + champion + RELEGATION_BONUS * len(down) + top_four,
    }


def value_of(club: str, place: int, spread: Mapping[str, list[float]], size: int) -> float:
    """What writing this club in this place is expected to be worth.

    The distance table plus the two bonuses that depend on one club alone. The
    top-four bonus needs all four right at once, so it cannot be priced per
    cell and is left to the search that follows.
    """
    odds = spread[club]
    total = sum(
        p * BY_DISTANCE.get(abs(place - actual), TOO_FAR)
        for actual, p in enumerate(odds)
        if p
    )
    if place == 0:
        total += CHAMPION_BONUS * odds[0]
    if place >= size - RELEGATION_PLACES:
        total += RELEGATION_BONUS * sum(odds[size - RELEGATION_PLACES :])
    return total


def _hungarian(value: list[list[float]]) -> list[int]:
    """The assignment maximising the total, exactly. Jonker-Volgenant style.

    Eighteen clubs into eighteen places is small enough to solve outright, and
    outright is worth having: a greedy order looks reasonable and leaves points
    on the table in exactly the cases the scoring table was designed to punish.

    Written out rather than imported because this project has no numeric
    dependencies, and adding scipy to place eighteen football clubs would be a
    poor trade.
    """
    n = len(value)
    cost = [[-v for v in row] for row in value]  # maximise by minimising
    INF = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (n + 1)
    p, way = [0] * (n + 1), [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv, used = [INF] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0], j0 = p[j1], j1

    order = [0] * n
    for j in range(1, n + 1):
        order[p[j] - 1] = j - 1
    return order


def best_order(
    spread: Mapping[str, list[float]], *, clubs: Sequence[str] | None = None
) -> list[str]:
    """The order to write down, chosen against the whole distribution.

    NOT a sort by most likely finish, and the difference is the point. +25 for
    an exact hit rewards conviction where a club's distribution is sharp; -5
    beyond three places punishes it where the distribution is flat. Sorting by
    the mean ignores both and answers a question nobody asked.

    The top-four bonus is the one term that cannot be priced cell by cell — it
    needs four clubs right at once — so the assignment is solved without it and
    then offered the twenty-four orderings of its own top four, keeping the
    best. That is exact over the part it can reach and honest about the rest.
    """
    names = list(clubs if clubs is not None else spread)
    size = len(names)
    value = [[value_of(club, place, spread, size) for place in range(size)] for club in names]
    assigned = _hungarian(value)

    order = [""] * size
    for index, place in enumerate(assigned):
        order[place] = names[index]

    # The top-four bonus, added back by trying every arrangement of the four
    # the assignment already chose. Reordering them cannot change which clubs
    # are in the top four, so nothing below place four moves.
    from itertools import permutations

    def worth(candidate: Sequence[str]) -> float:
        total = sum(
            value_of(club, place, spread, size) for place, club in enumerate(candidate)
        )
        exact = 1.0
        for place, club in enumerate(candidate[:TOP_FOUR]):
            exact *= spread[club][place]
        return total + TOP_FOUR_BONUS * exact

    best = max(
        (list(head) + order[TOP_FOUR:] for head in permutations(order[:TOP_FOUR])),
        key=worth,
    )
    return best
