from __future__ import annotations

import pytest
from helpers import rules_broken, selection_for

from liga_record_mcp.models import Selection, Squad
from liga_record_mcp.rules import legal_formations, validate_selection

#: A legal 4-4-2, with a bench that avoids the keepers so that mutating the XI
#: in one direction does not accidentally trip the duplicate check too.
XI = (
    "GK1",
    "DEF1",
    "DEF2",
    "DEF3",
    "DEF4",
    "MID1",
    "MID2",
    "MID3",
    "MID4",
    "FWD1",
    "FWD2",
)
BENCH = ("DEF5", "MID5", "FWD3", "DEF6")


def sheet(
    starters: tuple[str, ...] = XI,
    bench: tuple[str, ...] = BENCH,
    captain: str | None = "MID1",
    coach_id: str | None = "C1",
) -> Selection:
    return Selection(
        starters=starters, bench=bench, captain=captain, coach_id=coach_id
    )


def test_legal_sheet_passes(squad: Squad, legal_selection: Selection):
    result = validate_selection(squad, legal_selection)
    assert result.is_valid, result.violations
    assert result.formation == "4-4-2"


def test_only_seven_formations_are_legal():
    """§6.13 ranges allow 8-14 outfielders; the XI size cuts it to seven."""
    assert legal_formations() == [
        "3-4-3",
        "3-5-2",
        "4-3-3",
        "4-4-2",
        "4-5-1",
        "5-3-2",
        "5-4-1",
    ]


@pytest.mark.parametrize(
    "shape", [(3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 3, 2), (5, 4, 1)]
)
def test_every_legal_formation_is_accepted(squad: Squad, shape: tuple[int, int, int]):
    defenders, midfielders, forwards = shape
    result = validate_selection(
        squad, selection_for(squad, defenders, midfielders, forwards)
    )
    assert result.is_valid, result.violations
    assert result.formation == f"{defenders}-{midfielders}-{forwards}"


def test_two_keepers_rejected(squad: Squad):
    starters = (
        "GK1",
        "GK2",
        "DEF1",
        "DEF2",
        "DEF3",
        "MID1",
        "MID2",
        "MID3",
        "MID4",
        "FWD1",
        "FWD2",
    )
    result = validate_selection(squad, sheet(starters=starters))
    assert not result.is_valid
    assert any("GK" in v.detail for v in result.violations)


def test_six_defenders_rejected(squad: Squad):
    starters = (
        "GK1",
        "DEF1",
        "DEF2",
        "DEF3",
        "DEF4",
        "DEF5",
        "DEF6",
        "MID1",
        "MID2",
        "MID3",
        "FWD1",
    )
    result = validate_selection(
        squad, sheet(starters=starters, bench=("MID5", "MID6", "FWD3", "FWD4"))
    )
    assert not result.is_valid
    assert any("DEF" in v.detail for v in result.violations)


def test_a_forward_is_mandatory(squad: Squad):
    starters = (
        "GK1",
        "DEF1",
        "DEF2",
        "DEF3",
        "DEF4",
        "DEF5",
        "MID1",
        "MID2",
        "MID3",
        "MID4",
        "MID5",
    )
    result = validate_selection(
        squad, sheet(starters=starters, bench=("DEF6", "MID6", "FWD1", "FWD2"))
    )
    assert not result.is_valid
    assert any("FWD" in v.detail for v in result.violations)


def test_twelve_starters_rejected(squad: Squad):
    result = validate_selection(
        squad,
        sheet(starters=XI + ("FWD3",), bench=("DEF5", "MID5", "DEF6", "MID6")),
    )
    assert not result.is_valid
    assert any("starters" in v.detail for v in result.violations)


def test_bench_must_hold_exactly_four(squad: Squad):
    result = validate_selection(squad, sheet(bench=("DEF5", "MID5", "FWD3")))
    assert not result.is_valid
    assert any("substitutes" in v.detail for v in result.violations)


def test_a_player_cannot_start_and_sit_on_the_bench(squad: Squad):
    result = validate_selection(
        squad, sheet(bench=("DEF1", "MID5", "FWD3", "DEF6"))
    )
    assert not result.is_valid
    assert any("more than once" in v.detail for v in result.violations)


def test_players_outside_the_squad_rejected(squad: Squad):
    starters = XI[:-1] + ("NOBODY",)
    result = validate_selection(squad, sheet(starters=starters))
    assert not result.is_valid
    assert any("23-man squad" in v.detail for v in result.violations)


def test_captain_must_be_a_starter(squad: Squad):
    """§10.3(l) — the captain doubles points, so must be in the XI."""
    result = validate_selection(squad, sheet(captain="DEF5"))
    assert not result.is_valid
    assert "§10.3(l)" in rules_broken(result)


def test_captain_is_optional(squad: Squad):
    result = validate_selection(squad, sheet(captain=None))
    assert result.is_valid, result.violations


def test_a_coach_is_mandatory(squad: Squad):
    """§6.17 — no coach means the round scores zero."""
    result = validate_selection(squad, sheet(coach_id=None))
    assert not result.is_valid
    assert "§6.17" in rules_broken(result)
