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
    assert len(coaches) == len({c.club for c in coaches})  # one per club
    assert 0 < len(coaches) <= COACH_COUNT


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


def test_a_short_list_is_allowed_because_the_site_served_one(tmp_path: Path):
    """This used to be refused, and the refusal was wrong.

    The rule said eighteen, one per club, and called anything shorter a
    partial copy. On 27/10/2026 the site's own chooser offered SIXTEEN:
    Estoril and Famalicão had sacked their coaches and had nobody to pick, and
    §14.5 pays nothing to a man who cannot take the bench — so that week's
    email left those clubs out rather than scoring them zero. A guard written
    against a truncated paste was refusing the thing that actually happened.
    """
    path = write_coaches(tmp_path / "coaches.yaml", make_entries(16))
    assert len(load_coaches(path)) == 16


def test_two_coaches_for_one_club_are_still_refused(tmp_path: Path):
    """What the old rule was really protecting, and this keeps: a club cannot
    have two, whatever the length of the list."""
    entries = make_entries(16)
    entries[5]["club"] = entries[2]["club"]
    path = write_coaches(tmp_path / "coaches.yaml", entries)
    with pytest.raises(SquadSourceError, match="two coaches for"):
        load_coaches(path)


def test_more_coaches_than_clubs_are_refused(tmp_path: Path):
    path = write_coaches(tmp_path / "coaches.yaml", make_entries(COACH_COUNT + 1))
    with pytest.raises(SquadSourceError, match="only 18 clubs"):
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

def test_the_email_names_a_club_the_file_does_not_hold(tmp_path: Path):
    """What replaced counting to eighteen.

    The weekly email is an independent list of who is in post, so a club it
    pays a coach for that the file does not hold is either a sacking the file
    has not caught up with or a paste that lost its tail. Counting could not
    tell those from a league that genuinely has sixteen.
    """
    from liga_record_mcp.coaches import missing_from_file

    coaches = load_coaches(write_coaches(tmp_path / "coaches.yaml", make_entries(16)))
    email = {"treinadores": {f"Coach {i}|Club {i}": 3 for i in range(1, 19)}}
    assert missing_from_file(coaches, email) == ["Club 17", "Club 18"]
    # And nothing to say when the file covers everyone the email pays.
    assert missing_from_file(coaches, {"treinadores": {"Coach 1|Club 1": 3}}) == []

