"""The opponent grid, and the day `season_rate` changed meaning under it.

The ledger used to store a RATE in `season_rate` — which is what
`adjust_for_fixture`'s first parameter is called — and now stores the blend,
`playing * returns + (1 - playing) * -1`. The grid kept scaling it, so the
fixture was charged against the -1 a man collects for NOT playing, which no
opponent can move.

These tests are about the shape of that arithmetic rather than about any
number: a cell has to move with the opponent, and it has to move in the right
direction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.models import Position
from liga_record_mcp.stats import UNUSED_PENALTY, adjust_for_fixture

ROOT = Path(__file__).resolve().parents[1]


def blend(playing: float, returns: float, defensive: float, attacking: float) -> float:
    """What the grid should compute, written out independently of the script."""
    return playing * adjust_for_fixture(
        returns, Position.MID, defensive, attacking
    ) + (1 - playing) * float(UNUSED_PENALTY)


EASY, NEUTRAL, HARD = 1.45, 1.0, 0.6


def test_a_fringe_player_still_moves_with_the_opponent():
    """The bug: every cell printed the same number, whoever the opponent was.

    A blend of -0.30 sits below APPEARANCE_FLOOR, so scaling it ran
    max(0.0, -0.30 - 1.0) and clamped to zero — leaving the floor, 1.0, in
    every cell of every week.
    """
    cells = [blend(0.10, 6.0, m, m) for m in (EASY, NEUTRAL, HARD)]
    assert len(set(round(c, 3) for c in cells)) == 3, (
        "a fringe player's cells are identical across easy, neutral and hard"
    )
    assert cells[0] > cells[1] > cells[2]


def test_a_fringe_player_is_not_painted_green():
    """edge = projected - the unadjusted blend. It used to read +1.30."""
    unadjusted = 0.10 * 6.0 + 0.90 * float(UNUSED_PENALTY)
    edge = blend(0.10, 6.0, NEUTRAL, NEUTRAL) - unadjusted
    assert abs(edge) < 0.25, f"a neutral fixture reads as an edge of {edge:+.2f}"


def test_a_man_who_never_plays_is_not_helped_by_an_easy_week():
    """§10.3(i) pays the same -1 whoever the opponent is."""
    for multiplier in (EASY, NEUTRAL, HARD):
        assert blend(0.0, 9.0, multiplier, multiplier) == pytest.approx(
            float(UNUSED_PENALTY)
        )


def test_a_certain_starter_moves_by_the_full_multiplier():
    at_easy = blend(1.0, 6.0, EASY, EASY)
    assert at_easy == pytest.approx(adjust_for_fixture(6.0, Position.MID, EASY, EASY))


def test_the_grid_reads_the_halves_when_the_round_has_them():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    grid = source[source.index("def fixture_grid("):source.index("def hero(")]
    assert 'row.get("returns")' in grid and 'row.get("playing")' in grid, (
        "fixture_grid is scaling season_rate again — that field is the blend "
        "from round 4 onward, and the fixture must never touch the blend"
    )


def test_the_grid_still_handles_a_round_recorded_by_the_old_estimator():
    """Rounds 1-3 hold a rate and have no halves; they must not KeyError."""
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    grid = source[source.index("def fixture_grid("):source.index("def hero(")]
    assert "if returns is None or playing is None:" in grid
    assert 'row["season_rate"], position, defensive, attacking' in grid
