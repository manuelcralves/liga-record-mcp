"""Tests for the appearance record.

Liga Record never publishes who played. §10.3 pays an unused player -1 a round,
so the scoring reveals it — these pin that inference and the store built on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from liga_record_mcp.models import Fixture
from liga_record_mcp.source import (
    SquadSourceError,
    history_for,
    load_appearances,
    record_round,
    recorded_rounds,
    save_appearances,
)
from liga_record_mcp.stats import (
    NO_MATCH,
    PLAYED,
    UNUSED,
    appearance_rate,
    classify_appearance,
    clubs_playing_in,
    last_scored_round,
)

CALENDAR = [
    Fixture(round_number=1, home="Arouca", away="Porto", home_goals=1, away_goals=0),
    Fixture(round_number=1, home="Braga", away="Gil Vicente", home_goals=2, away_goals=2),
    Fixture(round_number=2, home="Porto", away="Arouca", home_goals=3, away_goals=1),
    Fixture(round_number=2, home="Braga", away="Gil Vicente", kickoff="20 AGO 20:30"),
    Fixture(round_number=3, home="Arouca", away="Braga", kickoff="23 AGO 18:00"),
]


# --- reading appearances out of the scoring --------------------------------


def test_the_last_scored_round_is_what_points_round_refers_to():
    assert last_scored_round(CALENDAR) == 2
    assert last_scored_round([f for f in CALENDAR if not f.played]) is None


def test_only_clubs_with_a_completed_match_count():
    assert clubs_playing_in(CALENDAR, 1) == {"Arouca", "Porto", "Braga", "Gil Vicente"}
    # Round 2: Braga v Gil Vicente is postponed, so only two clubs played.
    assert clubs_playing_in(CALENDAR, 2) == {"Porto", "Arouca"}
    assert clubs_playing_in(CALENDAR, 3) == set()


def test_minus_one_reads_as_unused():
    """§10.3 — an unused player scores exactly -1."""
    assert classify_appearance(-1, club_played=True) == UNUSED


def test_anything_else_reads_as_played():
    for score in (-4, -2, 0, 1, 7):
        assert classify_appearance(score, club_played=True) == PLAYED


def test_a_postponed_match_is_not_an_absence():
    """Those players score 0, not -1 — counting it against them would punish
    the club rather than the player."""
    assert classify_appearance(0, club_played=False) == NO_MATCH
    assert classify_appearance(-1, club_played=False) == NO_MATCH


def test_appearance_rate_ignores_rounds_with_no_match():
    history = {"1": PLAYED, "2": NO_MATCH, "3": UNUSED, "4": PLAYED}
    assert appearance_rate(history) == pytest.approx(2 / 3)


def test_appearance_rate_is_none_when_nothing_countable():
    assert appearance_rate({}) is None
    assert appearance_rate({"1": NO_MATCH, "2": NO_MATCH}) is None


def test_a_player_who_always_plays_and_one_who_never_does():
    assert appearance_rate({"1": PLAYED, "2": PLAYED}) == 1.0
    assert appearance_rate({"1": UNUSED, "2": UNUSED}) == 0.0


# --- the store -------------------------------------------------------------


def test_a_missing_file_is_an_empty_store_not_an_error(tmp_path: Path):
    """Nothing recorded yet is the normal state before the first round."""
    store = load_appearances(tmp_path / "none.json")
    assert store["rounds"] == {}
    assert recorded_rounds(store) == []


def test_a_corrupt_file_is_an_error(tmp_path: Path):
    path = tmp_path / "appearances.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="not valid JSON"):
        load_appearances(path)


def test_a_file_from_another_format_is_refused(tmp_path: Path):
    path = tmp_path / "appearances.json"
    path.write_text(json.dumps({"format": 99, "rounds": {}}), encoding="utf-8")
    with pytest.raises(SquadSourceError, match="format 99"):
        load_appearances(path)


def test_recording_round_trips_through_disk(tmp_path: Path):
    path = tmp_path / "appearances.json"
    store = record_round(
        load_appearances(path), 2, {"a": PLAYED, "b": UNUSED},
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    save_appearances(path, store)

    back = load_appearances(path)
    assert recorded_rounds(back) == [2]
    assert history_for(back, "a") == {"2": PLAYED}
    assert back["rounds"]["2"]["recorded_at"].startswith("2026-08-19")


def test_recording_the_same_round_twice_overwrites(tmp_path: Path):
    """The tool must be safe to run repeatedly without double-counting."""
    path = tmp_path / "appearances.json"
    store = record_round(load_appearances(path), 2, {"a": UNUSED})
    store = record_round(store, 2, {"a": PLAYED})
    save_appearances(path, store)

    assert recorded_rounds(load_appearances(path)) == [2]
    assert history_for(load_appearances(path), "a") == {"2": PLAYED}


def test_rounds_accumulate(tmp_path: Path):
    path = tmp_path / "appearances.json"
    store = load_appearances(path)
    for rnd, status in ((1, PLAYED), (2, UNUSED), (3, PLAYED)):
        store = record_round(store, rnd, {"a": status, "b": PLAYED})
    save_appearances(path, store)

    back = load_appearances(path)
    assert recorded_rounds(back) == [1, 2, 3]
    assert appearance_rate(history_for(back, "a")) == pytest.approx(2 / 3)
    assert appearance_rate(history_for(back, "b")) == 1.0


def test_an_unknown_player_has_no_history(tmp_path: Path):
    store = record_round(load_appearances(tmp_path / "x.json"), 1, {"a": PLAYED})
    assert history_for(store, "nobody") == {}
