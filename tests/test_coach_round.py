"""The coach of the round, chosen on the round's match.

Coaches are free and change every round (§6.13–6.16), and what one scores
rides on his club's result. The page ranked the eighteen once, on last
season's average, joined to openfootball's names word by word — "Sporting"
fitted Braga too — and round 7's right call, Sporting at home to Arouca over
FC Porto against Benfica, was worked out by hand on 18/09/2026.
`scripts/measure_coach_pick.py` measured the change before it was wired: +9 and
+3 points against one coach for the season, in 2024/25 and 2025/26.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from liga_record_mcp.final_table import coach_values
from liga_record_mcp.models import Fixture
from liga_record_mcp.stats import expected_coach_points

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Attack and defence in goals a match, roughly as the table stood before
#: round 7: three strong sides and a weak one.
BEFORE_ROUND_7 = {
    "Sporting": (2.4, 0.8),
    "FC Porto": (2.0, 0.8),
    "Benfica": (2.1, 0.8),
    "Arouca": (1.1, 1.6),
}


# --- what a coach is expected to score ----------------------------------------


def test_two_equal_sides_are_worth_nothing_to_either_coach():
    """A win and a loss, a clean sheet and a blank, cancel exactly."""
    assert expected_coach_points(1.35, 1.35) == pytest.approx(0.0, abs=1e-12)


def test_the_favourite_is_worth_more_and_the_underdog_the_mirror_of_it():
    favourite = expected_coach_points(2.4, 0.7)
    assert favourite > 0
    assert expected_coach_points(0.7, 2.4) == pytest.approx(-favourite)
    assert expected_coach_points(3.0, 0.7) > favourite, "a bigger edge is worth more"


# --- round 7, by rule rather than by hand -------------------------------------


def test_a_big_club_at_home_to_a_weak_one_beats_a_big_club_in_a_derby():
    values = coach_values(
        [("Sporting", "Arouca"), ("FC Porto", "Benfica")], BEFORE_ROUND_7
    )
    assert values["Sporting"] > values["FC Porto"]
    assert max(values, key=values.get) == "Sporting"
    # And a match is worth the same to both coaches, with opposite signs.
    assert values["Sporting"] == pytest.approx(-values["Arouca"])
    assert values["FC Porto"] == pytest.approx(-values["Benfica"])


def test_the_ranking_joins_on_the_exact_club_and_puts_no_match_last(dash):
    coaches = [
        {"id": "1", "name": "Rui Borges", "club": "Sporting"},
        {"id": "2", "name": "Farioli", "club": "FC Porto"},
        {"id": "3", "name": "Carlos Vicens", "club": "Sp. Braga"},
        {"id": "4", "name": "Idle", "club": "Estoril"},
    ]
    fixtures = [
        Fixture(round_number=7, home="Sporting", away="Arouca"),
        Fixture(round_number=7, home="FC Porto", away="Benfica"),
        Fixture(round_number=7, home="Casa Pia", away="Sp. Braga"),
        # Another round's match must not be read as this one's.
        Fixture(round_number=8, home="Estoril", away="Arouca"),
    ]
    strength = {**BEFORE_ROUND_7, "Sp. Braga": (1.6, 1.1), "Casa Pia": (1.0, 1.5)}

    rows = dash.rank_coaches(coaches, fixtures, strength, 7)

    assert [r["name"] for r in rows][0] == "Rui Borges"
    assert rows[-1]["name"] == "Idle" and rows[-1]["expected"] is None
    braga = next(r for r in rows if r["club"] == "Sp. Braga")
    # Braga's own match, away at Casa Pia — not Sporting's, which the old
    # word-by-word join could have handed it.
    assert (braga["opponent"], braga["at_home"]) == ("Casa Pia", False)
    sporting = next(r for r in rows if r["club"] == "Sporting")
    assert (sporting["opponent"], sporting["at_home"]) == ("Arouca", True)


# --- the file the page joins on -----------------------------------------------


def test_every_coach_is_filed_under_a_club_the_site_writes():
    """Joined on the exact name, so the coaches file must spell clubs as the
    site does — the market, the calendar and the weekly email agree, and it
    said "Académico" where they all say "Académico Viseu"."""
    filed = yaml.safe_load((ROOT / "data" / "coaches.yaml").read_text(encoding="utf-8"))
    clubs = [coach["club"] for coach in filed["coaches"]]
    email = json.loads(
        (ROOT / "data" / "pontuacoes" / "7.json").read_text(encoding="utf-8")
    )
    site = {key.split("|", 1)[1] for key in email["jogadores"]}
    assert len(set(clubs)) == 18
    assert sorted(set(clubs) - site) == []
