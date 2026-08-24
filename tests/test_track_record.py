"""Whether following the model would have paid — and the ways that can lie.

This is the only table on the site reporting what happened rather than arguing
about what should. That makes it the one where a lookahead would be most
convincing and least visible: score the model's eleven by picking it with the
results in hand and it beats him every week, forever, while being worthless.

So these tests are mostly about what the comparison is NOT allowed to know.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def squad_rows(projected: dict[str, float], actual: dict[str, int | None]):
    """Twenty-three under §6.6's quota: 3 GK, 8 DEF, 8 MID, 4 FWD."""
    plan = [("GK", 3), ("DEF", 8), ("MID", 8), ("FWD", 4)]
    rows, n = {}, 0
    for position, count in plan:
        for _ in range(count):
            n += 1
            key = str(n)
            rows[key] = {
                "name": f"p{n}",
                "position": position,
                "club": "c",
                "value": 1_000_000,
                "projected": projected.get(key, 1.0),
                "actual": actual.get(key, 0),
                "error": (
                    None
                    if actual.get(key, 0) is None
                    else actual.get(key, 0) - projected.get(key, 1.0)
                ),
            }
    return rows


def ledger(rows, filed=None) -> dict:
    return {
        "_all": {
            "5": {
                "players": rows,
                "filed": filed,
                "recorded_at": "2026-08-01T00:00:00+00:00",
            }
        },
        "players": rows,
    }


def test_a_partly_settled_round_reports_no_totals(dash):
    """Not even the ceiling, which is the one that would compute anyway.

    It picks from the players who have results, so it produces a number while
    the other two cannot. Printed beside two dashes it reads as the round's
    ceiling, when it is the best eleven among whoever happened to play.
    """
    actual = {str(i): (3 if i <= 15 else None) for i in range(1, 24)}
    rows = squad_rows({}, actual)
    found = dash.track_rounds({"stored": ledger(rows)})
    assert len(found) == 1
    row = found[0]
    assert row["whole"] is False
    assert row["model"] is None and row["mine"] is None and row["ceiling"] is None
    # The per-player figures still mean something and are still reported.
    assert row["error"] is not None
    assert row["settled"] == 15


def test_a_whole_round_scores_all_three(dash):
    rows = squad_rows({}, {str(i): 3 for i in range(1, 24)})
    row = dash.track_rounds({"stored": ledger(rows)})[0]
    assert row["whole"] is True
    assert row["model"] is not None
    assert row["ceiling"] is not None


def test_the_model_eleven_is_picked_on_projections_not_results(dash):
    """The lookahead that would make this table worthless.

    Player 1 is projected badly and scored brilliantly; player 2 the reverse.
    An eleven picked on projections holds player 2 and misses player 1, so the
    model's total must fall SHORT of the ceiling. If they came out equal, the
    picking had seen the results.
    """
    projected = {"1": 0.1, "2": 9.0}   # both goalkeepers
    actual = {str(i): 1 for i in range(1, 24)}
    actual["1"] = 30
    actual["2"] = 1
    rows = squad_rows(projected, actual)
    row = dash.track_rounds({"stored": ledger(rows)})[0]

    assert row["ceiling"] > row["model"], (
        "the model's eleven scored the ceiling — it was picked knowing results"
    )


def test_the_captain_counts_twice(dash):
    """§10.3(l) doubles him rather than replacing him."""
    rows = squad_rows({}, {str(i): 2 for i in range(1, 24)})
    filed = {
        "starters": [str(i) for i in [1, 4, 5, 6, 12, 13, 14, 15, 20, 21, 22]],
        "bench": ["2", "7", "16", "23"],
        "captain": "20",
    }
    row = dash.track_rounds({"stored": ledger(rows, filed)})[0]
    assert row["mine"] == 11 * 2 + 2, "eleven twos plus the captain again"


def test_his_sheet_is_read_from_the_round_it_was_filed_with(dash):
    """Not from today's squad file, which is a different round's team."""
    rows = squad_rows({}, {str(i): 1 for i in range(1, 24)})
    without = dash.track_rounds({"stored": ledger(rows, None)})[0]
    assert without["mine"] is None, "a round with no filed sheet cannot score one"


def test_a_round_with_nothing_settled_is_left_out(dash):
    rows = squad_rows({}, {str(i): None for i in range(1, 24)})
    assert dash.track_rounds({"stored": ledger(rows)}) == []


def test_the_recorder_stores_the_filed_sheet(dash):
    """Without it the ledger can never answer the question this table asks."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert '"filed"' in source, (
        "record_projection.py no longer snapshots the team sheet — the track "
        "record stops being reconstructible from that round on"
    )
