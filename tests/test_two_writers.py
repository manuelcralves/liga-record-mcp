"""The ledger has two writers, and only one of them sees the injury bulletin.

`record_projection` runs on the laptop and, twice a day, in the job on GitHub,
on a fresh checkout. data/boletim/ is gitignored, being paid content, and so is
the archive. A round already on file is recorded again before kickoff when the
list of who is out has moved, and to the job it always has when a snapshot names
a squad player the hand file does not: its list is the hand file alone.

So the job would record the round again, without the bulletin or the archive,
the laptop would record it back, and the two would take turns until kickoff —
whichever wrote last being the prediction on file. Found on 21/09/2026 before
it bit: round 7 escaped because the six squad players on the 18/09 snapshot
were the six in the hand file, and the same six are in the squad for round 8.

These run `main()` end to end, with the market, the squad file and the history
faked, and no network.
"""

from __future__ import annotations

import copy
import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.models import (
    Fixture,
    Player,
    Position,
    Selection,
    Squad,
    SquadSnapshot,
)

ROOT = Path(__file__).resolve().parents[1]
ROUND = 8
RECORDED_AT = "2026-09-21T07:20:05.311953+00:00"


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLAYERS = (
    Player(
        id="a",
        name="Jogador A",
        position=Position.DEF,
        club="Alfa",
        value=1_000_000,
        initial_value=1_000_000,
    ),
    Player(
        id="b",
        name="Jogador B",
        position=Position.FWD,
        club="Beta",
        value=1_000_000,
        initial_value=1_000_000,
    ),
)


def stored_round() -> dict:
    """The round as the laptop filed it: A out by the snapshot, and only by it."""
    return {
        "recorded_at": RECORDED_AT,
        "estimator": "valuation+fixture+recency",
        "squad_value": 2_000_000,
        "filed": {"starters": ["a", "b"], "bench": [], "captain": "a"},
        "advised": None,
        "players": {
            "a": {
                "name": "Jogador A",
                "position": "DEF",
                "club": "Alfa",
                "value": 1_000_000,
                "appearances": 70,
                "projected": -1.0,
                "actual": None,
                "unavailable": "lesionado a 25/09",
            },
            "b": {
                "name": "Jogador B",
                "position": "FWD",
                "club": "Beta",
                "value": 1_000_000,
                "appearances": 71,
                "projected": 4.2,
                "actual": None,
            },
        },
        "coach": {"id": "860", "actual": None},
    }


#: What the job computes for the same round: nobody out, and no archive behind it.
JOB_ROWS = {
    "a": {
        "name": "Jogador A",
        "position": "DEF",
        "club": "Alfa",
        "value": 1_000_000,
        "opponent": "Beta",
        "appearances": 2,
        "projected": 3.1,
        "actual": None,
    },
    "b": {
        "name": "Jogador B",
        "position": "FWD",
        "club": "Beta",
        "value": 1_000_000,
        "opponent": "Alfa",
        "appearances": 2,
        "projected": 4.0,
        "actual": None,
    },
}


#: The twenty-three after a transfer: B sold, C bought.
AFTER_A_TRANSFER = (
    PLAYERS[0],
    Player(
        id="c",
        name="Jogador C",
        position=Position.FWD,
        club="Beta",
        value=1_000_000,
        initial_value=1_000_000,
    ),
)


def squad_file(captain: str = "a", players=PLAYERS, starters=("a", "b")):
    """What ManualSquadSource reads off data/squad.yaml, sheet and coach included."""
    snapshot = SquadSnapshot(
        squad=Squad(team_id=1, team_name="Teste", players=players),
        selection=Selection(
            starters=starters, bench=(), captain=captain, coach_id="860"
        ),
        round_number=ROUND,
        fetched_at=datetime.datetime(2026, 9, 21, tzinfo=datetime.timezone.utc),
        source="teste",
    )

    class Source:
        def __init__(self, _path) -> None:
            pass

        def load(self) -> SquadSnapshot:
            return snapshot

    return Source


class Market:
    """A calendar in which the round has not kicked off."""

    def __init__(self, timeout=None) -> None:
        pass

    def fixtures(self):
        return [Fixture(round_number=ROUND, home="Alfa", away="Beta")]

    def search(self, position):
        return []


@pytest.fixture
def writer(ledger, tmp_path, monkeypatch):
    """The ledger script as the job runs it: no snapshot and no hand file on disk."""
    log = tmp_path / "projections.json"

    def ledger_with(rounds: dict) -> None:
        log.write_text(
            json.dumps({"rounds": rounds}, ensure_ascii=False, indent=2), "utf-8"
        )

    def on_file() -> dict:
        return json.loads(log.read_text("utf-8"))["rounds"]

    def run(*flags: str) -> None:
        monkeypatch.setattr("sys.argv", ["record_projection.py", *flags])
        ledger.main()

    ledger_with({str(ROUND): stored_round()})
    monkeypatch.setattr(ledger, "LOG_PATH", log)
    monkeypatch.setattr(ledger, "UNAVAILABLE_PATH", tmp_path / "indisponiveis.yaml")
    monkeypatch.setattr(ledger, "BULLETIN_DIR", tmp_path / "boletim")
    monkeypatch.setattr(ledger, "ManualSquadSource", squad_file())
    monkeypatch.setattr(ledger, "LigaRecordClient", Market)
    monkeypatch.setattr(ledger, "OpenFootballClient", lambda timeout=None: None)
    monkeypatch.setattr(
        ledger, "snapshot", lambda market, history, squad, round_number: copy.deepcopy(JOB_ROWS)
    )
    monkeypatch.setattr(
        ledger,
        "coach_snapshot",
        lambda history, counts, round_number, coach_id: {"id": coach_id, "actual": None},
    )
    monkeypatch.setattr(ledger, "advised_sheet", lambda rows: None)
    return SimpleNamespace(log=log, ledger_with=ledger_with, on_file=on_file, run=run)


def test_the_job_leaves_a_round_it_cannot_see_alone(writer):
    """The whole fix: byte for byte, and declined in the words routine.py knows."""
    before = writer.log.read_bytes()
    with pytest.raises(SystemExit, match="already on file"):
        writer.run("--no-rerecord")
    assert writer.log.read_bytes() == before


def test_without_the_flag_the_round_is_recorded_again(writer):
    """What the job did until now, and what the laptop still does when the list
    of who is out really moves: the round is recorded again before kickoff."""
    writer.run()
    again = writer.on_file()[str(ROUND)]
    assert again["recorded_at"] != RECORDED_AT
    assert "unavailable" not in again["players"]["a"]


def test_the_job_still_files_the_new_sheet(writer, ledger, monkeypatch):
    """The sheet comes from data/squad.yaml, which both writers read alike, so
    the job keeps it current even on a round whose projections it leaves be."""
    monkeypatch.setattr(ledger, "ManualSquadSource", squad_file(captain="b"))
    writer.run("--no-rerecord")
    kept = writer.on_file()[str(ROUND)]
    assert kept["filed"]["captain"] == "b"
    assert kept["players"] == stored_round()["players"]
    assert kept["recorded_at"] == RECORDED_AT


def test_after_a_transfer_the_job_files_the_sheet_entered(writer, ledger, monkeypatch):
    """The one case the job gives up, and what it leaves behind.

    A transfer pushed from another machine, with the laptop off until kickoff.
    Recording the round again here would file the poorer model under the good
    estimator's name, so the projections stay those of the old twenty-three.
    The sheet is a fact, and the true one is the sheet entered, new player and
    all. Keeping the old sheet instead would have the track record score an
    eleven nobody entered; with this one it leaves the round's own total blank,
    which test_track_record pins.
    """
    monkeypatch.setattr(
        ledger,
        "ManualSquadSource",
        squad_file(players=AFTER_A_TRANSFER, starters=("a", "c")),
    )
    writer.run("--no-rerecord")
    kept = writer.on_file()[str(ROUND)]
    assert kept["players"] == stored_round()["players"]
    assert kept["recorded_at"] == RECORDED_AT
    assert kept["filed"]["starters"] == ["a", "c"]


def test_the_job_still_records_a_round_nobody_has(writer):
    """The reason the job exists: a week with the laptop off is not a hole."""
    writer.ledger_with({})
    writer.run("--no-rerecord")
    assert writer.on_file()[str(ROUND)]["players"] == JOB_ROWS
