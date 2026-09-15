"""The chance of playing, weighted toward the rounds a player has just had.

The season rule weighed every round alike, so it could not tell a man who lost
his place last week from one who has just won it back — the same three games in
five, in a different order. These pin the difference, the size of the window,
and the one guard the live season needs that the harness never did.
"""

from __future__ import annotations

from statistics import mean
from types import SimpleNamespace

import pytest

from liga_record_mcp.advice import recent_playing, valuation
from liga_record_mcp.models import Position
from liga_record_mcp.stats import APPEARANCE_PRIOR


def player(club="Arouca", position=Position.MID):
    return SimpleNamespace(club=club, position=position)


def record(rounds, points_when_played=4.0):
    played = sum(1 for took_part in rounds.values() if took_part)
    return {
        "played": played,
        "points": points_when_played * played,
        "available": len(rounds),
        "rounds": dict(rounds),
    }


def same_record_in_a_different_order():
    return (
        {"dropped": player(), "back": player()},
        {
            "dropped": record({6: True, 7: True, 8: True, 9: False, 10: False}),
            "back": record({6: False, 7: False, 8: True, 9: True, 10: True}),
        },
    )


def test_a_player_dropped_in_his_last_two_rounds_plays_less_than_one_who_played_both():
    players, current = same_record_in_a_different_order()
    view = valuation(players, {}, current, recency=True)
    assert view["dropped"]["playing"] < view["back"]["playing"]


def test_the_season_rule_could_not_tell_them_apart():
    """Why the rule changed: the order of a player's rounds was invisible to it."""
    players, current = same_record_in_a_different_order()
    view = valuation(players, {}, current, recency=False)
    assert view["dropped"]["playing"] == pytest.approx(view["back"]["playing"])


def test_one_round_and_no_archive_is_neither_certain_nor_hopeless():
    """At matchday 7 the official season is one round long."""
    players = {"unused": player(), "started": player()}
    current = {"unused": record({6: False}), "started": record({6: True})}
    view = valuation(players, {}, current, recency=True)
    assert 0.0 < view["unused"]["playing"] < view["started"]["playing"] < 1.0


def test_with_no_rounds_this_season_his_record_is_all_there_is():
    players = {"back_from_injury": player(), "regular": player()}
    archive = {
        "back_from_injury": {"played": 30, "points": 90.0, "available": 34, "each": []}
    }
    current = {"regular": record({6: True})}
    view = valuation(players, archive, current, recency=True)
    league = mean([30 / 34, 1.0])
    assert view["back_from_injury"]["playing"] == pytest.approx(
        (30 + league * APPEARANCE_PRIOR) / (34 + APPEARANCE_PRIOR)
    )


def test_the_rule_by_hand():
    """One played, one not, a record of one in two, the league at a half.

    The record is (1 + 0.5 x 3) / (2 + 3) = 0.5, and the chance is
    (1 + 0.5 x 1) / (2 + 1) = 0.5.
    """
    got = recent_playing(
        record({6: True, 7: False}), played=1, seen=2, league_availability=0.5
    )
    assert got == pytest.approx(0.5)


def test_only_the_last_two_rounds_are_recent():
    """Round 5 is part of his record now, not his news.

    Record (2 + 1.5) / (3 + 3) = 7/12; chance (2 + 7/12) / 3 = 31/36. Had round 5
    counted as recent, it would be (2 + 7/12) / 4.
    """
    got = recent_playing(
        record({5: False, 6: True, 7: True}), played=2, seen=3, league_availability=0.5
    )
    assert got == pytest.approx(31 / 36)


def test_a_season_record_without_its_rounds_fails_loudly():
    players = {"x": player()}
    current = {"x": {"played": 1, "points": 4.0, "available": 1}}
    with pytest.raises(ValueError, match="rounds"):
        valuation(players, {}, current, recency=True)
