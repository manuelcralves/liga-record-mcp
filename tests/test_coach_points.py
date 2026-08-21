"""What a coach scores, under §14.

The project had a projection for coaches long before it had the rules that
generate a coach's points — it fitted a rate to observed totals and never knew
what the totals were made of. §14 was simply missing from everything this
server encodes, including the regulation it publishes to its own client.

He matters more than his obscurity suggests. §14.4 adds his points to the
team's, §6.17 makes a team without one score zero, and §6.4 makes him free: he
costs no budget and may be changed every round. He is the only pick in the game
with no constraint on it whatsoever.
"""

from __future__ import annotations

from liga_record_mcp.stats import (
    COACH_SENT_OFF,
    RATING_POINTS,
    coach_points,
)


def test_the_result_is_the_first_term():
    """§14.3(a). A draw is worth nothing, which is not the same as a loss."""
    assert coach_points(scored=1, conceded=0) == 1 + 1  # win, and a clean sheet
    assert coach_points(scored=1, conceded=1) == 0
    assert coach_points(scored=1, conceded=2) == -1


def test_a_comeback_pays_and_a_comeback_won_pays_double():
    """§14.3(b), checked against the regulation's own worked example: "4 pontos
    por ganhar tendo estado a perder por 2 golos"."""
    assert coach_points(scored=2, conceded=2, trailed_by=2) == 2
    # Four for the comeback, and the win itself is a separate term under (a).
    assert coach_points(scored=3, conceded=2, trailed_by=2) == 4 + 1


def test_a_comeback_that_fell_short_pays_nothing_for_itself():
    """The clause pays for drawing or winning from behind. Losing by less than
    you were losing by is not something §14.3(b) recognises."""
    assert coach_points(scored=1, conceded=2, trailed_by=2) == -1


def test_the_scoreline_is_worth_something_on_its_own():
    """§14.3(c) — four separate terms, and they stack with the result."""
    assert coach_points(scored=0, conceded=0) == 0 + (-1) + 1  # draw, blank, clean sheet
    assert coach_points(scored=4, conceded=0) == 1 + 1 + 1  # win, clean sheet, big win
    assert coach_points(scored=0, conceded=4) == -1 + (-1) + (-1)  # loss, blank, big loss


def test_more_than_two_goals_means_more_than_two():
    """"Vitória por mais de 2 golos de diferença" — three, not two. Reading it
    as two or more would pay a bonus on every comfortable win in the league."""
    assert coach_points(scored=2, conceded=0) == 1 + 1  # win and clean sheet, no bonus
    assert coach_points(scored=3, conceded=0) == 1 + 1 + 1
    assert coach_points(scored=3, conceded=1) == 1  # a two-goal margin, nothing extra
    assert coach_points(scored=4, conceded=1) == 1 + 1


def test_a_goal_off_the_bench_is_credited_to_the_man_who_made_the_change():
    """§14.3(d), one point for each."""
    assert coach_points(scored=2, conceded=1, substitute_goals=2) == 1 + 2


def test_being_sent_to_the_stand_costs_three():
    assert coach_points(scored=1, conceded=1, sent_off=True) == COACH_SENT_OFF


def test_a_coach_who_could_not_take_the_bench_scores_nothing_at_all():
    """§14.5, and "nothing" is not "zero plus the result". A suspended or
    sacked coach is out of the round entirely — his side can win 5-0 and the
    participant who picked him gets none of it."""
    assert coach_points(scored=5, conceded=0, rating_points=7, available=False) == 0
    assert coach_points(scored=0, conceded=5, available=False) == 0


def test_the_editorial_mark_rides_on_top_like_a_players():
    """§14.1 and §14.2 mirror §10.1 and §10.2 exactly: a mark from the same
    newsroom, on the same scale, which they will not discuss either."""
    for mark in RATING_POINTS:
        assert coach_points(scored=1, conceded=1, rating_points=mark) == mark


def test_the_worst_and_best_rounds_are_the_shape_you_would_expect():
    """A sanity range. A coach swings roughly a goal's worth of points either
    way, which is why he is worth picking well and not worth agonising over."""
    worst = coach_points(scored=0, conceded=5, sent_off=True, rating_points=0)
    best = coach_points(
        scored=5, conceded=0, trailed_by=2, substitute_goals=2, rating_points=7
    )
    assert worst == -1 - 1 - 1 - 3
    assert best == 7 + 1 + 4 + 1 + 1 + 2
