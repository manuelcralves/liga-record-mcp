"""What a coach scores beyond the scoreline — priced, measured, not adopted.

The machinery is here and dormant, like the insured eleven: `beyond=None` is
what every caller passes, so the model still pays a flat mark. Fitted on one
emailed round and measured on the other it won one fold and lost the other,
and against a control that fixed only the LEVEL it was worth 0.01.

So these tests pin two things. That the path is faithful — a flat mapping down
it reproduces, to the cent, the model that is actually running — and that
nothing has quietly started using the shape.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.coaches import coach_points_by_club, rank_coaches, same_club
from liga_record_mcp.final_table import coach_values
from liga_record_mcp.models import Fixture
from liga_record_mcp.stats import (
    COACH_BEYOND_RESULT,
    MEAN_MARK_POINTS,
    expected_coach_points,
    expected_coach_round,
    result_chances,
)

ROOT = Path(__file__).resolve().parents[1]

STRENGTH = {"Benfica": (1.85, 0.72), "Arouca": (0.88, 1.36)}


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location(
        "measure_coach_round", ROOT / "scripts" / "measure_coach_round.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_three_results_are_the_whole_of_what_can_happen():
    win, draw, loss = result_chances(1.6, 1.1)
    assert win + draw + loss == pytest.approx(1.0, abs=1e-5)
    assert win > loss, "the side scoring more wins more often"

    # Two equal sides: the win and the loss are the same size, and the draw is
    # neither of them. This is the symmetry §14.3's own expectation rests on.
    level_win, level_draw, level_loss = result_chances(1.3, 1.3)
    assert level_win == pytest.approx(level_loss)
    assert 0.0 < level_draw < level_win


def test_nothing_beyond_the_scoreline_is_the_rules_themselves():
    assert expected_coach_round(1.7, 0.9) == expected_coach_points(1.7, 0.9)
    assert expected_coach_round(1.7, 0.9, beyond=None) == expected_coach_points(1.7, 0.9)


def test_one_number_three_times_is_the_model_that_is_running():
    """The flat mark, said in the new shape. The comparison that refused the
    shape was only fair because this holds to the cent."""
    flat = {result: MEAN_MARK_POINTS for result in ("win", "draw", "loss")}
    for goals_for, goals_against in ((1.9, 0.8), (1.0, 1.0), (0.7, 2.1)):
        assert expected_coach_round(
            goals_for, goals_against, beyond=flat
        ) == pytest.approx(
            expected_coach_points(goals_for, goals_against) + MEAN_MARK_POINTS,
            abs=1e-5,
        )


def test_by_result_the_favourite_is_credited_more_than_the_underdog():
    """Which is the whole of why it could reorder the eighteen, and why it was
    measured instead of assumed."""
    favourite = expected_coach_round(2.0, 0.7, beyond=COACH_BEYOND_RESULT)
    favourite -= expected_coach_points(2.0, 0.7)
    underdog = expected_coach_round(0.7, 2.0, beyond=COACH_BEYOND_RESULT)
    underdog -= expected_coach_points(0.7, 2.0)
    assert favourite - underdog > 1.0
    # And neither leaves the range the fit was drawn from.
    assert COACH_BEYOND_RESULT["loss"] < underdog < favourite < COACH_BEYOND_RESULT["win"]


def test_the_page_and_the_ledger_still_pay_the_flat_mark():
    """The dormancy test. If a caller ever passes `beyond`, this fails, and it
    should: the shape reorders the eighteen and did not earn that."""
    fixtures = [Fixture(round_number=8, home="Benfica", away="Arouca")]
    rules = coach_values([("Benfica", "Arouca")], STRENGTH)
    ranked = rank_coaches(
        [
            {"id": 1, "name": "Marco Silva", "club": "Benfica"},
            {"id": 2, "name": "Vasco Seabra", "club": "Arouca"},
        ],
        fixtures,
        STRENGTH,
        8,
    )
    for row in ranked:
        assert row["expected"] == pytest.approx(rules[row["club"]] + MEAN_MARK_POINTS)


def test_coach_values_passes_the_leftover_down():
    rules = coach_values([("Benfica", "Arouca")], STRENGTH)
    priced = coach_values([("Benfica", "Arouca")], STRENGTH, beyond=COACH_BEYOND_RESULT)
    assert priced["Benfica"] - rules["Benfica"] > priced["Arouca"] - rules["Arouca"]


def test_the_email_and_the_calendar_spell_one_club_two_ways():
    """The bug this found on the way past: the email credits `Académico`, and
    everything else in the project says `Académico Viseu`, so settling that
    club's coach off the email quietly found nothing."""
    email = {
        "treinadores": {
            "Bruno Pinheiro|Académico": 6,
            "Marco Silva|Benfica": 7,
        }
    }
    assert coach_points_by_club(email, "Académico Viseu") == 6
    assert coach_points_by_club(email, "Benfica") == 7
    assert coach_points_by_club(email, "Sporting") is None

    assert same_club("Sp. Braga", "Sp Braga")
    assert not same_club("Nacional", "Internacional")
    assert not same_club("", "Benfica")


def test_the_fit_is_the_mean_of_each_result_and_the_control_is_one_mean(measure):
    rows = [
        {"left": 6.0, "result": "win"},
        {"left": 4.0, "result": "win"},
        {"left": 3.0, "result": "draw"},
        {"left": 1.0, "result": "loss"},
    ]
    assert measure.fit(rows) == {"win": 5.0, "draw": 3.0, "loss": 1.0}
    assert measure.level(rows)["win"] == pytest.approx(3.5)
    assert len(set(measure.level(rows).values())) == 1, "the control has no shape"

    # A result nobody had falls back to what the model pays today, which is the
    # one honest stand-in: it is what would have been paid without any of this.
    only_wins = measure.fit([{"left": 6.0, "result": "win"}])
    assert only_wins["draw"] == MEAN_MARK_POINTS


def test_a_round_is_fitted_only_on_matches_that_had_kicked_off(measure):
    """Dated, not numbered. A match postponed out of an earlier round is played
    later, and counting it as known would hand the fit a result nobody had."""
    early = Fixture(
        round_number=3,
        home="Benfica",
        away="Arouca",
        home_goals=2,
        away_goals=0,
        kickoff="15 SET 20:15",
    )
    postponed = Fixture(
        round_number=3,
        home="Sporting",
        away="Estoril",
        home_goals=1,
        away_goals=1,
        kickoff="30 SET 20:15",
    )
    week = Fixture(
        round_number=6, home="FC Porto", away="Nacional", kickoff="20 SET 18:00"
    )

    when = measure.kickoff([early, postponed, week], 6)
    known = measure.known_before([early, postponed, week], when)
    assert [f.home for f in known] == ["Benfica"]
    assert measure.outcome(2, 0) == "win"
    assert measure.outcome(1, 1) == "draw"
    assert measure.outcome(0, 3) == "loss"
