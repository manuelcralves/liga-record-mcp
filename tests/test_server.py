"""Tests for the MCP layer.

The tools are exercised as plain functions — the decorators register them
without wrapping — plus one test that they really are registered on the server,
so a tool that is written but never exposed cannot pass silently.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from helpers import make_squad, squad_document, write_squad_file

from liga_record_mcp import server as mcp_server
from liga_record_mcp.models import Squad, TeamStanding
from liga_record_mcp.source import SiteError

XI = (
    "GK1",
    "DEF1",
    "DEF2",
    "DEF3",
    "DEF4",
    "MID1",
    "MID2",
    "MID3",
    "MID4",
    "FWD1",
    "FWD2",
)
BENCH = ("DEF5", "MID5", "FWD3", "GK2")
#: Farioli (FC Porto) — a real id from data/coaches.yaml, since §6.15 is now checked.
COACH = "890"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the network.

    Tools now consult the calendar to normalise points per match, so without
    this the suite would quietly start making live requests — which is slow,
    flaky, and dishonest about what is being tested. Tests that need real
    market or calendar data install their own stub over this one.
    """

    class Offline:
        def search(self, position, **kwargs):
            return []

        def fixtures(self):
            return []

        def standings(self, **kwargs):
            return [], 0

    monkeypatch.setattr(mcp_server, "_market", Offline())


@pytest.fixture(autouse=True)
def squad_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, squad: Squad) -> Path:
    """Point the server at a fixture squad, with accents to exercise folding."""
    accented = {"MID1": "Javi Sánchez", "MID2": "Héctor Hernández", "FWD1": "Sánchez Júnior"}
    players = [
        p.model_copy(update={"name": accented[p.id]}) if p.id in accented else p
        for p in squad.players
    ]
    path = write_squad_file(
        tmp_path / "squad.yaml", squad_document(make_squad(players), round=3)
    )
    monkeypatch.setattr(mcp_server, "SQUAD_PATH", path)
    return path


def test_everything_is_registered_on_the_server():
    """A tool that exists but is not exposed is invisible to Claude."""

    async def collect():
        return (
            {t.name for t in await mcp_server.server.list_tools()},
            {str(r.uri) for r in await mcp_server.server.list_resources()},
            {p.name for p in await mcp_server.server.list_prompts()},
        )

    tools, resources, prompts = asyncio.run(collect())
    assert tools == {
        "get_squad",
        "get_player",
        "search_squad",
        "validate_selection",
        "simulate_autosubs",
        "check_transfer",
        "project_price",
        "search_market",
        "check_market_transfer",
        "get_fixtures",
        "squad_fixtures",
        "list_coaches",
        "standings",
        "find_differentials",
        "squad_exposure",
        "primeira_liga",
        "squad_value",
        "club_strength",
        "project_points",
        "record_appearances",
        "appearance_history",
    }
    assert resources == {"ligarecord://regulamento", "ligarecord://squad"}
    assert prompts == {"pick_starting_xi", "plan_transfers"}


def test_get_squad_reports_money_and_provenance():
    result = mcp_server.get_squad()
    assert result["source"] == "manual"
    assert result["round"] == 3
    assert result["as_of"]  # every read says how fresh it is
    assert result["is_legal"] is True
    assert result["squad_value"] == 39_100_000
    assert result["balance"] == 900_000
    assert len(result["players"]) == 23


def test_get_player_by_id():
    assert mcp_server.get_player("DEF1")["player"]["id"] == "DEF1"


def test_get_player_folds_accents():
    """`hector` should find `Héctor Hernández` — typing accents is a burden."""
    assert mcp_server.get_player("hector")["player"]["name"] == "Héctor Hernández"


def test_get_player_reports_ambiguity_rather_than_guessing():
    result = mcp_server.get_player("sanchez")
    assert result["found"] == 2
    assert "player" not in result
    assert {c["name"] for c in result["candidates"]} == {
        "Javi Sánchez",
        "Sánchez Júnior",
    }


def test_get_player_unknown():
    assert mcp_server.get_player("Cristiano Ronaldo")["found"] == 0


def test_search_squad_filters_and_ranks():
    result = mcp_server.search_squad(position="fwd")
    assert result["count"] == 4
    points = [p["points_total"] for p in result["players"]]
    assert points == sorted(points, reverse=True)


def test_search_squad_rejects_an_unknown_position():
    result = mcp_server.search_squad(position="SWEEPER")
    assert result["count"] == 0
    assert "GK, DEF, MID or FWD" in result["detail"]


def test_search_squad_combines_filters():
    result = mcp_server.search_squad(position="DEF", max_value=1_000_000)
    assert {p["id"] for p in result["players"]} == {"DEF1", "DEF2"}


def test_validate_selection_accepts_a_legal_sheet():
    result = mcp_server.validate_selection(list(XI), list(BENCH), "MID1", COACH)
    assert result["is_valid"] is True
    assert result["formation"] == "4-4-2"
    assert "coach_unverified" not in result


def test_validate_selection_reports_the_broken_rule():
    result = mcp_server.validate_selection(list(XI), list(BENCH), "DEF5", COACH)
    assert result["is_valid"] is False
    assert {v["rule"] for v in result["violations"]} == {"§10.3(l)"}


def test_validate_selection_catches_an_invented_coach():
    """§6.15 — before the coach list existed, any text was accepted here."""
    result = mcp_server.validate_selection(
        list(XI), list(BENCH), "MID1", "José Mourinho"
    )
    assert result["is_valid"] is False
    assert {v["rule"] for v in result["violations"]} == {"§6.15"}


def test_validate_selection_says_when_the_coach_went_unchecked(monkeypatch):
    """Skipping the check silently would be worse than not having it."""
    monkeypatch.setattr(mcp_server, "COACHES_PATH", Path("no-such-file.yaml"))
    result = mcp_server.validate_selection(list(XI), list(BENCH), "MID1", "anything")
    assert result["is_valid"] is True
    assert "not checked against the real 18" in result["coach_unverified"]


def test_list_coaches_ranks_by_points():
    result = mcp_server.list_coaches()
    assert result["count"] == 18
    totals = [c["points_total"] for c in result["coaches"]]
    assert totals == sorted(totals, reverse=True)
    assert result["coaches"][0]["name"] in {"Van der Gaag", "Vasco Seabra"}


def test_list_coaches_reports_a_missing_file(monkeypatch):
    monkeypatch.setattr(mcp_server, "COACHES_PATH", Path("no-such-file.yaml"))
    assert "could not read the coach list" in mcp_server.list_coaches()["detail"]


def test_simulate_autosubs_through_the_tool():
    result = mcp_server.simulate_autosubs(
        list(XI), list(BENCH), ["DEF2", "GK1"], captain="MID1"
    )
    assert [(s["out"], s["in"]) for s in result["substitutions"]] == [
        ("DEF2", "DEF5"),
        ("GK1", "GK2"),
    ]
    assert result["unreplaced"] == []


def test_simulate_autosubs_passes_on_the_armband():
    result = mcp_server.simulate_autosubs(
        list(XI), list(BENCH), ["MID1"], captain="MID1"
    )
    assert result["captain"] == "MID5"
    assert result["captain_inherited"] is True


def test_check_transfer_accepts_like_for_like():
    result = mcp_server.check_transfer("GK1", "Novo Guarda", "GK", "Estoril", 600_000)
    assert result["is_valid"] is True
    assert result["squad_value_after"] == 39_100_000


def test_check_transfer_rejects_a_position_change():
    result = mcp_server.check_transfer("GK1", "Novo Avançado", "FWD", "Estoril", 600_000)
    assert result["is_valid"] is False
    assert result["violations"][0]["rule"] == "§6.8"


def test_check_transfer_rejects_a_price_below_the_floor():
    """§12.1 — catch it as input, not as a pydantic crash."""
    result = mcp_server.check_transfer("GK1", "Barato", "GK", "Estoril", 100_000)
    assert result["is_valid"] is False
    assert result["violations"][0]["rule"] == "§12.1"


def test_check_transfer_rejects_unknown_enums():
    assert not mcp_server.check_transfer("GK1", "X", "SWEEPER", "Y", 600_000)["is_valid"]
    assert not mcp_server.check_transfer(
        "GK1", "X", "GK", "Y", 600_000, window="whenever"
    )["is_valid"]


def test_project_price_flags_the_gap_in_the_regulation():
    """Scores of 1-3 move nothing, and the tool says the rules don't cover it."""
    covered = mcp_server.project_price("DEF1", 10)
    assert covered["change"] == 150_000
    assert covered["in_regulation"] is True

    gap = mcp_server.project_price("DEF1", 2)
    assert gap["change"] == 0
    assert gap["in_regulation"] is False


def test_project_price_unknown_player():
    assert "not in the squad" in mcp_server.project_price("NOBODY", 5)["detail"]


def test_regulation_resource_is_generated_from_the_constants():
    text = mcp_server.regulation()
    assert "3 GK, 8 DEF, 8 MID, 4 FWD" in text
    assert "4-4-2" in text and "5-4-1" in text
    assert "40 000 000" in text
    # The open questions travel with the rules, so they reach Claude too.
    assert "Scores of 1, 2 or 3 move no price" in text


def test_squad_resource_renders_the_current_squad():
    text = mcp_server.squad_sheet()
    assert "Melro" in text
    assert "Javi Sánchez" in text
    assert "39 100 000" in text


def test_prompts_tell_claude_to_verify_rather_than_assert():
    assert "validate_selection" in mcp_server.pick_starting_xi()
    plan = mcp_server.plan_transfers()
    assert "search_market" in plan
    assert "check_market_transfer" in plan


# --------------------------------------------------------------------------
# Market tools — the client is stubbed, so these never touch the network.
# --------------------------------------------------------------------------


@pytest.fixture
def market_squad(
    squad_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, squad: Squad
) -> None:
    """A squad carrying real market ids, so ownership cross-referencing works.

    Only the ids are swapped, not the values — the fixture squad's arithmetic
    stays intact and the assertions below can be derived from it.
    """
    real_ids = {"GK1": "42180", "GK2": "38800", "GK3": "43430"}
    players = [
        p.model_copy(update={"id": real_ids[p.id]}) if p.id in real_ids else p
        for p in squad.players
    ]
    path = write_squad_file(
        tmp_path / "market_squad.yaml", squad_document(make_squad(players), round=3)
    )
    monkeypatch.setattr(mcp_server, "SQUAD_PATH", path)


@pytest.fixture
def stub_market(monkeypatch: pytest.MonkeyPatch):
    """Replace the live client with a fixed market drawn from the fixture."""
    import json

    from liga_record_mcp.models import Position
    from liga_record_mcp.source import parse_market_player

    rows = json.loads(
        (Path(__file__).parent / "fixtures" / "playersearch_gr.json").read_text(
            encoding="utf-8"
        )
    )
    keepers = [parse_market_player(r) for r in rows]

    class StubMarket:
        def search(self, position, **kwargs):
            if position is not Position.GK:
                return []
            found = keepers
            cap = kwargs.get("max_value")
            if cap is not None:
                found = [p for p in found if p.value <= cap]
            return found

        def fixtures(self):
            return []

    monkeypatch.setattr(mcp_server, "_market", StubMarket())


def test_search_market_returns_ranked_players(stub_market):
    result = mcp_server.search_market("GK", limit=3)
    assert result["matched"] == 61
    assert result["showing"] == 3
    assert result["players"][0]["name"] == "Diogo Costa"
    assert result["players"][0]["owned_percent"] == pytest.approx(25.97)


def test_search_market_flags_players_already_owned(stub_market, market_squad):
    """Suggesting a player you already have is a wasted recommendation."""
    result = mcp_server.search_market("GK", limit=61)
    by_name = {p["name"]: p for p in result["players"]}
    assert by_name["Diogo Costa"]["in_squad"] is True
    assert by_name["Samuel Soares"]["in_squad"] is False


def test_search_market_finds_differentials(stub_market):
    """Low ownership is the point of the filter."""
    result = mcp_server.search_market("GK", max_owned_percent=5.0, limit=61)
    assert all(p["owned_percent"] <= 5.0 for p in result["players"])
    assert "Lucão" not in {p["name"] for p in result["players"]}  # 34.69% owned


def test_search_market_rejects_an_unknown_position(stub_market):
    assert "GK, DEF, MID or FWD" in mcp_server.search_market("SWEEPER")["detail"]


def test_search_market_reports_a_failure_rather_than_crashing(monkeypatch):
    from liga_record_mcp.source import MarketError

    class Broken:
        def search(self, *a, **k):
            raise MarketError("connection refused")

    monkeypatch.setattr(mcp_server, "_market", Broken())
    assert "could not read the market" in mcp_server.search_market("GK")["detail"]


def test_check_market_transfer_prices_from_the_live_quote(stub_market, market_squad):
    """The incoming price comes from the market, not from the caller."""
    result = mcp_server.check_market_transfer("42180", "41452")

    assert result["is_valid"] is True
    assert result["in"]["name"] == "Lucas França"
    assert result["in"]["value"] == 500_000  # the live quote, not a guess
    # Fixture squad is 39 100 000; the outgoing keeper is 600 000.
    assert result["squad_value_after"] == 39_100_000 - 600_000 + 500_000
    assert result["balance_after"] == 40_000_000 - result["squad_value_after"]


def test_check_market_transfer_refuses_a_player_outside_the_position(
    stub_market, market_squad
):
    """§6.8 — only the outgoing player's position is searched, which enforces it."""
    result = mcp_server.check_market_transfer("DEF1", "41452")  # DEF out, GK in
    assert result["is_valid"] is False
    assert "positional contingent" in result["violations"][0]["detail"]


def test_check_market_transfer_refuses_an_unowned_outgoing_player(
    stub_market, market_squad
):
    result = mcp_server.check_market_transfer("99999", "41452")
    assert result["is_valid"] is False
    assert "not in the squad" in result["violations"][0]["detail"]


# --------------------------------------------------------------------------
# Calendar tools — also stubbed, no network.
# --------------------------------------------------------------------------


@pytest.fixture
def stub_calendar(monkeypatch: pytest.MonkeyPatch):
    """A three-round calendar where round 2 has one match still unplayed."""
    from liga_record_mcp.models import Fixture

    calendar = [
        Fixture(round_number=1, home="Club 1", away="Club 2", home_goals=1, away_goals=0),
        Fixture(round_number=2, home="Club 2", away="Club 1", home_goals=2, away_goals=2),
        # The straggler: postponed, so round 2 is still technically open.
        Fixture(round_number=2, home="Club 3", away="Club 4", kickoff="20 AGO 20:30"),
        Fixture(round_number=3, home="Club 1", away="Club 3", kickoff="23 AGO 18:00"),
        Fixture(round_number=3, home="Club 2", away="Club 5", kickoff="23 AGO 20:30"),
    ]

    class StubCalendar:
        def fixtures(self):
            return calendar

        def search(self, position, **kwargs):
            return []

    monkeypatch.setattr(mcp_server, "_market", StubCalendar())


def test_squad_fixtures_follows_the_squads_own_round(stub_calendar):
    """A postponed match keeps round 2 open; the squad says round 3, so use 3.

    Answering about round 2 because one fixture is outstanding would attach
    every player to the wrong opponent.
    """
    result = mcp_server.squad_fixtures()
    assert result["fixtures_round"] == 3


def test_squad_fixtures_reports_opponent_and_venue(stub_calendar):
    result = mcp_server.squad_fixtures(round_number=1)
    entries = {e["name"]: e for e in result["playing"]}
    someone = next(e for e in entries.values() if e["club"] == "Club 1")
    assert someone["opponent"] == "Club 2"
    assert someone["at"] == "home"


def test_squad_fixtures_separates_players_with_no_match(stub_calendar):
    """A player whose club is idle cannot score, so they must not be buried
    in the same list as everyone else."""
    result = mcp_server.squad_fixtures(round_number=1)
    idle_clubs = {e["club"] for e in result["no_match_this_round"]}
    assert "Club 3" in idle_clubs  # not in the round-1 fixture
    assert all(e["club"] in {"Club 1", "Club 2"} for e in result["playing"])


def test_get_fixtures_defaults_to_the_first_unplayed_round(stub_calendar):
    """get_fixtures is about the league, not the squad, so it stays literal."""
    result = mcp_server.get_fixtures()
    assert result["round"] == 2
    assert result["next_unplayed_round"] == 2
    assert result["count"] == 2


def test_get_fixtures_filters_by_club_without_accents(stub_calendar):
    result = mcp_server.get_fixtures(round_number=3, club="club 5")
    assert result["count"] == 1
    assert result["fixtures"][0]["away"] == "Club 5"


def test_get_fixtures_reports_a_failure_rather_than_crashing(monkeypatch):
    from liga_record_mcp.source import SiteError

    class Broken:
        def fixtures(self):
            raise SiteError("calendar page moved")

    monkeypatch.setattr(mcp_server, "_market", Broken())
    assert "could not read the calendar" in mcp_server.get_fixtures()["detail"]


# --------------------------------------------------------------------------
# Scoring rates — the fix for the postponed-fixture mistake.
# --------------------------------------------------------------------------


@pytest.fixture
def stub_uneven_calendar(monkeypatch: pytest.MonkeyPatch):
    """Club 1 and Club 2 have played twice; Club 3 only once."""
    from liga_record_mcp.models import Fixture

    calendar = [
        Fixture(round_number=1, home="Club 1", away="Club 2", home_goals=1, away_goals=0),
        Fixture(round_number=1, home="Club 3", away="Club 4", home_goals=2, away_goals=2),
        Fixture(round_number=2, home="Club 2", away="Club 1", home_goals=0, away_goals=0),
        Fixture(round_number=2, home="Club 3", away="Club 5", kickoff="20 AGO 20:30"),
    ]

    class StubCal:
        def fixtures(self):
            return calendar

        def search(self, position, **kwargs):
            return []

    monkeypatch.setattr(mcp_server, "_market", StubCal())


def test_get_squad_carries_the_denominator(stub_uneven_calendar):
    """A total without its match count is what caused the original mistake."""
    players = mcp_server.get_squad()["players"]
    assert all("matches_played" in p for p in players)
    assert all("points_per_match" in p for p in players)


def test_squad_value_warns_when_clubs_are_uneven(stub_uneven_calendar):
    result = mcp_server.squad_value()
    # 0 covers squad clubs the stub calendar never fixtures — a real state,
    # and one a points total would hide completely.
    assert result["match_counts_in_squad"] == [0, 1, 2]
    assert "never points_total" in result["warning"]


def test_squad_value_has_no_warning_when_clubs_are_even(monkeypatch, squad: Squad):
    from liga_record_mcp.models import Fixture

    every_club = sorted({p.club for p in squad.players})
    pairs = [
        Fixture(round_number=1, home=a, away=b, home_goals=1, away_goals=0)
        for a, b in zip(every_club[::2], every_club[1::2])
    ]

    class EvenCal:
        def fixtures(self):
            return pairs

        def search(self, position, **kwargs):
            return []

    monkeypatch.setattr(mcp_server, "_market", EvenCal())
    result = mcp_server.squad_value()
    assert result["match_counts_in_squad"] == [1]
    assert result["warning"] is None


def test_squad_value_ranks_by_rate(stub_uneven_calendar):
    rows = mcp_server.squad_value()["players"]
    rates = [r["points_per_match"] for r in rows if r["points_per_match"] is not None]
    assert rates == sorted(rates, reverse=True)


def test_squad_value_says_when_the_calendar_is_unreachable(monkeypatch):
    from liga_record_mcp.source import SiteError

    class Broken:
        def fixtures(self):
            raise SiteError("down")

    monkeypatch.setattr(mcp_server, "_market", Broken())
    assert "rates cannot be computed" in mcp_server.squad_value()["detail"]


def test_get_squad_says_when_rates_are_missing(monkeypatch):
    from liga_record_mcp.source import SiteError

    class Broken:
        def fixtures(self):
            raise SiteError("down")

    monkeypatch.setattr(mcp_server, "_market", Broken())
    result = mcp_server.get_squad()
    assert "not comparable across clubs" in result["rates_unavailable"]


def test_standings_reports_an_empty_field_without_pretending(monkeypatch):
    """No rows is an answer; it must not be dressed up as a ranking."""
    result = mcp_server.standings(team="nobody")

    assert result["teams"] == []
    assert "no team matched" in result["detail"]
    assert "field_size_estimate" not in result


def test_standings_sizes_the_field_only_when_unfiltered(monkeypatch):
    """6598th means nothing without knowing how many are playing.

    Filtering by name makes the page count describe the name matches, not the
    field, so the estimate must not be reported in that case.
    """

    class Ranked:
        def standings(self, *, team="", **kwargs):
            row = TeamStanding(
                team_id=1, team_name="Melro", user_name="u",
                points_total=87, points_round=38, position=6598,
            )
            return [row], 1909

    monkeypatch.setattr(mcp_server, "_market", Ranked())

    unfiltered = mcp_server.standings()
    assert unfiltered["field_size_estimate"] == 1909 * 20

    filtered = mcp_server.standings(team="Melro")
    assert "field_size_estimate" not in filtered
    assert filtered["teams"][0]["position"] == 6598


def test_standings_reports_a_site_failure_as_detail(monkeypatch):
    """An unreachable ranking must not look like an empty table."""

    class Broken:
        def standings(self, **kwargs):
            raise SiteError("could not reach the ranking: timeout")

    monkeypatch.setattr(mcp_server, "_market", Broken())
    result = mcp_server.standings()

    assert "could not reach" in result["detail"]
    assert "teams" not in result


def test_standings_falls_back_to_the_configured_league(monkeypatch):
    """The guid is not committed, so it arrives from the environment."""
    seen = {}

    class Ranked:
        def standings(self, *, league_guid=None, **kwargs):
            seen["guid"] = league_guid
            return [], 0

    monkeypatch.setattr(mcp_server, "_market", Ranked())
    monkeypatch.setattr(mcp_server, "DEFAULT_LEAGUE", "from-the-environment")

    assert mcp_server.standings()["scope"] == "private league"
    assert seen["guid"] == "from-the-environment"

    mcp_server.standings(league_guid="explicit-wins")
    assert seen["guid"] == "explicit-wins"


def test_standings_is_national_when_no_league_is_configured(monkeypatch):
    class Ranked:
        def standings(self, **kwargs):
            return [], 0

    monkeypatch.setattr(mcp_server, "_market", Ranked())
    monkeypatch.setattr(mcp_server, "DEFAULT_LEAGUE", None)

    assert mcp_server.standings()["scope"] == "national"


def test_standings_can_insist_on_the_national_table(monkeypatch):
    """A configured league must not silently answer a national question.

    With LIGA_RECORD_LEAGUE set, every unqualified call went to the private
    league — including the dashboard's own "how big is the field" call, whose
    answer was then printed as a national ranking.
    """
    seen = []

    class Ranked:
        def standings(self, *, league_guid=None, **kwargs):
            seen.append(league_guid)
            return [], 0

    monkeypatch.setattr(mcp_server, "_market", Ranked())
    monkeypatch.setattr(mcp_server, "DEFAULT_LEAGUE", "a-configured-league")

    assert mcp_server.standings()["scope"] == "private league"
    assert mcp_server.standings(national=True)["scope"] == "national"
    assert seen == ["a-configured-league", None]


def test_national_wins_over_an_explicit_guid_too(monkeypatch):
    """Saying both is a caller's mistake; national is the unambiguous half."""

    class Ranked:
        def standings(self, **kwargs):
            return [], 0

    monkeypatch.setattr(mcp_server, "_market", Ranked())
    assert mcp_server.standings(league_guid="x", national=True)["scope"] == "national"
