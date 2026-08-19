"""The round-by-round opponent adjustment.

`project` answers "how good is this player over a season". A team sheet asks a
narrower question — "how good is he against the side he plays on Saturday" —
and the two came apart badly in round 3: the season projection wanted three
Arouca defenders away at Porto, which is their worst fixture of the year.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.models import Position
from liga_record_mcp.stats import (
    APPEARANCE_FLOOR,
    FIXTURE_BOUNDS,
    adjust_for_fixture,
    fixture_multipliers,
)

# Roughly the Primeira Liga averages over the two completed seasons on file.
LEAGUE_GF = 1.40
LEAGUE_GA = 1.40


def multipliers(
    *,
    club_ga: float = 1.40,
    club_gf: float = 1.40,
    opponent_ga: float = 1.40,
    opponent_gf: float = 1.40,
    at_home: bool = True,
    bounds: tuple[float, float] = FIXTURE_BOUNDS,
) -> tuple[float, float]:
    """A league-average club against a league-average opponent, unless varied."""
    return fixture_multipliers(
        club_ga,
        club_gf,
        opponent_ga,
        opponent_gf,
        LEAGUE_GA,
        LEAGUE_GF,
        at_home=at_home,
        bounds=bounds,
    )


# --------------------------------------------------------------------------
# The multipliers
# --------------------------------------------------------------------------


def test_home_advantage_cuts_both_ways():
    """At home you score more and concede less, so both multipliers rise."""
    home_defensive, home_attacking = multipliers(at_home=True)
    away_defensive, away_attacking = multipliers(at_home=False)

    assert home_attacking > 1.0 > away_attacking
    assert home_defensive > away_defensive


def test_a_dangerous_opponent_lowers_the_defensive_multiplier():
    weak, _ = multipliers(opponent_gf=0.90)
    strong, _ = multipliers(opponent_gf=2.30)

    assert strong < weak
    assert strong < 1.0


def test_a_leaky_opponent_raises_the_attacking_multiplier():
    _, tight = multipliers(opponent_ga=0.71)
    _, leaky = multipliers(opponent_ga=1.66)

    assert leaky > tight
    assert leaky > 1.0


def test_an_average_fixture_leaves_a_club_at_its_own_level():
    """Strip out home advantage and a neutral opponent should change nothing."""
    defensive, attacking = fixture_multipliers(
        0.71, 1.93, LEAGUE_GA, LEAGUE_GF, LEAGUE_GA, LEAGUE_GF, at_home=True
    )
    # Only the venue is left to explain the movement.
    assert attacking == pytest.approx(1.10, abs=0.001)
    assert defensive > 1.0


def test_the_multipliers_stay_inside_their_bounds():
    """Two seasons of club form cannot justify an unbounded swing."""
    low, high = FIXTURE_BOUNDS
    absurd = multipliers(club_ga=4.0, club_gf=0.1, opponent_ga=0.01, opponent_gf=5.0)
    for value in absurd:
        assert low <= value <= high


def test_bounds_are_caller_supplied():
    defensive, attacking = multipliers(opponent_gf=3.0, bounds=(0.95, 1.05))
    assert 0.95 <= defensive <= 1.05
    assert 0.95 <= attacking <= 1.05


# --------------------------------------------------------------------------
# Applying them
# --------------------------------------------------------------------------


def test_the_appearance_floor_is_never_scaled():
    """Whoever the opponent is, turning out is worth the same."""
    for multiplier in (0.55, 1.0, 1.75):
        adjusted = adjust_for_fixture(
            APPEARANCE_FLOOR, Position.FWD, multiplier, multiplier
        )
        assert adjusted == pytest.approx(APPEARANCE_FLOOR)


def test_a_projection_below_the_floor_is_not_pushed_lower():
    """A hard fixture dents the variable part; it cannot eat the floor."""
    adjusted = adjust_for_fixture(1.0, Position.DEF, 0.55, 0.55)
    assert adjusted == pytest.approx(APPEARANCE_FLOOR)


def test_keepers_follow_the_defence_and_forwards_the_attack():
    """Same fixture, opposite readings — the position decides which one bites."""
    defensive, attacking = 1.60, 0.60
    keeper = adjust_for_fixture(6.0, Position.GK, defensive, attacking)
    forward = adjust_for_fixture(6.0, Position.FWD, defensive, attacking)

    assert keeper > 6.0 > forward


def test_a_defender_at_a_strong_side_loses_ground():
    """The round-3 regression: Fontán of Arouca away at Porto.

    Arouca conceded 1.66 a match over two seasons, Porto scored 1.93. The
    season projection had him third-best defender in the squad; against this
    opponent he should fall behind it.
    """
    defensive, attacking = fixture_multipliers(
        1.66, 1.21, 0.71, 1.93, LEAGUE_GA, LEAGUE_GF, at_home=False
    )
    season_rate = 3.5
    adjusted = adjust_for_fixture(season_rate, Position.DEF, defensive, attacking)

    assert adjusted < season_rate
    assert adjusted > APPEARANCE_FLOOR  # dented, not erased


def test_a_forward_against_the_worst_defence_gains():
    """Begraoui of Estoril at home to Rio Ave, who conceded 1.66 a match."""
    defensive, attacking = fixture_multipliers(
        1.62, 1.50, 1.66, 1.09, LEAGUE_GA, LEAGUE_GF, at_home=True
    )
    season_rate = 4.3
    adjusted = adjust_for_fixture(season_rate, Position.FWD, defensive, attacking)

    assert adjusted > season_rate


def test_every_position_has_a_defensive_share():
    """A missing position would raise KeyError at team-sheet time, not here."""
    for position in Position:
        assert adjust_for_fixture(5.0, position, 1.2, 1.2) > 0
