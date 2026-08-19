"""Tests for the §6.6 squad-composition check."""

from __future__ import annotations

from helpers import make_player, make_squad, rules_broken

from liga_record_mcp.models import Position, Squad
from liga_record_mcp.rules import validate_squad


def test_a_well_formed_squad_passes(squad: Squad):
    result = validate_squad(squad)
    assert result.is_valid, result.violations


def test_the_squad_must_hold_twenty_three(squad: Squad):
    result = validate_squad(make_squad(squad.players[:-1]))
    assert not result.is_valid
    assert any("got 22" in v.detail for v in result.violations)


def test_positional_quotas_are_exact(squad: Squad):
    """A ninth midfielder in place of a fourth forward is still 23 players."""
    players = [p for p in squad.players if p.id != "FWD4"]
    players.append(make_player("MID9", Position.MID, 1_000_000))

    result = validate_squad(make_squad(players))
    assert not result.is_valid
    assert any("MID: 9" in v.detail for v in result.violations)
    assert any("FWD: 3" in v.detail for v in result.violations)


def test_duplicate_player_ids_rejected(squad: Squad):
    players = list(squad.players)
    players[-1] = players[0]

    result = validate_squad(make_squad(players))
    assert not result.is_valid
    assert any("duplicate player ids: GK1" in v.detail for v in result.violations)


def test_a_squad_over_budget_is_rejected(squad: Squad):
    players = [p.model_copy(update={"value": 5_000_000}) for p in squad.players]

    result = validate_squad(make_squad(players))
    assert not result.is_valid
    assert "§6.4" in rules_broken(result)
