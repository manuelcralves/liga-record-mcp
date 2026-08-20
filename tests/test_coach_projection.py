"""Projecting the coach, who was left out of everything for two rounds.

He is selected every round and scores every round, and the eighteen ran from
14 points down to -2 after two — roughly seven points a round between the best
and the worst pick. That is larger than most of the differences this project
agonises over on the player side.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.models import ClubRecord
from liga_record_mcp.stats import PRIOR_STRENGTH, project_coach

LEAGUE_BASELINE = 3.3  # mean coach points per match over the eighteen
LEAGUE_GA = 1.40
LEAGUE_GF = 1.40


def record(*, goals_for: int, goals_against: int, matches: int = 68) -> ClubRecord:
    return ClubRecord(
        club="Test FC",
        seasons=("2024-25", "2025-26"),
        matches=matches,
        goals_for=goals_for,
        goals_against=goals_against,
        clean_sheets=matches // 3,
        wins=matches // 3,
    )


STRONG = record(goals_for=140, goals_against=50)   # Porto-shaped
WEAK = record(goals_for=60, goals_against=120)     # Casa Pia-shaped


def project(points, matches, club=STRONG, **kw):
    return project_coach(
        points, matches, club, LEAGUE_BASELINE, LEAGUE_GA, LEAGUE_GF, **kw
    )


def test_a_strong_club_lifts_its_coach():
    assert project(0, 0, STRONG)["prior_rate"] > project(0, 0, WEAK)["prior_rate"]


def test_a_promoted_club_is_neutral_and_says_so():
    """No top-flight record is not the same as an average one."""
    result = project(0, 0, None)

    assert result["club_has_history"] is False
    assert result["club_strength"] == pytest.approx(1.0)
    assert result["projected_rate"] == pytest.approx(LEAGUE_BASELINE)


def test_with_no_matches_the_projection_is_all_prior():
    result = project(0, 0)

    assert result["observed_rate"] is None
    assert result["weight_on_form"] == 0.0
    assert result["projected_rate"] == result["prior_rate"]


def test_form_counts_but_does_not_take_over():
    """Two rounds of a hot start is worth about a third, by design."""
    result = project(14, 2)

    # Rounded to two places, as every other projection field is.
    assert result["weight_on_form"] == round(2 / (2 + PRIOR_STRENGTH), 2)
    assert result["weight_on_form"] < 0.5


def test_form_gains_weight_as_the_season_runs():
    """Rate held fixed, so only the number of matches moves the weight."""
    early = project(14, 2)
    later = project(70, 10)

    assert early["observed_rate"] == later["observed_rate"] == 7.0
    assert later["weight_on_form"] > early["weight_on_form"]
    assert later["projected_rate"] > early["projected_rate"]


def test_a_hot_start_at_a_weak_club_is_pulled_down_harder():
    """The same 14 points mean less behind a side with no record of winning."""
    at_strong = project(14, 2, STRONG)["projected_rate"]
    at_weak = project(14, 2, WEAK)["projected_rate"]

    assert at_weak < at_strong


def test_defence_and_attack_count_equally():
    """A coach is paid on the result, not on the clean sheet alone.

    Mirrored clubs — one as good going forward as the other is at the back —
    must produce the same strength, or the split is not even.
    """
    forward = project(0, 0, record(goals_for=140, goals_against=95))
    backward = project(0, 0, record(goals_for=95, goals_against=140))

    assert forward["club_strength"] > backward["club_strength"]

    mirrored_a = project(0, 0, record(goals_for=140, goals_against=50))
    mirrored_b = project(0, 0, record(goals_for=50, goals_against=140))
    assert mirrored_a["club_strength"] > 1.0 > mirrored_b["club_strength"]


def test_the_working_is_shown():
    """An unexplained projection cannot be argued with."""
    result = project(11, 2)

    assert set(result) == {
        "matches",
        "observed_rate",
        "prior_rate",
        "projected_rate",
        "weight_on_form",
        "club_strength",
        "club_has_history",
    }


def test_a_negative_coach_is_not_clamped_to_zero():
    """Casa Pia's coach was on -2. That is a real score, not a floor error."""
    result = project(-2, 2, WEAK)

    assert result["observed_rate"] == -1.0
    assert result["projected_rate"] < LEAGUE_BASELINE
