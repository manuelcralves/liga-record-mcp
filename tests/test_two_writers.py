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


#: What the job's round rests on: no archive and no rebuilt rounds, the emails alone.
JOB_EVIDENCE = {"archive_players": 0, "email_rounds": [6], "estimated_rounds": []}

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
        ledger,
        "snapshot",
        lambda market, history, squad, round_number: (
            copy.deepcopy(JOB_ROWS),
            dict(JOB_EVIDENCE),
        ),
    )
    monkeypatch.setattr(
        ledger,
        "coach_snapshot",
        lambda history, fixtures, round_number, coach_id: {"id": coach_id, "actual": None},
    )
    monkeypatch.setattr(ledger, "advised_coach", lambda history, fixtures, round_number: None)
    monkeypatch.setattr(ledger, "advised_sheet", lambda rows, coach=None: None)
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
    # And the round says what it rested on, so it reads apart from the laptop's.
    assert writer.on_file()[str(ROUND)]["evidence"] == JOB_EVIDENCE


# --- and when this checkout can do better than the round on file ----------------


def unmoved() -> dict:
    """A round on file that nothing else would bring anyone back to.

    `stored_round` has a man the snapshot said was out and the hand file does
    not, which is a moved sheet — the reason the other tests here re-record.
    Dropping it leaves the evidence as the only thing that can decide.
    """
    round_on_file = stored_round()
    round_on_file["players"]["a"].pop("unavailable")
    return round_on_file


def filed_with(archive_players: int, estimated=()) -> dict:
    """The round as the job would have filed it: evidence, and little of it."""
    round_on_file = unmoved()
    round_on_file["evidence"] = {
        "archive_players": archive_players,
        "email_rounds": [6],
        "estimated_rounds": list(estimated),
    }
    return round_on_file


def archive_of(folder: Path, players: int) -> None:
    """An archive `archive_records` can read, with that many players in it."""
    data = folder / "data"
    data.mkdir(exist_ok=True)
    (data / "last-season.json").write_text(
        json.dumps(
            {
                "players": {
                    str(n): {"matches": [{"used": True, "points": 3}]}
                    for n in range(players)
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def richer(ledger, writer, tmp_path, monkeypatch):
    """A checkout with an archive, looking at a round filed without one."""
    archive_of(tmp_path, 3)
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    return writer


def test_the_laptop_records_again_when_it_can_do_better(richer):
    """The job files a round nobody has, from a checkout with no archive. The
    laptop comes back to it before kickoff, and before kickoff the prediction
    on file should be the best there is."""
    richer.ledger_with({str(ROUND): filed_with(archive_players=0)})
    richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] != RECORDED_AT


def test_it_says_which_reason_brought_it_back(richer, capsys):
    richer.ledger_with({str(ROUND): filed_with(archive_players=0)})
    richer.run()
    said = capsys.readouterr().out
    assert "mais evidencia" in said and "arquivo de 3 jogadores contra 0" in said


def test_the_job_itself_never_records_it_again(richer, capsys):
    """Only the laptop upgrades. The job keeps --no-rerecord, so the two never
    take turns."""
    richer.ledger_with({str(ROUND): filed_with(archive_players=0)})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run("--no-rerecord")
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT
    assert "--no-rerecord" in capsys.readouterr().out


def test_the_same_evidence_is_not_a_reason(richer):
    """Or every run would record the round again, which is the turn-taking this
    whole guard exists to prevent."""
    richer.ledger_with({str(ROUND): filed_with(archive_players=3)})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT


def test_a_round_filed_before_the_evidence_existed_is_left_alone(richer):
    """Everything on file before 22/09/2026. There is nothing to compare it
    with, and guessing would be the mistake this corrects."""
    richer.ledger_with({str(ROUND): unmoved()})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT


def test_a_round_that_has_begun_is_never_recorded_again(richer, ledger, monkeypatch):
    """However much better the evidence is. After kickoff the prediction is
    what it was, or it is not a prediction."""

    class Started:
        def __init__(self, timeout=None) -> None:
            pass

        def fixtures(self):
            return [
                Fixture(
                    round_number=ROUND, home="Alfa", away="Beta",
                    home_goals=1, away_goals=1,
                )
            ]

        def search(self, position):
            return []

    monkeypatch.setattr(ledger, "LigaRecordClient", Started)
    richer.ledger_with({str(ROUND): filed_with(archive_players=0)})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT


def test_a_half_written_archive_is_not_a_reason_and_not_a_crash(richer, ledger, tmp_path, capsys):
    """Both files are rebuilt locally in one non-atomic write of megabytes, so
    an interrupted rebuild leaves invalid JSON. Reading them now happens on
    every run, and a broken file must not turn 'nothing changed' into a crash."""
    (tmp_path / "data" / "last-season.json").write_text("{ nao e json", encoding="utf-8")
    richer.ledger_with({str(ROUND): filed_with(archive_players=0)})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT
    assert "nao consegui ler o arquivo" in capsys.readouterr().out


def test_a_rebuilt_season_with_nothing_early_in_it_is_not_a_reason(richer, tmp_path):
    """Present is not enough. A file still being written, or one whose ids do
    not meet this market, gives the round nothing — and claiming it does would
    record the same round again on every run, for ever."""
    (tmp_path / "data" / "season-2026-27.json").write_text(
        json.dumps({"players": {"x": {"matches": [{"round": 7, "used": True}]}}}),
        encoding="utf-8",
    )
    richer.ledger_with({str(ROUND): filed_with(archive_players=3)})
    with pytest.raises(SystemExit, match="already on file"):
        richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] == RECORDED_AT


def test_a_rebuilt_season_with_the_early_rounds_is_a_reason(richer, tmp_path):
    (tmp_path / "data" / "season-2026-27.json").write_text(
        json.dumps({"players": {"x": {"matches": [{"round": 3, "used": True}]}}}),
        encoding="utf-8",
    )
    richer.ledger_with({str(ROUND): filed_with(archive_players=3)})
    richer.run()
    assert richer.on_file()[str(ROUND)]["recorded_at"] != RECORDED_AT
