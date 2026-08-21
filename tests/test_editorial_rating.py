"""Recovering the editorial rating by subtracting what the calendar explains.

For most of this project the rating was treated as unreachable — a Record
writer's mark, published nowhere, about 62% of the variance in a score. It was
never unreachable: §10.1 and §10.3 are published, so the objective half is
arithmetic and the remainder is the rating.

Checked against real round-2 scores before these tests were written: 11 of 11
goalkeepers and 73 of 73 defenders resolved, none unexplained.
"""

from __future__ import annotations

from liga_record_mcp.models import Position
from liga_record_mcp.stats import (
    CLEAN_SHEET_POINTS,
    PLAYER_OF_THE_WEEK_BONUS,
    RATING_POINTS,
    decompose_round,
    objective_points,
    rating_history,
    read_rating,
)


def gk(points, *, conceded, won=False):
    return decompose_round(points, Position.GK, conceded=conceded, won=won)


def defender(points, *, conceded, won=False):
    return decompose_round(points, Position.DEF, conceded=conceded, won=won)


def forward(points, *, conceded=1, won=False):
    return decompose_round(points, Position.FWD, conceded=conceded, won=won)


# --------------------------------------------------------------------------
# The objective half
# --------------------------------------------------------------------------


def test_a_clean_sheet_pays_the_keeper_more_than_the_defender():
    assert objective_points(Position.GK, conceded=0, won=False) == 2
    assert objective_points(Position.DEF, conceded=0, won=False) == 1


def test_conceding_costs_the_keeper_twice_as_much():
    assert objective_points(Position.GK, conceded=2, won=False) == -4
    assert objective_points(Position.DEF, conceded=2, won=False) == -2


def test_the_win_bonus_reaches_every_position():
    for position in Position:
        won = objective_points(position, conceded=1, won=True)
        lost = objective_points(position, conceded=1, won=False)
        assert won - lost == 1


def test_midfielders_and_forwards_are_paid_nothing_for_the_defence():
    """§10.3(b) and (d) name only keepers and defenders."""
    for position in (Position.MID, Position.FWD):
        assert objective_points(position, conceded=0, won=False) == 0
        assert objective_points(position, conceded=3, won=False) == 0


# --------------------------------------------------------------------------
# What is left over
# --------------------------------------------------------------------------


def test_a_keeper_in_a_clean_sheet_win_leaves_his_rating():
    """3 points of calendar (win + clean sheet), so 6 means a rating of 3."""
    result = gk(6, conceded=0, won=True)

    assert result["objective"] == 3
    assert result["residual"] == 3
    assert result["rating"] == 3
    assert result["certain"] is True


def test_a_conceded_goal_is_subtracted_before_the_rating_is_read():
    """Two conceded costs a keeper 4, so 0 on the card is still a rating of 4."""
    result = gk(0, conceded=2, won=False)

    assert result["objective"] == -4
    assert result["rating"] == 4


def test_the_top_of_the_scale_jumps_to_seven():
    """§10.1 converts a 5 to 7, so 6 is not a legal rating and 7 is."""
    assert 5 not in RATING_POINTS and 6 not in RATING_POINTS
    assert gk(7, conceded=1, won=False)["objective"] == -2
    assert gk(5, conceded=1, won=False)["rating"] == 7


def test_an_unused_player_is_recognised_rather_than_decomposed():
    """-1 is the whole score (§10.3(i)); there is no rating to read."""
    result = defender(-1, conceded=0, won=True)

    assert result["used"] is False
    assert result["rating"] is None
    assert "not used" in result["candidates"][0]


# --------------------------------------------------------------------------
# When the residual is not just a rating
# --------------------------------------------------------------------------


def test_a_scoring_defender_is_read_as_a_goal_not_a_strange_rating():
    """A defender's goal is +4, so 8 is a rating of 4 and a goal — the real
    shape of four round-2 defenders."""
    result = defender(10, conceded=0, won=True)

    assert result["objective"] == 2
    assert result["residual"] == 8
    assert result["rating"] is None
    assert result["certain"] is False
    assert any("1 goal" in c for c in result["candidates"])


def test_a_sent_off_defender_is_read_as_a_card():
    """A defender losing 2-0 who scores -3 needs a card to explain it.

    Objective is -2, so the residual is -1: no legal rating, but a rating of 2
    less a straight red fits exactly. Two round-2 defenders had this shape.
    """
    result = defender(-3, conceded=2, won=False)

    assert result["used"] is True
    assert result["residual"] == -1
    assert result["rating"] is None
    assert any("straight red" in c for c in result["candidates"])


def test_minus_one_is_read_as_an_absence_but_never_claimed_as_certain():
    """§10.3(i) pays an unused player -1, and a used one can also net to -1.

    The two are indistinguishable from the score alone — a limit this module
    has acknowledged since the appearance tracker was written.
    """
    result = defender(-1, conceded=1, won=False)

    assert result["used"] is False
    assert result["certain"] is False
    assert len(result["candidates"]) == 2


def test_a_forward_is_never_certain_because_of_the_blank_penalty():
    """§10.3(h) docks a blank forward 1, so a legal-looking residual could be
    either the rating itself or one higher."""
    result = forward(3, conceded=1, won=False)

    assert result["residual"] == 3
    assert result["certain"] is False
    assert result["rating"] is None
    assert len(result["candidates"]) >= 2


def test_an_impossible_residual_says_so_rather_than_inventing_one():
    result = decompose_round(97, Position.MID, conceded=0, won=False)

    assert result["rating"] is None
    assert result["candidates"] == ["nothing in §10.3 explains this"]


def test_every_legal_rating_is_recoverable_for_a_midfielder():
    """A midfielder has no defensive terms, so the residual is the score."""
    for rating in RATING_POINTS:
        result = decompose_round(rating, Position.MID, conceded=2, won=False)
        assert result["rating"] == rating


# --------------------------------------------------------------------------
# Across rounds
# --------------------------------------------------------------------------


def test_the_mean_uses_only_the_rounds_that_could_be_read():
    rounds = [
        gk(6, conceded=0, won=True),      # rating 3
        gk(5, conceded=1, won=False),     # rating 7
        defender(10, conceded=0, won=True),  # ambiguous — residual 8, a goal
        defender(-1, conceded=0, won=True),  # unused
    ]
    summary = rating_history(rounds)

    assert summary["rounds_read"] == 2
    assert summary["ambiguous"] == 1
    assert summary["unused"] == 1
    assert summary["mean_rating"] == 5.0
    assert summary["ratings"] == [3, 7]


def test_a_player_with_nothing_readable_reports_no_mean():
    summary = rating_history([defender(-1, conceded=0, won=True)])

    assert summary["mean_rating"] is None
    assert summary["rounds_read"] == 0


def test_an_empty_history_does_not_explode():
    assert rating_history([])["mean_rating"] is None


# --------------------------------------------------------------------------
# The clauses that were missed the first time
# --------------------------------------------------------------------------


def test_the_player_of_the_week_bonus_is_subtracted_before_reading():
    """§10.3(k) pays the round's top scorer 5, and it broke the first version.

    Pavlidis took 24 in round 2: Benfica won 7-0, he scored a hat-trick, he was
    the round's highest scorer. Without the bonus in the sum nothing in §10.3
    could account for it, and the extractor correctly said so rather than
    inventing a reading.
    """
    without = decompose_round(24, Position.FWD, conceded=0, won=True)
    assert without["candidates"] == ["nothing in §10.3 explains this"]

    with_bonus = decompose_round(
        24, Position.FWD, conceded=0, won=True, player_of_the_week=True
    )
    assert with_bonus["objective"] == 6   # win + player of the week
    assert with_bonus["residual"] == 18
    assert "rating 7 plus 3 goals" in with_bonus["candidates"]


def test_an_own_goal_is_among_the_readings():
    """§10.3(j) costs 2, whoever scores it."""
    result = decompose_round(0, Position.MID, conceded=1, won=False)

    assert any("own goal" in c for c in result["candidates"])


def test_more_than_three_goals_is_considered():
    """The first version searched one to three and stopped, which is why a
    five-goal reading never surfaced."""
    result = decompose_round(
        18, Position.FWD, conceded=0, won=False, player_of_the_week=False
    )
    assert any("5 goals" in c for c in result["candidates"])


# --------------------------------------------------------------------------
# Reading the mark once the events of the match are known
# --------------------------------------------------------------------------


def test_the_round_that_forced_the_rules_to_be_read_properly():
    """Pavlidis, round 2: a hat-trick away at Casa Pia in a 7-0 win, and 24
    points that nothing in the code could account for. Chasing the gap is what
    turned up §10.3(f) and §10.3(k). Rebuilt here from the events alone.

        win 1 + three goals 6 + hat-trick 5 + Player of the Week 5 + a 5 worth
        7 = 24
    """
    built = objective_points(
        Position.FWD, conceded=0, won=True, goals=3, minutes=59
    )
    assert built == 1 + 6 + 5
    assert built + PLAYER_OF_THE_WEEK_BONUS + RATING_POINTS[5] == 24

    read = read_rating(
        24,
        Position.FWD,
        conceded=0,
        won=True,
        goals=3,
        minutes=59,
        player_of_the_week=True,
    )
    assert read["rating"] == 7
    assert read["certain"] is True


def test_a_forward_is_docked_for_a_blank_only_after_75_minutes():
    """§10.3(h). The threshold is the whole rule — a substitute who plays half
    an hour without scoring keeps his mark intact."""
    played = dict(position=Position.FWD, conceded=1, won=False, goals=0)
    assert objective_points(**played, minutes=90) == -1
    assert objective_points(**played, minutes=75) == -1
    assert objective_points(**played, minutes=74) == 0
    assert objective_points(**played, minutes=None) == 0


def test_a_blank_does_not_touch_anyone_else():
    """The clause names forwards. A midfielder who plays ninety without
    scoring is simply a midfielder who did not score."""
    for position in (Position.GK, Position.DEF, Position.MID):
        assert objective_points(
            position, conceded=0, won=False, goals=0, minutes=90
        ) == CLEAN_SHEET_POINTS.get(position, 0)


def test_goals_are_paid_by_position():
    """A keeper's goal is worth twenty and a forward's two — the widest spread
    in the whole table, and the reason position has to travel with the goal."""
    scored = dict(conceded=1, won=False, goals=1)
    assert objective_points(Position.GK, **scored) == 20 - 2
    assert objective_points(Position.DEF, **scored) == 4 - 1
    assert objective_points(Position.MID, **scored) == 3
    assert objective_points(Position.FWD, **scored) == 2


def test_the_hat_trick_bonus_starts_at_three_and_does_not_repeat():
    """§10.3(f) reads "three or more", so a fourth goal is worth a goal and
    not a second bonus."""
    three = objective_points(Position.MID, conceded=1, won=False, goals=3)
    four = objective_points(Position.MID, conceded=1, won=False, goals=4)
    assert three == 3 * 3 + 5
    assert four == 4 * 3 + 5


def test_cards_and_own_goals_come_off():
    assert objective_points(Position.MID, conceded=1, won=False, red_card=True) == -3
    assert objective_points(Position.MID, conceded=1, won=False, second_yellow=True) == -1
    assert objective_points(Position.MID, conceded=1, won=False, own_goals=1) == -2


def test_a_forward_becomes_readable_once_the_goals_are_known():
    """The point of the whole exercise. Without the events a forward's score is
    never certain, because a legal-looking residual is as likely to be one mark
    higher with §10.3(h) applied. With them there is nothing left to choose."""
    guessed = decompose_round(6, Position.FWD, conceded=1, won=True)
    assert guessed["certain"] is False

    known = read_rating(
        6, Position.FWD, conceded=1, won=True, goals=1, minutes=90
    )
    assert known["rating"] == 3
    assert known["certain"] is True


def test_an_impossible_residual_is_reported_rather_than_clamped():
    """A residual outside 0-7 means one of the inputs is wrong — an unrecorded
    penalty, a missed own goal. Rounding it into range would bury the only
    evidence that the reconstruction has a hole in it."""
    broken = read_rating(40, Position.DEF, conceded=0, won=True, goals=0, minutes=90)
    assert broken["rating"] is None
    assert broken["certain"] is False
    assert broken["residual"] == 38
    assert "not a rating" in broken["candidates"][0]


def test_an_unused_player_scores_the_flat_penalty_and_nothing_else():
    """§10.3(i). His club's win and clean sheet pass him by entirely."""
    bench = read_rating(-1, Position.DEF, conceded=0, won=True, used=False)
    assert bench["used"] is False
    assert bench["certain"] is True
    assert bench["rating"] is None
