"""A club with no fixture is a row, not a refusal.

`snapshot` raised SystemExit when a squad player's club was missing from the
round's calendar, so ONE such player made the whole round unrecordable. And an
unrecorded round is gone from the track record permanently: the snapshot has to
be taken before kickoff, and there is no going back to take it.

`build_dashboard`, looking at the same fact, writes 0.0 under §15.3 and carries
on. Two halves of one system answering one question in opposite ways, and the
half that refused was the half that lost data.

Latent rather than firing today — Record keeps a postponed match in its
original round without a date, so the club is still in the calendar. It becomes
live the moment a rescheduled game is moved to the round of its new date, and
then it takes a round with it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from liga_record_mcp.models import (
    FIRST_SCORING_MATCHDAY,
    ClubRecord,
    Fixture,
    Player,
    Position,
    Squad,
)

ROOT = Path(__file__).resolve().parents[1]

#: Inside the official phase. Since 15/09/2026 the ledger reads the season from
#: the weekly emails and counts matches from matchday 6, so a calendar of rounds
#: 3 and 4 would leave these tests recording a round nothing reads any more.
PLAYED_ROUND = FIRST_SCORING_MATCHDAY
ROUND = FIRST_SCORING_MATCHDAY + 1


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def official_scores(ledger, tmp_path, monkeypatch):
    """An email for the played round that agrees with the fake market.

    The ledger now refuses to record whenever the site's totals and the filed
    emails disagree. These tests are about a club with no fixture, not about
    that check, so they get emails that agree, in a folder of their own, and
    never the real data/pontuacoes.
    """
    squad, _, _ = build(orphan_club=None)
    jogadores = {f"{p.name}|{p.club}": p.points_total for p in squad.players}
    doc = {"ronda": PLAYED_ROUND, "jogadores": jogadores, "adiados": []}
    (tmp_path / f"{PLAYED_ROUND}.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(ledger, "OFFICIAL_DIR", tmp_path)


class FakeMarketPlayer:
    def __init__(self, player: Player) -> None:
        self._p = player
        self.id = player.id
        self.name = player.name
        self.position = player.position
        self.club = player.club
        self.value = player.value
        self.points_total = player.points_total
        self.points_round = 0

    def as_player(self) -> Player:
        return self._p


class FakeMarket:
    """Two clubs with a fixture, one without — the shape that used to refuse."""

    def __init__(self, players, fixtures) -> None:
        self._players = players
        self._fixtures = fixtures

    def fixtures(self):
        return self._fixtures

    def search(self, position):
        return [FakeMarketPlayer(p) for p in self._players if p.position is position]


class FakeHistory:
    def __init__(self, clubs) -> None:
        self._clubs = clubs

    def club_records(self):
        return {
            club: ClubRecord(
                club=club, matches=30, goals_for=40, goals_against=35, points=45
            )
            for club in self._clubs
        }


def build(*, orphan_club: str | None):
    """A legal squad across three clubs, and a calendar covering only two."""
    shape = [(Position.GK, 3), (Position.DEF, 8), (Position.MID, 8), (Position.FWD, 4)]
    clubs = ["Alfa", "Beta", "Gama"]
    players, n = [], 0
    for position, count in shape:
        for _ in range(count):
            n += 1
            players.append(
                Player(
                    id=str(1000 + n),
                    name=f"Jogador {n}",
                    position=position,
                    club=clubs[n % 3],
                    value=1_000_000,
                    initial_value=1_000_000,
                    points_total=n,
                )
            )
    if orphan_club:
        # One club plays nobody this round; the others meet each other.
        playing = [c for c in clubs if c != orphan_club]
        games = [Fixture(round_number=ROUND, home=playing[0], away=playing[1])]
    else:
        games = [
            Fixture(round_number=ROUND, home=clubs[0], away=clubs[1]),
            Fixture(round_number=ROUND, home=clubs[2], away="Delta"),
        ]
    # A played round so the season has some history behind it.
    games.append(
        Fixture(round_number=PLAYED_ROUND, home=clubs[0], away=clubs[1], home_goals=1, away_goals=0)
    )
    squad = Squad(team_id=1, team_name="Melro", players=tuple(players))
    return squad, FakeMarket(players, games), FakeHistory([*clubs, "Delta"])


# --- the refusal ---------------------------------------------------------------


def test_a_club_with_no_fixture_no_longer_kills_the_round(ledger):
    """The defect: one orphaned player and nothing at all gets recorded."""
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    assert len(rows) == len(squad.players), (
        "the round was refused, or players went missing from it"
    )


def test_the_orphaned_players_are_marked_and_scored_at_zero(ledger):
    """§15.3: a match not played before the next round begins scores nothing —
    worse than a hard fixture, better than the -1 for a man left out."""
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    orphans = [r for r in rows.values() if r["club"] == "Gama"]
    assert orphans, "the fixture-less club vanished from the squad"
    for row in orphans:
        assert row["no_fixture"] is True
        assert row["projected"] == 0.0
        assert row["opponent"] is None
        assert row["kickoff"] is None


def test_the_others_are_recorded_normally_beside_them(ledger):
    """One club's missing fixture must not flatten anybody else's estimate."""
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    rest = [r for r in rows.values() if r["club"] != "Gama"]
    assert rest
    assert all(r["opponent"] is not None for r in rest)
    assert all("no_fixture" not in r for r in rest)
    assert any(r["projected"] != 0.0 for r in rest)


# --- the estimate is the player's, not the fixture's ---------------------------
#
# Nulling `season_rate` along with the opponent would have been a second bug:
# `fixture_grid` scales it for FUTURE rounds and sorts the table on it, and the
# players table prints it with a format spec. None in any of those is a
# TypeError and no page at all.


def test_a_fixtureless_player_keeps_his_own_estimate(ledger):
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    for row in (r for r in rows.values() if r["club"] == "Gama"):
        assert isinstance(row["season_rate"], float)
        assert isinstance(row["returns"], float)
        assert isinstance(row["playing"], float)
        assert row["appearances"] is not None


def test_every_row_carries_what_the_page_formats(ledger):
    """The fields the dashboard reads with a format spec or sorts on."""
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    for row in rows.values():
        assert row["season_rate"] is not None, f"{row['name']} would break the sort"
        assert f"{row['season_rate']:.1f}"
        assert row["points_before"] is not None


def test_only_the_fixtures_own_fields_are_null(ledger):
    squad, market, history = build(orphan_club="Gama")
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    row = next(r for r in rows.values() if r["club"] == "Gama")
    nulls = {k for k, v in row.items() if v is None}
    assert nulls == {
        "opponent",
        "at_home",
        "kickoff",
        "defensive_multiplier",
        "attacking_multiplier",
        "actual",
    }, f"unexpected nulls: {sorted(nulls)}"


# --- and a round where everybody plays is untouched ---------------------------


def test_a_full_calendar_marks_nobody(ledger):
    squad, market, history = build(orphan_club=None)
    rows, _ = ledger.snapshot(market, history, squad, ROUND)
    assert all("no_fixture" not in r for r in rows.values())
    assert all(r["opponent"] is not None for r in rows.values())


# --- and what the round rested on ---------------------------------------------


def test_the_round_says_what_it_rested_on(ledger):
    """Filed beside the rows since phase 2: the archive's reach, and which
    rounds of this season came from the emails and which were rebuilt."""
    squad, market, history = build(orphan_club=None)
    _, evidence = ledger.snapshot(market, history, squad, ROUND)
    assert set(evidence) == {"archive_players", "email_rounds", "estimated_rounds"}
    assert evidence["email_rounds"] == [PLAYED_ROUND]
    # Nobody in this made-up market is in the rebuild.
    assert evidence["estimated_rounds"] == []
