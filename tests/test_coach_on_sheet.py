"""The coach on the ledger is the one on the sheet, and changing him re-records.

Until 18/09/2026 `record_projection` carried the coach as a constant, Farioli's
id, and round 7 went on file with him while the sheet had Rui Borges — a switch
worth about a point and a half that round, because FC Porto hosted Benfica and
Sporting hosted Arouca. The page read its formation from a constant too, and
called a 4-3-3 a 3-4-3.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stored(coach_id="890", out=()):
    return {
        "players": {
            "a": {"unavailable": "lesionado"} if "a" in out else {},
            "b": {},
        },
        "coach": {"id": coach_id} if coach_id else None,
    }


# --- which coach the sheet names ---------------------------------------------------


def test_the_coach_is_read_off_the_sheet(ledger):
    sheet = SimpleNamespace(selection=SimpleNamespace(coach_id="860"))
    assert ledger.sheet_coach(sheet) == "860"


def test_a_sheet_without_a_coach_names_none(ledger):
    assert ledger.sheet_coach(SimpleNamespace(selection=None)) is None
    assert ledger.sheet_coach(SimpleNamespace(selection=SimpleNamespace(coach_id=""))) is None


def test_no_coach_on_the_sheet_records_no_coach(ledger):
    """§6.17 scores that round zero; inventing a coach would be a false record."""
    assert ledger.coach_snapshot(None, {}, 7, None) is None


def test_the_snapshot_is_of_the_coach_named(ledger, tmp_path, monkeypatch):
    # All eighteen: the loader refuses a short list as a partial copy (§6.15).
    entries = [("890", "Farioli", "FC Porto"), ("860", "Rui Borges", "Sporting")]
    entries += [(str(1000 + n), f"Coach {n}", f"Club {n}") for n in range(16)]
    lines = ["coaches:"]
    for coach_id, name, club in entries:
        lines += [
            f'  - id: "{coach_id}"',
            f"    name: {name}",
            f"    club: {club}",
            "    points_total: 5",
            "    points_round: 1",
        ]
    coaches = tmp_path / "coaches.yaml"
    coaches.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ledger, "COACHES_PATH", coaches)
    monkeypatch.setattr(ledger, "project_coach", lambda *a, **k: {"projected_rate": 1.5})
    record = SimpleNamespace(
        has_history=True, goals_against_per_match=1.0, goals_for_per_match=1.4
    )
    history = SimpleNamespace(club_records=lambda: {"Sporting": record, "FC Porto": record})

    got = ledger.coach_snapshot(history, {"Sporting": 6, "FC Porto": 6}, 7, "860")
    assert (got["id"], got["name"], got["club"]) == ("860", "Rui Borges", "Sporting")


# --- what makes the round on file stale ---------------------------------------------


def test_nothing_moved_is_not_a_reason_to_record_again(ledger):
    assert not ledger.sheet_moved(stored(), {"a", "b"}, set(), "890")


def test_a_new_coach_records_the_round_again(ledger):
    assert ledger.sheet_moved(stored("890"), {"a", "b"}, set(), "860")


def test_a_round_on_file_without_a_coach_takes_one(ledger):
    assert ledger.sheet_moved(stored(None), {"a", "b"}, set(), "860")


def test_the_squad_and_the_injury_list_still_count(ledger):
    assert ledger.sheet_moved(stored(), {"a", "c"}, set(), "890")
    assert ledger.sheet_moved(stored(), {"a", "b"}, {"a"}, "890")
    assert not ledger.sheet_moved(stored(out=("a",)), {"a", "b"}, {"a"}, "890")


# --- the constants that are gone ------------------------------------------------------


def test_no_coach_is_hard_coded_any_more(ledger):
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "CHOSEN_COACH" not in source


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def eleven(shape):
    positions = ["GK"] + ["DEF"] * shape[0] + ["MID"] * shape[1] + ["FWD"] * shape[2]
    return {str(n): {"position": pos} for n, pos in enumerate(positions)}


def test_the_formation_is_counted_from_the_eleven(dash):
    """Found by the code review: the first test only read the source text."""
    four_three_three = eleven((4, 3, 3))
    assert dash.formation_of(list(four_three_three), four_three_three) == "4-3-3"
    three_four_three = eleven((3, 4, 3))
    assert dash.formation_of(list(three_four_three), three_four_three) == "3-4-3"


def test_the_page_prints_the_counted_formation():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "<dd>3-4-3</dd>" not in source
    assert "<dd>{formation}</dd>" in source
    assert "formation = formation_of(XI, rows)" in source
    assert "o Benfica a 9" not in source
