"""Predicting the final eighteen, and the claim that the order is not a sort.

The interesting test in here is the last one. Everything else checks arithmetic;
that one checks the reason the module exists — that under a scoring table which
pays +25 for an exact hit and -5 for missing by four, the best order is not the
one you get by sorting clubs by where they will probably finish.
"""

from __future__ import annotations

import random

import pytest

from liga_record_mcp.final_table import (
    CHAMPION_BONUS,
    LEAGUE_GOALS,
    RELEGATION_BONUS,
    TOP_FOUR_BONUS,
    _hungarian,
    best_order,
    distribution,
    everyone,
    expected_goals,
    play_out,
    score,
    strengths,
    value_of,
)


class Record:
    def __init__(self, matches, goals_for, goals_against):
        self.matches = matches
        self.goals_for = goals_for
        self.goals_against = goals_against


def row(club, *, played=0, points=0, goals_for=0, goals_against=0, position=1):
    return {
        "club": club,
        "played": played,
        "points": points,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "position": position,
    }


# --- the game's own scoring ---------------------------------------------------


ACTUAL = [f"c{i}" for i in range(1, 19)]


def test_a_perfect_entry_collects_everything():
    got = score(ACTUAL, ACTUAL)
    assert got["places"] == 18 * 25
    assert got["champion"] == CHAMPION_BONUS
    assert got["relegation"] == 2 * RELEGATION_BONUS
    assert got["top_four"] == TOP_FOUR_BONUS


@pytest.mark.parametrize("distance,points", [(0, 25), (1, 5), (2, 2), (3, 0), (4, -5), (9, -5)])
def test_the_distance_table(distance, points):
    """One club moved, the rest held, so the difference is that club alone."""
    moved = list(ACTUAL)
    if distance:
        club = moved.pop(8)
        moved.insert(8 + distance, club)
    got = score(moved, ACTUAL)
    # The displaced clubs each shift by one, so isolate the moved club's term.
    shifted = 0 if not distance else distance * 5
    assert got["places"] == 18 * 25 - (25 - points) - (distance * 25 - shifted)


def test_the_champion_bonus_needs_the_champion():
    swapped = [ACTUAL[1], ACTUAL[0]] + ACTUAL[2:]
    assert score(swapped, ACTUAL)["champion"] == 0


def test_the_top_four_bonus_needs_the_order_not_just_the_names():
    """Complete AND in the right order, so a swap inside it loses the lot."""
    inside = [ACTUAL[0], ACTUAL[2], ACTUAL[1], ACTUAL[3]] + ACTUAL[4:]
    assert score(inside, ACTUAL)["top_four"] == 0


def test_relegation_is_membership_not_placing():
    """A club tipped 17th that goes down 18th was still tipped to go down.

    The places are already paid by the distance table, so reading the bonus as
    an exact-place rule would charge for the same fact twice.
    """
    swapped = ACTUAL[:16] + [ACTUAL[17], ACTUAL[16]]
    assert score(swapped, ACTUAL)["relegation"] == 2 * RELEGATION_BONUS


def test_a_club_that_is_not_in_the_table_is_skipped_not_scored():
    entry = ["promoted"] + ACTUAL[:17]
    got = score(entry, ACTUAL)
    assert got["champion"] == 0
    assert isinstance(got["places"], int)


# --- strength and goals -------------------------------------------------------


def test_a_club_with_no_record_sits_at_the_league_mean():
    found = strengths({}, [row("new", position=1)])
    attack, defence = found["new"]
    assert attack == pytest.approx(LEAGUE_GOALS)
    assert defence == pytest.approx(LEAGUE_GOALS)


def test_three_rounds_cannot_make_a_champion():
    """The whole reason there is a prior at all."""
    hot = strengths({}, [row("hot", played=3, goals_for=12, goals_against=0)])
    attack, _ = hot["hot"]
    assert attack < 3.0, "a three-nil-times-four start was believed outright"
    assert attack > LEAGUE_GOALS


def test_the_archive_and_this_season_are_added_not_averaged():
    """Two sources of the same quantity, weighted by their own sample sizes."""
    records = {"c": Record(matches=68, goals_for=136, goals_against=34)}
    with_season = strengths(records, [row("c", played=3, goals_for=0, goals_against=9)])
    without = strengths(records, [])
    assert with_season["c"][1] > without["c"][1], "conceding nine changed nothing"


def test_the_home_side_is_expected_to_score_more():
    even = {"h": (LEAGUE_GOALS, LEAGUE_GOALS), "a": (LEAGUE_GOALS, LEAGUE_GOALS)}
    at_home, at_away = expected_goals("h", "a", even)
    assert at_home > at_away


def test_a_strong_attack_against_a_weak_defence_scores_most():
    strength = {"strong": (2.5, 0.8), "weak": (0.8, 2.5)}
    lots, few = expected_goals("strong", "weak", strength)
    assert lots > 3.0 and few < 0.8


# --- playing the season out ---------------------------------------------------


def test_the_table_carries_forward_and_every_club_is_placed():
    table = [row(f"c{i}", played=1, points=3 if i == 1 else 0, position=i) for i in range(1, 5)]
    finished = play_out(table, [("c2", "c3")], {}, random.Random(0))
    assert sorted(finished) == sorted(r["club"] for r in table)


def test_a_runaway_leader_stays_ahead_of_a_club_with_nothing():
    table = [
        row("far ahead", played=30, points=90, goals_for=90, goals_against=10, position=1),
        row("far behind", played=30, points=5, goals_for=10, goals_against=90, position=2),
    ]
    finished = play_out(table, [], {}, random.Random(0))
    assert finished[0] == "far ahead"


def test_the_same_seed_gives_the_same_season():
    """A recommendation that moves between runs is not a recommendation."""
    table = [row(f"c{i}", position=i) for i in range(1, 5)]
    rest = [("c1", "c2"), ("c3", "c4")]
    first = distribution(table, rest, {}, draws=50, seed=7)
    again = distribution(table, rest, {}, draws=50, seed=7)
    assert first == again


def test_every_club_gets_a_probability_for_every_place():
    table = [row(f"c{i}", position=i) for i in range(1, 5)]
    spread = distribution(table, [("c1", "c2")], {}, draws=40)
    assert set(spread) == {"c1", "c2", "c3", "c4"}
    for odds in spread.values():
        assert len(odds) == 4
        assert sum(odds) == pytest.approx(1.0)


# --- the assignment -----------------------------------------------------------


def test_the_solver_finds_the_obvious_answer():
    assert _hungarian([[9, 1, 1], [1, 9, 1], [1, 1, 9]]) == [0, 1, 2]
    assert _hungarian([[1, 9, 1], [9, 1, 1], [1, 1, 9]]) == [1, 0, 2]


def test_the_solver_beats_taking_the_best_cell_first():
    """Greedy takes 10 then is forced into 1; the answer is 9 and 9."""
    value = [[10.0, 9.0], [9.0, 1.0]]
    assigned = _hungarian(value)
    total = sum(value[i][assigned[i]] for i in range(2))
    assert total == pytest.approx(18.0)


def test_the_order_is_not_a_sort_by_most_likely_finish():
    """The claim the whole module rests on.

    `sharp` is certain of second. `flat` is spread across the top three and its
    single most likely place is also second, by a nose. Sorting by most likely
    finish has to break that tie arbitrarily and can put `flat` second, which
    pushes `sharp` off the one place it was going to hit exactly.

    Choosing by assignment cannot make that mistake: it prices both cells
    against the whole distribution and gives second to the club that will
    actually be there.
    """
    spread = {
        "sharp": [0.00, 1.00, 0.00],
        "flat": [0.34, 0.35, 0.31],
        "last": [0.10, 0.10, 0.80],
    }
    order = best_order(spread, clubs=["sharp", "flat", "last"])
    assert order[1] == "sharp", (
        "second place went to the club that merely peaks there, not the one "
        "that is certain of it"
    )

    naive = sorted(spread, key=lambda c: -spread[c].index(max(spread[c])))
    chosen = sum(value_of(c, i, spread, 3) for i, c in enumerate(order))
    other = sum(value_of(c, i, spread, 3) for i, c in enumerate(naive))
    assert chosen >= other


# --- the table is not the league ---------------------------------------------
#
# `stats.league_table` builds standings from PLAYED fixtures, so a club that
# has played none is absent from it while the calendar still lists its games.
# Reading the club list off the table alone made `play_out` insert that club on
# its own and `distribution` raise KeyError against a row it never allocated —
# and only on draws where the missing club won or drew, so the same command
# passed on one seed and died on another.


def test_a_club_with_nothing_played_is_still_in_the_league():
    """The crash, and the seventeen-club table hiding behind it."""
    table = [
        row("A", played=1, points=3, goals_for=2, goals_against=0, position=1),
        row("B", played=1, points=0, goals_for=0, goals_against=2, position=2),
    ]
    spread = distribution(table, [("A", "C"), ("B", "C")], {}, draws=200, seed=0)
    assert set(spread) == {"A", "B", "C"}
    assert sum(spread["C"]) == pytest.approx(1.0)
    assert all(len(odds) == 3 for odds in spread.values()), (
        "a place matrix sized for the table cannot hold the league"
    )


def test_it_does_not_depend_on_which_draws_the_missing_club_wins():
    """The RNG-dependence: it used to survive seeds where C lost every match."""
    table = [row("A", played=1, points=3, goals_for=9, goals_against=0, position=1)]
    for seed in range(6):
        spread = distribution(table, [("A", "C")], {}, draws=50, seed=seed)
        assert set(spread) == {"A", "C"}


def test_a_club_with_no_row_starts_from_zero_not_from_its_first_result():
    """Seeded explicitly, rather than conjured by whichever match it plays first."""
    table = [row("A", played=30, points=90, goals_for=90, goals_against=10, position=1)]
    finished = play_out(table, [], {}, random.Random(0))
    assert finished == ["A"]

    with_newcomer = play_out(table, [("A", "B")], {}, random.Random(0))
    assert set(with_newcomer) == {"A", "B"}
    assert with_newcomer[0] == "A", "ninety points did not survive a newcomer"


def test_everyone_is_sorted_so_the_matrix_order_cannot_vary():
    """Set order decided an answer in this project once already."""
    table = [row("Z", position=1), row("A", position=2)]
    assert everyone(table, [("M", "Z"), ("A", "M")]) == ["A", "M", "Z"]


# --- the type of the sequence must not change the score -----------------------
#
# `predicted[:1] == actual[:1]` compared raw slices while the line under it
# wrapped both sides in `list()`. Both parameters are declared `Sequence[str]`,
# so `score(tuple(order), actual)` is a legal call — and it lost sixty points,
# because `('Sporting',) == ['Sporting']` is false, while the top-four bonus
# beside it went on paying because it normalised.
#
# Latent: every caller today passes a list. The `list()` next door is the
# evidence that the risk was known and then guarded in one of the two places.


@pytest.mark.parametrize(
    "wrap", [list, tuple], ids=["list", "tuple"]
)
def test_a_perfect_entry_scores_the_same_whatever_it_is_made_of(wrap):
    assert score(wrap(ACTUAL), wrap(ACTUAL)) == score(ACTUAL, ACTUAL)


def test_a_tuple_entry_still_collects_the_champion_bonus():
    """The defect, on its own, in the units it was lost in."""
    got = score(tuple(ACTUAL), ACTUAL)
    assert got["champion"] == CHAMPION_BONUS, (
        "sixty points went missing because the entry was a tuple"
    )


def test_a_tuple_actual_table_works_too():
    """The other side of the comparison, which nothing guarded either."""
    assert score(ACTUAL, tuple(ACTUAL))["champion"] == CHAMPION_BONUS


def test_a_generator_is_not_silently_half_scored():
    """`Sequence` excludes generators, but the normalisation makes the failure
    a clean one rather than a table scored against an exhausted iterator."""
    got = score(iter(ACTUAL), ACTUAL)
    assert got["champion"] == CHAMPION_BONUS


def test_the_bonuses_still_refuse_a_wrong_order_after_normalising():
    """The fix must not turn every comparison true."""
    swapped = tuple([ACTUAL[1], ACTUAL[0]] + ACTUAL[2:])
    got = score(swapped, ACTUAL)
    assert got["champion"] == 0
    assert got["top_four"] == 0
