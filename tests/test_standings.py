"""The leaderboard: the one thing here that is measured rather than argued.

Every other number this project produces is a projection. A position is a fact,
and it is the only check on whether any of the reasoning is working.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liga_record_mcp.models import TeamStanding
from liga_record_mcp.source import parse_standing
from liga_record_mcp.source.live import LigaRecordClient, MarketError, SiteError

FIXTURE = Path(__file__).parent / "fixtures" / "teams_getranking.json"


@pytest.fixture
def payload() -> list:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload, *, text: str = "", content_type: str = "application/json"):
        self._payload = payload
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_a_real_ranking_row_parses(payload):
    rows = payload[0]["Teams"]
    standing = parse_standing(rows[0])

    assert standing.position == 1
    assert standing.points_total > 0
    assert standing.team_name


def test_the_whole_page_parses(payload):
    rows = [parse_standing(r) for r in payload[0]["Teams"]]

    assert len(rows) == 10
    assert [r.position for r in rows] == sorted(r.position for r in rows)


def test_numbers_arrive_as_strings_and_come_back_as_integers(payload):
    raw = payload[0]["Teams"][0]
    assert isinstance(raw["PointsTotal"], str)  # the site's own shape

    assert isinstance(parse_standing(raw).points_total, int)


def test_an_unscored_round_is_none_not_zero():
    """Before a round scores, the site sends "" — which is not a position."""
    standing = parse_standing(
        {
            "IdTeam": 1,
            "NameTeam": "Test",
            "NameUser": "u",
            "PointsTotal": "10",
            "PointsRound": "0",
            "Position": "5",
            "PositionRound": "",
            "PositionChange": "",
        }
    )
    assert standing.position_round is None
    assert standing.position_change is None


def test_a_row_without_a_team_id_is_refused():
    with pytest.raises(MarketError, match="missing"):
        parse_standing({"NameTeam": "No id"})


def test_movement_is_read_the_way_the_site_signs_it():
    """Position grew means the team dropped, so a positive change is bad news."""
    climbed = TeamStanding(
        team_id=1, team_name="Up", user_name="u", points_total=1,
        points_round=1, position=10, position_change=-5,
    )
    dropped = climbed.model_copy(update={"position_change": 5})
    flat = climbed.model_copy(update={"position_change": 0})

    assert climbed.moved_up is True
    assert dropped.moved_up is False
    assert flat.moved_up is None


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------


def test_the_page_count_comes_back_with_the_page(payload, monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse(payload))
    rows, pages = LigaRecordClient().standings()

    assert len(rows) == 10
    assert pages == payload[1]["PageCount"]


def test_a_private_league_uses_the_other_service(payload, monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        return FakeResponse(payload)

    monkeypatch.setattr("httpx.post", capture)
    LigaRecordClient().standings(league_guid="ABC123")

    assert "teamsleague_getranking.ashx" in seen["url"]
    assert "guid=ABC123" in seen["url"]


def test_the_national_service_is_used_without_a_guid(payload, monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        return FakeResponse(payload)

    monkeypatch.setattr("httpx.post", capture)
    LigaRecordClient().standings(team="Melro")

    assert "teams_getranking_search.ashx" in seen["url"]
    assert "team=Melro" in seen["url"]


def test_a_login_redirect_is_reported_not_swallowed(monkeypatch):
    """A redirect to the login page arrives as HTML with a healthy 200."""
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: FakeResponse(None, text="<html>login</html>", content_type="text/html"),
    )
    with pytest.raises(SiteError, match="not JSON"):
        LigaRecordClient().standings()


def test_an_unfamiliar_shape_is_refused(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse({"Teams": []}))
    with pytest.raises(SiteError, match="unfamiliar shape"):
        LigaRecordClient().standings()


def test_an_empty_page_is_not_an_error(monkeypatch):
    """No match for a name is a real answer, not a failure."""
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: FakeResponse([{"Teams": []}, {"PageCount": 0}])
    )
    rows, pages = LigaRecordClient().standings(team="nobody")

    assert rows == []
    assert pages == 0


def test_a_private_league_carries_its_own_position():
    """PositionLeague is the number that decides a league among friends.

    The national service leaves it empty, so it must not be confused with
    `position` — a team can be 6598th in the country and 8th of 30 friends.
    """
    standing = parse_standing(
        {
            "IdTeam": 156412,
            "NameTeam": "Melro",
            "NameUser": "manuelcralves",
            "PointsTotal": "87",
            "PointsRound": "38",
            "Position": "6598",
            "PositionLeague": "8",
        }
    )
    assert standing.position == 6598
    assert standing.position_league == 8


def test_a_national_row_has_no_league_position(payload):
    """The national service sends "" here, which is absence, not zero."""
    standing = parse_standing(payload[0]["Teams"][0])

    assert standing.position_league is None
