"""Finding players who out-produce their ownership.

Ownership is the strongest single predictor in the market, so "buy what nobody
owns" loses: the least-owned band scores below nothing. What can actually close
a gap is the residual — someone producing more than his level of attention
normally buys.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.models import MarketPlayer, Position
from liga_record_mcp.stats import (
    differential_rows,
    expected_rate,
    ownership_baseline,
)

MATCHES = {"Test FC": 2}


def market_player(
    player_id: str,
    *,
    position: Position = Position.MID,
    owned: float = 5.0,
    points: int = 6,
    club: str = "Test FC",
    value: int = 1_000_000,
) -> MarketPlayer:
    return MarketPlayer(
        id=player_id,
        name=f"Player {player_id}",
        position=position,
        club=club,
        value=value,
        initial_value=value,
        points_total=points,
        points_round=0,
        owned_by_teams=1,
        owned_percent=owned,
    )


def a_market(position: Position = Position.MID) -> list[MarketPlayer]:
    """Ownership and scoring rising together, as they really do."""
    return [
        market_player("1", position=position, owned=0.5, points=0),
        market_player("2", position=position, owned=3.0, points=4),
        market_player("3", position=position, owned=10.0, points=8),
        market_player("4", position=position, owned=25.0, points=12),
        market_player("5", position=position, owned=50.0, points=16),
    ]


# --------------------------------------------------------------------------
# The fit
# --------------------------------------------------------------------------


def test_ownership_predicts_scoring():
    """A positive slope is the whole premise; without it the residual is noise."""
    baseline = ownership_baseline(a_market(), MATCHES)
    _, slope = baseline[Position.MID]

    assert slope > 0


def test_each_position_gets_its_own_line():
    baseline = ownership_baseline(
        a_market(Position.DEF) + [market_player("9", position=Position.FWD, owned=1.0)],
        MATCHES,
    )
    assert Position.DEF in baseline
    assert Position.FWD in baseline


def test_positions_are_not_fitted_together():
    """The regression this function was rewritten for.

    Forwards out-score defenders at every ownership level. A single shared line
    puts every forward above it and every defender below, and the ranking
    becomes a position table wearing the costume of a discovery — the first run
    returned forwards in all of the top eight.
    """
    defenders = [
        market_player(f"d{i}", position=Position.DEF, owned=own, points=pts)
        for i, (own, pts) in enumerate([(1.0, 2), (10.0, 6), (40.0, 10)])
    ]
    # Same ownership, uniformly higher scoring.
    forwards = [
        market_player(f"f{i}", position=Position.FWD, owned=own, points=pts)
        for i, (own, pts) in enumerate([(1.0, 10), (10.0, 14), (40.0, 18)])
    ]
    rows = differential_rows(defenders + forwards, MATCHES)

    top_half = {r["position"] for r in rows[: len(rows) // 2]}
    assert top_half != {"FWD"}, "forwards swept the ranking — positions fitted together"
    # Every player sits on his own position's line, so nothing stands out.
    assert all(abs(r["residual"]) < 0.5 for r in rows)


def test_a_player_on_the_line_has_no_residual():
    players = a_market()
    baseline = ownership_baseline(players, MATCHES)
    on_the_line = expected_rate(10.0, baseline[Position.MID])

    assert on_the_line == pytest.approx(4.0, abs=1.0)


def test_an_empty_market_does_not_explode():
    assert ownership_baseline([], MATCHES) == {}
    assert differential_rows([], MATCHES) == []


def test_clubs_that_have_not_played_are_left_out_of_the_fit():
    """They have no rate; entering them at zero would tilt every line."""
    players = a_market() + [market_player("x", club="Unplayed FC", owned=40.0)]
    with_them = ownership_baseline(players, {"Test FC": 2, "Unplayed FC": 0})
    without = ownership_baseline(a_market(), MATCHES)

    assert with_them[Position.MID] == pytest.approx(without[Position.MID])


# --------------------------------------------------------------------------
# The ranking
# --------------------------------------------------------------------------


def test_a_quiet_over_performer_ranks_first():
    players = a_market() + [
        market_player("gem", owned=1.0, points=18)  # heavily owned scoring, nobody has him
    ]
    rows = differential_rows(players, MATCHES)

    assert rows[0]["id"] == "gem"
    assert rows[0]["residual"] > 0


def test_a_crowded_under_performer_ranks_last():
    players = a_market() + [market_player("dud", owned=60.0, points=0)]
    rows = differential_rows(players, MATCHES)

    assert rows[-1]["id"] == "dud"
    assert rows[-1]["residual"] < 0


def test_owned_players_can_be_excluded():
    """A differential you already own is not a move."""
    rows = differential_rows(a_market(), MATCHES, exclude={"3", "4"})

    assert {r["id"] for r in rows}.isdisjoint({"3", "4"})


def test_players_who_never_took_the_field_are_left_out():
    """-1 a round is not a scoring rate, it is an absence."""
    players = a_market() + [market_player("bench", owned=2.0, points=-2)]
    rows = differential_rows(players, MATCHES)

    assert "bench" not in {r["id"] for r in rows}


def test_a_thin_sample_can_be_required_away():
    players = a_market() + [
        market_player("thin", club="One Match FC", owned=1.0, points=7)
    ]
    matches = {"Test FC": 2, "One Match FC": 1}

    loose = differential_rows(players, matches, min_matches=1)
    strict = differential_rows(players, matches, min_matches=2)

    assert "thin" in {r["id"] for r in loose}
    assert "thin" not in {r["id"] for r in strict}


def test_every_row_carries_its_sample_size():
    """A high residual on one match is noise wearing a number."""
    rows = differential_rows(a_market(), MATCHES)

    assert all(r["matches_played"] == 2 for r in rows)


def test_the_ranking_is_ordered_and_deterministic():
    rows = differential_rows(a_market(), MATCHES)
    residuals = [r["residual"] for r in rows]

    assert residuals == sorted(residuals, reverse=True)
    assert rows == differential_rows(a_market(), MATCHES)
