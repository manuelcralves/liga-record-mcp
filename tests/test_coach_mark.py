"""What §14.3 leaves unexplained about a coach's round.

The number this produces decided something: measured over matchdays 6 and 7,
what is left over grows with the result — +5.00 on a win against +2.50 on a
loss — so the model's single constant is the wrong shape and was left alone.
A measurement that decides has to be right about what it subtracts.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.models import Fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location(
        "measure_coach_mark", ROOT / "scripts" / "measure_coach_mark.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(home, away, home_goals=None, away_goals=None, round_number=6):
    return Fixture(
        round_number=round_number,
        home=home,
        away=away,
        home_goals=home_goals,
        away_goals=away_goals,
    )


def test_the_two_spellings_of_a_club_are_matched(measure):
    """The email writes "Académico" where the calendar writes "Académico
    Viseu", and a club missed here is a coach missing from the measurement."""
    assert measure.same_club("Académico", "Académico Viseu")
    assert measure.same_club("Sp. Braga", "Sp Braga")
    assert not measure.same_club("Nacional", "Internacional")
    assert not measure.same_club("", "Benfica")


def test_only_a_match_that_was_played_gives_a_scoreline(measure):
    fixtures = [
        fixture("Benfica", "Arouca", 2, 1),
        fixture("Sporting", "Estoril"),
    ]
    found = measure.results_in(fixtures, 6)
    assert found == {"Benfica": (2, 1), "Arouca": (1, 2)}


def test_what_is_left_is_the_round_minus_what_the_rules_pay(measure):
    """§14.3 is scored on the real result, with no mark and none of the events
    a results model cannot see; the difference is what the constant covers.

    The numbers are pinned rather than derived, because the arithmetic this
    test exists to protect — which side is `scored` and which is `conceded` —
    survives every assertion that only restates the code.
    """
    rounds = {
        6: {
            "ronda": 6,
            "treinadores": {
                "Marco Silva|Benfica": 7,
                "Vasco Seabra|Arouca": 2,
                "Quem Nao Jogou|Sporting": 4,
            },
        }
    }
    fixtures = [fixture("Benfica", "Arouca", 3, 0), fixture("Sporting", "Estoril")]
    rows = {row["name"]: row for row in measure.rows_for(rounds, fixtures)}

    # A club whose match was not played is not a row at all.
    assert set(rows) == {"Marco Silva", "Vasco Seabra"}
    winner, loser = rows["Marco Silva"], rows["Vasco Seabra"]
    assert (winner["result"], loser["result"]) == ("vitoria", "derrota")

    # 3-0 is §14.3(a) the win, (c) the clean sheet and (c) the margin over two.
    assert (winner["rules"], loser["rules"]) == (3, -3)
    assert (winner["left"], loser["left"]) == (7 - 3, 2 + 3)


def test_the_interval_is_the_one_the_bar_is_read_on(measure):
    """1.645 standard errors of the mean, and nothing else: a different
    constant, the population deviation, or dividing by n would all move it."""
    average, low, high = measure.interval([2.0, 4.0, 6.0])
    assert average == pytest.approx(4.0)
    assert low == pytest.approx(4.0 - 1.645 * 2.0 / 3**0.5)
    assert high == pytest.approx(4.0 + 1.645 * 2.0 / 3**0.5)
    assert measure.interval([3.0]) == (3.0, 0.0, 0.0)


def row(left, result, name="X", club="Y", round_number=6):
    return {
        "round": round_number,
        "name": name,
        "club": club,
        "points": left,
        "rules": 0,
        "left": left,
        "result": result,
    }


def test_a_measurement_with_nothing_in_it_never_reads_as_a_pass(measure):
    """It did, before this test: an empty run has no interval, `interval`
    answers 0.0, and 0.0 excludes 2.59 the way a real result would — the
    script printed a confident verdict over no data at all."""
    for rows in ([], [row(4.0, "vitoria")]):
        answer = measure.verdict(rows)
        assert answer["level"] is False
        assert answer["change"] is False


def test_the_shape_governs_the_level(measure):
    """The decision that was actually taken on 23/09/2026: the level bar passes
    on its own, and the constant still does not move, because the leftover
    walks with the result.

    The rows sit far above whatever the model pays, so this keeps deciding the
    same way when the constant is refitted on a later round.
    """
    walking = [row(7.0, "vitoria"), row(7.2, "vitoria"), row(6.8, "vitoria")] + [
        row(6.0, "derrota"),
        row(6.1, "derrota"),
        row(5.9, "derrota"),
    ]
    answer = measure.verdict(walking)
    assert answer["level"] is True, "6.5 is far enough from the constant to pass"
    assert answer["flat"] is False
    assert answer["change"] is False

    # Level the two sides and the same rows do move it.
    steady = [row(7.0, "vitoria"), row(7.2, "vitoria"), row(6.8, "vitoria")] + [
        row(7.1, "derrota"),
        row(6.9, "derrota"),
        row(7.0, "derrota"),
    ]
    assert measure.verdict(steady)["change"] is True


def test_the_players_average_mark_still_means_what_it_says():
    """It was left where it was, and it is the players' number: `reconstruct_points`
    uses it for a match that went unrated, which is a different question from
    what a coach is paid."""
    from liga_record_mcp.models import Position
    from liga_record_mcp.stats import MEAN_MARK_POINTS, reconstruct_points

    unrated = reconstruct_points(
        Position.MID, conceded=1, won=False, used=True, rating=None, goals=0, minutes=90
    )
    assert unrated["mark"] == pytest.approx(MEAN_MARK_POINTS)
    assert unrated["mark_source"] == "assumed average"
