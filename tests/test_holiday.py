"""When to spend §6.17's holidays: the bar is the value of waiting.

A holiday pays half the round winner, whatever the team does. It is worth
taking in a week the team is expected to fall below that, and the question is
only whether a worse week is still to come. These pin the shape of the answer:
never below zero, zero when there is nothing left to wait for, falling as the
season runs out — and never "spend it so it is not wasted".
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from liga_record_mcp.holiday import (
    LAST_HOLIDAY_ROUND,
    holiday_advice,
    holiday_bar,
)
from liga_record_mcp.models import HOLIDAY_ROUNDS, LAST_MATCHDAY, Player, Position
from liga_record_mcp.stats import COACH_BEYOND_POINTS

ROOT = Path(__file__).resolve().parents[1]


def test_the_last_three_rounds_are_closed():
    assert LAST_HOLIDAY_ROUND == LAST_MATCHDAY - 3


@pytest.mark.parametrize("chips, rounds", [(1, 24), (2, 10), (3, 24), (3, 5), (1, 2)])
def test_the_bar_is_never_negative(chips, rounds):
    assert holiday_bar(chips, rounds, mean=-2.5, spread=4.0) >= 0.0


def test_one_holiday_and_one_round_left_takes_any_positive_gain():
    assert holiday_bar(1, 1, mean=-2.5, spread=4.0) == 0.0


def test_as_many_holidays_as_rounds_takes_any_positive_gain():
    """Nothing to wait for: every remaining week can have one."""
    assert holiday_bar(3, 3, mean=-2.5, spread=4.0) == pytest.approx(0.0)
    assert holiday_bar(3, 2, mean=-2.5, spread=4.0) == pytest.approx(0.0)


def test_the_bar_falls_as_the_season_runs_out():
    bars = [holiday_bar(1, rounds, mean=-2.5, spread=4.0) for rounds in (24, 12, 6, 2)]
    assert bars == sorted(bars, reverse=True)
    assert bars[0] > bars[-1]


def test_more_holidays_in_hand_lower_the_bar():
    assert holiday_bar(3, 20, -2.5, 4.0) < holiday_bar(1, 20, -2.5, 4.0)


def advice(score, *, payout=42.5, typical=45.0, used=(), round_number=8):
    return holiday_advice(
        expected_score=score,
        expected_payout=payout,
        typical_score=typical,
        spread=4.0,
        used=used,
        round_number=round_number,
    )


def test_an_ordinary_week_keeps_it():
    found = advice(45.0)
    assert found["verdict"] == "guarda"
    assert found["gain"] < 0


def test_a_negative_gain_is_never_spent_even_at_the_very_end():
    """An unused holiday costs nothing; a wasted one costs points."""
    found = advice(46.0, round_number=LAST_HOLIDAY_ROUND, used=(9, 10))
    assert found["verdict"] == "guarda"


def test_a_gutted_squad_takes_it():
    found = advice(28.0)
    assert found["verdict"] == "usa"
    assert found["gain"] > found["bar"]


def test_after_the_last_open_round_it_says_so():
    assert advice(20.0, round_number=LAST_HOLIDAY_ROUND + 1)["verdict"] == "fora"


def test_with_all_three_spent_it_says_so():
    found = advice(20.0, used=(8, 9, 10)[:HOLIDAY_ROUNDS])
    assert found["verdict"] == "gastas"
    assert found["left"] == 0


# --- the page -----------------------------------------------------------------


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rounds_are_labelled_as_matchdays_not_the_sites_numbers(dash, monkeypatch):
    """The ranking's round 1 is matchday 6; the page wrote "Jornada 1"."""
    paid = {1: 78, 2: 91}
    asked = []

    def holiday_round(site_round):
        asked.append(site_round)
        winner = paid.get(site_round)
        if winner is None:
            return {}
        return {
            "round_winner": winner, "holiday_pays": -(-winner // 2),
            "our_score": 50, "teams_ranked": 158940,
        }

    monkeypatch.setattr(dash.mcp, "holiday_round", holiday_round)
    rows = dash.holiday_rows(8)
    assert asked == [1, 2], "asked the service for rounds it numbers differently"
    assert [r["round"] for r in rows] == [6, 7]
    assert [r["pays"] for r in rows] == [39, 46]


def test_the_plan_adds_the_coach_and_reads_the_holidays_from_the_decisions(
    dash, monkeypatch, tmp_path
):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps({"format": 1, "rounds": {"7": {"holiday": True}}, "season": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dash, "DECISIONS_PATH", decisions)
    plan = dash.holiday_plan(
        {
            "model": {"round_score": 40.0, "typical_score": 41.0},
            "holidays": [{"pays": 39}, {"pays": 46}],
            # The coach of the round, mark included, as `rank_coaches` gives it.
            "coaches": [
                {"expected": 3.91, "opponent": "V. Guimarães"},
                {"expected": 3.55, "opponent": "Marítimo"},
            ],
        },
        8,
    )
    assert plan["expected_score"] == pytest.approx(40.0 + 3.91)
    assert plan["expected_payout"] == pytest.approx(42.5)
    assert plan["used"] == [7] and plan["left"] == HOLIDAY_ROUNDS - 1
    assert plan["verdict"] == "guarda"


def test_the_page_says_keep_or_use_with_the_sums(dash):
    keep = dash.holiday_verdict(advice(45.0) | {"rounds_seen": 2})
    assert "Guarda as férias" in keep and "42.5" in keep
    use = dash.holiday_verdict(advice(28.0) | {"rounds_seen": 2})
    assert "Usa uma ronda de férias" in use
    closed = dash.holiday_verdict(
        advice(20.0, round_number=LAST_HOLIDAY_ROUND + 1) | {"rounds_seen": 2}
    )
    assert "Já não se pode" in closed


def test_this_rounds_absences_lower_the_round_and_not_the_normal_week(dash):
    """The normal week is the bar's baseline. Folding this round's absences
    into it dragged it down in the one week they mattered, and raised the bar
    a depleted squad had to clear — found by the review of 22/09/2026."""
    shape = {Position.GK: 3, Position.DEF: 8, Position.MID: 8, Position.FWD: 4}
    market = {
        f"{position.value}{n}": Player(
            id=f"{position.value}{n}", name=f"{position.value}{n}",
            position=position, club="Benfica", value=1_000_000,
            initial_value=1_000_000,
        )
        for position, count in shape.items()
        for n in range(count)
    }
    squad = list(market)
    returns = {i: 4.0 + (1.0 if i.startswith("FWD") else 0.0) for i in squad}
    playing = {i: 0.95 for i in squad}
    views = [returns, returns, returns]
    out = {"FWD0": "lesionado", "FWD1": "castigado", "MID0": "lesionado"}

    now, normal = dash.round_and_typical(squad, market, views, playing, out, draws=64)
    fit_now, fit_normal = dash.round_and_typical(squad, market, views, playing, {}, draws=64)

    assert now < fit_now, "three men out did not lower the round"
    assert normal == fit_normal, "this round's news leaked into the normal week"


def test_the_field_is_counted_not_remembered(dash):
    said = dash.holiday_section(
        {"holidays": [{"round": 6, "winner": 78, "pays": 39, "ours": 63, "teams": 158940}]}
    )
    assert "158 940" in said and "127 mil" not in said


def test_without_a_ranking_the_holiday_plan_pays_an_average_coach(
    dash, monkeypatch, tmp_path
):
    """The fallback nobody exercised. A round with no coach ranking — every
    club idle, or the ranking not built — still has a coach worth something,
    and it is what a coach makes beyond the result."""
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps({"format": 1, "rounds": {}, "season": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(dash, "DECISIONS_PATH", decisions)
    plan = dash.holiday_plan(
        {
            "model": {"round_score": 40.0, "typical_score": 41.0},
            "holidays": [{"pays": 39}, {"pays": 46}],
            "coaches": [{"expected": 0.0, "opponent": None}],
        },
        8,
    )
    assert plan["expected_score"] == pytest.approx(40.0 + COACH_BEYOND_POINTS)
