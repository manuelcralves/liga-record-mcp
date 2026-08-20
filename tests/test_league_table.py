"""The Primeira Liga table, folded out of the calendar we already hold.

Nothing new is fetched: the calendar carries scores for played matches, so the
standings are arithmetic over data the project has had all along.
"""

from __future__ import annotations

from liga_record_mcp.models import Fixture
from liga_record_mcp.stats import league_table


def played(home: str, away: str, home_goals: int, away_goals: int, rnd: int = 1) -> Fixture:
    return Fixture(
        round_number=rnd,
        home=home,
        away=away,
        home_goals=home_goals,
        away_goals=away_goals,
        kickoff="09 AGO 20:30",
    )


def scheduled(home: str, away: str, rnd: int = 2) -> Fixture:
    return Fixture(round_number=rnd, home=home, away=away, kickoff="16 AGO 20:30")


def by_club(rows):
    return {r["club"]: r for r in rows}


def test_a_win_is_three_points_and_a_draw_is_one():
    rows = by_club(league_table([played("A", "B", 2, 0), played("C", "D", 1, 1)]))

    assert rows["A"]["points"] == 3
    assert rows["B"]["points"] == 0
    assert rows["C"]["points"] == rows["D"]["points"] == 1


def test_goals_are_counted_from_both_ends():
    rows = by_club(league_table([played("A", "B", 3, 1)]))

    assert (rows["A"]["goals_for"], rows["A"]["goals_against"]) == (3, 1)
    assert (rows["B"]["goals_for"], rows["B"]["goals_against"]) == (1, 3)
    assert rows["A"]["goal_difference"] == 2
    assert rows["B"]["goal_difference"] == -2


def test_results_are_tallied_into_won_drawn_lost():
    rows = by_club(
        league_table(
            [played("A", "B", 1, 0), played("A", "C", 1, 1, 2), played("D", "A", 2, 0, 3)]
        )
    )
    assert (rows["A"]["won"], rows["A"]["drawn"], rows["A"]["lost"]) == (1, 1, 1)
    assert rows["A"]["played"] == 3


def test_an_unplayed_fixture_contributes_nothing():
    """A scheduled match is not a nil-nil."""
    rows = league_table([played("A", "B", 1, 0), scheduled("C", "D")])

    assert {r["club"] for r in rows} == {"A", "B"}


def test_clubs_are_not_assumed_to_be_level_on_matches():
    """One postponement puts two clubs a game behind.

    A table that hides that is the same error as a points total without its
    denominator, which is the mistake this whole module exists to prevent.
    """
    rows = by_club(
        league_table([played("A", "B", 1, 0), played("A", "C", 1, 0, 2)])
    )
    assert rows["A"]["played"] == 2
    assert rows["B"]["played"] == 1


def test_difference_separates_clubs_level_on_points():
    rows = league_table([played("Narrow", "X", 1, 0), played("Wide", "Y", 3, 0)])

    # Both won; +3 beats +1.
    assert [r["club"] for r in rows][:2] == ["Wide", "Narrow"]


def test_goals_scored_separate_clubs_level_on_difference():
    rows = league_table([played("Quiet", "X", 1, 0), played("Loud", "Y", 2, 1)])

    # Both on 3 points and +1; the one who scored more goes above.
    assert [r["club"] for r in rows][:2] == ["Loud", "Quiet"]


def test_a_dead_heat_breaks_by_name_so_the_table_is_stable():
    rows = league_table([played("Zeta", "A", 1, 0), played("Alfa", "B", 1, 0)])

    assert [r["club"] for r in rows][:2] == ["Alfa", "Zeta"]
    assert league_table([played("Zeta", "A", 1, 0), played("Alfa", "B", 1, 0)]) == rows


def test_positions_are_numbered_from_one():
    rows = league_table([played("A", "B", 4, 0), played("C", "D", 1, 1)])

    assert [r["position"] for r in rows] == [1, 2, 3, 4]


def test_an_empty_calendar_is_an_empty_table():
    assert league_table([]) == []
    assert league_table([scheduled("A", "B")]) == []
