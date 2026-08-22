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
    SUSPENDED_PLAYS,
    adjusted_projection,
    card_table,
    position_bias,
    club_form_upto,
    fixture_adjusted_projection,
    fixture_table,
    suspensions,
    two_part_projection,
)
from liga_record_mcp.models import LAST_MATCHDAY, Position
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
    """A hard fixture dents the variable part; it cannot eat the floor.

    Strictly below, and expressed against the constant rather than as a number.
    This used to pass 1.0 against a floor of 2.0; when the floor was remeasured
    down to 1.0 the test went on passing while testing a value exactly ON it,
    which is a weaker claim wearing the same name.
    """
    adjusted = adjust_for_fixture(
        APPEARANCE_FLOOR / 2, Position.DEF, 0.55, 0.55
    )
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


# --------------------------------------------------------------------------
# The one absence that is known before the deadline
# --------------------------------------------------------------------------


def booked(round_number, yellows=0, red=False, club="Porto", opponent="Braga"):
    return {
        "round": round_number,
        "club": club,
        "opponent": opponent,
        "at_home": True,
        "scored": 1,
        "conceded": 0,
        "yellow_cards": yellows,
        "red_card": red,
        "points": 3.0,
        "minutes": 90,
    }


def test_a_red_card_bans_the_next_round_and_only_the_next():
    """Measured, not assumed. Of the players sent off, 12% and 0% played the
    round after; by the round after that 82% and 91% were back — at or above
    the 86% baseline for anyone who played at all. One round."""
    cards = card_table([({"p": {"matches": [booked(4, red=True)]}}, 0)])
    assert suspensions(cards, 5) == {"p"}
    assert suspensions(cards, 6) == set()
    assert suspensions(cards, 4) == set()


def test_the_fifth_yellow_bans_and_the_fourth_does_not():
    """Five is not looked up, it is measured. Crossing a multiple of five takes
    a player from 86% to 22% and 10%; three, four and six do nothing at all —
    82/83, 82/90, 80/79 against that same 86%. One threshold has a cliff and
    its neighbours are flat, which is what a real rule looks like from outside.
    """
    def upto_round(*per_round):
        return card_table(
            [({"p": {"matches": [booked(m + 1, y) for m, y in enumerate(per_round)]}}, 0)]
        )

    assert suspensions(upto_round(1, 1, 1, 1), 5) == set()
    assert suspensions(upto_round(1, 1, 1, 1, 1), 6) == {"p"}
    # And again at ten, not only the first time.
    assert suspensions(upto_round(*([1] * 10)), 11) == {"p"}
    assert suspensions(upto_round(*([1] * 9)), 10) == set()


def test_bookings_do_not_carry_across_the_turn_of_the_season():
    """The bug the archive caught.

    The season before sits on matchdays at or below zero. A total swept through
    the boundary put a man on four yellows from last year one card from a ban
    in August, which is not a rule in any competition — and it was invisible
    until the archive was run as its own configuration: the signal measured
    +0.0172 and +0.0211 on the two seasons alone and MINUS 0.0015 with the
    archive attached. A weak effect does not change sign. A bug does.
    """
    last_year = {"p": {"matches": [booked(m, 1) for m in range(31, 35)]}}
    this_year = {"p": {"matches": [booked(1, 1)]}}
    cards = card_table([(this_year, 0), (last_year, -LAST_MATCHDAY)])

    # Four from last season plus one from this is not five.
    assert suspensions(cards, 2) == set()
    # And a red in the archive's last round does not reach the opening day.
    crossing = card_table(
        [({"p": {"matches": [booked(1)]}}, 0),
         ({"p": {"matches": [booked(LAST_MATCHDAY, red=True)]}}, -LAST_MATCHDAY)]
    )
    assert suspensions(crossing, 1) == set()


def test_a_ban_is_settled_before_the_round_it_applies_to():
    """The rule the whole backtest rests on. Nothing about round N may be read
    to decide round N — and a suspension does not need it, which is exactly
    what makes it usable: §6.13 closes fifteen minutes before the first match
    of the round, so a confirmed line-up is too late for eight games in nine
    while a red card was shown a week earlier."""
    cards = card_table([({"p": {"matches": [booked(4, red=True), booked(5, red=True)]}}, 0)])
    before = suspensions(cards, 5)

    rewritten = {
        key: value for key, value in cards.items() if key[0] < 5
    }
    assert suspensions(rewritten, 5) == before == {"p"}


def test_a_ban_moves_the_appearance_half_and_leaves_the_returns_half_alone():
    """What a man is worth on the days he plays has nothing to do with his not
    playing this one. Fold them together and an easy fixture starts arguing for
    a player who is banned."""
    points = {"p": {m: 9.0 for m in range(1, 6)}}
    minutes = {"p": {m: 90 for m in range(1, 6)}}
    cells = {"p": ("Porto", Position.FWD.value)}
    cards = card_table([({"p": {"matches": [booked(4, red=True)]}}, 0)])

    playing, returns = two_part_projection(points, minutes, cells, upto=5, parts=True)
    banned = adjusted_projection(points, minutes, cells, upto=5, cards=cards)

    # The returns half is untouched, so the whole move is in `playing`.
    expected = SUSPENDED_PLAYS * returns["p"] + (1 - SUSPENDED_PLAYS) * ABSENT
    assert banned["p"] == pytest.approx(expected)
    assert banned["p"] < playing["p"] * returns["p"] + (1 - playing["p"]) * ABSENT


# --------------------------------------------------------------------------
# Correcting a position against the positions
# --------------------------------------------------------------------------


def test_the_bias_is_the_mean_miss_of_the_position():
    """Nothing cleverer. A ridge given position as a plain one-hot found
    +0.0077, +0.0030 and +0.0033 of correlation in this gap; this is the closed
    form of the same thing, with no constant to set."""
    cells = {
        "a": ("Porto", Position.FWD.value),
        "b": ("Braga", Position.FWD.value),
        "c": ("Porto", Position.GK.value),
    }
    points = {"a": {1: 8.0}, "b": {1: 4.0}, "c": {1: 1.0}}
    seen = {1: {"a": 5.0, "b": 3.0, "c": 4.0}}

    bias = position_bias(points, cells, seen)
    assert bias[Position.FWD] == pytest.approx(2.0)   # missed by +3 and +1
    assert bias[Position.GK] == pytest.approx(-3.0)
    # A position nobody played is left at zero rather than guessed.
    assert bias[Position.DEF] == pytest.approx(0.0)


def test_the_bias_cannot_see_the_round_it_corrects():
    """The rule the whole backtest rests on, applied to the newest thing that
    reads the past.

    `seen` is what was said BEFORE each round, and the correction is the mean
    of how wrong that turned out. Rebuilding those estimates later, with what
    is known now, would score the model against rounds it had already been
    fitted on — and the bias would come back flattering and useless. So the
    caller passes the estimates it actually made, and this asserts that nothing
    from an unseen round can reach the answer.
    """
    cells = {"a": ("Porto", Position.FWD.value)}
    points = {"a": {1: 8.0, 2: 4.0, 3: 999.0}}
    seen = {1: {"a": 5.0}, 2: {"a": 3.0}}

    before = position_bias(points, cells, seen)
    # Round 3 is not in `seen`, so rewriting it must change nothing.
    wrecked = {"a": {1: 8.0, 2: 4.0, 3: -999.0}}
    assert position_bias(wrecked, cells, seen) == before
    # And it does move when a round it HAS seen changes, or the guard above
    # would pass on a function that read nothing.
    changed = {"a": {1: 0.0, 2: 4.0, 3: 999.0}}
    assert position_bias(changed, cells, seen) != before


def test_the_correction_lands_on_the_blend_and_not_on_the_returns():
    """A position being under-projected is a fact about the whole estimate, not
    about what a man scores on the days he plays. It is added last, after the
    two halves have been put back together."""
    points = {"p": {m: 6.0 for m in range(1, 5)}}
    minutes = {"p": {m: 90 for m in range(1, 5)}}
    cells = {"p": ("Porto", Position.FWD.value)}

    plain = adjusted_projection(points, minutes, cells, upto=4)
    lifted = adjusted_projection(
        points, minutes, cells, upto=4, bias={Position.FWD: 1.5}
    )
    assert lifted["p"] == pytest.approx(plain["p"] + 1.5)


def test_the_appearance_floor_is_what_was_measured():
    """Pinned, because it moved on evidence and the old value has a reasoned
    comment behind it that reads just as convincingly. 2.0 was the low end of
    a two-to-three reading; the sweep put the optimum lower on both seasons."""
    assert APPEARANCE_FLOOR == 1.0
