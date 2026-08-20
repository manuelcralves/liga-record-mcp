"""How much of a squad rides on one match, and on one club.

Round two is the reason this exists. Ten of the twenty-three players held then
belonged to Sp. Braga and Gil Vicente — three of the four forwards among them —
and when that single fixture was postponed all ten scored nothing at once.
Nothing in the project was looking at the shape of that risk.
"""

from __future__ import annotations

from helpers import make_player

from liga_record_mcp.models import Fixture, Position
from liga_record_mcp.stats import (
    club_concentration,
    fixture_exposure,
    upcoming_opponents,
)


def fixture(round_number: int, home: str, away: str, *, played: bool = False) -> Fixture:
    return Fixture(
        round_number=round_number,
        home=home,
        away=away,
        home_goals=1 if played else None,
        away_goals=0 if played else None,
        kickoff="22 AGO 20:30",
    )


CALENDAR = [
    fixture(3, "FC Porto", "Arouca"),
    fixture(3, "Sporting", "Alverca"),
    fixture(3, "Gil Vicente", "Casa Pia"),
    fixture(4, "Arouca", "Sporting"),
    fixture(4, "Casa Pia", "FC Porto"),
    fixture(5, "FC Porto", "Gil Vicente"),
]


def squad(*clubs: str):
    return [
        make_player(f"p{i}", Position.DEF, 1_000_000, club=club)
        for i, club in enumerate(clubs)
    ]


# --------------------------------------------------------------------------
# Upcoming fixtures
# --------------------------------------------------------------------------


def test_the_next_fixtures_come_back_in_round_order():
    found = upcoming_opponents(CALENDAR, "FC Porto", from_round=3)

    assert found == [(3, "Arouca", True), (4, "Casa Pia", False), (5, "Gil Vicente", True)]


def test_earlier_rounds_are_not_included():
    assert upcoming_opponents(CALENDAR, "FC Porto", from_round=4)[0] == (
        4,
        "Casa Pia",
        False,
    )


def test_the_count_is_respected():
    assert len(upcoming_opponents(CALENDAR, "FC Porto", from_round=3, count=2)) == 2


def test_a_club_with_no_fixtures_gets_nothing():
    assert upcoming_opponents(CALENDAR, "Benfica", from_round=3) == []


def test_rounds_are_read_in_order_not_by_date():
    """A postponed club has its rounds out of chronological sequence.

    Sorting by kickoff would quietly reorder who it plays next, which is how a
    fixture grid ends up showing the wrong opponent for the wrong week.
    """
    jumbled = [
        Fixture(round_number=4, home="A", away="B", kickoff="31 AGO 20:45"),
        Fixture(round_number=3, home="C", away="A", kickoff="10 SET 20:15"),
    ]
    assert upcoming_opponents(jumbled, "A", from_round=3) == [
        (3, "C", False),
        (4, "B", True),
    ]


# --------------------------------------------------------------------------
# One match, many players
# --------------------------------------------------------------------------


def test_the_heaviest_match_comes_first():
    players = squad("FC Porto", "FC Porto", "FC Porto", "Sporting")
    rows = fixture_exposure(players, CALENDAR, 3)

    assert rows[0]["home"] == "FC Porto"
    assert rows[0]["count"] == 3


def test_players_on_both_sides_are_flagged():
    """Clean sheets are mutually exclusive — this caps the upside too."""
    players = squad("FC Porto", "FC Porto", "Arouca")
    rows = fixture_exposure(players, CALENDAR, 3)

    assert rows[0]["both_sides"] is True
    assert rows[0]["count"] == 3
    assert len(rows[0]["home_players"]) == 2
    assert len(rows[0]["away_players"]) == 1


def test_one_sided_exposure_is_not_flagged():
    rows = fixture_exposure(squad("FC Porto", "FC Porto"), CALENDAR, 3)

    assert rows[0]["both_sides"] is False


def test_matches_without_any_of_the_squad_are_omitted():
    rows = fixture_exposure(squad("FC Porto"), CALENDAR, 3)

    assert [r["home"] for r in rows] == ["FC Porto"]


def test_only_the_round_asked_for_is_considered():
    players = squad("FC Porto", "Casa Pia")
    third = fixture_exposure(players, CALENDAR, 3)
    fourth = fixture_exposure(players, CALENDAR, 4)

    assert {r["home"] for r in third} == {"FC Porto", "Gil Vicente"}
    assert fourth[0]["home"] == "Casa Pia"
    assert fourth[0]["both_sides"] is True


def test_an_empty_squad_produces_nothing():
    assert fixture_exposure([], CALENDAR, 3) == []


# --------------------------------------------------------------------------
# One club, many players
# --------------------------------------------------------------------------


def test_clubs_are_ranked_by_how_many_players_are_held():
    counts = club_concentration(squad("Arouca", "FC Porto", "Arouca", "Arouca", "Sporting"))

    assert counts[0] == ("Arouca", 3)
    assert counts[1] == ("FC Porto", 1)


def test_ties_break_by_name_so_the_order_is_stable():
    counts = club_concentration(squad("Sporting", "Arouca"))

    assert counts == [("Arouca", 1), ("Sporting", 1)]


def test_the_round_two_shape_is_what_it_looks_like():
    """The real exposure that cost the round: ten players, one fixture."""
    players = squad(*(["Sp. Braga"] * 4 + ["Gil Vicente"] * 6 + ["FC Porto"] * 3))
    calendar = [fixture(2, "Sp. Braga", "Gil Vicente")]
    rows = fixture_exposure(players, calendar, 2)

    assert rows[0]["count"] == 10
    assert rows[0]["both_sides"] is True
    assert club_concentration(players)[0] == ("Gil Vicente", 6)
