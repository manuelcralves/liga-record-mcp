"""Tests for the openfootball archive and the projection built on it.

Offline: two real recorded seasons live in tests/fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import make_player

from liga_record_mcp.models import ClubRecord, Position
from liga_record_mcp.source import CLUB_NAMES, OpenFootballClient, combine, parse_season
from liga_record_mcp.source.live import SiteError
from liga_record_mcp.stats import club_factor, price_factor, project

FIXTURES = Path(__file__).parent / "fixtures"


def season(tag: str) -> dict:
    return json.loads((FIXTURES / f"openfootball_pt1_{tag}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def records() -> dict[str, ClubRecord]:
    return combine([(tag, parse_season(season(tag))) for tag in ("2024-25", "2025-26")])


def test_a_real_season_parses():
    totals = parse_season(season("2025-26"))
    assert len(totals) == 16  # 18 clubs, two of which are not in this season's league
    porto = totals["FC Porto"]
    assert porto["matches"] == 34
    assert porto["clean_sheets"] > 0


def test_club_names_map_to_liga_record_labels():
    """The join key is the Liga Record name, or nothing joins to a Player."""
    totals = parse_season(season("2025-26"))
    for label in ("FC Porto", "Sp. Braga", "V. Guimarães", "E. Amadora"):
        assert label in totals, label


def test_both_score_shapes_are_handled():
    """openfootball nests `ft` in some rows and writes a bare pair in others."""
    payload = {
        "matches": [
            {"team1": "FC Porto", "team2": "Sport Lisboa e Benfica",
             "score": {"ft": [2, 0]}},
            {"team1": "Sport Lisboa e Benfica", "team2": "FC Porto",
             "score": [1, 1]},
        ]
    }
    totals = parse_season(payload)
    assert totals["FC Porto"]["matches"] == 2
    assert totals["FC Porto"]["goals_for"] == 3
    assert totals["FC Porto"]["clean_sheets"] == 1


def test_unscored_matches_are_skipped():
    payload = {"matches": [{"team1": "FC Porto", "team2": "CD Nacional", "score": None}]}
    assert parse_season(payload) == {}


def test_a_payload_without_matches_is_refused():
    with pytest.raises(SiteError, match="no `matches` list"):
        parse_season({"season": "2025-26"})


def test_seasons_are_summed_not_averaged(records: dict[str, ClubRecord]):
    """Two seasons of evidence should outweigh one."""
    porto = records["FC Porto"]
    assert porto.matches == 68
    assert porto.seasons == ("2024-25", "2025-26")


def test_promoted_clubs_are_marked_unknown_not_average(records: dict[str, ClubRecord]):
    """A club with no top-flight record is not an average club."""
    for promoted in ("Marítimo", "Académico"):
        assert promoted in records
        assert records[promoted].has_history is False
        assert records[promoted].goals_against_per_match is None
        assert CLUB_NAMES[promoted] is None


def test_the_archive_reflects_reality(records: dict[str, ClubRecord]):
    """Porto should concede less than Arouca — if not, the mapping is wrong."""
    assert records["FC Porto"].goals_against_per_match < records["Arouca"].goals_against_per_match
    assert records["FC Porto"].clean_sheet_rate > records["Arouca"].clean_sheet_rate


def test_the_client_caches(monkeypatch: pytest.MonkeyPatch):
    client = OpenFootballClient(seasons=("2025-26",))
    calls = []
    monkeypatch.setattr(
        client, "_fetch", lambda tag: (calls.append(tag), season(tag))[1]
    )
    first = client.club_records()
    second = client.club_records()
    assert len(calls) == 1
    assert first is second


# --- the projection --------------------------------------------------------


def test_a_solid_defence_lifts_defenders(records: dict[str, ClubRecord]):
    porto, _ = club_factor(records["FC Porto"], Position.DEF, 1.3, 1.3)
    arouca, _ = club_factor(records["Arouca"], Position.DEF, 1.3, 1.3)
    assert porto > 1.0 > arouca


def test_a_promoted_club_is_neutral_and_flagged(records: dict[str, ClubRecord]):
    factor, has_history = club_factor(records["Marítimo"], Position.DEF, 1.3, 1.3)
    assert factor == 1.0
    assert has_history is False


def test_price_is_damped_not_trusted():
    """Price correlates at about r = 0.30, so double the price is not double."""
    assert price_factor(2_000_000, 1_000_000) == pytest.approx(1.25)
    assert price_factor(500_000, 1_000_000) == pytest.approx(0.875)


def test_form_gains_weight_as_matches_accumulate(records: dict[str, ClubRecord]):
    """Same scoring rate, more evidence — the projection should trust it more.

    Points must scale with matches, otherwise the rate itself changes and the
    test measures nothing about the weighting.
    """
    baselines = {Position.DEF: 1.0}
    RATE = 8.0

    def at(matches: int) -> dict:
        player = make_player(
            "p", Position.DEF, 1_000_000, points_total=int(RATE * matches)
        ).model_copy(update={"club": "FC Porto"})
        return project(player, matches, records["FC Porto"], baselines, 1_000_000, 1.3, 1.3)

    early, later = at(2), at(20)
    assert early["observed_rate"] == later["observed_rate"] == RATE
    assert early["weight_on_form"] < later["weight_on_form"]
    # A hot start is pulled toward the prior early and released as evidence builds.
    assert early["projected_rate"] < later["projected_rate"] < RATE


def test_a_projection_shows_its_working(records: dict[str, ClubRecord]):
    player = make_player("p", Position.DEF, 1_000_000, points_total=8).model_copy(
        update={"club": "FC Porto"}
    )
    out = project(player, 2, records["FC Porto"], {Position.DEF: 1.0}, 1_000_000, 1.3, 1.3,
                  rounds_remaining=32)
    assert set(out["components"]) == {"position_baseline", "club_factor", "price_factor"}
    assert out["club_has_history"] is True
    assert out["projected_remaining"] == round(out["projected_rate"] * 32)


def test_a_club_with_no_matches_yet_falls_back_to_the_prior(records):
    player = make_player("p", Position.MID, 1_000_000, points_total=0).model_copy(
        update={"club": "Marítimo"}
    )
    out = project(player, 0, records["Marítimo"], {Position.MID: 1.5}, 1_000_000, 1.3, 1.3)
    assert out["observed_rate"] is None
    assert out["weight_on_form"] == 0.0
    assert out["projected_rate"] == out["prior_rate"]


def test_price_is_measured_net_of_the_club():
    """Price and club strength correlate at about r = 0.62 in the real data.

    Without dividing by the club's price index, an expensive player at an
    expensive club is credited twice — once by the price factor and again by
    the club factor.
    """
    # Same absolute price, but one plays for a squad that costs twice as much.
    at_cheap_club = price_factor(2_000_000, 1_000_000, club_index=0.5)
    at_rich_club = price_factor(2_000_000, 1_000_000, club_index=2.0)
    assert at_cheap_club > at_rich_club


def test_club_price_index_is_relative_to_the_league():
    from liga_record_mcp.stats import club_price_index

    players = [
        make_player("a", Position.MID, 4_000_000).model_copy(update={"club": "Rich"}),
        make_player("b", Position.MID, 4_000_000).model_copy(update={"club": "Rich"}),
        make_player("c", Position.MID, 1_000_000).model_copy(update={"club": "Poor"}),
        make_player("d", Position.MID, 1_000_000).model_copy(update={"club": "Poor"}),
    ]
    index = club_price_index(players, league_mean_value=2_500_000)
    assert index["Rich"] == pytest.approx(1.6)
    assert index["Poor"] == pytest.approx(0.4)
