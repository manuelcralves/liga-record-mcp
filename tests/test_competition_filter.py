"""Telling the Primeira Liga apart from every other country's first division.

zerozero labels a competition by its tier and not by its country, so "D1" is
the Swiss Super League, the Belgian Pro League and the Danish Superliga just as
much as it is the Primeira Liga. A season filtered to "D1" keeps all of them.

It cost 9% of a first reconstruction, and it was almost invisible: the only
outward sign was a handful of players with thirty-seven matches in a
thirty-four-round league. Everything else looked like football.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.source.history import SeasonFixture, parse_season_fixtures
from liga_record_mcp.source.live import SiteError
from liga_record_mcp.stats import clubs_in_calendar


def calendar(*rows):
    return [
        {"round": r, "date": d, "home_goals": h, "away_goals": a} for r, d, h, a in rows
    ]


def appearance(club, round_number, date, home_goals, away_goals):
    return {
        "club": club,
        "round": round_number,
        "date": date,
        "home_goals": home_goals,
        "away_goals": away_goals,
    }


# --------------------------------------------------------------------------
# Deciding who is in the competition
# --------------------------------------------------------------------------


def test_a_club_whose_matches_are_in_the_calendar_belongs():
    known = calendar((1, "2025-08-08", 0, 2), (2, "2025-08-15", 1, 1))
    seen = [
        appearance("Casa Pia AC", 1, "2025/08/08", 0, 2),
        appearance("Casa Pia AC", 2, "2025/08/15", 1, 1),
    ]
    assert clubs_in_calendar(seen, known)["Casa Pia AC"]["member"] is True


def test_another_countrys_first_division_does_not():
    """The whole reason this exists. Nothing about a Swiss match says Swiss —
    it has a round, a date, a scoreline and a rating, exactly like a real one."""
    known = calendar((1, "2025-08-08", 0, 2))
    swiss = [
        appearance("Luzern", 1, "2025/07/26", 2, 1),
        appearance("Luzern", 2, "2025/08/02", 3, 2),
    ]
    verdict = clubs_in_calendar(swiss, known)["Luzern"]
    assert verdict["member"] is False
    assert verdict["share"] == 0.0


def test_a_member_survives_the_sources_disagreeing_about_one_result():
    """These two archives disagree about three matches of the final round —
    one says Casa Pia beat Rio Ave 2-1, the other says they drew 1-1. Judging
    match by match would throw away a real round of real football over it."""
    known = calendar(
        (32, "2026-05-02", 1, 0), (33, "2026-05-09", 2, 0), (34, "2026-05-16", 2, 1)
    )
    seen = [
        appearance("Casa Pia AC", 32, "2026/05/02", 1, 0),
        appearance("Casa Pia AC", 33, "2026/05/09", 2, 0),
        appearance("Casa Pia AC", 34, "2026/05/16", 1, 1),  # the disputed one
    ]
    verdict = clubs_in_calendar(seen, known)["Casa Pia AC"]
    assert verdict["member"] is True
    assert verdict["in_calendar"] == 2
    assert verdict["matches"] == 3


def test_the_two_sources_may_punctuate_a_date_differently():
    """One writes 2026/05/16 and the other 2026-05-16, and comparing them raw
    would put every club at zero and empty the season."""
    known = calendar((1, "2025-08-08", 3, 1))
    seen = [appearance("Benfica", 1, "2025/08/08", 3, 1)]
    assert clubs_in_calendar(seen, known)["Benfica"]["share"] == 1.0


def test_a_scoreline_is_compared_the_way_the_calendar_writes_it():
    """The calendar is home-then-away. A row entered from the away side with
    the goals the other way round matches nothing."""
    known = calendar((1, "2025-08-08", 0, 2))
    right = [appearance("Sporting", 1, "2025-08-08", 0, 2)]
    wrong = [appearance("Sporting", 1, "2025-08-08", 2, 0)]
    assert clubs_in_calendar(right, known)["Sporting"]["share"] == 1.0
    assert clubs_in_calendar(wrong, known)["Sporting"]["share"] == 0.0


def test_every_club_seen_is_reported_including_the_excluded_ones():
    """A filter that drops 9% of the data in silence is how the problem got in
    to begin with."""
    known = calendar((1, "2025-08-08", 0, 2))
    seen = [
        appearance("Casa Pia AC", 1, "2025-08-08", 0, 2),
        appearance("Luzern", 1, "2025-07-26", 2, 1),
    ]
    verdicts = clubs_in_calendar(seen, known)
    assert set(verdicts) == {"Casa Pia AC", "Luzern"}
    assert verdicts["Luzern"]["matches"] == 1


def test_a_relegated_club_is_kept_and_a_promoted_one_is_not_assumed():
    """Membership is of last season's league, not this one. AVS and Tondela
    were in it and are not in this one; a list of current clubs would have to
    be maintained backwards through time, and would be wrong the first year
    nobody remembered to."""
    known = calendar((1, "2025-08-08", 1, 0), (2, "2025-08-15", 0, 3))
    seen = [
        appearance("CD Tondela", 1, "2025-08-08", 1, 0),
        appearance("CD Tondela", 2, "2025-08-15", 0, 3),
    ]
    assert clubs_in_calendar(seen, known)["CD Tondela"]["member"] is True


def test_nothing_seen_is_nothing_claimed():
    assert clubs_in_calendar([], calendar((1, "2025-08-08", 0, 2))) == {}


# --------------------------------------------------------------------------
# Reading the calendar itself
# --------------------------------------------------------------------------


def test_a_payload_without_matches_is_refused():
    with pytest.raises(SiteError):
        parse_season_fixtures({"name": "Primeira Liga"})


def test_a_fixture_not_yet_played_is_left_out():
    """A calendar entry with no score cannot confirm anything, and keeping it
    with zeros would confirm every 0-0 in Europe."""
    parsed = parse_season_fixtures(
        {
            "matches": [
                {"round": "Matchday 1", "date": "2025-08-08", "team1": "A", "team2": "B"},
                {
                    "round": "Matchday 1",
                    "date": "2025-08-08",
                    "team1": "C",
                    "team2": "D",
                    "score": {"ft": [2, 1]},
                },
            ]
        }
    )
    assert [f.home for f in parsed] == ["C"]


def test_both_shapes_of_score_are_read():
    """openfootball is not consistent: some rows nest `ft`, others are bare."""
    parsed = parse_season_fixtures(
        {
            "matches": [
                {"round": "Matchday 3", "date": "2025-09-01", "team1": "A", "team2": "B",
                 "score": {"ft": [1, 0]}},
                {"round": "Matchday 3", "date": "2025-09-01", "team1": "C", "team2": "D",
                 "score": [0, 4]},
            ]
        }
    )
    assert [(f.home_goals, f.away_goals) for f in parsed] == [(1, 0), (0, 4)]


def test_the_round_is_read_out_of_its_label():
    parsed = parse_season_fixtures(
        {
            "matches": [
                {"round": "Matchday 12", "date": "2025-11-02", "team1": "A",
                 "team2": "B", "score": {"ft": [1, 1]}}
            ]
        }
    )
    assert parsed[0] == SeasonFixture(
        round_number=12, date="2025-11-02", home="A", away="B",
        home_goals=1, away_goals=1,
    )
