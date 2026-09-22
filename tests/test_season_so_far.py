"""This season as the valuation reads it: rounds 1-5 rebuilt, the emails from 6.

Phase 2. The site's reset of 15/09/2026 took rounds 1-5 with it, and they come
back rebuilt from zerozero. A round counts for a man under one rule: his club
played, and he was on the market on 19 August or his club had already called
him up. Without the rebuilt file nothing changes.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

from liga_record_mcp.source.appearances import current_records
from liga_record_mcp.source.season import (
    REBUILT_FILE,
    _merged,
    early_records,
    season_so_far,
)

ROOT = Path(__file__).resolve().parents[1]


def man(player_id: str, club: str = "Benfica"):
    return SimpleNamespace(id=player_id, name=player_id.title(), club=club)


def row(number: int, *, club: str = "Benfica", used: bool = True, points: float = 5.0):
    return {"round": number, "club": club, "used": used, "points": points}


def absence(number: int):
    """What `fill_absent_rounds` writes for a week he was not in the squad."""
    return {"round": number, "used": False, "absent": True, "points": -1.0}


#: Every club's players are in the rebuild, so a club that played shows in it:
#: Benfica played rounds 1-7, FC Porto had no match in round 3.
OTHERS = {
    "b-other": {"matches": [row(n) for n in range(1, 8)]},
    "p-other": {"matches": [row(n, club="FC Porto") for n in (1, 2, 4, 5)]},
}


def early(rebuilt, market, at_start=()):
    return early_records({**OTHERS, **rebuilt}, market, at_start=set(at_start))


# --- the rule ------------------------------------------------------------------


def test_a_round_on_the_pitch_is_played_and_one_on_the_bench_is_not():
    found = early(
        {"a": {"matches": [row(1), row(2, used=False), row(3, points=7.0)]}},
        {"a": man("a")},
        at_start={"a"},
    )["a"]
    assert found["rounds"] == {1: True, 2: False, 3: True, 4: False, 5: False}
    assert (found["played"], found["points"], found["available"]) == (2, 12.0, 5)


def test_a_round_his_club_did_not_play_does_not_count():
    found = early(
        {"p": {"matches": [row(1, club="FC Porto"), row(2, club="FC Porto")]}},
        {"p": man("p", club="FC Porto")},
        at_start={"p"},
    )["p"]
    assert 3 not in found["rounds"]
    assert sorted(found["rounds"]) == [1, 2, 4, 5]


def test_a_late_signing_counts_from_his_first_call_up():
    rebuilt = {"n": {"matches": [row(3), row(4)]}}
    late = early(rebuilt, {"n": man("n")})["n"]
    assert sorted(late["rounds"]) == [3, 4, 5]
    # The same rows for a man already on the market on 19/08: he was at the
    # club and not picked, and a manager holding him paid -1 for it.
    held = early(rebuilt, {"n": man("n")}, at_start={"n"})["n"]
    assert held["rounds"] == {1: False, 2: False, 3: True, 4: True, 5: False}


def test_a_man_never_called_up_counts_only_if_he_was_on_the_market_at_the_start():
    linked = {"q": {"matches": []}}
    found = early(linked, {"q": man("q")}, at_start={"q"})
    assert found["q"]["rounds"] == {n: False for n in range(1, 6)}
    assert early(linked, {"q": man("q")}) == {}


def test_a_man_the_rebuild_does_not_cover_gets_nothing_from_it():
    """Not linked to zerozero, or his page unread: no evidence either way. Read
    as never called up he would get five rounds on the bench he may have spent
    on the pitch — and he was on the market at the start, so the rule would
    have let them count."""
    assert early({}, {"u": man("u")}, at_start={"u"}) == {}


def test_a_week_out_of_the_squad_is_available_and_not_played():
    """The filled-in absence is not a call-up, and its -1 is not points."""
    found = early(
        {"a": {"matches": [row(1), absence(2), row(3)]}},
        {"a": man("a")},
    )["a"]
    assert found["rounds"][2] is False
    assert found["points"] == 10.0


def test_the_round_three_match_played_late_counts_with_what_he_did():
    """Moreirense 0-4 Benfica, 9/09: §15.3 put it at 0 in the game, and the
    emails read it as no round. The football happened, and it says who plays."""
    found = early({"a": {"matches": [row(3, points=9.0)]}}, {"a": man("a")})["a"]
    assert found["rounds"][3] is True
    assert found["points"] == 9.0


def test_the_rebuild_stops_where_the_emails_begin():
    found = early({"a": {"matches": [row(5), row(6), row(7)]}}, {"a": man("a")})["a"]
    assert max(found["rounds"]) == 5


# --- the two halves --------------------------------------------------------------


def test_the_halves_add_up_and_the_last_rounds_are_the_emails():
    market = {"a": man("a"), "e": man("e"), "r": man("r")}
    emails = {
        "a": {"played": 1, "points": 6, "available": 2, "rounds": {6: True, 7: False}},
        "e": {"played": 1, "points": 3, "available": 1, "rounds": {6: True}},
    }
    rebuilt = {
        "a": {"played": 2, "points": 9.0, "available": 3, "rounds": {3: True, 4: True, 5: False}},
        "r": {"played": 0, "points": 0.0, "available": 1, "rounds": {5: False}},
    }
    merged = _merged(market, emails, rebuilt)
    assert merged["a"] == {
        "played": 3,
        "points": 15.0,
        "available": 5,
        "rounds": {3: True, 4: True, 5: False, 6: True, 7: False},
    }
    assert merged["e"] == emails["e"] and merged["r"] == rebuilt["r"]
    # The chance of playing leans on the last two rounds: the emails'.
    assert sorted(merged["a"]["rounds"])[-2:] == [6, 7]


def email_folder(tmp_path: Path) -> Path:
    (tmp_path / "pontuacoes").mkdir()
    doc = {"ronda": 6, "jogadores": {"A|Benfica": 4, "B|Benfica": -1}, "adiados": []}
    (tmp_path / "pontuacoes" / "6.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def test_without_the_rebuild_the_season_is_the_emails_as_before(tmp_path):
    folder = email_folder(tmp_path)
    market = {"a": man("a"), "b": man("b")}
    season, evidence = season_so_far(market, folder)
    emails = {6: json.loads((folder / "pontuacoes" / "6.json").read_text("utf-8"))}
    assert season == current_records(market, emails)
    assert evidence == {"email_rounds": [6], "estimated_rounds": []}


def test_with_the_rebuild_rounds_one_to_five_come_first(tmp_path):
    folder = email_folder(tmp_path)
    rebuilt = {
        **OTHERS,
        "a": {"matches": [row(1), row(2, used=False)]},
        # Linked, and no match all season.
        "b": {"matches": []},
    }
    (folder / REBUILT_FILE).write_text(json.dumps({"players": rebuilt}), encoding="utf-8")
    (folder / "appearances.json").write_text(
        json.dumps({"format": 1, "rounds": {"2": {"players": {"b": "unused"}}}}),
        encoding="utf-8",
    )
    market = {"a": man("a"), "b": man("b")}
    season, evidence = season_so_far(market, folder)
    assert season["a"]["rounds"] == {1: True, 2: False, 3: False, 4: False, 5: False, 6: True}
    assert season["a"]["points"] == 9.0
    # On the market on 19/08 and never called up: five rounds out, then -1 in 6.
    assert season["b"]["rounds"] == {1: False, 2: False, 3: False, 4: False, 5: False, 6: False}
    assert evidence == {"email_rounds": [6], "estimated_rounds": [1, 2, 3, 4, 5]}


def test_the_emails_handed_in_are_the_ones_read_and_are_left_alone(tmp_path):
    """The ledger passes the rounds its consistency check read, so the check
    and the valuation see the same files — and the check never a rebuilt round."""
    rebuilt = {**OTHERS, "a": {"matches": [row(1)]}}
    (tmp_path / REBUILT_FILE).write_text(json.dumps({"players": rebuilt}), encoding="utf-8")
    emails = {6: {"ronda": 6, "jogadores": {"A|Benfica": 4}, "adiados": []}}
    before = copy.deepcopy(emails)
    season, evidence = season_so_far({"a": man("a")}, tmp_path, emails=emails)
    assert emails == before
    assert evidence["email_rounds"] == [6]
    assert season["a"]["rounds"][1] is True


# --- one door ----------------------------------------------------------------------


READERS = (
    "scripts/record_projection.py",
    "scripts/build_dashboard.py",
    "scripts/propose_squad.py",
    "src/liga_record_mcp/server.py",
)


def test_the_four_readers_take_the_season_from_one_place():
    for path in READERS:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "season_so_far(" in source, f"{path} reads the season its own way"
        assert "current_records(" not in source, f"{path} kept its own reading"


def test_the_ledgers_guard_still_reads_the_emails_alone():
    """The site's totals start at matchday 6; a rebuilt round in the check
    would make every total disagree and stop the ledger for good."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert re.search(r"consistency_problems\(\s*whole,\s*official,", source)
    assert "season_so_far(whole, ROOT / \"data\", emails=official)" in source


def test_the_ledger_files_the_evidence_with_the_round():
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "rows, evidence = snapshot(" in source
    assert '"evidence": evidence,' in source
