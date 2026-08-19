from __future__ import annotations

import pytest
from helpers import make_player, make_squad
from pydantic import ValidationError

from liga_record_mcp.models import (
    BASE_BUDGET,
    MIN_PRICE,
    SQUAD_QUOTA,
    SQUAD_SIZE,
    Position,
    Squad,
    Valuation,
)


def test_squad_quota_matches_the_regulation():
    """§6.6 — 3 keepers, 8 defenders, 8 midfielders, 4 forwards."""
    assert SQUAD_QUOTA[Position.GK] == 3
    assert SQUAD_QUOTA[Position.DEF] == 8
    assert SQUAD_QUOTA[Position.MID] == 8
    assert SQUAD_QUOTA[Position.FWD] == 4
    assert SQUAD_SIZE == 23


def test_squad_value_budget_and_balance(squad: Squad):
    assert squad.value() == 39_100_000
    assert squad.budget == BASE_BUDGET
    assert squad.balance() == 900_000


def test_bonus_raises_and_penalties_cut_the_budget(squad: Squad):
    """§6.4 — 40M may only be exceeded by earned bonus."""
    adjusted = squad.model_copy(update={"bonus": 500_000, "penalties": 100_000})
    assert adjusted.budget == 40_400_000


def test_valuation_basis_selects_between_va_and_vi(squad: Squad):
    """V.A. is the live quote, V.I. the price paid (§12.2)."""
    players = [
        p.model_copy(update={"value": 1_000_000}) if p.id == "GK1" else p
        for p in squad.players
    ]
    patched = make_squad(players)
    # GK1 was bought at 600 000 and now quotes at 1 000 000.
    assert patched.value(Valuation.CURRENT) == 39_500_000
    assert patched.value(Valuation.PAID) == 39_100_000


def test_a_price_below_the_floor_is_rejected():
    """§12.1 — no quote exists below 500 000, so the model refuses to hold one."""
    with pytest.raises(ValidationError):
        make_player("X1", Position.GK, MIN_PRICE - 50_000)

    assert make_player("X1", Position.GK, MIN_PRICE).value == MIN_PRICE


def test_negative_penalties_are_rejected(squad: Squad):
    """A negative penalty would quietly raise the budget above 40M."""
    with pytest.raises(ValidationError):
        Squad(
            team_id=1,
            team_name="Melro",
            players=squad.players,
            penalties=-1_000_000,
        )


def test_negative_bonus_is_rejected(squad: Squad):
    with pytest.raises(ValidationError):
        Squad(team_id=1, team_name="Melro", players=squad.players, bonus=-1)


def test_squad_is_immutable(squad: Squad):
    """Squad joins Player, Coach and Selection in being frozen."""
    with pytest.raises(ValidationError):
        squad.penalties = 1_000_000
