"""Reading zerozero, and refusing to guess who a player is.

The historical prior here is entirely team-level — openfootball carries 306
results a season and not one player name — so the model knows nothing about any
individual before the current campaign. zerozero has the appearances, minutes
and goals that would fix that.

The danger is not the fetching, it is the matching. "Pavlidis" returns four
players, and picking the wrong one would feed one footballer's minutes into
another's projection with nothing downstream noticing. Most of what follows
tests the refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from liga_record_mcp.source.zerozero import (
    ZeroZeroClient,
    ZeroZeroError,
    ZeroZeroPlayer,
    clubs_agree,
    fold,
    parse_search,
    parse_seasons,
    pick,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def search_html() -> str:
    return (FIXTURES / "zerozero_search_pavlidis.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def player_html() -> str:
    return (FIXTURES / "zerozero_player_pavlidis.html").read_text(encoding="utf-8")


def candidate(name: str, club: str) -> ZeroZeroPlayer:
    return ZeroZeroPlayer(zid="1", path="/jogador/x/1", name=name, club=club)


# --------------------------------------------------------------------------
# Reading a search page
# --------------------------------------------------------------------------


def test_a_real_search_page_parses(search_html):
    found = parse_search(search_html)

    assert len(found) >= 4
    assert all(p.zid.isdigit() and p.path.startswith("/jogador/") for p in found)


def test_the_right_player_carries_his_club(search_html):
    vangelis = next(p for p in parse_search(search_html) if p.zid == "459841")

    assert vangelis.name == "Vangelis Pavlidis"
    assert vangelis.club == "Benfica"
    assert vangelis.position == "Ava"


def test_the_shirt_number_is_not_part_of_the_name(search_html):
    """The title node carries a squad number in its own span."""
    vangelis = next(p for p in parse_search(search_html) if p.zid == "459841")

    assert not vangelis.name[0].isdigit()


def test_the_club_is_separated_from_the_birthplace(search_html):
    """`div.details` holds both; the nested `div.local` is the birth line."""
    vangelis = next(p for p in parse_search(search_html) if p.zid == "459841")

    assert "1998" not in vangelis.club
    assert "Grécia" not in vangelis.club


def test_a_page_with_no_results_is_empty_not_an_error():
    assert parse_search("<html><body>nada</body></html>") == []


# --------------------------------------------------------------------------
# Reading a player page
# --------------------------------------------------------------------------


def test_the_season_table_parses(player_html):
    seasons = parse_seasons(player_html)

    league = next(s for s in seasons if "Liga Portuguesa" in s.competition)
    assert (league.matches, league.minutes, league.goals) == (2, 149, 4)


def test_the_total_row_is_dropped(player_html):
    """It is the sum of the others; keeping it doubles anything added up."""
    seasons = parse_seasons(player_html)

    assert all(s.competition.lower() != "total" for s in seasons)
    assert sum(s.goals for s in seasons) == 9  # 5 in Europe, 4 in the league


def test_minutes_per_match_is_none_without_matches():
    """No data and zero are different claims — the rule this project runs on."""
    from liga_record_mcp.source.zerozero import ZeroZeroSeason

    idle = ZeroZeroSeason(competition="x", matches=0, minutes=0, goals=0, assists=0)
    played = ZeroZeroSeason(competition="x", matches=2, minutes=149, goals=4, assists=1)

    assert idle.minutes_per_match is None
    assert played.minutes_per_match == pytest.approx(74.5)


def test_an_unfamiliar_page_is_refused_rather_than_half_read():
    with pytest.raises(ZeroZeroError, match="no table"):
        parse_seasons("<html><body>nada</body></html>")

    with pytest.raises(ZeroZeroError, match="unfamiliar columns"):
        parse_seasons("<table><tr><th>Foo</th><th>Bar</th></tr></table>")


# --------------------------------------------------------------------------
# Matching, which is where this would fail silently
# --------------------------------------------------------------------------


def test_the_real_ambiguity_resolves_on_club(search_html):
    found = parse_search(search_html)
    chosen, why = pick(found, club="Benfica")

    assert chosen is not None and chosen.zid == "459841"
    assert "club" in why


def test_a_wrong_club_refuses_and_says_what_it_saw(search_html):
    chosen, why = pick(parse_search(search_html), club="FC Porto")

    assert chosen is None
    assert "no candidate plays for FC Porto" in why
    assert "Benfica" in why  # the clubs it did see


def test_two_candidates_at_one_club_refuse_rather_than_pick_the_first():
    both = [candidate("A Silva", "Benfica"), candidate("B Silva", "Benfica")]
    chosen, why = pick(both, club="Benfica")

    assert chosen is None
    assert "2 candidates" in why


def test_an_empty_search_refuses():
    chosen, why = pick([], club="Benfica")

    assert chosen is None
    assert "no candidate" in why


def test_club_names_are_matched_across_the_two_sites():
    """Liga Record and zerozero write the same clubs differently."""
    assert clubs_agree("FC Porto", "FC Porto")
    assert clubs_agree("Sp. Braga", "SC Braga")
    assert clubs_agree("V. Guimarães", "Vitória SC")
    assert clubs_agree("E. Amadora", "Estrela da Amadora")
    assert clubs_agree("Marítimo", "Maritimo")


def test_different_clubs_do_not_agree():
    assert not clubs_agree("Benfica", "Sporting")
    assert not clubs_agree("FC Porto", "FC Marko")
    assert not clubs_agree("Benfica", "")


def test_folding_ignores_accents_and_case():
    assert fold("Vitória SC") == fold("VITORIA sc")


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------


def test_a_page_is_read_from_disk_the_second_time(tmp_path, monkeypatch):
    """The point of the cache is one fetch ever, not one per process."""
    calls = []

    class Response:
        text = "<html><body>nada</body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)
    client = ZeroZeroClient(tmp_path, pause=0)

    client.search("Pavlidis")
    client.search("Pavlidis")

    assert len(calls) == 1
    assert client.requests_made == 1

    # A fresh client with the same directory must not fetch again either.
    again = ZeroZeroClient(tmp_path, pause=0)
    again.search("Pavlidis")
    assert len(calls) == 1


def test_a_failed_request_is_reported_not_cached(tmp_path, monkeypatch):
    import httpx

    def fake_get(url, **kwargs):
        raise httpx.ConnectTimeout("nope")

    monkeypatch.setattr("httpx.get", fake_get)
    client = ZeroZeroClient(tmp_path, pause=0)

    with pytest.raises(ZeroZeroError, match="could not read"):
        client.search("Pavlidis")
    assert not list(tmp_path.glob("*.html"))


def test_the_client_identifies_itself_and_does_not_carry_an_email(tmp_path, monkeypatch):
    """An honest User-Agent is the courtesy; a personal address is not ours to
    hand to a site that has nothing to do with the person."""
    seen = {}

    class Response:
        text = "<html></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)
    ZeroZeroClient(tmp_path, pause=0).search("x")

    agent = seen.get("User-Agent", "")
    assert "liga-record-mcp" in agent
    assert "@" not in agent


def test_a_club_name_is_not_matched_as_a_substring():
    """Found by running the real squad: "Nacional" matched "Internacional" and
    even "Nigéria (Dupla Nacionalidade)", giving two candidates to a player who
    had one. Words, not substrings."""
    assert clubs_agree("Nacional", "Nacional")
    assert not clubs_agree("Nacional", "Internacional")
    assert not clubs_agree("Nacional", "Nigéria (Dupla Nacionalidade)")
    assert not clubs_agree("Porto", "Portimonense")


def test_punctuation_does_not_block_a_match():
    assert clubs_agree("Sp. Braga", "S.C. Braga")
    assert clubs_agree("E. Amadora", "Estrela da Amadora")


def test_a_keeper_column_is_read_as_conceded_not_scored():
    """A keeper's table carries GS (conceded) exactly where an outfielder's
    carries GM (scored). Reading the fourth cell either way filed every
    keeper's conceded goals as goals he had scored — found on the real sweep."""
    keeper = parse_seasons(
        "<table><tr><th></th><th>J</th><th>M</th><th>GS</th><th>AST</th></tr>"
        "<tr><td>Liga Portuguesa</td><td>2</td><td>180</td><td>3</td><td>0</td></tr>"
        "</table>"
    )[0]
    assert keeper.goals == 0
    assert keeper.conceded == 3

    outfield = parse_seasons(
        "<table><tr><th></th><th>J</th><th>M</th><th>GM</th><th>AST</th></tr>"
        "<tr><td>Liga Portuguesa</td><td>2</td><td>180</td><td>3</td><td>1</td></tr>"
        "</table>"
    )[0]
    assert outfield.goals == 3
    assert outfield.conceded is None


def test_columns_are_found_wherever_they_sit():
    """Indexed by header rather than position, so a reordered table still reads."""
    row = parse_seasons(
        "<table><tr><th></th><th>AST</th><th>M</th><th>J</th><th>GM</th></tr>"
        "<tr><td>Liga</td><td>2</td><td>150</td><td>2</td><td>4</td></tr></table>"
    )[0]
    assert (row.matches, row.minutes, row.goals, row.assists) == (2, 150, 4, 2)


def test_the_squad_page_is_the_fallback_for_a_nickname(tmp_path, monkeypatch):
    """One-word nicknames defeat the name search — "Lucão" and "Samu" return
    pages of unrelated footballers. Being listed in a club's squad is itself
    the evidence."""
    pages = {
        "/pesquisa": '<div class="zz-search-item player">'
        '<a class="title" href="/jogador/outro-lucao/999">Lucão</a>'
        '<div class="details"><div class="local">Brasil</div>Viettel FC</div></div>',
        "/equipa/gil-vicente": '<a href="/jogador/lucao/491757">Lucão</a>',
    }

    class Response:
        def __init__(self, body):
            self.text = body

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        for path, body in pages.items():
            if url.endswith(path):
                return Response(body)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("httpx.get", fake_get)
    chosen, why = ZeroZeroClient(tmp_path, pause=0).find("Lucão", "Gil Vicente")

    assert chosen is not None and chosen.zid == "491757"
    assert "squad page" in why


def test_the_fallback_cannot_invent_a_match(tmp_path, monkeypatch):
    """If the club path were wrong the name is simply absent, and this refuses
    rather than reaching for whoever is on the page."""

    class Response:
        text = '<a href="/jogador/alguem/1">Outra Pessoa</a>'

        def raise_for_status(self):
            return None

    monkeypatch.setattr("httpx.get", lambda url, **kw: Response())
    chosen, why = ZeroZeroClient(tmp_path, pause=0).find("Lucão", "Gil Vicente")

    assert chosen is None
    assert "no candidate" in why
