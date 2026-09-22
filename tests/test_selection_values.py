"""Who the page's eleven may pick: the fit first, the unavailable and the departed last.

Found by the code review of 15/09/2026. The eleven was picked before anyone asked
who had left the league. `valuation` still values a man who has gone, so he can
be priced and sold, and pooled over the whole market his stale number could win
him a place in the model's eleven, or the armband.
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


def test_a_man_who_left_the_league_is_ranked_last_at_the_nothing_he_is_shown(dash):
    """`advice.round_projection` has already put him at 0.0; this only ranks him."""
    shown, ranking = dash.selection_values(
        {"gone": 0.0, "fit": 2.0}, unavailable={}, gone=["gone"]
    )
    assert shown == {"gone": 0.0, "fit": 2.0}
    assert ranking["gone"] < ranking["fit"]


def test_an_unavailable_man_is_ranked_last_at_the_minus_one_he_is_shown(dash):
    shown, ranking = dash.selection_values(
        {"hurt": -1.0, "fit": 1.5}, unavailable={"hurt": "lesionado"}, gone=[]
    )
    assert shown == {"hurt": -1.0, "fit": 1.5}
    assert ranking["hurt"] < ranking["fit"]


def test_the_printed_number_is_the_projection_not_a_second_rule(dash):
    """Out, with no match: §15.3's zero, as the ledger files it.

    Until 22/09/2026 this applied the -1 a second time, so the page printed
    -1 for a man the ledger had at 0."""
    shown, ranking = dash.selection_values(
        {"idle": 0.0, "fit": 1.5}, unavailable={"idle": "lesionado"}, gone=[]
    )
    assert shown["idle"] == 0.0
    assert ranking["idle"] < ranking["fit"]


def test_with_nobody_out_the_ranking_is_the_estimate(dash):
    expected = {"a": 3.0, "b": -0.5}
    shown, ranking = dash.selection_values(expected, unavailable={}, gone=[])
    assert shown == expected
    assert ranking == expected


def test_the_eleven_is_picked_after_asking_who_has_gone(dash):
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    asked = source.index("gone = left_the_league(")
    picked = source.index("sheet = best_eleven(rows, ranking)")
    assert asked < picked, "the eleven is chosen before anyone asks who has left"
    assert "selection_values(expected" in source
