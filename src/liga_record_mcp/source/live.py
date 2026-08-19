"""A read-only client for liga.record.pt.

The endpoint behind the site's own player search — `playersearch.ashx` — turns
out to need no authentication. Verified with a plain request carrying no cookie,
which is why the session problem the plan anticipated does not arise for this
half of step 4. Reading a *specific team's* squad still requires a login, so
that stays on the manual source for now.

READ-ONLY BY DESIGN. The site also exposes `team_buysellplayer.ashx` and
`team_renegociateplayer.ashx`, which spend a budget and change a squad. Their
contracts are known — reading the page's own JavaScript revealed them — and they
are deliberately not implemented here. A tool that can read is a different risk
class from a tool that can buy, and confirming a transfer should stay a human's
click on Record's own site.

The parameter contract came from the site's `SearchPlayers()` function rather
than from guesswork: every parameter is required, and `playerposition` takes the
Portuguese codes GR/DF/MD/AV — passing an integer returns an empty array with a
perfectly healthy 200.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..models import MIN_PRICE, Fixture, MarketPlayer, Position

#: The site's own codes. Our Position enum stays English; this is the boundary.
POSITION_CODE: dict[Position, str] = {
    Position.GK: "GR",
    Position.DEF: "DF",
    Position.MID: "MD",
    Position.FWD: "AV",
}
POSITION_FROM_CODE: dict[str, Position] = {c: p for p, c in POSITION_CODE.items()}

#: The bounds the site's own search box uses.
MARKET_MIN_VALUE = MIN_PRICE
MARKET_MAX_VALUE = 12_000_000

ORDER_BY = ("points", "teams")
ORDER_DIR = ("desc", "asc")


class SiteError(RuntimeError):
    """The site could not be read, or came back in a shape we don't know."""


class MarketError(SiteError):
    """Specifically the player market."""


def _percent(raw: Any) -> float:
    """`PercentTeams` arrives as a Portuguese decimal: "25,97"."""
    if raw in (None, ""):
        return 0.0
    try:
        return float(str(raw).replace(",", "."))
    except ValueError as exc:
        raise MarketError(f"could not read ownership percentage {raw!r}") from exc


def parse_market_player(row: dict[str, Any]) -> MarketPlayer:
    """Turn one `playersearch.ashx` row into a MarketPlayer."""
    try:
        code = row["PlayerPosition"]
        position = POSITION_FROM_CODE[code]
        return MarketPlayer(
            id=str(row["IdPlayer"]),
            name=row["Name"],
            position=position,
            club=row["NameClub"],
            value=int(row["CurrentValue"]),
            initial_value=int(row["InitialValue"]),
            points_total=int(row.get("PointsTotal", 0)),
            points_round=int(row.get("Points", 0)),
            owned_by_teams=int(row.get("NumTeams", 0)),
            owned_percent=_percent(row.get("PercentTeams")),
        )
    except KeyError as exc:
        raise MarketError(f"market row is missing {exc}") from exc


# --------------------------------------------------------------------------
# The calendar
#
# Unlike the market, this one has no JSON endpoint — it is parsed out of the
# page. That is brittle by nature, so every selector lives in this one function
# and a recorded copy of the page is checked into tests/fixtures. When the site
# is redesigned, one test fails and one function needs editing.
#
# The whole 34-round season arrives in a single response, so there is no
# pagination to walk.
# --------------------------------------------------------------------------

_ROUND_RE = re.compile(r"(\d+)")
_KICKOFF_RE = re.compile(r"(\d{1,2}\s*\w{3})\s*(\d{1,2}:\d{2})")


def _score(raw: str) -> tuple[int | None, int | None]:
    """`1-1` when played, `-` when not."""
    home, _, away = raw.strip().partition("-")
    try:
        return int(home.strip()), int(away.strip())
    except ValueError:
        return None, None


def _clean(raw: str) -> str:
    """Collapse whitespace, including the non-breaking spaces the site emits."""
    return " ".join((raw or "").replace(" ", " ").split())


def _kickoff(raw: str) -> str | None:
    """The site runs day and time together: `07 AGO20:15`.

    Returns None for rounds not yet scheduled — the far end of the season
    publishes a date but no time, which is a real state and not an error.
    """
    found = _KICKOFF_RE.search(_clean(raw))
    if not found:
        return None
    return f"{_clean(found.group(1))} {found.group(2)}"


def parse_fixtures(html: str) -> list[Fixture]:
    """Every match of the season, from the calendar page."""
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section.calendario")
    if section is None:
        raise SiteError("no calendar section on the page — the layout may have changed")

    fixtures: list[Fixture] = []
    for article in section.select(".slick > article"):
        heading = article.select_one("h2")
        if heading is None:
            continue
        found = _ROUND_RE.search(heading.get_text())
        if not found:
            continue
        round_number = int(found.group(1))

        for row in article.select("div.list ul"):
            teams = row.select("li.team")
            if len(teams) != 2:
                continue
            names = []
            for side in teams:
                label = side.select_one("i")
                names.append(_clean(label.get_text()) if label else "")
            if not all(names):
                continue

            score_cell = row.select_one("li.score")
            home_goals = away_goals = None
            kickoff = None
            if score_cell is not None:
                span = score_cell.select_one("span")
                if span is not None:
                    home_goals, away_goals = _score(span.get_text())
                stamp = score_cell.select_one("time")
                if stamp is not None:
                    kickoff = _kickoff(stamp.get_text())

            fixtures.append(
                Fixture(
                    round_number=round_number,
                    home=names[0],
                    away=names[1],
                    home_goals=home_goals,
                    away_goals=away_goals,
                    kickoff=kickoff,
                )
            )

    if not fixtures:
        raise SiteError("the calendar page held no readable fixtures")
    return fixtures


class LigaRecordClient:
    """Reads the public player market.

    Results are cached per query for `cache_ttl` seconds. Quotes only move when
    a round is scored, so re-fetching per tool call would hammer Record's
    servers to learn nothing. The clock lives here at the boundary, never in
    rules.py.
    """

    BASE_URL = "https://liga.record.pt"
    SEARCH_PATH = "/common/services/playersearch.ashx"
    CALENDAR_PATH = "/info/calendario.aspx"
    name = "ligarecord"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 15.0,
        cache_ttl: float = 900.0,
        fixtures_ttl: float = 21_600.0,
    ) -> None:
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        #: The calendar is a 100 KB page that changes only when a match is
        #: rescheduled, so it is held far longer than a quote.
        self.fixtures_ttl = fixtures_ttl
        self._cache: dict[tuple[Any, ...], tuple[float, list[MarketPlayer]]] = {}
        self._fixtures: tuple[float, list[Fixture]] | None = None

    def fixtures(self) -> list[Fixture]:
        """Every match of the season. One request covers all 34 rounds."""
        now = time.monotonic()
        if self._fixtures is not None and self._fixtures[0] > now:
            return self._fixtures[1]
        parsed = parse_fixtures(self._fetch_text(self.CALENDAR_PATH))
        self._fixtures = (now + self.fixtures_ttl, parsed)
        return parsed

    def _fetch_text(self, path: str) -> str:
        try:
            response = httpx.get(
                f"{self.base_url}{path}",
                timeout=self.timeout,
                headers={"User-Agent": "liga-record-mcp (personal use)"},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SiteError(f"could not reach {path}: {exc}") from exc
        return response.text

    def search(
        self,
        position: Position,
        *,
        name: str = "",
        club: str = "",
        min_value: int = MARKET_MIN_VALUE,
        max_value: int = MARKET_MAX_VALUE,
        order_by: str = "points",
        order_dir: str = "desc",
    ) -> list[MarketPlayer]:
        """Every player of one position matching the filters.

        `position` is required: the endpoint returns an empty array without it.
        """
        if order_by not in ORDER_BY:
            raise MarketError(f"order_by must be one of {ORDER_BY}, got {order_by!r}")
        if order_dir not in ORDER_DIR:
            raise MarketError(f"order_dir must be one of {ORDER_DIR}, got {order_dir!r}")

        params = {
            "playerposition": POSITION_CODE[position],
            "name": name,
            "club": club,
            "minval": str(min_value),
            "maxval": str(max_value),
            "order_by": order_by,
            "order_dir": order_dir,
        }
        key = tuple(sorted(params.items()))
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]

        rows = self._fetch(params)
        players = [parse_market_player(r) for r in rows]
        self._cache[key] = (now + self.cache_ttl, players)
        return players

    def _fetch(self, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{self.base_url}{self.SEARCH_PATH}"
        try:
            response = httpx.get(
                url,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "liga-record-mcp (personal use)"},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketError(f"could not reach the market: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            # A login redirect lands here as HTML rather than an HTTP error.
            raise MarketError(
                f"the market returned {response.headers.get('content-type')}, not JSON"
            ) from exc

        if not isinstance(payload, list):
            raise MarketError(
                f"expected a list of players, got {type(payload).__name__}"
            )
        return payload
