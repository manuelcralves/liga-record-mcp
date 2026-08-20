"""Tests for the live market client.

No network: every test runs against a recorded response from the real endpoint
(`tests/fixtures/playersearch_gr.json`), so the suite stays fast, offline, and
honest about the shape the site actually returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from liga_record_mcp.models import MIN_PRICE, MarketPlayer, Position
from liga_record_mcp.source import (
    POSITION_CODE,
    POSITION_FROM_CODE,
    LigaRecordClient,
    MarketError,
    parse_market_player,
)

FIXTURE = Path(__file__).parent / "fixtures" / "playersearch_gr.json"


@pytest.fixture
def rows() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client(rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> LigaRecordClient:
    """A client whose HTTP layer is replaced by the recorded response."""
    made = LigaRecordClient()
    calls: list[dict[str, str]] = []

    def fake_fetch(params: dict[str, str]) -> list[dict[str, Any]]:
        calls.append(params)
        return rows

    monkeypatch.setattr(made, "_fetch", fake_fetch)
    made.calls = calls  # type: ignore[attr-defined]
    return made


def test_position_codes_round_trip():
    """Our enum is English; the site speaks Portuguese. Both directions must hold."""
    assert POSITION_CODE[Position.GK] == "GR"
    assert POSITION_CODE[Position.FWD] == "AV"
    for position, code in POSITION_CODE.items():
        assert POSITION_FROM_CODE[code] is position


def test_parses_a_real_row(rows: list[dict[str, Any]]):
    row = next(r for r in rows if r["Name"] == "Diogo Costa")
    player = parse_market_player(row)

    assert player.id == "38800"
    assert player.position is Position.GK
    assert player.club == "FC Porto"
    assert player.value == 5_500_000
    assert player.points_total == 14
    assert player.points_round == 7
    assert player.owned_by_teams == 5559


def test_ownership_percentage_uses_a_portuguese_decimal(rows: list[dict[str, Any]]):
    """`PercentTeams` arrives as "25,97", not "25.97"."""
    row = next(r for r in rows if r["Name"] == "Diogo Costa")
    assert row["PercentTeams"] == "25,97"
    assert parse_market_player(row).owned_percent == pytest.approx(25.97)


def test_every_recorded_row_parses(rows: list[dict[str, Any]]):
    """A shape change on the site should fail loudly here, not in a tool call."""
    players = [parse_market_player(r) for r in rows]
    assert len(players) == 61
    assert all(p.position is Position.GK for p in players)
    assert all(p.value >= MIN_PRICE for p in players)


def test_a_missing_field_is_reported_clearly(rows: list[dict[str, Any]]):
    broken = dict(rows[0])
    del broken["CurrentValue"]
    with pytest.raises(MarketError, match="missing"):
        parse_market_player(broken)


def test_an_unreadable_percentage_is_reported_clearly(rows: list[dict[str, Any]]):
    broken = dict(rows[0])
    broken["PercentTeams"] = "not a number"
    with pytest.raises(MarketError, match="ownership percentage"):
        parse_market_player(broken)


def test_market_player_converts_to_a_squad_player(rows: list[dict[str, Any]]):
    """The rules functions take Player, so the conversion has to be lossless."""
    market = parse_market_player(next(r for r in rows if r["Name"] == "Diogo Costa"))
    player = market.as_player()

    assert player.id == market.id
    assert player.position is market.position
    assert player.value == market.value
    assert player.initial_value == market.initial_value
    assert player.points_total == market.points_total


def test_search_sends_the_contract_the_site_expects(client: LigaRecordClient):
    client.search(Position.MID, name="samu", club="guimaraes", max_value=2_000_000)
    sent = client.calls[0]  # type: ignore[attr-defined]

    # Every parameter is required — the endpoint 200s with an empty array if
    # order_by/order_dir are missing, which is how this was found.
    assert sent["playerposition"] == "MD"
    assert sent["name"] == "samu"
    assert sent["club"] == "guimaraes"
    assert sent["maxval"] == "2000000"
    assert sent["order_by"] == "points"
    assert sent["order_dir"] == "desc"


def test_search_rejects_unknown_ordering(client: LigaRecordClient):
    with pytest.raises(MarketError, match="order_by"):
        client.search(Position.GK, order_by="price")
    with pytest.raises(MarketError, match="order_dir"):
        client.search(Position.GK, order_dir="sideways")


def test_repeated_searches_hit_the_cache(client: LigaRecordClient):
    """Quotes only move when a round is scored — don't hammer Record for that."""
    first = client.search(Position.GK)
    second = client.search(Position.GK)

    assert len(client.calls) == 1  # type: ignore[attr-defined]
    assert first == second


def test_a_different_query_is_fetched_separately(client: LigaRecordClient):
    client.search(Position.GK)
    client.search(Position.GK, max_value=1_000_000)
    assert len(client.calls) == 2  # type: ignore[attr-defined]


def test_an_expired_cache_entry_is_refetched(rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch):
    made = LigaRecordClient(cache_ttl=0.0)
    calls = []
    monkeypatch.setattr(made, "_fetch", lambda p: (calls.append(p), rows)[1])

    made.search(Position.GK)
    made.search(Position.GK)
    assert len(calls) == 2


def test_non_list_payloads_are_refused(monkeypatch: pytest.MonkeyPatch):
    """A login redirect would arrive as something other than a list."""
    made = LigaRecordClient()

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return {"error": "login required"}

    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResponse())
    with pytest.raises(MarketError, match="expected a list"):
        made.search(Position.GK)


def test_html_instead_of_json_is_refused(monkeypatch: pytest.MonkeyPatch):
    made = LigaRecordClient()

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            raise ValueError("not json")

    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResponse())
    with pytest.raises(MarketError, match="not JSON"):
        made.search(Position.GK)


def test_the_client_is_read_only():
    """The mutating endpoints are known and deliberately absent.

    Reading the site's own JavaScript revealed team_buysellplayer.ashx and
    team_renegociateplayer.ashx, and later the sheet setters — team_captain,
    team_manager, team_changename. A tool that can spend a budget or change a
    line-up is a different risk class from one that reads, so this pins the
    decision rather than leaving it to memory.
    """
    import inspect

    from liga_record_mcp.source import live

    source = inspect.getsource(live)
    body = source[source.index("class LigaRecordClient") :]
    for endpoint in (
        "buysellplayer",
        "renegociateplayer",
        "team_import",
        "team_captain",
        "team_manager",
        "team_changename",
    ):
        assert endpoint not in body, f"{endpoint} must not be callable from the client"
    assert "SEARCH_PATH" in body


def test_every_post_the_client_makes_is_a_query():
    """The rankings are POST, which is the site's choice, not a mutation.

    Once one POST exists in the client, "we never POST" stops being the guard.
    The guard becomes: every POST goes to a ranking service, which reads a
    leaderboard and changes nothing.
    """
    import inspect
    import re

    from liga_record_mcp.source import live

    body = inspect.getsource(live)
    body = body[body.index("class LigaRecordClient") :]
    for call in re.finditer(r"httpx\.post\(\s*([A-Za-z_][\w.]*)", body):
        target = call.group(1)
        assert target == "url", f"unexpected POST target {target!r}"
    assert body.count("httpx.post(") == 1, "a new POST needs its own justification"
    assert "RANKING_PATH" in body and "LEAGUE_RANKING_PATH" in body


def test_market_player_still_enforces_the_price_floor():
    with pytest.raises(Exception):
        MarketPlayer(
            id="1",
            name="Too Cheap",
            position=Position.GK,
            club="X",
            value=MIN_PRICE - 1,
            initial_value=MIN_PRICE,
        )
