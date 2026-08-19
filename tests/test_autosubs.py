"""Tests for the §11 automatic substitution algorithm."""

from __future__ import annotations

from helpers import make_squad

from liga_record_mcp.models import Selection, Squad
from liga_record_mcp.rules import simulate_autosubs

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


def sheet(bench: tuple[str, ...], captain: str | None = "MID1") -> Selection:
    return Selection(starters=XI, bench=bench, captain=captain, coach_id="C1")


def swaps(result) -> list[tuple[str, str]]:
    return [(s.out_id, s.in_id) for s in result.substitutions]


def test_nobody_missing_means_no_substitutions(squad: Squad):
    result = simulate_autosubs(squad, sheet(("DEF5", "MID5", "FWD3", "DEF6")), [])
    assert result.substitutions == []
    assert result.unreplaced == []
    assert result.captain_id == "MID1"
    assert not result.captain_inherited


def test_a_same_position_substitute_comes_on(squad: Squad):
    result = simulate_autosubs(
        squad, sheet(("DEF5", "MID5", "FWD3", "DEF6")), ["DEF2"]
    )
    assert swaps(result) == [("DEF2", "DEF5")]
    assert result.unreplaced == []


def test_substitutions_are_same_position_only(squad: Squad):
    """§11.2 — a benched defender cannot cover a missing forward."""
    result = simulate_autosubs(
        squad, sheet(("DEF5", "DEF6", "DEF7", "DEF8")), ["FWD1"]
    )
    assert result.substitutions == []
    assert result.unreplaced == ["FWD1"]


def test_bench_order_decides_who_comes_on(squad: Squad):
    """§11.2 — MID5 is named ahead of MID6, so MID5 gets the shirt."""
    result = simulate_autosubs(
        squad, sheet(("MID5", "MID6", "DEF5", "DEF6")), ["MID3"]
    )
    assert swaps(result) == [("MID3", "MID5")]


def test_at_most_three_substitutes_come_on(squad: Squad):
    """§6.13 — four are named but only three may enter."""
    result = simulate_autosubs(
        squad,
        sheet(("DEF5", "DEF6", "DEF7", "DEF8")),
        ["DEF1", "DEF2", "DEF3", "DEF4"],
    )
    assert len(result.substitutions) == 3
    assert result.unreplaced == ["DEF4"]


def test_tie_break_prefers_the_lower_valued_starter(squad: Squad):
    """§11.3 — DEF1 quotes at 900 000, DEF3 at 1 100 000, one sub available."""
    result = simulate_autosubs(
        squad, sheet(("DEF5", "MID5", "FWD3", "MID6")), ["DEF1", "DEF3"]
    )
    assert swaps(result) == [("DEF1", "DEF5")]
    assert result.unreplaced == ["DEF3"]


def test_tie_break_falls_through_to_points_when_values_match(squad: Squad):
    """§11.3 second criterion — equal value, so the lower scorer is replaced."""
    patched = []
    for player in squad.players:
        if player.id == "DEF1":
            patched.append(
                player.model_copy(update={"value": 1_000_000, "points_total": 9})
            )
        elif player.id == "DEF3":
            patched.append(
                player.model_copy(update={"value": 1_000_000, "points_total": 2})
            )
        else:
            patched.append(player)

    result = simulate_autosubs(
        make_squad(patched), sheet(("DEF5", "MID5", "FWD3", "MID6")), ["DEF1", "DEF3"]
    )
    assert swaps(result) == [("DEF3", "DEF5")]


def test_a_replaced_captain_passes_the_armband(squad: Squad):
    """§11.5 — whoever comes on inherits the doubling."""
    result = simulate_autosubs(
        squad, sheet(("MID5", "DEF5", "FWD3", "DEF6"), captain="MID1"), ["MID1"]
    )
    assert swaps(result) == [("MID1", "MID5")]
    assert result.captain_id == "MID5"
    assert result.captain_inherited


def test_captain_kept_when_they_played(squad: Squad):
    result = simulate_autosubs(
        squad, sheet(("DEF5", "MID5", "FWD3", "DEF6"), captain="MID1"), ["DEF2"]
    )
    assert result.captain_id == "MID1"
    assert not result.captain_inherited


def test_a_substitute_who_also_missed_out_cannot_come_on(squad: Squad):
    """§10.3(i) — only players who actually take the field count."""
    result = simulate_autosubs(
        squad, sheet(("DEF5", "DEF6", "MID5", "FWD3")), ["DEF1", "DEF5"]
    )
    assert swaps(result) == [("DEF1", "DEF6")]


def test_tie_break_settles_on_id_when_value_and_points_match(squad: Squad):
    """§11.3 stops at two criteria, so id keeps the outcome order-independent.

    DEF1 and DEF3 are made identical on both criteria, and DEF3 is listed ahead
    of DEF1 in the XI. Without the id tie-break the winner would depend on
    team-sheet order rather than being deterministic.
    """
    patched = [
        p.model_copy(update={"value": 1_000_000, "points_total": 5})
        if p.id in {"DEF1", "DEF3"}
        else p
        for p in squad.players
    ]
    out_of_order = Selection(
        starters=(
            "GK1",
            "DEF3",
            "DEF1",
            "DEF2",
            "DEF4",
            "MID1",
            "MID2",
            "MID3",
            "MID4",
            "FWD1",
            "FWD2",
        ),
        bench=("DEF5", "MID5", "FWD3", "MID6"),
        captain="MID1",
        coach_id="C1",
    )
    result = simulate_autosubs(make_squad(patched), out_of_order, ["DEF1", "DEF3"])
    assert swaps(result) == [("DEF1", "DEF5")]


def test_a_repeated_bench_id_cannot_come_on_twice(squad: Squad):
    """A malformed sheet must not yield two appearances for one substitute.

    validate_selection rejects a repeated id, but simulate_autosubs must not
    silently invent a second appearance if such a sheet reaches it.
    """
    result = simulate_autosubs(
        squad, sheet(("DEF5", "DEF5", "MID5", "FWD3")), ["DEF1", "DEF2"]
    )
    assert swaps(result) == [("DEF1", "DEF5")]
    assert result.unreplaced == ["DEF2"]


def test_unknown_unavailable_ids_are_ignored(squad: Squad):
    result = simulate_autosubs(
        squad, sheet(("DEF5", "MID5", "FWD3", "DEF6")), ["WHOEVER"]
    )
    assert result.substitutions == []
    assert result.unreplaced == []
