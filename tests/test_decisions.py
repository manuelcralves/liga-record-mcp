"""The ledger of what was decided, and what it was worth.

The projection ledger records what was expected of each player and what he did.
This records the other half — what the manager did with that. Only the pair
answers the question the whole project rests on: over a season, did taking the
advice beat not taking it?

Most of what follows guards the ledger's honesty rather than its arithmetic. A
record that can be quietly rewritten after the result is a story, and a story
about your own forecasting is worth less than nothing.
"""

from __future__ import annotations

import json

import pytest

from liga_record_mcp.source import SquadSourceError
from liga_record_mcp.source.decisions import (
    FORMAT,
    holidays_used,
    load_decisions,
    record_decision,
    record_season_choice,
    recorded_rounds,
    save_decisions,
    track_record,
)


def fresh():
    return load_decisions("nowhere/at/all.json")


# --------------------------------------------------------------------------
# Reading and writing
# --------------------------------------------------------------------------


def test_a_ledger_that_does_not_exist_yet_is_not_an_error():
    """It is the normal state before the first round."""
    assert recorded_rounds(fresh()) == []


def test_a_corrupt_ledger_is_an_error_rather_than_an_empty_one():
    """Silently starting again would lose a season's record and look like a
    fresh start, which is the one failure that cannot be noticed later."""
    with pytest.raises(SquadSourceError):
        load_decisions(__file__)


def test_a_ledger_from_another_format_fails_loudly(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"format": FORMAT + 1, "rounds": {}}), encoding="utf-8")
    with pytest.raises(SquadSourceError):
        load_decisions(path)


def test_a_round_survives_a_round_trip(tmp_path):
    store = fresh()
    record_decision(store, 7, our_points=44, transfer_out="A", transfer_in="B")
    path = save_decisions(tmp_path / "decisions.json", store)
    assert recorded_rounds(load_decisions(path)) == [7]


# --------------------------------------------------------------------------
# Honesty
# --------------------------------------------------------------------------


def test_a_recorded_round_is_not_quietly_rewritten():
    """Rewriting a decision after the result turns a record into a story."""
    store = fresh()
    record_decision(store, 7, our_points=44)
    with pytest.raises(SquadSourceError):
        record_decision(store, 7, our_points=99)
    assert store["rounds"]["7"]["our_points"] == 44


def test_it_can_be_rewritten_when_that_is_the_point(tmp_path):
    """A score filled in later, or a typo. Deliberate, never accidental."""
    store = fresh()
    record_decision(store, 7)
    record_decision(store, 7, our_points=44, overwrite=True)
    assert store["rounds"]["7"]["our_points"] == 44


def test_advice_that_was_not_taken_is_kept():
    """The most useful row in the ledger, and the one that disappears if only
    the decisions are written down: a suggestion that was ignored and would
    have worked."""
    store = fresh()
    record_decision(
        store, 7, transfer_out="A", transfer_in="B", suggested_out="C", suggested_in="D"
    )
    entry = store["rounds"]["7"]
    assert entry["suggested"] == {"out": "C", "in": "D"}
    assert entry["followed"] is False


def test_following_the_advice_is_noticed_without_being_claimed():
    store = fresh()
    record_decision(
        store, 7, transfer_out="C", transfer_in="D", suggested_out="C", suggested_in="D"
    )
    assert store["rounds"]["7"]["followed"] is True


def test_making_no_transfer_when_one_was_advised_is_not_following_it():
    """And it is a real decision, not an absence of one — §6.8's allowance does
    not accumulate, so a round skipped is a round spent."""
    store = fresh()
    record_decision(store, 7, suggested_out="C", suggested_in="D")
    assert store["rounds"]["7"]["followed"] is False


def test_no_advice_and_no_transfer_makes_no_claim_either_way():
    store = fresh()
    record_decision(store, 7, our_points=40)
    assert store["rounds"]["7"]["followed"] is None


# --------------------------------------------------------------------------
# What it adds up to
# --------------------------------------------------------------------------


def test_the_holidays_are_counted_and_three_is_the_whole_allowance():
    """§6.17. They do not come back, and a season has three."""
    store = fresh()
    record_decision(store, 5, our_points=30)
    record_decision(store, 9, our_points=59, holiday=True)
    record_decision(store, 14, our_points=48, holiday=True)
    assert holidays_used(store) == [9, 14]
    assert track_record(store)["holidays_left"] == 1


def test_a_rank_larger_than_the_field_is_not_a_wonderful_round():
    """It is a bad reading of the site. Filtering the ranking by team name
    narrows the RESULT, so its page count is the size of the filtered list —
    which once turned a 53rd percentile into 53380%."""
    store = fresh()
    record_decision(store, 5, our_points=40, our_rank=106_760, field_size=20)
    assert track_record(store)["best_percentile"] is None


def test_the_percentile_is_the_best_round_and_lower_is_better():
    store = fresh()
    record_decision(store, 5, our_points=49, our_rank=2_000, field_size=127_260)
    record_decision(store, 6, our_points=38, our_rank=64_000, field_size=127_260)
    assert track_record(store)["best_percentile"] == 1.6


def test_a_season_choice_outlives_the_rounds():
    """§10.3(m)'s bet is made once, when the team is first submitted, and is
    worth up to twenty points nobody notices until May."""
    store = fresh()
    record_season_choice(store, top_scorer="Pavlidis")
    record_decision(store, 5, our_points=40)
    assert track_record(store)["top_scorer_bet"] == "Pavlidis"


def test_an_empty_ledger_reports_nothing_rather_than_zeroes():
    """A per-round average of 0.0 reads as a terrible season, which is a very
    different claim from having played none."""
    summary = track_record(fresh())
    assert summary["rounds"] == 0
    assert summary["per_round"] is None
    assert summary["best"] is None
