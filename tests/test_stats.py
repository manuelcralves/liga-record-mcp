"""Tests for the derived statistics.

These exist because of a specific mistake: a transfer proposal ranked the squad
on total points while two clubs had a postponed fixture and one match fewer.
The regression test at the bottom reproduces exactly that situation.
"""

from __future__ import annotations

import pytest
from helpers import make_player

from liga_record_mcp.models import Fixture, Position
from liga_record_mcp.stats import (
    NO_MATCH,
    PLAYED,
    PRIOR_STRENGTH,
    UNUSED,
    availability,
    matches_played,
    never_played,
    per_match,
    project,
    rate_rows,
    recent_appearance_rate,
)

CALENDAR = [
    Fixture(round_number=1, home="Arouca", away="Porto", home_goals=1, away_goals=0),
    Fixture(round_number=1, home="Braga", away="Gil Vicente", home_goals=2, away_goals=2),
    Fixture(round_number=2, home="Porto", away="Arouca", home_goals=3, away_goals=1),
    # Braga v Gil Vicente postponed — both sit a match behind.
    Fixture(round_number=2, home="Braga", away="Gil Vicente", kickoff="20 AGO 20:30"),
]


def test_only_played_matches_are_counted():
    counts = matches_played(CALENDAR)
    assert counts == {"Arouca": 2, "Porto": 2, "Braga": 1, "Gil Vicente": 1}


def test_an_empty_calendar_counts_nothing():
    assert matches_played([]) == {}
    assert matches_played([f for f in CALENDAR if not f.played]) == {}


def test_per_match_divides():
    assert per_match(9, 2) == 4.5
    assert per_match(6, 1) == 6.0


def test_no_matches_gives_none_not_zero():
    """"No data" and "scored nothing" are different claims."""
    assert per_match(0, 0) is None
    assert per_match(5, 0) is None
    assert per_match(0, 2) == 0.0


def test_never_played_reads_the_unused_penalty():
    """§10.3 — an unused player scores -1 a round."""
    idle = make_player("X", Position.DEF, 500_000, points_total=-2)
    assert never_played(idle, 2) is True

    played_badly = make_player("Y", Position.DEF, 500_000, points_total=-1)
    assert never_played(played_badly, 2) is False

    unknown = make_player("Z", Position.DEF, 500_000, points_total=0)
    assert never_played(unknown, 0) is False  # no matches yet, so nothing to say


def test_rate_rows_rank_by_rate_not_total():
    """The whole point: 6 points in one match beats 9 in two."""
    counts = matches_played(CALENDAR)
    players = [
        make_player("A", Position.MID, 5_000_000, points_total=9, name="Zalazar"),
        make_player("B", Position.FWD, 2_500_000, points_total=6, name="Fran Navarro"),
    ]
    players[0] = players[0].model_copy(update={"club": "Porto"})
    players[1] = players[1].model_copy(update={"club": "Braga"})

    rows = rate_rows(players, counts)
    assert [r["name"] for r in rows] == ["Fran Navarro", "Zalazar"]
    assert rows[0]["points_per_match"] == 6.0
    assert rows[1]["points_per_match"] == 4.5


def test_value_rate_normalises_price_as_well():
    counts = matches_played(CALENDAR)
    cheap = make_player("A", Position.DEF, 500_000, points_total=4).model_copy(
        update={"club": "Porto"}
    )
    dear = make_player("B", Position.DEF, 4_000_000, points_total=4).model_copy(
        update={"club": "Porto"}
    )
    rows = {r["id"]: r for r in rate_rows([cheap, dear], counts)}

    assert rows["A"]["points_per_match"] == rows["B"]["points_per_match"] == 2.0
    assert rows["A"]["value_rate"] == 4.0  # 2.0 per match per €0.5M
    assert rows["B"]["value_rate"] == 0.5


def test_players_without_matches_sort_last():
    counts = matches_played(CALENDAR)
    scorer = make_player("A", Position.MID, 500_000, points_total=4).model_copy(
        update={"club": "Porto"}
    )
    unknown = make_player("B", Position.MID, 500_000, points_total=0).model_copy(
        update={"club": "Nowhere"}
    )
    rows = rate_rows([unknown, scorer], counts)
    assert [r["id"] for r in rows] == ["A", "B"]
    assert rows[1]["points_per_match"] is None


def test_the_postponed_fixture_regression():
    """The exact mistake this module was written to prevent.

    Fran Navarro (Braga, one match) scored 6; Javi Sánchez (Arouca, two) scored
    12. Ranked on totals Fran looks half as good. Per match he is better.
    """
    counts = matches_played(CALENDAR)
    fran = make_player("fran", Position.FWD, 2_500_000, points_total=6).model_copy(
        update={"club": "Braga"}
    )
    javi = make_player("javi", Position.DEF, 500_000, points_total=12).model_copy(
        update={"club": "Arouca"}
    )

    by_total = sorted([fran, javi], key=lambda p: -p.points_total)
    assert by_total[0].id == "javi"  # what the flawed proposal did

    by_rate = rate_rows([fran, javi], counts)
    assert by_rate[0]["id"] == "fran"
    assert by_rate[0]["points_per_match"] == 6.0
    assert by_rate[1]["points_per_match"] == 6.0
    # A tie on rate — which is the honest answer, not a 2:1 gap.


def test_the_prior_is_worth_what_it_was_measured_to_be_worth():
    """`PRIOR_STRENGTH` was a judgement call for as long as there was nothing
    to check it against. A reconstructed season measured it at 8 to 20, flat
    across that range, and 4 — the old value — measurably worse.

    Pinned here so that changing it is a decision rather than a drift. If a
    later measurement moves it, move this with it and say what measured it.
    """
    assert 8.0 <= PRIOR_STRENGTH <= 20.0


def test_a_hot_start_moves_a_projection_less_than_the_player_who_had_it_would_like():
    """The finding behind that constant: a player's own record early on is
    weaker evidence than what his club and position usually return. After two
    rounds the projection should still be mostly prior.
    """
    weight_on_two_rounds = 2 / (2 + PRIOR_STRENGTH)
    assert weight_on_two_rounds < 0.25


# --------------------------------------------------------------------------
# Whether he is in the side
# --------------------------------------------------------------------------


def played_rounds(*statuses):
    return {str(n): status for n, status in enumerate(statuses, 1)}


def test_two_appearances_are_not_a_guarantee():
    """The error this shrinkage exists to stop. A player who appeared in both
    of the two rounds on file used to come out at a flat 100%, and since a
    certain starter projects at roughly twice a naive average, the
    overconfidence went straight into the recommendation.

    The band is measured rather than chosen. Across both reconstructed seasons,
    a player who played the previous two rounds played the next one 87.3% of
    the time, over 5395 cases — so an estimate near there is right and one at
    100% is not. The bound used to be a flat 0.9, which was a guess that
    happened to be close and would have failed for the wrong reason.
    """
    estimate = availability(played_rounds(PLAYED, PLAYED), league_rate=0.51)
    assert 0.80 < estimate < 0.95
    assert abs(estimate - 0.873) < 0.06


def test_a_season_of_appearances_is_close_to_a_guarantee():
    """The shrinkage must fade as evidence accumulates, or a regular would be
    permanently understated."""
    always = played_rounds(*[PLAYED] * 20)
    assert availability(always, league_rate=0.51) > 0.95


def test_losing_a_place_shows_up_quickly():
    """The whole point of the recent window. A man who played nine and has sat
    out the last five is not a 64% player, whatever the season says."""
    faded = played_rounds(*([PLAYED] * 9 + [UNUSED] * 5))
    assert availability(faded, league_rate=0.51) < 0.35


def test_coming_back_into_the_side_shows_up_quickly_too():
    """Symmetry. A signal that only notices bad news would sell every player
    who ever had an injury and buy none of them back."""
    returning = played_rounds(*([UNUSED] * 9 + [PLAYED] * 5))
    assert availability(returning, league_rate=0.51) > 0.5


def test_a_postponed_round_is_not_a_dropping():
    """Rounds where the club did not play are skipped. Counting them as
    absences would punish every player at a club with a rearranged fixture."""
    postponed = played_rounds(PLAYED, NO_MATCH, NO_MATCH, PLAYED)
    straight = played_rounds(PLAYED, PLAYED)
    assert availability(postponed, league_rate=0.51) == availability(
        straight, league_rate=0.51
    )


def test_a_player_nobody_has_a_record_for_gets_the_league_rate():
    """Which is what "we do not know" means, and is a long way from zero."""
    assert availability(None, league_rate=0.51) == 0.51
    assert availability({}, league_rate=0.51) == 0.51
    assert availability(played_rounds(NO_MATCH), league_rate=0.51) == 0.51


def test_the_recent_window_reports_how_much_it_rests_on():
    """A rate from two rounds and a rate from five are different claims, and
    the caller has to weight them differently."""
    assert recent_appearance_rate(played_rounds(PLAYED, UNUSED), window=5) == (0.5, 2)
    assert recent_appearance_rate(
        played_rounds(*[PLAYED] * 8), window=5
    ) == (1.0, 5)
    assert recent_appearance_rate({}, window=5) is None


def test_splitting_a_projection_does_not_inflate_it():
    """The check that makes the split safe to turn on. Working per appearance
    means dividing the prior by the league's availability, which nearly doubles
    it — and if that were not exactly undone by the chance of not playing, every
    projection in the tool would have silently jumped.

    A player at exactly the league rate must come out identical either way.
    """
    from liga_record_mcp.models import ClubRecord

    player = make_player("p1", position=Position.MID, points_total=8, value=2_000_000)
    common = dict(
        record=None,
        baselines={Position.MID: 2.5},
        position_mean_value=2_000_000,
        league_goals_against=1.4,
        league_goals_for=1.4,
    )
    league_rate = 0.51
    folded = project(player, 4, **common)
    split = project(
        player,
        4,
        **common,
        appearances=2,
        expected_availability=league_rate,
        league_availability=league_rate,
    )
    # A rate per club match becomes a rate per appearance by taking the
    # league's absences back out of it, not by dividing.
    expected = (folded["prior_rate"] + (1 - league_rate)) / league_rate
    assert abs(split["prior_rate"] - expected) < 0.02

    # And the round trip closes: a player who has never played, at exactly the
    # league's availability, projects identically either way.
    unseen = make_player("p2", position=Position.MID, points_total=0, value=2_000_000)
    assert abs(
        project(
            unseen,
            1,
            **common,
            appearances=0,
            expected_availability=league_rate,
            league_availability=league_rate,
        )["projected_rate"]
        - project(unseen, 0, **common)["projected_rate"]
    ) < 0.01
