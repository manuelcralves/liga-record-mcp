"""Tests for the derived statistics.

These exist because of a specific mistake: a transfer proposal ranked the squad
on total points while two clubs had a postponed fixture and one match fewer.
The regression test at the bottom reproduces exactly that situation.
"""

from __future__ import annotations

import pytest
from helpers import make_player

from liga_record_mcp.models import Fixture, Position
from liga_record_mcp.stats import matches_played, never_played, per_match, rate_rows

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
