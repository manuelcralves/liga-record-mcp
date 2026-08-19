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

import time
from typing import Any

import httpx

from ..models import MIN_PRICE, MarketPlayer, Position

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


class MarketError(RuntimeError):
    """The market could not be read, or came back in a shape we don't know."""


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


class LigaRecordClient:
    """Reads the public player market.

    Results are cached per query for `cache_ttl` seconds. Quotes only move when
    a round is scored, so re-fetching per tool call would hammer Record's
    servers to learn nothing. The clock lives here at the boundary, never in
    rules.py.
    """

    BASE_URL = "https://liga.record.pt"
    SEARCH_PATH = "/common/services/playersearch.ashx"
    name = "ligarecord"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 15.0,
        cache_ttl: float = 900.0,
    ) -> None:
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple[Any, ...], tuple[float, list[MarketPlayer]]] = {}

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
