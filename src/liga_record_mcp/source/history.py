"""Completed Primeira Liga seasons, from openfootball.

Open data on GitHub: no key, no account, no terms restricting collection —
which is why it is used here rather than a commercial statistics site. Four
seasons are published, one file each.

What it carries: every match, with date and full-time score. What it does not
carry: anything per player. Minutes and injuries — the single largest effect in
Liga Record, since 42% of the market never takes the field — are not here and
would need a keyed API.

Two clubs in the current league were promoted and have no top-flight record.
They are reported with `has_history` false rather than given a made-up average,
because "we do not know" and "average" are different claims.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from ..models import ClubRecord
from .live import SiteError

#: Liga Record's club labels mapped to openfootball's. The join key is the
#: Liga Record name, since that is what a Player carries.
CLUB_NAMES: dict[str, str | None] = {
    "Sporting": "Sporting Clube de Portugal",
    "Benfica": "Sport Lisboa e Benfica",
    "FC Porto": "FC Porto",
    "Sp. Braga": "Sporting Clube de Braga",
    "V. Guimarães": "Vitória Guimarães",
    "Famalicão": "FC Famalicão",
    "Gil Vicente": "Gil Vicente FC",
    "Arouca": "FC Arouca",
    "Estoril": "GD Estoril Praia",
    "Rio Ave": "Rio Ave FC",
    "Casa Pia": "Casa Pia AC",
    "Moreirense": "Moreirense FC",
    "Nacional": "CD Nacional",
    "Santa Clara": "CD Santa Clara",
    "E. Amadora": "CF Estrela da Amadora",
    "Alverca": "FC Alverca",
    # Promoted for the current season — no Primeira Liga record to draw on.
    "Marítimo": None,
    "Académico": None,
}

#: Newest last. More recent seasons carry more weight when combined.
DEFAULT_SEASONS = ("2024-25", "2025-26")


class SeasonFixture(BaseModel):
    """One completed match from the archive, with its round and its date.

    Separate from `Fixture`, which models the live calendar and carries the
    site's own kickoff label with no year in it. This one is history: the date
    is a full ISO date, and the score is always there, because a match without
    one is not kept.
    """

    model_config = ConfigDict(frozen=True)

    round_number: int
    date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    #: Half time, where the archive carries it. §14.3(b) pays a coach for
    #: coming from behind, and the break is the only moment a public archive
    #: records the state of a match at. It misses a side that went behind and
    #: equalised inside the same half, so it undercounts comebacks rather than
    #: inventing them.
    home_half: int | None = None
    away_half: int | None = None


def parse_season_fixtures(payload: dict[str, Any]) -> list[SeasonFixture]:
    """Every completed match of one season.

    `parse_season` folds the same payload into per-club totals, which is what
    the projection needs. This keeps the matches themselves, which is what it
    takes to tell whether some other source's match really was in this
    competition — zerozero labels the Swiss, Belgian and Danish first divisions
    "D1" exactly as it labels the Portuguese one.
    """
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise SiteError("season payload has no `matches` list")

    found: list[SeasonFixture] = []
    for match in matches:
        result = _full_time(match.get("score"))
        if result is None:
            continue
        label = str(match.get("round") or "")
        digits = "".join(c for c in label if c.isdigit())
        if not digits:
            continue
        home, away = match.get("team1"), match.get("team2")
        date = match.get("date")
        if not (home and away and date):
            continue
        raw = match.get("score")
        interval = raw.get("ht") if isinstance(raw, dict) else None
        half = (
            (int(interval[0]), int(interval[1]))
            if isinstance(interval, (list, tuple)) and len(interval) >= 2
            else (None, None)
        )
        found.append(
            SeasonFixture(
                round_number=int(digits),
                date=str(date),
                home=str(home),
                away=str(away),
                home_goals=result[0],
                away_goals=result[1],
                home_half=half[0],
                away_half=half[1],
            )
        )
    return found


def _full_time(score: Any) -> tuple[int, int] | None:
    """openfootball is not consistent: some rows nest `ft`, others are bare."""
    if not score:
        return None
    raw = score.get("ft") if isinstance(score, dict) else score
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None


def parse_season(payload: dict[str, Any], club_names: dict[str, str | None] | None = None):
    """Per-club totals for one season's match list."""
    names = club_names if club_names is not None else CLUB_NAMES
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise SiteError("season payload has no `matches` list")

    lookup = {source: label for label, source in names.items() if source}
    totals: dict[str, dict[str, int]] = {}
    for match in matches:
        result = _full_time(match.get("score"))
        if result is None:
            continue
        home_goals, away_goals = result
        for source, scored, conceded in (
            (match.get("team1"), home_goals, away_goals),
            (match.get("team2"), away_goals, home_goals),
        ):
            label = lookup.get(source)
            if label is None:
                continue
            row = totals.setdefault(
                label,
                {"matches": 0, "goals_for": 0, "goals_against": 0, "clean_sheets": 0, "wins": 0},
            )
            row["matches"] += 1
            row["goals_for"] += scored
            row["goals_against"] += conceded
            row["clean_sheets"] += 1 if conceded == 0 else 0
            row["wins"] += 1 if scored > conceded else 0
    return totals


def combine(per_season: list[tuple[str, dict[str, dict[str, int]]]]) -> dict[str, ClubRecord]:
    """Fold several seasons into one record per club.

    Seasons are summed rather than averaged so a club with two seasons of
    history counts for more than one with a single season — which is the
    honest weighting when the question is "how reliable is this".
    """
    merged: dict[str, dict[str, Any]] = {}
    for tag, totals in per_season:
        for club, row in totals.items():
            acc = merged.setdefault(club, {"seasons": [], **{k: 0 for k in row}})
            acc["seasons"].append(tag)
            for key, value in row.items():
                acc[key] += value

    records: dict[str, ClubRecord] = {}
    for club, acc in merged.items():
        records[club] = ClubRecord(
            club=club,
            seasons=tuple(acc["seasons"]),
            matches=acc["matches"],
            goals_for=acc["goals_for"],
            goals_against=acc["goals_against"],
            clean_sheets=acc["clean_sheets"],
            wins=acc["wins"],
        )
    # Promoted clubs get an explicit empty record rather than being absent.
    for club, source in CLUB_NAMES.items():
        if source is None:
            records.setdefault(club, ClubRecord(club=club))
    return records


class OpenFootballClient:
    """Reads completed seasons. Cached for a day — they never change."""

    BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
    name = "openfootball"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        seasons: tuple[str, ...] = DEFAULT_SEASONS,
        timeout: float = 20.0,
        cache_ttl: float = 86_400.0,
    ) -> None:
        self.base_url = base_url or self.BASE_URL
        self.seasons = seasons
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: tuple[float, dict[str, ClubRecord]] | None = None

    def club_records(self) -> dict[str, ClubRecord]:
        now = time.monotonic()
        if self._cache is not None and self._cache[0] > now:
            return self._cache[1]

        per_season = []
        for tag in self.seasons:
            per_season.append((tag, parse_season(self._fetch(tag))))
        records = combine(per_season)
        if not records:
            raise SiteError("no club records could be read from the archive")
        self._cache = (now + self.cache_ttl, records)
        return records

    def season_fixtures(self, season: str) -> list[SeasonFixture]:
        """One season's matches, for checking whether a match was in it."""
        return parse_season_fixtures(self._fetch(season))

    def _fetch(self, season: str) -> dict[str, Any]:
        url = f"{self.base_url}/{season}/pt.1.json"
        try:
            response = httpx.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": "liga-record-mcp (personal use)"},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SiteError(f"could not read season {season}: {exc}") from exc
        try:
            return json.loads(response.text)
        except ValueError as exc:
            raise SiteError(f"season {season} is not valid JSON") from exc
