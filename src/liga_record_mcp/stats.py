"""Derived statistics. Pure functions, no I/O — same contract as rules.py.

This module exists because of a real mistake. A transfer proposal ranked the
squad on total points while Sp. Braga and Gil Vicente had a postponed fixture
and had played one match fewer than everyone else. Four of the twelve players
it recommended selling were among the best in the squad per match — Fran
Navarro at 6.0 a game looked like a 6-point player next to someone else's 12.

Totals are only comparable when the denominators match. Nothing here presents a
total without the number of matches behind it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import mean

from .models import ClubRecord, Fixture, Player, Position


def matches_played(fixtures: Iterable[Fixture]) -> dict[str, int]:
    """How many matches each club has actually completed.

    A postponed fixture means clubs are mid-season on different counts, which
    is exactly the trap this module exists to close.
    """
    counts: dict[str, int] = {}
    for fixture in fixtures:
        if not fixture.played:
            continue
        for club in (fixture.home, fixture.away):
            counts[club] = counts.get(club, 0) + 1
    return counts


def per_match(points: int, matches: int) -> float | None:
    """Points per match, or None when the club has not played yet.

    None rather than 0.0 on purpose: "no data" and "scored nothing" are
    different claims, and rounding them together is how the original mistake
    got made.
    """
    if matches <= 0:
        return None
    return points / matches


def never_played(player: Player, matches: int) -> bool:
    """True when a player has been left out of every match so far.

    §10.3 gives an unused player -1 a round, so someone sitting at exactly
    minus the number of matches has not taken the field once. Across the
    market that is 42% of players, and the gap between them and a regular
    starter is the largest single effect in the game.
    """
    return matches > 0 and player.points_total == -matches


# --------------------------------------------------------------------------
# Projection
#
# Two matches of form is a thin basis for a season, so a projection blends what
# has been seen with a prior built from things that are known more reliably:
# the club's record over completed seasons, and the price Record itself set
# before a ball was kicked.
#
# Every constant here is a judgement call, named and bounded so it can be
# argued with rather than hidden.
# --------------------------------------------------------------------------

#: How many matches the prior is worth. The variance decomposition on two
#: rounds suggests roughly 1, but that measures week-to-week consistency —
#: largely "does he play" — rather than how well a hot start predicts a season.
#: 4 is deliberately more conservative than the data alone would justify.
PRIOR_STRENGTH = 4.0

#: Price correlates with points at about r = 0.30, so it is a real signal but a
#: weak one. A player at twice his position's average price is credited with
#: 25% more, not 100%.
PRICE_SENSITIVITY = 0.25

CLUB_FACTOR_BOUNDS = (0.6, 1.6)
PRICE_FACTOR_BOUNDS = (0.7, 1.8)

#: Keepers and defenders live off clean sheets; midfielders and forwards off
#: goals scored. The club record is read from whichever end matters.
DEFENSIVE = (Position.GK, Position.DEF)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, value))


def position_baselines(
    players: Sequence[Player], matches_by_club: dict[str, int]
) -> dict[Position, float]:
    """Mean points per match by position, over players who have actually played.

    Players who never took the field are excluded: including their -1 would
    drag every baseline down and describe the bench rather than the league.
    """
    gathered: dict[Position, list[float]] = {}
    for player in players:
        matches = matches_by_club.get(player.club, 0)
        if matches <= 0 or never_played(player, matches):
            continue
        gathered.setdefault(player.position, []).append(player.points_total / matches)
    return {pos: mean(rates) for pos, rates in gathered.items() if rates}


def club_factor(
    record: ClubRecord | None,
    position: Position,
    league_goals_against: float,
    league_goals_for: float,
) -> tuple[float, bool]:
    """How much a club's history lifts or lowers a player in this position.

    Returns the factor and whether it rests on real history. A promoted club
    gets 1.0 and False — neutral, and honestly labelled, rather than an
    invented average.
    """
    if record is None or not record.has_history:
        return 1.0, False

    if position in DEFENSIVE:
        conceded = record.goals_against_per_match or league_goals_against
        if conceded <= 0:
            return CLUB_FACTOR_BOUNDS[1], True
        return _clamp(league_goals_against / conceded, CLUB_FACTOR_BOUNDS), True

    scored = record.goals_for_per_match or league_goals_for
    if league_goals_for <= 0:
        return 1.0, True
    return _clamp(scored / league_goals_for, CLUB_FACTOR_BOUNDS), True


def club_price_index(
    players: Sequence[Player], league_mean_value: float
) -> dict[str, float]:
    """How expensive each club's squad is, relative to the league.

    Needed to stop the projection counting one thing twice. A player's price
    and his club's strength correlate at about r = 0.62 — expensive players
    play for good clubs — so multiplying a raw price factor by a club factor
    credits the same fact twice over.
    """
    if league_mean_value <= 0:
        return {}
    by_club: dict[str, list[int]] = {}
    for player in players:
        by_club.setdefault(player.club, []).append(player.value)
    return {club: mean(vals) / league_mean_value for club, vals in by_club.items() if vals}


def price_factor(
    value: int, position_mean_value: float, club_index: float = 1.0
) -> float:
    """Record's own valuation, damped, and net of the player's club.

    Dividing by `club_index` leaves only the part of a price that is about this
    player rather than about his club — "expensive for a Sporting player" is
    information; "expensive because he plays for Sporting" is already in the
    club factor.
    """
    if position_mean_value <= 0:
        return 1.0
    ratio = (value / position_mean_value) / max(club_index, 0.1)
    return _clamp(1 + PRICE_SENSITIVITY * (ratio - 1), PRICE_FACTOR_BOUNDS)


def project(
    player: Player,
    matches: int,
    record: ClubRecord | None,
    baselines: dict[Position, float],
    position_mean_value: float,
    league_goals_against: float,
    league_goals_for: float,
    *,
    club_index: float = 1.0,
    prior_strength: float = PRIOR_STRENGTH,
    rounds_remaining: int | None = None,
) -> dict[str, object]:
    """Blend observed form with a prior, and show the working.

    The components are returned alongside the answer so the number can be
    explained — an unexplained projection is worse than none, because it
    cannot be argued with.
    """
    observed = per_match(player.points_total, matches)
    baseline = baselines.get(player.position, 0.0)
    club, has_history = club_factor(
        record, player.position, league_goals_against, league_goals_for
    )
    price = price_factor(player.value, position_mean_value, club_index)
    prior = baseline * club * price

    if observed is None:
        projected = prior
        weight = 0.0
    else:
        weight = matches / (matches + prior_strength)
        projected = weight * observed + (1 - weight) * prior

    out: dict[str, object] = {
        "id": player.id,
        "name": player.name,
        "position": player.position.value,
        "club": player.club,
        "value": player.value,
        "matches_played": matches,
        "observed_rate": None if observed is None else round(observed, 2),
        "prior_rate": round(prior, 2),
        "projected_rate": round(projected, 2),
        "weight_on_form": round(weight, 2),
        "components": {
            "position_baseline": round(baseline, 2),
            "club_factor": round(club, 2),
            "price_factor": round(price, 2),
        },
        "club_has_history": has_history,
        "never_played": never_played(player, matches),
    }
    if rounds_remaining is not None:
        out["projected_remaining"] = round(projected * rounds_remaining)
    return out


def rate_rows(
    players: Sequence[Player], matches_by_club: dict[str, int]
) -> list[dict[str, object]]:
    """Players with their scoring rate, best first.

    `value_rate` is points per match per million — the figure that actually
    answers "is this player worth his price", since it normalises both the
    denominator and the cost.
    """
    rows: list[dict[str, object]] = []
    for player in players:
        matches = matches_by_club.get(player.club, 0)
        rate = per_match(player.points_total, matches)
        rows.append(
            {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "value": player.value,
                "points_total": player.points_total,
                "matches_played": matches,
                "points_per_match": None if rate is None else round(rate, 2),
                "value_rate": (
                    None if rate is None else round(rate / (player.value / 1e6), 2)
                ),
                "never_played": never_played(player, matches),
            }
        )
    rows.sort(key=lambda r: (r["points_per_match"] is None, -(r["points_per_match"] or 0)))
    return rows
