"""The round-by-round opponent adjustment.

`project` answers "how good is this player over a season". A team sheet asks a
narrower question — "how good is he against the side he plays on Saturday" —
and the two came apart badly in round 3: the season projection wanted three
Arouca defenders away at Porto, which is their worst fixture of the year.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.backtest import (
    ABSENT,
    club_form_upto,
    fixture_adjusted_projection,
    fixture_table,
    two_part_projection,
)
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


# --------------------------------------------------------------------------
# Reading the calendar back out of the reconstruction
# --------------------------------------------------------------------------


def rows(*matches):
    """One player, however many rounds."""
    return {"p": {"matches": list(matches)}}


def match(round_number, club, opponent, *, at_home=True, scored=1, conceded=0):
    return {
        "round": round_number,
        "club": club,
        "opponent": opponent,
        "at_home": at_home,
        "scored": scored,
        "conceded": conceded,
        "points": 3.0,
        "minutes": 90,
    }


def test_a_cup_tie_sharing_a_round_number_is_outvoted():
    """2024/25 has four of these. Sporting played a third-tier side in the cup
    the same week as league round 4, and both rows say `round: 4`.

    Last-writer-wins would record the league round as away at the cup side —
    and then price every Sporting player that week against a defence that was
    never on the pitch. Eleven league team-mates outvote the two who travelled.
    """
    players = {
        f"league{n}": {"matches": [match(4, "Sporting", "Benfica")]}
        for n in range(11)
    }
    players.update(
        {
            f"cup{n}": {"matches": [match(4, "Sporting", "SC Lusitania")]}
            for n in range(2)
        }
    )
    table = fixture_table([(players, 0)])
    assert table[(4, "Sporting")][0] == "Benfica"


def test_the_other_half_of_the_fixture_is_filled_in():
    """A club whose players are barely in the market appears on a handful of
    rounds. Its opponents can see it even when it cannot see itself, and the
    fixture is the same match read from the other end: venue flipped, goals
    the other way about."""
    table = fixture_table(
        [(rows(match(3, "Porto", "Tondela", at_home=True, scored=2, conceded=1)), 0)]
    )
    assert table[(3, "Tondela")] == ("Porto", False, 1, 2)


def test_what_the_reconstruction_says_is_never_overwritten_by_the_mirror():
    """Both ends are on file for most matches. The club's own row wins."""
    table = fixture_table(
        [
            (
                {
                    "a": {"matches": [match(3, "Porto", "Tondela", scored=2, conceded=1)]},
                    "b": {"matches": [match(3, "Tondela", "Porto", at_home=False,
                                            scored=1, conceded=2)]},
                },
                0,
            )
        ]
    )
    assert table[(3, "Tondela")] == ("Porto", False, 1, 2)


def test_club_form_cannot_see_the_round_it_is_asked_about():
    """The rule the whole backtest rests on, applied to the calendar.

    A club's goals for and against may be read from rounds strictly earlier.
    Its opponent and its venue for the round itself are fair game — the
    calendar is published weeks ahead, and knowing who you play on Saturday is
    not foresight — but how that match finished is not.
    """
    played = [match(m, "Porto", "Braga", scored=1, conceded=1) for m in range(1, 6)]
    table = fixture_table([(rows(*played), 0)])
    before = club_form_upto(table, 5)

    rewritten = {
        key: (value[0], value[1], 99, 99) if key[0] >= 5 else value
        for key, value in table.items()
    }
    assert club_form_upto(rewritten, 5) == before
    # And it does move when its own past changes, or the guard above would
    # pass on a function that read nothing at all.
    earlier = {
        key: (value[0], value[1], 7, 7) if key[0] < 5 else value
        for key, value in table.items()
    }
    assert club_form_upto(earlier, 5) != before


def test_a_club_with_no_fixture_on_the_round_is_left_alone():
    """Not scored zero. The live pages score a blank round zero because §15.3
    means the round genuinely did not happen for that club; here a missing row
    means only that the reconstruction could not tell us, and §10.3(i)'s real
    -1 is already in the points. Guessing would be worse than not knowing.
    """
    points = {"p": {1: 6.0, 2: 6.0, 3: 6.0}}
    minutes = {"p": {1: 90, 2: 90, 3: 90}}
    cells = {"p": ("Porto", Position.FWD.value)}
    table = fixture_table([(rows(match(1, "Porto", "Braga")), 0)])

    plain = two_part_projection(points, minutes, cells, upto=3)
    # Round 3 is not in the table at all.
    assert fixture_adjusted_projection(
        points, minutes, cells, table, upto=3
    ) == pytest.approx(plain)
    # Round 1 is, so that one does move.
    assert fixture_adjusted_projection(
        points, minutes, cells, table, upto=1
    ) != pytest.approx(two_part_projection(points, minutes, cells, upto=1))


def test_only_the_returns_half_is_rescaled():
    """§10.3(i) pays the same -1 whoever the opponent is. Scaling the blended
    estimate would make an easy fixture a reason to own a man who is not in the
    side, which is the opposite of what the adjustment is for.

    A player who never appears is pinned to the penalty, and no opponent moves
    him off it.

    Projected at matchday 2, so matchday 1 is evidence and matchday 2 is the
    fixture. At matchday 1 there is no history at all and every estimate falls
    back on the league, which would make this test pass without meaning it.
    """
    points = {"absent": {1: ABSENT, 2: ABSENT}, "plays": {1: 8.0, 2: 8.0}}
    minutes = {"absent": {1: 0, 2: 0}, "plays": {1: 90, 2: 90}}
    cells = {i: ("Porto", Position.FWD.value) for i in points}
    table = fixture_table(
        [(rows(match(1, "Porto", "Braga"), match(2, "Porto", "Benfica")), 0)]
    )

    adjusted = fixture_adjusted_projection(points, minutes, cells, table, upto=2)
    assert adjusted["absent"] == pytest.approx(ABSENT)
    assert adjusted["plays"] != pytest.approx(
        two_part_projection(points, minutes, cells, upto=2)["plays"]
    )
