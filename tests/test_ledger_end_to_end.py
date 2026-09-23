"""The ledger, run the way the scheduled task runs it, with nothing stubbed
between the market and the file it writes.

`test_two_writers` runs `main()` too, but with `snapshot` replaced by fixed
rows — it is about which writer may record, not about what is recorded. This
one keeps the real projection: a made-up market, calendar, score email and
squad go in, and what comes out is the round as `data/projections.json` would
hold it. It is the path that runs unattended twice a day and writes the only
record this project keeps of whether the model is any good, and until now
nothing exercised it whole.

No network, and no reading of the real data directory: `ROOT` is moved to a
temporary one, so the archive and the rebuilt season are simply absent, which
is what a fresh checkout has.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import make_squad

from liga_record_mcp.models import (
    FIRST_SCORING_MATCHDAY,
    ClubRecord,
    Fixture,
    Position,
    Selection,
    Squad,
    SquadSnapshot,
)

ROOT = Path(__file__).resolve().parents[1]

#: The round being recorded, and the one already scored behind it.
ROUND = FIRST_SCORING_MATCHDAY + 1
PLAYED = FIRST_SCORING_MATCHDAY


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: What each man scored in the round already played, and so what the site's
#: total must say: the guard refuses to record while the two disagree, which
#: is the check that caught the site zeroing every total on 15/09/2026.
_BASE = make_squad()
SCORED = {player.id: (n % 9) - 1 for n, player in enumerate(_BASE.players)}
SQUAD = Squad(
    team_id=_BASE.team_id,
    team_name=_BASE.team_name,
    players=tuple(
        player.model_copy(update={"points_total": SCORED[player.id]})
        for player in _BASE.players
    ),
)

CLUBS = sorted({player.club for player in SQUAD.players})


def pairs(clubs):
    """Clubs two by two, so every one of them has a match."""
    return [(clubs[n], clubs[n + 1]) for n in range(0, len(clubs) - 1, 2)]


class MarketPlayer(SimpleNamespace):
    def as_player(self):
        return self.player


def market_entry(player):
    return MarketPlayer(
        id=player.id,
        name=player.name,
        club=player.club,
        position=player.position,
        points_total=SCORED[player.id],
        player=player,
    )


class Market:
    """The round already played, the one being recorded still to come."""

    def __init__(self, timeout=None) -> None:
        pass

    def fixtures(self):
        played = [
            Fixture(
                round_number=PLAYED, home=home, away=away, home_goals=1, away_goals=0,
                kickoff="10 OUT 18:00",
            )
            for home, away in pairs(CLUBS)
        ]
        coming = [
            Fixture(round_number=ROUND, home=away, away=home, kickoff="17 OUT 18:00")
            for home, away in pairs(CLUBS)
        ]
        return played + coming

    def search(self, position):
        return [market_entry(p) for p in SQUAD.players if p.position is position]


class History:
    def club_records(self):
        return {
            club: ClubRecord(club=club, matches=34, goals_for=40, goals_against=40)
            for club in CLUBS
        }

    def __init__(self, timeout=None) -> None:
        pass


def squad_source():
    snapshot = SquadSnapshot(
        squad=SQUAD,
        selection=Selection(
            starters=tuple(p.id for p in SQUAD.players[:11]),
            bench=tuple(p.id for p in SQUAD.players[11:15]),
            captain=SQUAD.players[0].id,
            coach_id="860",
        ),
        round_number=ROUND,
        fetched_at=datetime.datetime(2026, 10, 12, tzinfo=datetime.timezone.utc),
        source="teste",
    )

    class Source:
        def __init__(self, _path) -> None:
            pass

        def load(self) -> SquadSnapshot:
            return snapshot

    return Source


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "projections.json"


@pytest.fixture
def recorder(ledger, tmp_path, log_path, monkeypatch):
    """Everything the script touches, pointed at a temporary world."""
    emails = tmp_path / "pontuacoes"
    emails.mkdir()
    (emails / f"{PLAYED}.json").write_text(
        json.dumps(
            {
                "ronda": PLAYED,
                "jogadores": {
                    f"{p.name}|{p.club}": SCORED[p.id] for p in SQUAD.players
                },
                "adiados": [],
            }
        ),
        encoding="utf-8",
    )
    log = log_path
    log.write_text(json.dumps({"rounds": {}}), encoding="utf-8")

    # ROOT is looked up inside `snapshot`, so moving it hides the archive and
    # the rebuilt season. The other paths were computed at import and have to
    # be moved one by one, or a stub dropped later would quietly read the real
    # data directory — `COACHES_PATH` above all, which is edited every week.
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "SQUAD_PATH", tmp_path / "squad.yaml")
    monkeypatch.setattr(ledger, "COACHES_PATH", tmp_path / "coaches.yaml")
    monkeypatch.setattr(ledger, "OFFICIAL_DIR", emails)
    monkeypatch.setattr(ledger, "LOG_PATH", log)
    monkeypatch.setattr(ledger, "UNAVAILABLE_PATH", tmp_path / "indisponiveis.yaml")
    monkeypatch.setattr(ledger, "BULLETIN_DIR", tmp_path / "boletim")
    monkeypatch.setattr(ledger, "ManualSquadSource", squad_source())
    monkeypatch.setattr(ledger, "LigaRecordClient", Market)
    monkeypatch.setattr(ledger, "OpenFootballClient", History)
    # The coach is its own path, with its own tests; this one is the projection.
    monkeypatch.setattr(
        ledger,
        "coach_snapshot",
        lambda history, fixtures, round_number, coach_id: {"id": coach_id},
    )
    monkeypatch.setattr(ledger, "advised_coach", lambda history, fixtures, number: None)

    def run(*flags: str) -> dict:
        monkeypatch.setattr("sys.argv", ["record_projection.py", *flags])
        ledger.main()
        return json.loads(log.read_text("utf-8"))["rounds"]

    return run


def test_a_round_is_recorded_whole(recorder):
    rounds = recorder()
    assert list(rounds) == [str(ROUND)]

    filed = rounds[str(ROUND)]
    assert len(filed["players"]) == len(SQUAD.players)
    assert filed["estimator"] == "valuation+fixture+recency"
    assert filed["squad_value"] == SQUAD.value()

    played_man = next(p for p in SQUAD.players if SCORED[p.id] != -1)
    row = filed["players"][played_man.id]
    assert row["opponent"] and row["at_home"] in (True, False)
    assert row["actual"] is None, "a projection is written before the round, never after"
    assert isinstance(row["projected"], float)
    # The one round of evidence there is, and no archive in this world. A man
    # the email paid -1 has none at all, which is the other half of the split.
    assert row["appearances"] == 1
    benched = next(p for p in SQUAD.players if SCORED[p.id] == -1)
    assert filed["players"][benched.id]["appearances"] == 0


def test_the_round_says_what_it_rested_on(recorder):
    """A fresh checkout has neither the archive nor the rebuilt rounds, and the
    round it writes has to say so — the job on GitHub is exactly that."""
    filed = recorder()[str(ROUND)]
    assert filed["evidence"] == {
        "archive_players": 0,
        "email_rounds": [PLAYED],
        "estimated_rounds": [],
    }


def test_the_sheet_on_file_is_the_one_the_squad_file_named(recorder):
    filed = recorder()[str(ROUND)]
    assert filed["filed"]["captain"] == SQUAD.players[0].id
    assert filed["filed"]["starters"] == [p.id for p in SQUAD.players[:11]]
    # And the model's own eleven beside it, eleven names and a captain.
    assert len(filed["advised"]["starters"]) == 11
    assert filed["advised"]["captain"] in filed["advised"]["starters"]


def test_a_round_already_on_file_is_refused_not_rewritten(recorder, log_path):
    """The ordering is the whole point: a prediction written after the result
    is worthless, and a careless re-run is the easiest way to get one. It
    refuses out loud, and the round on file does not move."""
    first = recorder()[str(ROUND)]
    with pytest.raises(SystemExit, match="already on file"):
        recorder()
    again = json.loads(log_path.read_text("utf-8"))["rounds"][str(ROUND)]
    assert again == first
