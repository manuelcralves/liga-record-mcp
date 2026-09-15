"""The season read from the weekly score emails, and the guard that keeps it honest.

On 15 September 2026 Liga Record put every player's total back to zero for the
official phase. Pavlidis went from 40 to 9, and every reading of the season
that divided the site's total by the calendar's matches saw nine points in six
games. These pin what replaced it: one round is one round, a -1 is an absence,
a postponed club is no round at all — and a projection is refused whenever the
site and the emails disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import make_player

from liga_record_mcp.models import Fixture, Position
from liga_record_mcp.source.base import SquadSourceError
from liga_record_mcp.source.scores import (
    consistency_problems,
    load_official_rounds,
    season_records,
)

ROOT = Path(__file__).resolve().parents[1]


def player(pid, name, club, points_total=0):
    return make_player(
        pid, Position.FWD, 500_000, points_total=points_total, name=name
    ).model_copy(update={"club": club})


def round_doc(number, jogadores, adiados=(), anulados=None):
    doc = {"ronda": number, "jogadores": dict(jogadores), "adiados": list(adiados)}
    if anulados:
        doc["anulados_15_3"] = dict(anulados)
    return doc


def played(number, home, away):
    return Fixture(round_number=number, home=home, away=away, home_goals=1, away_goals=0)


# --- one round is one round ---------------------------------------------------


def test_a_player_who_scored_is_one_match_with_his_points():
    """The acceptance line: Pavlidis on 1 match and 9 points, not 9 over six."""
    market = {"42896": player("42896", "Pavlidis", "Benfica", points_total=9)}
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 9})}
    assert season_records(market, rounds)["42896"] == {
        "played": 1, "points": 9, "available": 1, "rounds": {6: True}
    }


def test_minus_one_is_an_absence_not_a_match():
    market = {"x": player("x", "Nehuén Pérez", "FC Porto", points_total=-1)}
    rounds = {6: round_doc(6, {"Nehuén Pérez|FC Porto": -1})}
    assert season_records(market, rounds)["x"] == {
        "played": 0, "points": 0, "available": 1, "rounds": {6: False}
    }


def test_a_real_zero_is_a_match_played():
    """Lekovic made 0 for E. Amadora on matchday 6. Zero is a score."""
    market = {"x": player("x", "Lekovic", "E. Amadora")}
    rounds = {6: round_doc(6, {"Lekovic|E. Amadora": 0})}
    assert season_records(market, rounds)["x"] == {
        "played": 1, "points": 0, "available": 1, "rounds": {6: True}
    }


def test_a_postponed_club_is_no_round_at_all():
    market = {"x": player("x", "Pavlidis", "Benfica")}
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 0}, adiados=["Benfica"])}
    assert "x" not in season_records(market, rounds)


def test_a_match_voided_by_15_3_is_no_round_at_all():
    market = {"x": player("x", "Pavlidis", "Benfica")}
    rounds = {
        6: round_doc(
            6,
            {"Pavlidis|Benfica": 0},
            anulados={"Benfica": "jogado depois da ronda seguinte"},
        )
    }
    assert "x" not in season_records(market, rounds)


def test_a_player_missing_from_the_email_gets_no_round():
    market = {"x": player("x", "Ninguém", "Alverca")}
    assert season_records(market, {6: round_doc(6, {"Outro|Alverca": 3})}) == {}


def test_rounds_add_up():
    market = {"x": player("x", "Paciência", "Santa Clara")}
    rounds = {
        6: round_doc(6, {"Paciência|Santa Clara": 7}),
        7: round_doc(7, {"Paciência|Santa Clara": -1}),
        8: round_doc(8, {"Paciência|Santa Clara": 2}),
    }
    assert season_records(market, rounds)["x"] == {
        "played": 2, "points": 9, "available": 3,
        "rounds": {6: True, 7: False, 8: True},
    }


def test_a_player_who_moved_club_keeps_the_rounds_he_played_for_the_old_one():
    """A January transfer files him under his old club in the rounds before."""
    market = {"x": player("x", "Stoica", "Sporting")}
    rounds = {
        6: round_doc(6, {"Stoica|E. Amadora": 6}),
        7: round_doc(7, {"Stoica|Sporting": 2}),
    }
    assert season_records(market, rounds)["x"] == {
        "played": 2, "points": 8, "available": 2, "rounds": {6: True, 7: True}
    }


def test_a_shared_name_is_never_guessed():
    """There are two Samu. A third who matches neither club takes neither."""
    market = {"x": player("x", "Samu", "Alverca")}
    rounds = {6: round_doc(6, {"Samu|FC Porto": -1, "Samu|V. Guimarães": 3})}
    assert season_records(market, rounds) == {}


def test_the_real_email_puts_pavlidis_on_one_match_and_nine_points():
    rounds = load_official_rounds(ROOT / "data" / "pontuacoes", first_round=6)
    market = {"42896": player("42896", "Pavlidis", "Benfica", points_total=9)}
    assert season_records(market, {6: rounds[6]})["42896"] == {
        "played": 1, "points": 9, "available": 1, "rounds": {6: True}
    }


# --- the guard ----------------------------------------------------------------


def test_a_played_round_without_its_email_is_refused():
    market = {"x": player("x", "Pavlidis", "Benfica", points_total=9)}
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 9})}
    fixtures = [played(6, "Benfica", "Gil Vicente"), played(7, "FC Porto", "Benfica")]
    problems = consistency_problems(market, rounds, fixtures, first_round=6)
    assert problems and "7" in problems[0]


def test_a_total_the_emails_do_not_add_up_to_is_refused_and_named():
    """The reset itself, as the guard would have seen it the other way round."""
    market = {"x": player("x", "Pavlidis", "Benfica", points_total=40)}
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 9})}
    problems = consistency_problems(
        market, rounds, [played(6, "Benfica", "Gil Vicente")], first_round=6
    )
    assert problems and "Pavlidis" in problems[0] and "40" in problems[0]


def test_consistent_data_is_let_through():
    market = {
        "x": player("x", "Pavlidis", "Benfica", points_total=9),
        "y": player("y", "Zaidu", "FC Porto", points_total=-1),
    }
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 9, "Zaidu|FC Porto": -1})}
    fixtures = [played(6, "Benfica", "Gil Vicente"), played(6, "Casa Pia", "FC Porto")]
    assert consistency_problems(market, rounds, fixtures, first_round=6) == []


def test_the_trial_rounds_are_not_asked_for():
    """Rounds 1-5 were filed for the squad only. Their absence is not a gap."""
    market = {"x": player("x", "Pavlidis", "Benfica", points_total=9)}
    rounds = {6: round_doc(6, {"Pavlidis|Benfica": 9})}
    fixtures = [played(3, "Moreirense", "Benfica"), played(6, "Benfica", "Gil Vicente")]
    assert consistency_problems(market, rounds, fixtures, first_round=6) == []


def test_a_site_one_round_behind_its_emails_is_let_through():
    """The known lag: round 7 is emailed and filed, the site's totals stop at 6.

    Found by the code review of this change. The season is read from the
    emails, so nothing the projection uses is stale — and refusing here would
    fail the scheduled job at every run until the site caught up.
    """
    market = {
        "x": player("x", "Pavlidis", "Benfica", points_total=9),
        "y": player("y", "Zaidu", "FC Porto", points_total=-1),
    }
    rounds = {
        6: round_doc(6, {"Pavlidis|Benfica": 9, "Zaidu|FC Porto": -1}),
        7: round_doc(7, {"Pavlidis|Benfica": 3, "Zaidu|FC Porto": -1}),
    }
    fixtures = [played(6, "Benfica", "Gil Vicente"), played(7, "FC Porto", "Benfica")]
    assert consistency_problems(market, rounds, fixtures, first_round=6) == []


def test_a_site_two_rounds_behind_is_refused():
    """One round is the site being slow. Two is beyond anything it has done."""
    market = {"x": player("x", "Pavlidis", "Benfica", points_total=9)}
    rounds = {
        6: round_doc(6, {"Pavlidis|Benfica": 9}),
        7: round_doc(7, {"Pavlidis|Benfica": 3}),
        8: round_doc(8, {"Pavlidis|Benfica": 4}),
    }
    fixtures = [played(r, "Benfica", "Gil Vicente") for r in (6, 7, 8)]
    problems = consistency_problems(market, rounds, fixtures, first_round=6)
    assert problems and "Pavlidis" in problems[0]


def test_players_disagreeing_about_how_far_behind_the_site_is_are_refused():
    """A uniform lag is the site being slow. A mixed one is something else."""
    market = {
        "x": player("x", "Pavlidis", "Benfica", points_total=12),
        "y": player("y", "Suárez", "Sporting", points_total=5),
    }
    rounds = {
        6: round_doc(6, {"Pavlidis|Benfica": 9, "Suárez|Sporting": 5}),
        7: round_doc(7, {"Pavlidis|Benfica": 3, "Suárez|Sporting": 4}),
    }
    fixtures = [played(6, "Benfica", "Gil Vicente"), played(7, "Sporting", "Arouca")]
    problems = consistency_problems(market, rounds, fixtures, first_round=6)
    assert problems and "Suárez" in problems[0]


# --- loading the files --------------------------------------------------------


def test_only_numbered_official_rounds_are_loaded(tmp_path):
    (tmp_path / "5.json").write_text(json.dumps(round_doc(5, {})), encoding="utf-8")
    (tmp_path / "6.json").write_text(json.dumps(round_doc(6, {"A|B": 1})), encoding="utf-8")
    (tmp_path / "notas.json").write_text("{}", encoding="utf-8")
    assert list(load_official_rounds(tmp_path, first_round=6)) == [6]


def test_a_file_that_names_another_round_fails_loudly(tmp_path):
    (tmp_path / "7.json").write_text(json.dumps(round_doc(6, {})), encoding="utf-8")
    with pytest.raises(SquadSourceError, match="round 6"):
        load_official_rounds(tmp_path, first_round=6)


def test_a_file_without_a_score_table_fails_loudly(tmp_path):
    (tmp_path / "6.json").write_text(json.dumps({"ronda": 6}), encoding="utf-8")
    with pytest.raises(SquadSourceError):
        load_official_rounds(tmp_path, first_round=6)


def test_the_real_files_load_from_the_official_phase_on():
    rounds = load_official_rounds(ROOT / "data" / "pontuacoes", first_round=6)
    assert 6 in rounds and min(rounds) >= 6
    assert len(rounds[6]["jogadores"]) == 542


# --- one reading, everywhere --------------------------------------------------


def test_the_squad_proposal_no_longer_keeps_its_own_copy():
    source = (ROOT / "scripts" / "propose_squad.py").read_text(encoding="utf-8")
    assert "def this_season" not in source
    assert "current_records(" in source


def test_the_ledger_checks_before_it_records():
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "consistency_problems(" in source


def test_the_squad_proposal_values_players_with_the_one_function():
    """Its copy of the chance of playing outlived the replay that measured it worse."""
    source = (ROOT / "scripts" / "propose_squad.py").read_text(encoding="utf-8")
    assert "valuation(" in source
    assert "APPEARANCE_PRIOR" not in source
    assert "def history" not in source


# --- the rounds the recent rule reads ------------------------------------------


def test_rounds_say_which_he_played_in_round_order():
    """The chance of playing leans on the last two, so the order is the information.

    Filed out of order on purpose, with a postponed round in between: a
    postponed round is no round at all, here as everywhere else.
    """
    market = {"x": player("x", "Paciência", "Santa Clara")}
    rounds = {
        8: round_doc(8, {"Paciência|Santa Clara": 2}),
        6: round_doc(6, {"Paciência|Santa Clara": -1}),
        7: round_doc(7, {"Paciência|Santa Clara": 0}, adiados=["Santa Clara"]),
    }
    got = season_records(market, rounds)["x"]["rounds"]
    assert list(got.items()) == [(6, False), (8, True)]
