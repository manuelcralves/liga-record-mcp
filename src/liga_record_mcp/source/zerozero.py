"""A slow, cached reader for zerozero.pt.

WHY THIS EXISTS. The historical prior in this project is entirely team-level:
openfootball gives 306 match results a season and not one player name, so the
model knows nothing about any individual before the current campaign. It reads
a player as "a forward at this club at this price". zerozero publishes what is
missing — appearances, minutes, goals and assists, per season and per
competition.

WHY THIS SITE AND NOT ANOTHER. zerozero's robots.txt disallows a single
internal path and publishes sitemaps for players, matches, teams and coaches:
it invites crawlers. SofaScore returns 403 on its own robots.txt and fbref
sits behind a Cloudflare challenge — both are saying no, and no amount of
patience changes that. Reading politely here is ordinary; working around a
refusal there would not be, so it is not done.

HOW IT BEHAVES. One request at a time, a real pause between them, an honest
User-Agent, and every page written to disk so it is never fetched twice. A
full sweep of a squad is a few dozen reads spread over a couple of minutes.

WHAT IT REFUSES. Matching a Liga Record name to a zerozero player is where
this kind of work fails silently. "Pavlidis" returns four players — Vangelis,
Vasilis, Charalambos, Giorgos — and picking the wrong one would poison the
model without ever raising an error. A match is accepted only when exactly one
candidate's club agrees; anything else returns None and says why.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from .base import SquadSourceError


class ZeroZeroError(SquadSourceError):
    """zerozero could not be read, or came back in a shape we don't know."""


#: Liga Record and zerozero write the same clubs differently. Only the ones
#: that actually differ are listed; the rest match once accents and case are
#: folded away.
CLUB_ALIASES: dict[str, tuple[str, ...]] = {
    "FC Porto": ("porto",),
    "Sp. Braga": ("braga", "sc braga", "sporting braga"),
    "Sporting": ("sporting cp", "sporting clube de portugal"),
    "V. Guimarães": ("vitoria sc", "vitoria guimaraes", "guimaraes"),
    "Santa Clara": ("santa clara",),
    "Gil Vicente": ("gil vicente",),
    # zerozero writes this one "Est. Amadora": the whole club refused
    # on a sweep because no alias reached it.
    "E. Amadora": ("est amadora", "estrela amadora", "estrela da amadora"),
    "Casa Pia": ("casa pia",),
    "Rio Ave": ("rio ave",),
    "Marítimo": ("maritimo",),
    "Académico": ("academico viseu", "academico de viseu", "ac viseu"),
    "Alverca": ("alverca",),
    "Arouca": ("arouca",),
    "Benfica": ("benfica",),
    "Estoril": ("estoril", "estoril praia"),
    "Famalicão": ("famalicao",),
    "Moreirense": ("moreirense",),
    "Nacional": ("nacional",),
}


#: zerozero's own page for each Primeira Liga club, resolved once by searching
#: and pinned here so a sweep does not re-derive them. Used only as a fallback
#: when a name search is inconclusive: a wrong path here cannot cause a wrong
#: match, because the player simply will not be in that squad and the lookup
#: refuses.
CLUB_PATHS: dict[str, str] = {
    "Académico": "/equipa/academico/2181",
    "Alverca": "/equipa/fc-alverca/1",
    "Arouca": "/equipa/fc-arouca/3555",
    "Benfica": "/equipa/benfica",
    "Casa Pia": "/equipa/casa-pia-ac/2412",
    # 300706 is the under-10 side. zerozero keeps every age group under
    # the same slug, and the search returns them all.
    "E. Amadora": "/equipa/est-amadora/253884",
    "Estoril": "/equipa/estoril-praia/1734",
    "FC Porto": "/equipa/fc-porto",
    "Famalicão": "/equipa/fc-famalicao/2175",
    "Gil Vicente": "/equipa/gil-vicente",
    "Marítimo": "/equipa/maritimo",
    "Moreirense": "/equipa/moreirense/6",
    "Nacional": "/equipa/nacional/27",
    "Rio Ave": "/equipa/rio-ave/31",
    "Santa Clara": "/equipa/santa-clara/32",
    "Sp. Braga": "/equipa/sc-braga",
    "Sporting": "/equipa/sporting",
    "V. Guimarães": "/equipa/vitoria-sc",
}


def fold(text: str) -> str:
    """Lowercase, strip accents and collapse spaces, for comparing names."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())


def _words(text: str) -> set[str]:
    """Folded words, with punctuation stripped, for comparing club names."""
    return {w for w in re.split(r"[^\w]+", fold(text)) if w}


def clubs_agree(liga_record: str, zerozero: str) -> bool:
    """Whether a Liga Record club name and a zerozero one are the same club.

    Compared word by word rather than as substrings. Substring matching looked
    fine and was not: "Nacional" matched "Internacional", and matched "Nigéria
    (Dupla Nacionalidade)", which is not even a club. That produced two
    candidates for a player who had one, and the only reason it surfaced is
    that the refusal reported what it had seen.
    """
    theirs = _words(zerozero)
    if not theirs:
        return False
    for name in (liga_record, *CLUB_ALIASES.get(liga_record, ())):
        ours = _words(name)
        if ours and (ours <= theirs or theirs <= ours):
            return True
    return False


class ZeroZeroPlayer(BaseModel):
    """One search result, enough to decide whether it is the right person."""

    model_config = ConfigDict(frozen=True)

    zid: str
    path: str
    name: str
    alt_name: str = ""
    club: str = ""
    position: str = ""


class ZeroZeroSeason(BaseModel):
    """One competition's line from a player's season table."""

    model_config = ConfigDict(frozen=True)

    competition: str
    matches: int
    minutes: int
    goals: int = 0
    assists: int = 0
    #: Keepers get a conceded column where everyone else gets goals scored.
    conceded: int | None = None

    @property
    def minutes_per_match(self) -> float | None:
        """None rather than 0.0 when he has not played — the same rule as
        everywhere else in this project."""
        if self.matches <= 0:
            return None
        return self.minutes / self.matches


_ID_RE = re.compile(r"/jogador/[^/]+/(\d+)")


def _number(raw: str) -> int:
    """A table cell as an integer; blanks and dashes read as zero."""
    digits = re.sub(r"[^\d-]", "", raw or "")
    try:
        return int(digits)
    except ValueError:
        return 0


def parse_search(html: str) -> list[ZeroZeroPlayer]:
    """Every player on a search results page.

    The club lives in `div.details` with the birthplace and date in a nested
    `div.local`; removing the inner node leaves the club alone, which is
    steadier than slicing the combined string.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: list[ZeroZeroPlayer] = []
    for item in soup.select(".zz-search-item.player"):
        link = item.select_one("a.title")
        if link is None or not link.get("href"):
            continue
        href = link["href"].split("?")[0]
        match = _ID_RE.search(href)
        if match is None:
            continue

        # The title carries a shirt number in its own span; drop it.
        title = link.get_text(" ", strip=True)
        number = link.select_one("span")
        if number is not None:
            title = title.replace(number.get_text(strip=True), "", 1)

        details = item.select_one("div.details")
        club = ""
        if details is not None:
            copy = BeautifulSoup(str(details), "html.parser")
            local = copy.select_one("div.local")
            if local is not None:
                local.decompose()
            club = " ".join(copy.get_text(" ", strip=True).split())

        subtitle = item.select_one("div.subtitle")
        position = item.select_one("span.stamp.position")
        found.append(
            ZeroZeroPlayer(
                zid=match.group(1),
                path=href,
                name=" ".join(title.split()),
                alt_name=subtitle.get_text(" ", strip=True) if subtitle else "",
                club=club,
                position=position.get_text(strip=True) if position else "",
            )
        )
    return found


def parse_seasons(html: str) -> list[ZeroZeroSeason]:
    """The current season's competition lines from a player page.

    The first table on the page is the per-competition breakdown, with columns
    J (matches), M (minutes), GM (goals) and AST (assists). The Total row is
    dropped — it is the sum of the rest, and keeping it would double every
    figure a caller adds up.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table")
    if table is None:
        raise ZeroZeroError("no table on the player page — the layout may have changed")

    headers = [h.get_text(strip=True).upper() for h in table.select("th")]
    # Columns are read by name, not by position: a keeper's table carries GS
    # (goals conceded) exactly where an outfielder's carries GM (goals scored),
    # and reading the fourth cell either way would have filed every keeper's
    # conceded goals as goals he had scored.
    index = {name: i for i, name in enumerate(headers) if name}
    if not {"J", "M"} <= set(index):
        raise ZeroZeroError(f"unfamiliar columns on the player page: {headers}")

    def cell(cells: list[str], name: str) -> int:
        position = index.get(name)
        if position is None or position >= len(cells):
            return 0
        return _number(cells[position])

    rows: list[ZeroZeroSeason] = []
    for row in table.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.select("td")]
        if len(cells) < 3:
            continue
        competition = " ".join(cells[0].split())
        if not competition or fold(competition) == "total":
            continue
        rows.append(
            ZeroZeroSeason(
                competition=competition,
                matches=cell(cells, "J"),
                minutes=cell(cells, "M"),
                goals=cell(cells, "GM"),
                assists=cell(cells, "AST"),
                conceded=cell(cells, "GS") if "GS" in index else None,
            )
        )
    return rows


def pick(
    candidates: list[ZeroZeroPlayer], *, club: str
) -> tuple[ZeroZeroPlayer | None, str]:
    """The one candidate at the right club, or None and the reason.

    Refusing is the whole point. A wrong match here would feed one footballer's
    minutes into another's projection and nothing downstream would notice.
    """
    if not candidates:
        return None, "no candidate returned by the search"
    agreeing = [c for c in candidates if clubs_agree(club, c.club)]
    if not agreeing:
        seen = ", ".join(sorted({c.club for c in candidates if c.club})) or "none"
        return None, f"no candidate plays for {club} (clubs seen: {seen})"
    if len(agreeing) > 1:
        names = ", ".join(c.name for c in agreeing)
        return None, f"{len(agreeing)} candidates at {club}: {names}"
    return agreeing[0], "matched on club"


class ZeroZeroClient:
    """Reads zerozero slowly, once per page, and remembers what it read.

    The cache is on disk rather than in memory because the point is to fetch
    each page once ever, not once per process.
    """

    BASE_URL = "https://www.zerozero.pt"
    SEARCH_PATH = "/pesquisa"
    USER_AGENT = "liga-record-mcp/0.1 (personal project; slow, cached reads)"

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        base_url: str | None = None,
        timeout: float = 40.0,
        pause: float = 2.0,
    ) -> None:
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        #: Seconds between requests. Deliberately unhurried: nothing here is
        #: time-critical and the courtesy costs us nothing.
        self.pause = pause
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0
        self.requests_made = 0

    def _cached(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
        return self.cache_dir / f"{safe}.html"

    def _fetch(self, path: str, params: dict[str, str] | None = None) -> str:
        key = path + ("?" + json.dumps(params, sort_keys=True) if params else "")
        cached = self._cached(key)
        if cached.exists():
            return cached.read_text(encoding="utf-8")

        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.pause:
            time.sleep(self.pause - elapsed)

        try:
            response = httpx.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": self.USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ZeroZeroError(f"could not read {path}: {exc}") from exc
        finally:
            self._last_request = time.monotonic()

        self.requests_made += 1
        cached.write_text(response.text, encoding="utf-8")
        return response.text

    def search(self, name: str) -> list[ZeroZeroPlayer]:
        return parse_search(self._fetch(self.SEARCH_PATH, {"search_txt": name}))

    def seasons(self, player: ZeroZeroPlayer) -> list[ZeroZeroSeason]:
        return parse_seasons(self._fetch(player.path))

    def squad(self, club: str) -> list[ZeroZeroPlayer]:
        """Every player on a club's zerozero page, as bare name and link.

        The squad page carries no club column — it does not need one, since
        being on the page is the claim — so these come back with the Liga
        Record club name attached.
        """
        path = CLUB_PATHS.get(club)
        if path is None:
            return []
        soup = BeautifulSoup(self._fetch(path), "html.parser")
        seen: dict[str, ZeroZeroPlayer] = {}
        for link in soup.select('a[href*="/jogador/"]'):
            href = (link.get("href") or "").split("?")[0]
            match = _ID_RE.search(href)
            name = " ".join(link.get_text(" ", strip=True).split())
            if match is None or not name or match.group(1) in seen:
                continue
            seen[match.group(1)] = ZeroZeroPlayer(
                zid=match.group(1), path=href, name=name, club=club
            )
        if not seen:
            # A top-flight squad page lists dozens. None means the path is
            # wrong, and the wrongness is easy to miss: zerozero files every
            # age group under one slug, and this pointed at an under-10 side
            # for a while. Returning empty blamed the player — "no candidate
            # plays for E. Amadora" — for a mistake in this table.
            raise ZeroZeroError(
                f"{path} lists no players, so it is not {club}'s senior squad"
            )
        return list(seen.values())

    def find(self, name: str, club: str) -> tuple[ZeroZeroPlayer | None, str]:
        """Find a Liga Record player on zerozero, or refuse and say why.

        Two passes. The name search settles most of them on the club shown in
        the results. One-word nicknames defeat it — "Lucão" and "Samu" return
        pages of unrelated footballers and the right one is nowhere near the
        top — so the fallback reads the club's own squad page, where being
        listed is itself the evidence.

        The fallback cannot introduce a wrong match: if the club path were
        wrong, the name would not appear in that squad and this still refuses.
        """
        chosen, why = pick(self.search(name), club=club)
        if chosen is not None:
            return chosen, why

        wanted = fold(name)
        if not wanted:
            return None, why
        agreeing = [
            p
            for p in self.squad(club)
            if wanted == fold(p.name) or wanted in fold(p.name).split()
        ]
        if len(agreeing) == 1:
            return agreeing[0], "matched on the club's squad page"
        if len(agreeing) > 1:
            names = ", ".join(p.name for p in agreeing)
            return None, f"{len(agreeing)} players named {name} at {club}: {names}"
        return None, why
