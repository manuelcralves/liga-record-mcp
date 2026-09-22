"""The one projection of a round: `advice.round_projection` over `round_weeks`.

The page's eleven, the ledger and the server's `project_points` each built this
in a copy of their own until 22/09/2026, and the copies disagreed at the edges:
the page printed -1 for an injured man whose club had no match where the ledger
filed §15.3's zero, the ledger kept projecting a man who had left the league,
and the server ran another estimator altogether.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.advice import round_projection, round_weeks
from liga_record_mcp.models import ClubRecord, Fixture, Position
from liga_record_mcp.stats import UNUSED_PENALTY, adjust_for_fixture, fixture_multipliers

ROOT = Path(__file__).resolve().parents[1]

WEEK = {
    "opponent": "Arouca",
    "at_home": True,
    "kickoff": "10 OUT 18:00",
    "defensive": 1.2,
    "attacking": 1.1,
}
SEASON = {"returns": 5.0, "playing": 0.8, "expected": 3.8, "appearances": 30}


def project(*, club="Benfica", out=None, gone=()):
    player = SimpleNamespace(id="p", club=club, position=Position.MID)
    return round_projection(
        [player], {"p": SEASON}, {"Benfica": WEEK}, unavailable=out, gone=gone
    )["p"]


def test_a_fit_man_is_moved_by_the_opponent_only_when_he_plays():
    row = project()
    assert row["expected"] == pytest.approx(
        0.8 * adjust_for_fixture(5.0, Position.MID, 1.2, 1.1) + 0.2 * UNUSED_PENALTY
    )
    assert (row["opponent"], row["at_home"], row["kickoff"]) == (
        "Arouca",
        True,
        "10 OUT 18:00",
    )
    assert (row["no_fixture"], row["unavailable"], row["gone"]) == (False, None, False)


def test_out_with_a_match_reads_minus_one():
    row = project(out={"p": "lesionado"})
    assert row["expected"] == float(UNUSED_PENALTY) == -1.0
    assert row["unavailable"] == "lesionado"


def test_out_without_a_match_reads_zero():
    """§15.3 binds before §10.3(i): without a match nobody scores, fit or not."""
    row = project(club="Arouca", out={"p": "lesionado"})
    assert row["expected"] == 0.0
    assert row["no_fixture"] is True
    assert row["opponent"] is None and row["defensive"] is None
    # Still named, because the ledger compares its out list to this field and
    # a man on the list but not in the field would re-record every run.
    assert row["unavailable"] == "lesionado"


def test_a_man_who_has_left_the_league_reads_zero_whatever_his_old_club_plays():
    row = project(gone=["p"])
    assert row["expected"] == 0.0
    assert row["gone"] is True
    assert row["unavailable"] is None, "gone is not a reason to be on the out list"


def test_gone_binds_before_out():
    row = project(gone=["p"], out={"p": "lesionado"})
    assert row["expected"] == 0.0
    assert row["unavailable"] == "lesionado"


def test_the_season_rides_along_whatever_the_round_says():
    row = project(out={"p": "lesionado"})
    assert (row["season"], row["returns"], row["playing"], row["appearances"]) == (
        3.8,
        5.0,
        0.8,
        30,
    )


def club(name, scored, conceded):
    return ClubRecord(club=name, matches=68, goals_for=scored, goals_against=conceded)


RECORDS = {
    "Sporting": club("Sporting", 170, 50),
    "Benfica": club("Benfica", 150, 50),
    "Arouca": club("Arouca", 75, 110),
}
LEAGUE_GA = sum(r.goals_against_per_match for r in RECORDS.values()) / 3
LEAGUE_GF = sum(r.goals_for_per_match for r in RECORDS.values()) / 3


def test_round_weeks_gives_both_sides_of_every_match_in_the_round_and_no_other():
    weeks = round_weeks(
        RECORDS,
        [
            Fixture(round_number=8, home="Sporting", away="Arouca", kickoff="10 OUT 18:00"),
            Fixture(round_number=9, home="Benfica", away="Sporting"),
        ],
        8,
    )
    assert set(weeks) == {"Sporting", "Arouca"}
    assert (weeks["Sporting"]["opponent"], weeks["Sporting"]["at_home"]) == ("Arouca", True)
    assert (weeks["Arouca"]["opponent"], weeks["Arouca"]["at_home"]) == ("Sporting", False)
    assert weeks["Arouca"]["kickoff"] == "10 OUT 18:00"


def test_round_weeks_prices_the_match_with_the_grids_multipliers():
    weeks = round_weeks(RECORDS, [Fixture(round_number=8, home="Sporting", away="Arouca")], 8)
    home, away = RECORDS["Sporting"], RECORDS["Arouca"]
    assert (weeks["Arouca"]["defensive"], weeks["Arouca"]["attacking"]) == pytest.approx(
        fixture_multipliers(
            away.goals_against_per_match,
            away.goals_for_per_match,
            home.goals_against_per_match,
            home.goals_for_per_match,
            LEAGUE_GA,
            LEAGUE_GF,
            at_home=False,
        )
    )


def test_a_club_without_a_record_stands_at_the_league_mean():
    """Promoted, or unknown: the mean of the clubs that have a record."""
    weeks = round_weeks(RECORDS, [Fixture(round_number=8, home="Sporting", away="Novo")], 8)
    home = RECORDS["Sporting"]
    assert (weeks["Sporting"]["defensive"], weeks["Sporting"]["attacking"]) == pytest.approx(
        fixture_multipliers(
            home.goals_against_per_match,
            home.goals_for_per_match,
            LEAGUE_GA,
            LEAGUE_GF,
            LEAGUE_GA,
            LEAGUE_GF,
            at_home=True,
        )
    )


def test_round_weeks_is_empty_for_a_round_the_calendar_does_not_have():
    assert round_weeks(RECORDS, [Fixture(round_number=9, home="A", away="B")], 8) == {}


def test_the_page_the_ledger_and_the_server_all_call_it():
    page, ledger, server = (
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "scripts/build_dashboard.py",
            "scripts/record_projection.py",
            "src/liga_record_mcp/server.py",
        )
    )
    for source in (page, ledger, server):
        assert "round_projection(" in source
    # The page still moves returns by the opponent for the rounds AHEAD — the
    # transfer's horizon and the grid — but the round itself comes from here.
    for source in (ledger, server):
        assert "adjust_for_fixture(" not in source, "a copy of the rule survived"
