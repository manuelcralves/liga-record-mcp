"""Tests for the §6.4/§6.8 single-transfer checks.

The fixture squad is worth 39 100 000 against a 40 000 000 budget, and GK1
quotes at 600 000 — the numbers below lean on both.
"""

from __future__ import annotations

from helpers import make_player, rules_broken

from liga_record_mcp.models import (
    REOPENED_MAX_SWAPS,
    Position,
    Squad,
    TransferWindow,
)
from liga_record_mcp.rules import validate_transfer


def a_keeper(value: int = 600_000):
    return make_player("NEW1", Position.GK, value, name="Novo Guarda")


def test_like_for_like_within_budget_is_allowed(squad: Squad):
    result = validate_transfer(squad, "GK1", a_keeper())
    assert result.is_valid, result.violations
    assert result.value_after == 39_100_000
    assert result.balance_after == 900_000


def test_a_dearer_replacement_eats_into_the_balance(squad: Squad):
    result = validate_transfer(squad, "GK1", a_keeper(700_000))
    assert result.is_valid, result.violations
    assert result.value_after == 39_200_000
    assert result.balance_after == 800_000


def test_positional_contingent_must_hold(squad: Squad):
    """§6.8 — a keeper out means a keeper in."""
    incoming = make_player("NEW1", Position.FWD, 600_000)
    result = validate_transfer(squad, "GK1", incoming)
    assert not result.is_valid
    assert "§6.8" in rules_broken(result)


def test_going_over_budget_is_rejected(squad: Squad):
    result = validate_transfer(squad, "GK1", a_keeper(2_000_000))
    assert not result.is_valid
    assert result.value_after == 40_500_000
    assert result.balance_after == -500_000
    assert "§6.4" in rules_broken(result)
    assert any("500 000 over budget" in v.detail for v in result.violations)


def test_bonus_can_fund_an_otherwise_illegal_transfer(squad: Squad):
    """§6.4 — the 40M ceiling only moves on earned bonus."""
    incoming = a_keeper(2_000_000)
    assert not validate_transfer(squad, "GK1", incoming).is_valid

    enriched = squad.model_copy(update={"bonus": 500_000})
    assert validate_transfer(enriched, "GK1", incoming).is_valid


def test_february_closure_blocks_transfers(squad: Squad):
    """§6.8 — no transfers in February until the market reopens."""
    result = validate_transfer(
        squad, "GK1", a_keeper(), window=TransferWindow.CLOSED
    )
    assert not result.is_valid
    assert "§6.8" in rules_broken(result)


def test_reopened_market_allows_swaps(squad: Squad):
    """§6.9 — up to six swaps once the market reopens."""
    result = validate_transfer(
        squad,
        "GK1",
        a_keeper(),
        transfers_available=REOPENED_MAX_SWAPS,
        window=TransferWindow.REOPENED,
    )
    assert result.is_valid, result.violations


def test_no_transfers_left_this_round(squad: Squad):
    result = validate_transfer(squad, "GK1", a_keeper(), transfers_available=0)
    assert not result.is_valid
    assert "§6.8" in rules_broken(result)


def test_cannot_sell_a_player_you_do_not_own(squad: Squad):
    result = validate_transfer(squad, "NOBODY", a_keeper())
    assert not result.is_valid
    assert result.value_after is None
    assert result.balance_after is None


def test_cannot_buy_a_player_already_in_the_squad(squad: Squad):
    already_owned = squad.by_id()["GK2"]
    result = validate_transfer(squad, "GK1", already_owned)
    assert not result.is_valid
    assert "§6.6" in rules_broken(result)
