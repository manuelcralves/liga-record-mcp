"""Scoring a season Liga Record never published a mark for.

Everything §10.3 pays for survives in zerozero's match pages except Record's
own mark, which §10.2 says they will not discuss with anyone. So a past
season's score splits in two: an objective half that is arithmetic on facts,
and one term that has to be stood in for.

The standing-in was measured before it was written. Fitted on 489 marks read
off real 2026/27 scores and tested on rounds held back from the fit, zerozero's
rating lands within 0.44 points of the mark, against scores whose own spread is
2.66 — and against 0.75 for assuming the average, so the rating earns its
place. What makes that tolerable is what stays exact: goals, cards, clean
sheets and absences are computed, never guessed.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.models import Position
from liga_record_mcp.stats import (
    MEAN_MARK_POINTS,
    PLAYER_OF_THE_WEEK_BONUS,
    RATING_POINTS,
    expected_rating_points,
    likely_rating_points,
    read_rating,
    reconstruct_points,
)


def forward(**kwargs):
    base = dict(conceded=1, won=False, used=True, minutes=90)
    return reconstruct_points(Position.FWD, **{**base, **kwargs})


# --------------------------------------------------------------------------
# Standing in for the mark
# --------------------------------------------------------------------------


def test_a_reconstructed_mark_is_one_record_could_have_given():
    """§10.1 allows six values and no others. Five and six points are not marks
    at all — the scale jumps from 4 straight to 7 — so an estimate that lands
    between them has to come down on one side."""
    for tenth in range(0, 101):
        assert likely_rating_points(tenth / 10) in RATING_POINTS


def test_a_better_performance_never_scores_worse():
    """The fit could in principle be non-monotonic once snapped. If it ever
    were, a player would be punished for playing better."""
    marks = [likely_rating_points(tenth / 10) for tenth in range(0, 101)]
    assert marks == sorted(marks)


def test_the_unrounded_estimate_stays_inside_the_scale():
    """The line is fitted, not bounded, and extended far enough it would pay a
    mark of nine or charge one of minus four."""
    assert expected_rating_points(0.0) == min(RATING_POINTS)
    assert expected_rating_points(10.0) <= max(RATING_POINTS)


def test_a_rating_off_the_scale_is_refused():
    """A rating of 47 is a parsing accident, and quietly scoring it would put a
    fabricated number into a season total."""
    for impossible in (-1.0, 10.5, 47.0):
        with pytest.raises(ValueError):
            expected_rating_points(impossible)


# --------------------------------------------------------------------------
# Scoring a match
# --------------------------------------------------------------------------


def test_the_estimated_part_is_kept_apart_from_the_computed_part():
    """Only the mark is a guess. Reporting a single number would hide which
    half of a score can be trusted."""
    built = forward(rating=6.5, goals=1, conceded=0, won=True)
    assert built["objective"] == 1 + 2  # a win, and a forward's goal
    assert built["mark_source"] == "rating"
    assert built["points"] == built["objective"] + built["mark"]


def test_the_round_that_forced_the_rules_to_be_read_properly_rebuilds():
    """Pavlidis, round 2: a hat-trick away at Casa Pia, zerozero's only 10 of
    the season, and 24 points from Record. Rebuilt here without ever being
    shown the 24 — the goals and the win are facts, the mark comes from the 10,
    and §10.3(k) is added by the round, since it belongs to the round."""
    built = reconstruct_points(
        Position.FWD,
        conceded=0,
        won=True,
        used=True,
        rating=10.0,
        goals=3,
        minutes=59,
    )
    assert built["objective"] == 1 + 6 + 5
    assert built["mark"] == 7
    assert built["points"] + PLAYER_OF_THE_WEEK_BONUS == 24


def test_a_man_left_on_the_bench_scores_the_flat_penalty():
    """§10.3(i), and his club's win and clean sheet pass him by. Nothing about
    an absence is estimated, so it is not marked as such."""
    bench = reconstruct_points(
        Position.DEF, conceded=0, won=True, used=False, rating=None
    )
    assert bench["points"] == -1
    assert bench["mark_source"] == "unused"
    assert bench["estimated"] is False


def test_an_unrated_match_falls_back_to_the_average_and_says_so():
    """zerozero leaves some appearances unrated. Dropping them would quietly
    shorten a season; scoring them as zero would invent a terrible match."""
    unrated = forward(rating=None, goals=0)
    assert unrated["mark"] == MEAN_MARK_POINTS
    assert unrated["mark_source"] == "assumed average"


def test_summing_a_season_and_scoring_one_match_are_asked_for_separately():
    """Per match the answer has to be a mark Record could give; over a season
    the halves that cancel are worth keeping. The two disagree on purpose."""
    single = forward(rating=6.8, per_match=True)
    whole = forward(rating=6.8, per_match=False)
    assert single["mark"] == float(likely_rating_points(6.8))
    assert whole["mark"] == pytest.approx(expected_rating_points(6.8))
    assert single["mark"] != whole["mark"]


def test_what_is_built_can_be_read_back():
    """The two directions have to agree, or one of them has the rules wrong."""
    built = reconstruct_points(
        Position.DEF, conceded=0, won=True, used=True, rating=7.4, minutes=90
    )
    read = read_rating(
        int(built["points"]), Position.DEF, conceded=0, won=True, minutes=90
    )
    assert read["rating"] == built["mark"]
    assert read["certain"] is True
