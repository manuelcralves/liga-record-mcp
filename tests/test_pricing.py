from __future__ import annotations

import pytest

from liga_record_mcp.models import MIN_PRICE
from liga_record_mcp.rules import project_new_price, project_price_change


@pytest.mark.parametrize(
    ("round_points", "delta"),
    [
        (25, 150_000),
        (10, 150_000),
        (9, 100_000),
        (6, 100_000),
        (5, 50_000),
        (4, 50_000),
        (0, -50_000),
        (-1, -100_000),
        (-7, -100_000),
    ],
)
def test_price_bands(round_points: int, delta: int):
    """§12.3 upward, §12.4 downward."""
    assert project_price_change(round_points) == delta


@pytest.mark.parametrize("round_points", [1, 2, 3])
def test_scores_of_one_to_three_move_nothing(round_points: int):
    """§12.3 starts at 4 and §12.4 stops at 0, so 1-3 is a gap in the rules."""
    assert project_price_change(round_points) == 0


def test_new_price_applies_the_movement():
    assert project_new_price(1_000_000, 10) == 1_150_000
    assert project_new_price(1_000_000, -2) == 900_000
    assert project_new_price(1_000_000, 2) == 1_000_000


def test_quote_never_falls_below_the_floor():
    """§12.1 — never below 500 000."""
    assert project_new_price(MIN_PRICE, -5) == MIN_PRICE
    assert project_new_price(MIN_PRICE, 0) == MIN_PRICE
    assert project_new_price(MIN_PRICE + 50_000, 0) == MIN_PRICE
