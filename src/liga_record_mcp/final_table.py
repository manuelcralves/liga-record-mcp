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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .models import FIRST_SCORING_MATCHDAY

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

#: How many times over this season's matches are counted against the archive's.
#: Provisionally 1 — face value — until the sweep across past seasons settles
#: it. See scripts/backtest_final_table.py, which is the only test this model
#: can have.
RECENT_WEIGHT = 1.0

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
    recent_weight: float = RECENT_WEIGHT,
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
            # Counted `recent_weight` times over. Four rounds against two full
            # seasons is four against sixty-eight, and at face value that is
            # almost no weight at all — while those four are the only matches
            # played by the squad that exists now. The multiplier is what says
            # how much a summer changes a club, and it is measured rather than
            # chosen: see scripts/backtest_final_table.py.
            played += row["played"] * recent_weight
            scored += row["goals_for"] * recent_weight
            conceded += row["goals_against"] * recent_weight

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


def everyone(
    table: Sequence[Mapping[str, Any]], remaining: Sequence[tuple[str, str]]
) -> list[str]:
    """Every club in the league, from both halves of what we are given.

    THE TABLE ALONE IS NOT THE LEAGUE. `stats.league_table` builds standings
    from PLAYED fixtures, so a club that has played none — the opening weeks,
    or a side whose first matches were all postponed — is simply absent from
    it, while the calendar still lists every game it will play.

    Reading the club list off the table alone left `play_out` inserting that
    club on its own and `distribution` raising KeyError against a row it had
    never allocated. Worse, it did so only on draws where the missing club won
    or drew, so the same command passed on one seed and died on another, and
    when it did not die it returned a seventeen-club table.

    Sorted, and not a set, because the order reaches the counts matrix and this
    project has already paid once for letting set iteration decide an answer.
    """
    known = {row["club"] for row in table}
    return sorted(known | {club for game in remaining for club in game})


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
    # Seeded for every club, so one that has played nothing starts at zero
    # rather than being conjured into the standings by its first simulated draw.
    clubs = everyone(table, remaining)
    points = {club: 0 for club in clubs}
    scored = {club: 0 for club in clubs}
    against = {club: 0 for club in clubs}
    for row in table:
        points[row["club"]] = row["points"]
        scored[row["club"]] = row["goals_for"]
        against[row["club"]] = row["goals_against"]

    for home, away in remaining:
        at_home, at_away = expected_goals(home, away, strength)
        goals_home, goals_away = _poisson(at_home, draw), _poisson(at_away, draw)
        scored[home] += goals_home
        scored[away] += goals_away
        against[home] += goals_away
        against[away] += goals_home
        if goals_home > goals_away:
            points[home] += 3
        elif goals_away > goals_home:
            points[away] += 3
        else:
            points[home] += 1
            points[away] += 1

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
    clubs = everyone(table, remaining)
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

    NORMALISED ONCE, AT THE TOP. The champion line used to compare raw slices
    while the line under it wrapped both sides in `list()` — so
    `score(tuple(order), actual)` quietly dropped sixty points, because
    `('Sporting',) == ['Sporting']` is false, while the top-four bonus beside it
    went on paying. Both parameters are declared `Sequence[str]`, so passing a
    tuple is not a misuse; and the `list()` next door proves the risk was known
    and then guarded in only one of the two places.

    Converting here rather than at each comparison means the next line added to
    this function cannot reintroduce it.
    """
    predicted = list(predicted)
    actual = list(actual)

    where = {club: place for place, club in enumerate(actual)}
    places = 0
    for place, club in enumerate(predicted):
        if club not in where:
            continue
        places += BY_DISTANCE.get(abs(place - where[club]), TOO_FAR)

    champion = CHAMPION_BONUS if predicted[:1] == actual[:1] else 0
    top_four = TOP_FOUR_BONUS if predicted[:TOP_FOUR] == actual[:TOP_FOUR] else 0
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


# --------------------------------------------------------------------------
# Chips
#
# From the lock to matchday 29 there is one chip a week, moving a single club
# up to three places. Three bonus chips move one up to five, at matchdays 18,
# 24 and 29, and stack with that week's ordinary chip. Every chip is played
# blind — it opens when the previous round ends and shuts when the next begins
# — and is lost if unused.
#
# So roughly ninety places of correction are available across a season, which
# is a great deal: the entry locked at matchday 5 is a starting position rather
# than an answer. The tiebreak runs the other way, rewarding fewer chips and
# fewer places moved, so a move worth almost nothing is worth not making.
# --------------------------------------------------------------------------

#: HOW CLOSE THE PERFECT TABLE IS, since the same entry plays for it. Chips are
#: blind — the one for matchday 29 opens when 28 ends — so the last information
#: anyone can act on is the table after matchday 28, and six rounds then play
#: out untouchable. Simulating fifty thousand seasons from that point:
#:
#:      season     already final   best guess   exact tables in 50,000
#:      2023-24       13 of 18      12 of 18            4
#:      2024-25        6 of 18       6 of 18            0
#:      2025-26        8 of 18       8 of 18            1
#:
#: So roughly one in twelve thousand in a settled season and worse in a loose
#: one, playing every chip perfectly all year to reach the best guess available.
#: A lottery ticket rather than a plan — which is why the policy above maximises
#: expected points and never reaches for the perfect table.
WEEKLY_REACH = 3
BONUS_REACH = 5
BONUS_ROUNDS = (18, 24, 29)
LAST_CHIP_ROUND = 29

#: Expected points a move must be worth before it is taken.
#:
#: MEASURED TWICE, because the first measurement was wrong in a way that
#: mattered and the second contradicts the correction as well as the original.
#:
#: The first table played each season with one Monte Carlo stream restarted at
#: every matchday — twenty-five chip decisions against twenty-five copies of
#: one sample. The error stopped cancelling across a season and accumulated
#: common-mode instead, inflating exactly the spread the constant was read off:
#: the argument was "the settings are within the noise, take the cheap one",
#: and the noise was manufactured. Varying the stream per matchday and drawing
#: once, greedy appeared to win by fifty. That was one draw, and it does not
#: survive either.
#:
#: `scripts/sweep_worth_moving.py`, four seasons x twelve seeds x 2500 draws,
#: every threshold scored against the SAME distributions so the comparison is
#: paired and the hundred-point season-to-season variance differences away.
#: n = 48 per setting:
#:
#:      threshold   mean   chips   places    vs 7.0   std err
#:      0.0          391    26.9     58.6      -2.6      13.0
#:      0.5          392    26.8     58.4      -2.2      13.0
#:      2.0          392    24.1     53.7      -1.9      13.0
#:      4.0          402    18.9     44.5      +8.5      12.3
#:      7.0          394    12.6     28.7       0.0         -
#:      10.0         394     9.0     20.6      +0.5       6.6
#:      15.0         366     5.6     12.7     -27.8       7.9
#:      20.0         308     3.7      8.1     -85.7      11.2
#:
#: ANYTHING FROM 0 TO 10 IS THE SAME. Not "close" — 4.0's +8.5 is two thirds of
#: a standard error, and greedy, which a single draw had winning by fifty, comes
#: out 2.6 BEHIND. Above about fifteen the policy is properly worse, and by a
#: margin far outside the error: at twenty it plays under four chips a season
#: and gives up eighty-six points for the privilege.
#:
#: So the choice inside [0, 10] is not a scoring choice, and the tiebreak decides
#: it: fewest chips, then fewest places moved. Seven spends 12.6 chips where
#: greedy spends 26.9 for the same points. That is the original argument, and it
#: survives — but NOT its stated reason. It claimed a lower threshold "burns
#: chips", as though a chip kept were a chip banked; chips do not bank, the
#: block above says each is lost if unused. Saving one buys nothing. What a
#: threshold actually does is refuse to act on a difference that is sampling
#: error and then spend next week's chip undoing the move, which is why the
#: floor of the safe band moves with the number of seasons drawn.
#:
#: Seven, unchanged — chosen this time because it sits in the middle of the band
#: that cannot be told apart, not at an edge where the next measurement moves it.
WORTH_MOVING = 7.0


def moved(order: Sequence[str], club: str, to: int) -> list[str]:
    """The order with one club lifted out and put back at `to`."""
    rest = [c for c in order if c != club]
    return rest[:to] + [club] + rest[to:]


def expected(order: Sequence[str], spread: Mapping[str, list[float]]) -> float:
    """What an entry is worth against a distribution, bonuses included.

    The top-four term is the joint probability of all four being exactly right,
    which is the only part `value_of` cannot carry on its own.
    """
    size = len(order)
    total = sum(value_of(club, place, spread, size) for place, club in enumerate(order))
    exact = 1.0
    for place, club in enumerate(order[:TOP_FOUR]):
        exact *= spread[club][place]
    return total + TOP_FOUR_BONUS * exact


def best_chip(
    order: Sequence[str],
    spread: Mapping[str, list[float]],
    *,
    reach: int,
    threshold: float = WORTH_MOVING,
) -> tuple[list[str], str | None, int]:
    """The move worth making this week, if any is.

    Returns the new order, the club moved, and how many places it travelled.
    Every legal move is priced — eighteen clubs by at most `reach` either way
    is small enough to enumerate, so there is no need to be clever and no room
    for a heuristic to be wrong.
    """
    here = expected(order, spread)
    best, who, distance = list(order), None, 0
    for place, club in enumerate(order):
        for to in range(max(0, place - reach), min(len(order), place + reach + 1)):
            if to == place:
                continue
            candidate = moved(order, club, to)
            gain = expected(candidate, spread) - here
            if gain > threshold and gain > expected(best, spread) - here:
                best, who, distance = candidate, club, abs(to - place)
    return best, who, distance


def reaches_at(matchday: int) -> list[int]:
    """The chips playable at a matchday, in the order they should be played.

    None before the entry locks — there is nothing to correct yet, because the
    entry is still being written. One a week after that until matchday 29, and
    at three of those weeks a bonus chip on top, which stacks with the ordinary
    one. Nothing after 29: the last six rounds play out untouchable.

    The weekly chip is listed first deliberately. Two chips in one week are two
    sequential decisions, not one combined move, and the second is priced
    against the order the first leaves behind.
    """
    if matchday <= FIRST_SCORING_MATCHDAY or matchday > LAST_CHIP_ROUND:
        return []
    reaches = [WEEKLY_REACH]
    if matchday in BONUS_ROUNDS:
        reaches.append(BONUS_REACH)
    return reaches


def chip_plan(
    order: Sequence[str],
    spread: Mapping[str, list[float]],
    matchday: int,
    *,
    threshold: float = WORTH_MOVING,
) -> tuple[list[str], list[dict]]:
    """Every chip available this matchday, played against the distribution.

    THE POLICY LIVES HERE so that the thing measured is the thing run. The
    backtest used to carry its own copy of this loop, which meant the score it
    reported belonged to a policy nothing in production executed — and for a
    while nothing in production executed any policy at all: `best_chip` was
    reachable only from the backtest, while the chips are worth roughly 280 of
    the model's 380 points.

    Returns the order after playing them and one entry per chip, including the
    chips deliberately not spent — a week where nothing clears the threshold is
    a decision and the page should say so rather than fall silent.
    """
    current = list(order)
    plays: list[dict] = []
    for reach in reaches_at(matchday):
        before = current
        current, who, distance = best_chip(
            current, spread, reach=reach, threshold=threshold
        )
        gain = expected(current, spread) - expected(before, spread)
        plays.append(
            {
                "reach": reach,
                "bonus": reach != WEEKLY_REACH,
                "club": who,
                "from": before.index(who) + 1 if who else None,
                "to": current.index(who) + 1 if who else None,
                "places": distance,
                "gain": gain,
            }
        )
    return current, plays


def apply_chips(entry: Sequence[str], chips: Iterable[Mapping]) -> list[str]:
    """The order as it stands now: the submitted entry with the chips played.

    THE ENTRY IS STATE. It is written once, at the lock, and then twenty-five
    weeks of chips move it — so the current order cannot be recomputed from
    today's model, only replayed from what was actually submitted. Recomputing
    it would quietly show Manuel an order he never entered, and price next
    week's chip against a position he is not in.

    Each chip names a club and the place it was moved to, one-based to match
    what the site shows.
    """
    order = list(entry)
    for chip in chips:
        club = chip.get("clube") or chip.get("club")
        to = chip.get("para") or chip.get("to")
        if club is None or to is None or club not in order:
            continue
        order = moved(order, club, int(to) - 1)
    return order
