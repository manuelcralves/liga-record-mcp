"""Tests for the calendar parser.

This is the one scraped surface in the project — there is no JSON endpoint for
the calendar — so it is pinned against a recorded copy of the real page. When
Record redesigns, these fail and `parse_fixtures` needs editing. That is the
intended failure mode: loud, in one place, and offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from liga_record_mcp.models import Fixture
from liga_record_mcp.source import SiteError, parse_fixtures

FIXTURE = Path(__file__).parent / "fixtures" / "calendario.html"


@pytest.fixture(scope="module")
def fixtures() -> list[Fixture]:
    return parse_fixtures(FIXTURE.read_text(encoding="utf-8"))


def test_the_whole_season_is_one_page(fixtures: list[Fixture]):
    """18 clubs over 34 rounds is 9 matches a round — no pagination to walk."""
    assert len(fixtures) == 306
    assert min(f.round_number for f in fixtures) == 1
    assert max(f.round_number for f in fixtures) == 34
    clubs = {f.home for f in fixtures} | {f.away for f in fixtures}
    assert len(clubs) == 18


def test_every_round_has_nine_matches(fixtures: list[Fixture]):
    for rnd in range(1, 35):
        assert sum(1 for f in fixtures if f.round_number == rnd) == 9, rnd


def test_a_played_match_carries_its_score(fixtures: list[Fixture]):
    match = next(
        f for f in fixtures if f.round_number == 1 and f.home == "Estoril"
    )
    assert match.away == "Famalicão"
    assert (match.home_goals, match.away_goals) == (1, 1)
    assert match.played is True
    assert match.kickoff == "07 AGO 20:15"


def test_an_unplayed_match_has_no_score(fixtures: list[Fixture]):
    """The site writes `-` rather than omitting the cell."""
    match = next(f for f in fixtures if f.round_number == 4)
    assert match.home_goals is None
    assert match.away_goals is None
    assert match.played is False
    assert match.kickoff  # near rounds are already scheduled


def test_far_future_rounds_have_no_kickoff_time(fixtures: list[Fixture]):
    """Not an error — the far end of the season is dated but not yet timed."""
    late = [f for f in fixtures if f.round_number == 34]
    assert late and all(f.kickoff is None for f in late)


def test_kickoffs_carry_no_non_breaking_spaces(fixtures: list[Fixture]):
    """The site emits U+00A0 between day and month; it must not reach a caller."""
    timed = [f.kickoff for f in fixtures if f.kickoff]
    assert timed
    assert not any(" " in k for k in timed)


def test_home_and_away_are_not_swapped(fixtures: list[Fixture]):
    """The markup mirrors itself — the home badge precedes its name, the away
    badge follows. Reading `li.team` in order is what keeps this straight."""
    match = next(
        f for f in fixtures if f.round_number == 1 and f.home == "V. Guimarães"
    )
    assert match.away == "Arouca"
    assert (match.home_goals, match.away_goals) == (0, 1)


def test_a_club_plays_once_per_round(fixtures: list[Fixture]):
    for rnd in range(1, 35):
        appearing = [
            club
            for f in fixtures
            if f.round_number == rnd
            for club in (f.home, f.away)
        ]
        assert len(appearing) == len(set(appearing)) == 18, rnd


def test_opponent_lookup_works_from_either_side(fixtures: list[Fixture]):
    match = next(f for f in fixtures if f.round_number == 1 and f.home == "Estoril")
    assert match.opponent_of("Estoril") == "Famalicão"
    assert match.opponent_of("Famalicão") == "Estoril"
    assert match.opponent_of("Benfica") is None
    assert match.is_home_for("Estoril") is True
    assert match.is_home_for("Famalicão") is False


def test_club_names_are_left_verbatim(fixtures: list[Fixture]):
    """They are the join key back to a player's club — tidying them breaks it."""
    clubs = {f.home for f in fixtures} | {f.away for f in fixtures}
    for expected in ("Sp. Braga", "V. Guimarães", "FC Porto", "E. Amadora"):
        assert expected in clubs


def test_a_page_without_a_calendar_fails_loudly():
    with pytest.raises(SiteError, match="no calendar section"):
        parse_fixtures("<html><body><p>logged out</p></body></html>")


def test_a_calendar_with_no_rows_fails_loudly():
    empty = '<html><section class="calendario"><div class="slick"></div></section></html>'
    with pytest.raises(SiteError, match="no readable fixtures"):
        parse_fixtures(empty)


def test_malformed_rows_are_skipped_not_crashed():
    """A half-rendered row should cost one fixture, not the whole calendar."""
    html = """
    <section class="calendario"><div class="slick">
      <article><header><h2>1ª jornada</h2></header><div class="list">
        <ul><li class="team"><i>Only One</i></li></ul>
        <ul>
          <li class="team"><img/><i>Benfica</i></li>
          <li class="score"><span>2-0</span><time>07 AGO20:15</time></li>
          <li class="team"><i>Porto</i><img/></li>
        </ul>
      </div></article>
    </div></section>
    """
    parsed = parse_fixtures(html)
    assert len(parsed) == 1
    assert parsed[0].home == "Benfica"
    assert parsed[0].away == "Porto"
