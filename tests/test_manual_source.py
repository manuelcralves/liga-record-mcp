"""Tests for the hand-maintained YAML squad source."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from liga_record_mcp.models import Squad
from liga_record_mcp.source import ManualSquadSource, SquadSource, SquadSourceError

LEGAL_XI = [
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
]


def as_document(squad: Squad, **extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "team": {
            "id": squad.team_id,
            "name": squad.team_name,
            "bonus": squad.bonus,
            "penalties": squad.penalties,
        },
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "position": p.position.value,
                "club": p.club,
                "value": p.value,
                "initial_value": p.initial_value,
                "points_total": p.points_total,
            }
            for p in squad.players
        ],
    }
    document.update(extra)
    return document


def write_squad(tmp_path: Path, document: dict[str, Any]) -> ManualSquadSource:
    path = tmp_path / "squad.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return ManualSquadSource(path)


def test_loads_a_valid_squad(tmp_path: Path, squad: Squad):
    snapshot = write_squad(tmp_path, as_document(squad, round=3)).load()

    assert snapshot.source == "manual"
    assert snapshot.round_number == 3
    assert len(snapshot.squad.players) == 23
    assert snapshot.squad.value() == 39_100_000
    assert snapshot.selection is None


def test_the_manual_source_satisfies_the_protocol(tmp_path: Path):
    assert isinstance(ManualSquadSource(tmp_path / "squad.yaml"), SquadSource)


def test_freshness_defaults_to_the_file_mtime(tmp_path: Path, squad: Squad):
    source = write_squad(tmp_path, as_document(squad))
    expected = datetime.fromtimestamp(source.path.stat().st_mtime, tz=timezone.utc)
    assert source.load().fetched_at == expected


def test_an_explicit_updated_date_wins(tmp_path: Path, squad: Squad):
    source = write_squad(tmp_path, as_document(squad, updated=date(2026, 8, 1)))
    assert source.load().fetched_at == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_a_missing_file_is_reported_clearly(tmp_path: Path):
    with pytest.raises(SquadSourceError, match="no squad file"):
        ManualSquadSource(tmp_path / "nope.yaml").load()


def test_malformed_yaml_is_reported_clearly(tmp_path: Path):
    path = tmp_path / "squad.yaml"
    path.write_text("team: [unclosed\n", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="not valid YAML"):
        ManualSquadSource(path).load()


def test_the_document_must_be_a_mapping(tmp_path: Path):
    path = tmp_path / "squad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="mapping at the top level"):
        ManualSquadSource(path).load()


def test_a_missing_players_list_is_reported(tmp_path: Path, squad: Squad):
    document = as_document(squad)
    del document["players"]
    with pytest.raises(SquadSourceError, match="missing a `players` list"):
        write_squad(tmp_path, document).load()


def test_an_illegal_squad_is_refused_at_load(tmp_path: Path, squad: Squad):
    """The whole point of the source: drift fails here, not three tools later."""
    document = as_document(squad)
    document["players"].pop()
    with pytest.raises(SquadSourceError, match="not a legal squad"):
        write_squad(tmp_path, document).load()


def test_an_unknown_position_is_refused(tmp_path: Path, squad: Squad):
    document = as_document(squad)
    document["players"][0]["position"] = "SWEEPER"
    with pytest.raises(SquadSourceError, match="bad player entry"):
        write_squad(tmp_path, document).load()


def test_a_price_below_the_floor_is_refused(tmp_path: Path, squad: Squad):
    """§12.1 — the model floor catches a mistyped quote at the boundary."""
    document = as_document(squad)
    document["players"][0]["value"] = 100_000
    with pytest.raises(SquadSourceError, match="bad player entry"):
        write_squad(tmp_path, document).load()


def test_a_selection_is_parsed_when_present(tmp_path: Path, squad: Squad):
    document = as_document(
        squad,
        selection={
            "starters": LEGAL_XI,
            "bench": ["DEF5", "MID5", "FWD3", "DEF6"],
            "captain": "MID1",
            "coach": "C1",
        },
    )
    snapshot = write_squad(tmp_path, document).load()

    assert snapshot.selection is not None
    assert snapshot.selection.captain == "MID1"
    assert snapshot.selection.coach_id == "C1"
    # Bench order is meaningful (§11.2), so it must survive the round trip.
    assert snapshot.selection.bench == ("DEF5", "MID5", "FWD3", "DEF6")


def test_the_shipped_example_parses_and_says_what_is_missing():
    """The template must stay valid YAML and keep guiding whoever fills it in."""
    example = Path(__file__).resolve().parents[1] / "data" / "squad.example.yaml"

    with pytest.raises(SquadSourceError) as caught:
        ManualSquadSource(example).load()

    message = str(caught.value)
    assert "not a legal squad" in message
    assert "expected 23 players, got 4" in message
    assert "GK: 1 in the squad, must be exactly 3" in message


def test_an_illegal_selection_is_refused(tmp_path: Path, squad: Squad):
    document = as_document(
        squad,
        selection={
            "starters": ["GK1", "GK2"] + LEGAL_XI[2:],
            "bench": ["DEF5", "MID5", "FWD3", "DEF6"],
            "captain": "MID1",
            "coach": "C1",
        },
    )
    with pytest.raises(SquadSourceError, match="illegal selection"):
        write_squad(tmp_path, document).load()
