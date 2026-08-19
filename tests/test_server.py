"""Tests for the MCP layer.

The tools are exercised as plain functions — the decorators register them
without wrapping — plus one test that they really are registered on the server,
so a tool that is written but never exposed cannot pass silently.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from helpers import make_squad, squad_document, write_squad_file

from liga_record_mcp import server as mcp_server
from liga_record_mcp.models import Squad

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
BENCH = ("DEF5", "MID5", "FWD3", "GK2")


@pytest.fixture(autouse=True)
def squad_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, squad: Squad) -> Path:
    """Point the server at a fixture squad, with accents to exercise folding."""
    accented = {"MID1": "Javi Sánchez", "MID2": "Héctor Hernández", "FWD1": "Sánchez Júnior"}
    players = [
        p.model_copy(update={"name": accented[p.id]}) if p.id in accented else p
        for p in squad.players
    ]
    path = write_squad_file(
        tmp_path / "squad.yaml", squad_document(make_squad(players), round=3)
    )
    monkeypatch.setattr(mcp_server, "SQUAD_PATH", path)
    return path


def test_everything_is_registered_on_the_server():
    """A tool that exists but is not exposed is invisible to Claude."""

    async def collect():
        return (
            {t.name for t in await mcp_server.server.list_tools()},
            {str(r.uri) for r in await mcp_server.server.list_resources()},
            {p.name for p in await mcp_server.server.list_prompts()},
        )

    tools, resources, prompts = asyncio.run(collect())
    assert tools == {
        "get_squad",
        "get_player",
        "search_squad",
        "validate_selection",
        "simulate_autosubs",
        "check_transfer",
        "project_price",
    }
    assert resources == {"ligarecord://regulamento", "ligarecord://squad"}
    assert prompts == {"pick_starting_xi", "plan_transfers"}


def test_get_squad_reports_money_and_provenance():
    result = mcp_server.get_squad()
    assert result["source"] == "manual"
    assert result["round"] == 3
    assert result["as_of"]  # every read says how fresh it is
    assert result["is_legal"] is True
    assert result["squad_value"] == 39_100_000
    assert result["balance"] == 900_000
    assert len(result["players"]) == 23


def test_get_player_by_id():
    assert mcp_server.get_player("DEF1")["player"]["id"] == "DEF1"


def test_get_player_folds_accents():
    """`hector` should find `Héctor Hernández` — typing accents is a burden."""
    assert mcp_server.get_player("hector")["player"]["name"] == "Héctor Hernández"


def test_get_player_reports_ambiguity_rather_than_guessing():
    result = mcp_server.get_player("sanchez")
    assert result["found"] == 2
    assert "player" not in result
    assert {c["name"] for c in result["candidates"]} == {
        "Javi Sánchez",
        "Sánchez Júnior",
    }


def test_get_player_unknown():
    assert mcp_server.get_player("Cristiano Ronaldo")["found"] == 0


def test_search_squad_filters_and_ranks():
    result = mcp_server.search_squad(position="fwd")
    assert result["count"] == 4
    points = [p["points_total"] for p in result["players"]]
    assert points == sorted(points, reverse=True)


def test_search_squad_rejects_an_unknown_position():
    result = mcp_server.search_squad(position="SWEEPER")
    assert result["count"] == 0
    assert "GK, DEF, MID or FWD" in result["detail"]


def test_search_squad_combines_filters():
    result = mcp_server.search_squad(position="DEF", max_value=1_000_000)
    assert {p["id"] for p in result["players"]} == {"DEF1", "DEF2"}


def test_validate_selection_accepts_a_legal_sheet():
    result = mcp_server.validate_selection(list(XI), list(BENCH), "MID1", "C1")
    assert result["is_valid"] is True
    assert result["formation"] == "4-4-2"


def test_validate_selection_reports_the_broken_rule():
    result = mcp_server.validate_selection(list(XI), list(BENCH), "DEF5", "C1")
    assert result["is_valid"] is False
    assert result["violations"][0]["rule"] == "§10.3(l)"


def test_simulate_autosubs_through_the_tool():
    result = mcp_server.simulate_autosubs(
        list(XI), list(BENCH), ["DEF2", "GK1"], captain="MID1"
    )
    assert [(s["out"], s["in"]) for s in result["substitutions"]] == [
        ("DEF2", "DEF5"),
        ("GK1", "GK2"),
    ]
    assert result["unreplaced"] == []


def test_simulate_autosubs_passes_on_the_armband():
    result = mcp_server.simulate_autosubs(
        list(XI), list(BENCH), ["MID1"], captain="MID1"
    )
    assert result["captain"] == "MID5"
    assert result["captain_inherited"] is True


def test_check_transfer_accepts_like_for_like():
    result = mcp_server.check_transfer("GK1", "Novo Guarda", "GK", "Estoril", 600_000)
    assert result["is_valid"] is True
    assert result["squad_value_after"] == 39_100_000


def test_check_transfer_rejects_a_position_change():
    result = mcp_server.check_transfer("GK1", "Novo Avançado", "FWD", "Estoril", 600_000)
    assert result["is_valid"] is False
    assert result["violations"][0]["rule"] == "§6.8"


def test_check_transfer_rejects_a_price_below_the_floor():
    """§12.1 — catch it as input, not as a pydantic crash."""
    result = mcp_server.check_transfer("GK1", "Barato", "GK", "Estoril", 100_000)
    assert result["is_valid"] is False
    assert result["violations"][0]["rule"] == "§12.1"


def test_check_transfer_rejects_unknown_enums():
    assert not mcp_server.check_transfer("GK1", "X", "SWEEPER", "Y", 600_000)["is_valid"]
    assert not mcp_server.check_transfer(
        "GK1", "X", "GK", "Y", 600_000, window="whenever"
    )["is_valid"]


def test_project_price_flags_the_gap_in_the_regulation():
    """Scores of 1-3 move nothing, and the tool says the rules don't cover it."""
    covered = mcp_server.project_price("DEF1", 10)
    assert covered["change"] == 150_000
    assert covered["in_regulation"] is True

    gap = mcp_server.project_price("DEF1", 2)
    assert gap["change"] == 0
    assert gap["in_regulation"] is False


def test_project_price_unknown_player():
    assert "not in the squad" in mcp_server.project_price("NOBODY", 5)["detail"]


def test_regulation_resource_is_generated_from_the_constants():
    text = mcp_server.regulation()
    assert "3 GK, 8 DEF, 8 MID, 4 FWD" in text
    assert "4-4-2" in text and "5-4-1" in text
    assert "40 000 000" in text
    # The open questions travel with the rules, so they reach Claude too.
    assert "Scores of 1, 2 or 3 move no price" in text


def test_squad_resource_renders_the_current_squad():
    text = mcp_server.squad_sheet()
    assert "Melro" in text
    assert "Javi Sánchez" in text
    assert "39 100 000" in text


def test_prompts_tell_claude_to_verify_rather_than_assert():
    assert "validate_selection" in mcp_server.pick_starting_xi()
    assert "check_transfer" in mcp_server.plan_transfers()
