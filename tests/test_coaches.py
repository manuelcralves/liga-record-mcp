"""Tests for the coach list and the §6.15 check it enables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import selection_for

from liga_record_mcp.models import COACH_COUNT, Coach, Squad
from liga_record_mcp.rules import validate_selection
from liga_record_mcp.source import SquadSourceError, load_coaches

SHIPPED = Path(__file__).resolve().parents[1] / "data" / "coaches.yaml"


def write_coaches(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(
        yaml.safe_dump({"coaches": entries}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def make_entries(count: int = COACH_COUNT) -> list[dict[str, Any]]:
    return [
        {"id": str(i), "name": f"Coach {i}", "club": f"Club {i}",
         "points_total": i, "points_round": 0}
        for i in range(1, count + 1)
    ]


def test_the_shipped_list_loads():
    coaches = load_coaches(SHIPPED)
    assert len(coaches) == COACH_COUNT
    assert len({c.club for c in coaches}) == COACH_COUNT  # one per club


def test_the_shipped_list_holds_real_data():
    by_club = {c.club: c for c in load_coaches(SHIPPED)}
    assert by_club["FC Porto"].name == "Farioli"
    assert by_club["Benfica"].name == "Marco Silva"
    assert by_club["Sporting"].name == "Rui Borges"


def test_a_missing_file_is_reported_clearly(tmp_path: Path):
    with pytest.raises(SquadSourceError, match="no coach file"):
        load_coaches(tmp_path / "nope.yaml")


def test_a_file_without_a_coaches_list_is_refused(tmp_path: Path):
    path = tmp_path / "coaches.yaml"
    path.write_text("something: else\n", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="missing a `coaches` list"):
        load_coaches(path)


def test_a_short_list_is_refused(tmp_path: Path):
    """§6.15 is one coach per club — a short list is usually a partial copy."""
    path = write_coaches(tmp_path / "coaches.yaml", make_entries(12))
    with pytest.raises(SquadSourceError, match="lists 12 coaches"):
        load_coaches(path)


def test_duplicate_ids_are_refused(tmp_path: Path):
    entries = make_entries()
    entries[3]["id"] = entries[0]["id"]
    path = write_coaches(tmp_path / "coaches.yaml", entries)
    with pytest.raises(SquadSourceError, match="duplicate coach ids"):
        load_coaches(path)


def test_a_bad_entry_is_reported_clearly(tmp_path: Path):
    entries = make_entries()
    del entries[0]["club"]
    path = write_coaches(tmp_path / "coaches.yaml", entries)
    with pytest.raises(SquadSourceError, match="bad coach entry"):
        load_coaches(path)


# --- the rule the list makes checkable -------------------------------------


def test_a_real_coach_passes(squad: Squad):
    coaches = load_coaches(SHIPPED)
    sheet = selection_for(squad, 4, 4, 2, coach_id=coaches[0].id)
    assert validate_selection(squad, sheet, coaches).is_valid


def test_an_invented_coach_is_caught(squad: Squad):
    """Without the list this was accepted as any old text."""
    coaches = load_coaches(SHIPPED)
    sheet = selection_for(squad, 4, 4, 2, coach_id="José Mourinho")
    check = validate_selection(squad, sheet, coaches)

    assert not check.is_valid
    assert any(v.rule == "§6.15" for v in check.violations)


def test_without_the_list_any_text_still_passes(squad: Squad):
    """Documents the weaker behaviour, so the difference is not accidental."""
    sheet = selection_for(squad, 4, 4, 2, coach_id="José Mourinho")
    assert validate_selection(squad, sheet).is_valid


def test_a_missing_coach_still_beats_a_wrong_one(squad: Squad):
    """§6.17 fires on absence; §6.15 on a name that is not a coach."""
    coaches = load_coaches(SHIPPED)
    absent = validate_selection(squad, selection_for(squad, 4, 4, 2, coach_id=None), coaches)
    assert {v.rule for v in absent.violations} == {"§6.17"}


def test_coaches_never_cost_budget(squad: Squad):
    """§6.15 — picking one must not move the squad's value."""
    coaches = load_coaches(SHIPPED)
    before = squad.value()
    validate_selection(squad, selection_for(squad, 4, 4, 2, coach_id=coaches[0].id), coaches)
    assert squad.value() == before
    assert not hasattr(Coach, "value")
